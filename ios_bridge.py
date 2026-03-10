import base64
import json
import logging
import os
import platform
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests


class IOSBridgeError(RuntimeError):
    """Explicit iOS bridge runtime error (debug-first, no fake success)."""


class IOSBridge:
    """iOS bridge for macOS using go-ios + WDA with auto tunnel/runwda/readiness/session."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = dict(config or {})
        self._cfg = cfg

        self.enabled = bool(cfg.get("ios_enabled", False))
        self.go_ios_bin = str(cfg.get("ios_go_ios_binary", "go-ios") or "go-ios").strip()
        self.default_udid = str(cfg.get("ios_default_udid", "") or "").strip() or None
        self.wda_base_url = str(cfg.get("ios_wda_base_url", "http://127.0.0.1:8100") or "").rstrip("/")
        self.command_timeout = max(3, int(cfg.get("ios_command_timeout", 20)))

        self.auto_start_tunnel = bool(cfg.get("ios_auto_start_tunnel", True))
        self.auto_start_runwda = bool(cfg.get("ios_auto_start_runwda", True))
        self.wda_ready_timeout = max(3, int(cfg.get("ios_wda_ready_timeout", 40)))
        self.wda_ready_interval = max(0.2, float(cfg.get("ios_wda_ready_interval", 1.5)))
        self.health_check_ensure_session = bool(cfg.get("ios_health_check_ensure_session", True))

        self.logs_dir = Path(str(cfg.get("ios_logs_dir", "./logs") or "./logs"))
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        self._tunnel_proc: Optional[subprocess.Popen] = None
        self._runwda_proc: Optional[subprocess.Popen] = None
        self._tunnel_log_handle = None
        self._runwda_log_handle = None

        self._session_id: Optional[str] = None
        self._session_udid: Optional[str] = None

        parsed = urlparse(self.wda_base_url)
        self._wda_host = parsed.hostname or "127.0.0.1"
        self._wda_port = int(parsed.port or 8100)

    def _ensure_macos(self):
        if platform.system() != "Darwin":
            raise IOSBridgeError(
                f"iOS bridge requires macOS (Darwin). Current platform: {platform.system()}"
            )

    def _ensure_enabled(self):
        if not self.enabled:
            raise IOSBridgeError(
                "iOS bridge is disabled. Set ios_enabled=true in config.json before using iOS actions."
            )

    def _ensure_go_ios(self):
        if shutil.which(self.go_ios_bin) is None:
            raise IOSBridgeError(
                f"go-ios binary not found: {self.go_ios_bin}. Please install go-ios and ensure PATH is correct."
            )

    def _resolve_udid(self, udid: Optional[str]) -> str:
        picked = str(udid or "").strip() or self.default_udid
        if not picked:
            raise IOSBridgeError("Missing iOS device udid. Provide udid or set ios_default_udid in config.")
        return picked

    def _run_cmd(self, args: List[str], timeout: Optional[int] = None) -> str:
        self._ensure_macos()
        self._ensure_enabled()
        self._ensure_go_ios()
        timeout_s = int(timeout or self.command_timeout)

        try:
            proc = subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            if err:
                logging.info(f"go-ios stderr: {err}")
            return out
        except subprocess.TimeoutExpired as e:
            raise IOSBridgeError(f"Command timeout ({timeout_s}s): {' '.join(args)}") from e
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            stdout = (e.stdout or "").strip()
            raise IOSBridgeError(
                f"Command failed: {' '.join(args)} | stdout={stdout} | stderr={stderr}"
            ) from e

    def _wda_get(self, endpoint: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        url = f"{self.wda_base_url}{endpoint}"
        try:
            resp = requests.get(url, timeout=(timeout or self.command_timeout))
        except Exception as e:
            raise IOSBridgeError(f"WDA GET failed: {url} | error={e}") from e

        if resp.status_code >= 400:
            raise IOSBridgeError(f"WDA GET {url} status={resp.status_code}: {resp.text[:300]}")

        try:
            return resp.json()
        except Exception as e:
            raise IOSBridgeError(f"WDA GET {url} returned non-JSON: {resp.text[:200]}") from e

    def _wda_post(self, endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.wda_base_url}{endpoint}"
        try:
            resp = requests.post(url, json=(payload or {}), timeout=self.command_timeout)
        except Exception as e:
            raise IOSBridgeError(f"WDA POST failed: {url} | error={e}") from e

        if resp.status_code >= 400:
            raise IOSBridgeError(f"WDA POST {url} status={resp.status_code}: {resp.text[:300]}")

        try:
            return resp.json()
        except Exception as e:
            raise IOSBridgeError(f"WDA POST {url} returned non-JSON: {resp.text[:200]}") from e

    def _wda_delete(self, endpoint: str) -> Dict[str, Any]:
        url = f"{self.wda_base_url}{endpoint}"
        try:
            resp = requests.delete(url, timeout=self.command_timeout)
        except Exception as e:
            raise IOSBridgeError(f"WDA DELETE failed: {url} | error={e}") from e

        if resp.status_code >= 400:
            raise IOSBridgeError(f"WDA DELETE {url} status={resp.status_code}: {resp.text[:300]}")

        try:
            return resp.json()
        except Exception as e:
            raise IOSBridgeError(f"WDA DELETE {url} returned non-JSON: {resp.text[:200]}") from e

    @staticmethod
    def _wda_value(data: Dict[str, Any]) -> Any:
        if "value" not in data:
            raise IOSBridgeError(f"WDA response missing 'value': {data}")
        return data["value"]

    @staticmethod
    def _tail_text(path: Path, max_lines: int = 30) -> str:
        if not path.exists():
            return ""
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except Exception:
            return ""
        if not lines:
            return ""
        return "\n".join(lines[-max_lines:])

    def _format_template_cmd(self, raw: Any, default: List[str], udid: str) -> List[str]:
        values = {
            "go_ios": self.go_ios_bin,
            "udid": udid,
            "host": self._wda_host,
            "port": str(self._wda_port),
        }

        if isinstance(raw, list):
            parts = [str(x) for x in raw]
        elif isinstance(raw, str) and raw.strip():
            parts = shlex.split(raw.strip())
        else:
            parts = default

        rendered = []
        for item in parts:
            try:
                rendered.append(str(item).format(**values))
            except KeyError as e:
                raise IOSBridgeError(f"Invalid template in iOS command: {item}, missing key {e}") from e

        if not rendered:
            raise IOSBridgeError("Resolved iOS process command is empty")
        return rendered

    @staticmethod
    def _proc_alive(proc: Optional[subprocess.Popen]) -> bool:
        return bool(proc and proc.poll() is None)

    def _start_background_process(self, cmd: List[str], log_path: Path, label: str) -> Tuple[subprocess.Popen, Any]:
        try:
            log_f = open(log_path, "a", encoding="utf-8")
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except Exception as e:
            raise IOSBridgeError(f"Failed to start {label}: {' '.join(cmd)} | error={e}") from e

        time.sleep(0.35)
        if proc.poll() is not None:
            exit_code = proc.returncode
            log_f.flush()
            tail = self._tail_text(log_path)
            log_f.close()
            raise IOSBridgeError(
                f"{label} exited immediately (code={exit_code}). cmd={' '.join(cmd)} | log_tail={tail}"
            )

        return proc, log_f

    def _stop_process(self, proc: Optional[subprocess.Popen], handle: Any, label: str):
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

        if handle:
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass

        logging.info(f"iOS {label} process stopped")

    def _is_wda_ready(self) -> bool:
        try:
            data = self._wda_get("/status", timeout=2)
            _ = self._wda_value(data)
            return True
        except Exception:
            return False

    def _wait_wda_ready(self, timeout_s: Optional[int] = None):
        deadline = time.time() + float(timeout_s or self.wda_ready_timeout)
        last_err = ""

        while time.time() < deadline:
            try:
                data = self._wda_get("/status", timeout=2)
                _ = self._wda_value(data)
                return
            except Exception as e:
                last_err = str(e)
                time.sleep(self.wda_ready_interval)

        tunnel_tail = self._tail_text(self.logs_dir / "ios_tunnel.log")
        runwda_tail = self._tail_text(self.logs_dir / "ios_runwda.log")
        raise IOSBridgeError(
            "WDA not ready within timeout. "
            f"last_error={last_err}; "
            f"tunnel_alive={self._proc_alive(self._tunnel_proc)}; "
            f"runwda_alive={self._proc_alive(self._runwda_proc)}; "
            f"tunnel_log_tail={tunnel_tail}; "
            f"runwda_log_tail={runwda_tail}"
        )

    @staticmethod
    def _extract_udid(device_item: Any) -> str:
        if isinstance(device_item, dict):
            return str(device_item.get("udid", "")).strip()
        if isinstance(device_item, str):
            return device_item.strip()
        return ""

    def discover_devices(self) -> List[Dict[str, Any]]:
        raw = self._run_cmd([self.go_ios_bin, "list", "--json"])
        if not raw:
            raise IOSBridgeError("go-ios list returned empty output")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            # Fallback: plain text list
            devices: List[Dict[str, Any]] = []
            for line in raw.splitlines():
                txt = line.strip()
                if not txt:
                    continue
                devices.append({"udid": txt, "raw": txt})
            if not devices:
                raise IOSBridgeError(f"Unable to parse go-ios device list: {raw}")
            return devices

        if isinstance(parsed, list):
            raw_devices = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get("devices"), list):
            raw_devices = parsed["devices"]
        else:
            raise IOSBridgeError(f"Unexpected go-ios list JSON format: {parsed}")

        devices: List[Dict[str, Any]] = []
        for item in raw_devices:
            udid = self._extract_udid(item)
            if not udid:
                continue
            if isinstance(item, dict):
                normalized = dict(item)
                normalized["udid"] = udid
            else:
                normalized = {"udid": udid, "raw": item}
            devices.append(normalized)

        if not devices:
            raise IOSBridgeError("No iOS devices discovered by go-ios")
        return devices

    def start_tunnel(self, udid: Optional[str] = None) -> Dict[str, Any]:
        self._ensure_macos()
        self._ensure_enabled()
        self._ensure_go_ios()

        active_udid = self._resolve_udid(udid)
        if self._proc_alive(self._tunnel_proc):
            return {"ok": True, "already_running": True}

        cmd = self._format_template_cmd(
            self._cfg.get("ios_tunnel_command"),
            [self.go_ios_bin, "tunnel", "--udid", "{udid}"],
            active_udid,
        )
        log_path = self.logs_dir / "ios_tunnel.log"
        proc, handle = self._start_background_process(cmd, log_path, "tunnel")

        self._tunnel_proc = proc
        self._tunnel_log_handle = handle
        return {"ok": True, "started": True, "command": cmd, "log": str(log_path)}

    def start_runwda(self, udid: Optional[str] = None) -> Dict[str, Any]:
        self._ensure_macos()
        self._ensure_enabled()
        self._ensure_go_ios()

        active_udid = self._resolve_udid(udid)
        if self._proc_alive(self._runwda_proc):
            return {"ok": True, "already_running": True}

        cmd = self._format_template_cmd(
            self._cfg.get("ios_runwda_command"),
            [self.go_ios_bin, "runwda", "--udid", "{udid}"],
            active_udid,
        )
        log_path = self.logs_dir / "ios_runwda.log"
        proc, handle = self._start_background_process(cmd, log_path, "runwda")

        self._runwda_proc = proc
        self._runwda_log_handle = handle
        return {"ok": True, "started": True, "command": cmd, "log": str(log_path)}

    def ensure_wda_ready(self, udid: Optional[str] = None) -> Dict[str, Any]:
        self._ensure_macos()
        self._ensure_enabled()
        self._ensure_go_ios()

        active_udid = self._resolve_udid(udid)
        if self._is_wda_ready():
            return {"ok": True, "wda_ready": True, "auto_started": False, "udid": active_udid}

        actions: Dict[str, Any] = {"tunnel": "skipped", "runwda": "skipped"}

        if self.auto_start_tunnel:
            actions["tunnel"] = self.start_tunnel(active_udid)
        if self.auto_start_runwda:
            actions["runwda"] = self.start_runwda(active_udid)

        if not self.auto_start_tunnel and not self.auto_start_runwda:
            raise IOSBridgeError(
                "WDA not reachable and auto-start disabled (ios_auto_start_tunnel=false and ios_auto_start_runwda=false)."
            )

        self._wait_wda_ready(self.wda_ready_timeout)
        return {
            "ok": True,
            "wda_ready": True,
            "auto_started": True,
            "actions": actions,
            "udid": active_udid,
        }

    def create_session(self, udid: Optional[str] = None, force_new: bool = False) -> Dict[str, Any]:
        active_udid = self._resolve_udid(udid)
        self.ensure_wda_ready(active_udid)

        if self._session_id and self._session_udid == active_udid and not force_new:
            return {"ok": True, "session_id": self._session_id, "reused": True}

        if force_new and self._session_id:
            self.delete_session(ignore_missing=True)

        payload = {"capabilities": {"alwaysMatch": {}, "firstMatch": [{}]}}
        data = self._wda_post("/session", payload)

        value = data.get("value") if isinstance(data, dict) else None
        sid = None
        if isinstance(value, dict):
            sid = value.get("sessionId")
        sid = sid or data.get("sessionId")

        if not sid:
            raise IOSBridgeError(f"WDA create session response missing sessionId: {data}")

        self._session_id = str(sid)
        self._session_udid = active_udid
        return {"ok": True, "session_id": self._session_id, "reused": False}

    def ensure_session(self, udid: Optional[str] = None) -> str:
        return str(self.create_session(udid=udid, force_new=False)["session_id"])

    def delete_session(self, ignore_missing: bool = False) -> Dict[str, Any]:
        if not self._session_id:
            return {"ok": True, "deleted": False, "reason": "no_session"}

        sid = self._session_id
        try:
            _ = self._wda_delete(f"/session/{sid}")
        except IOSBridgeError as e:
            if not ignore_missing:
                raise
            logging.info(f"Delete session ignored: {e}")

        self._session_id = None
        self._session_udid = None
        return {"ok": True, "deleted": True, "session_id": sid}

    def prepare(self, udid: Optional[str] = None, ensure_session: bool = True) -> Dict[str, Any]:
        active_udid = self._resolve_udid(udid)
        prep = self.ensure_wda_ready(active_udid)
        out = {"ok": True, "udid": active_udid, "wda": prep}
        if ensure_session:
            sess = self.create_session(active_udid)
            out["session"] = sess
        return out

    def shutdown(self, stop_processes: bool = True, ignore_errors: bool = True) -> Dict[str, Any]:
        errors: List[str] = []
        try:
            self.delete_session(ignore_missing=True)
        except Exception as e:
            if not ignore_errors:
                raise
            errors.append(str(e))

        if stop_processes:
            try:
                self._stop_process(self._runwda_proc, self._runwda_log_handle, "runwda")
            except Exception as e:
                if not ignore_errors:
                    raise
                errors.append(f"stop runwda failed: {e}")

            try:
                self._stop_process(self._tunnel_proc, self._tunnel_log_handle, "tunnel")
            except Exception as e:
                if not ignore_errors:
                    raise
                errors.append(f"stop tunnel failed: {e}")

            self._runwda_proc = None
            self._runwda_log_handle = None
            self._tunnel_proc = None
            self._tunnel_log_handle = None

        return {"ok": len(errors) == 0, "errors": errors}

    def _session_post(self, endpoint: str, payload: Optional[Dict[str, Any]] = None, udid: Optional[str] = None) -> Dict[str, Any]:
        sid = self.ensure_session(udid=udid)
        try:
            return self._wda_post(f"/session/{sid}{endpoint}", payload or {})
        except IOSBridgeError as e:
            if "invalid session" not in str(e).lower():
                raise
            sid = str(self.create_session(udid=udid, force_new=True)["session_id"])
            return self._wda_post(f"/session/{sid}{endpoint}", payload or {})

    def _session_get(self, endpoint: str, udid: Optional[str] = None) -> Dict[str, Any]:
        sid = self.ensure_session(udid=udid)
        try:
            return self._wda_get(f"/session/{sid}{endpoint}")
        except IOSBridgeError as e:
            if "invalid session" not in str(e).lower():
                raise
            sid = str(self.create_session(udid=udid, force_new=True)["session_id"])
            return self._wda_get(f"/session/{sid}{endpoint}")

    def health_check(self, udid: Optional[str] = None) -> Dict[str, Any]:
        self._ensure_macos()
        self._ensure_enabled()
        self._ensure_go_ios()

        devices = self.discover_devices()
        active_udid = self._resolve_udid(udid)

        found = any(self._extract_udid(d) == active_udid for d in devices)
        if not found:
            raise IOSBridgeError(f"Configured udid not found in go-ios list: {active_udid}")

        prep = self.ensure_wda_ready(active_udid)

        session = None
        if self.health_check_ensure_session:
            session = self.create_session(active_udid)

        wda_status = self._wda_get("/status")
        value = self._wda_value(wda_status)

        return {
            "ok": True,
            "device_count": len(devices),
            "selected_udid": active_udid,
            "wda_state": value,
            "wda_ready": True,
            "session": session,
            "prepare": prep,
            "checked_at": int(time.time()),
        }

    def screenshot(self, udid: Optional[str] = None, out_path: Optional[str] = None) -> str:
        _ = self._resolve_udid(udid)
        payload = self._session_get("/screenshot", udid=udid)
        b64 = str(self._wda_value(payload) or "").strip()
        if not b64:
            raise IOSBridgeError("WDA screenshot returned empty base64 data")

        target = out_path
        if not target:
            tmp_dir = Path("./screenshots")
            tmp_dir.mkdir(parents=True, exist_ok=True)
            target = str(tmp_dir / f"ios_{int(time.time() * 1000)}.png")

        try:
            with open(target, "wb") as f:
                f.write(base64.b64decode(b64))
        except Exception as e:
            raise IOSBridgeError(f"Failed to decode/save iOS screenshot to {target}: {e}") from e

        if not os.path.exists(target) or os.path.getsize(target) < 8:
            raise IOSBridgeError(f"Saved screenshot is invalid: {target}")
        return target

    def tap(self, x: int, y: int, udid: Optional[str] = None) -> Dict[str, Any]:
        data = self._session_post("/wda/tap/0", {"x": int(x), "y": int(y)}, udid=udid)
        return {"ok": True, "value": self._wda_value(data), "session_id": self._session_id}

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.2, udid: Optional[str] = None) -> Dict[str, Any]:
        data = self._session_post(
            "/wda/dragfromtoforduration",
            {
                "fromX": int(x1),
                "fromY": int(y1),
                "toX": int(x2),
                "toY": int(y2),
                "duration": float(duration),
            },
            udid=udid,
        )
        return {"ok": True, "value": self._wda_value(data), "session_id": self._session_id}

    def type_text(self, text: str, udid: Optional[str] = None) -> Dict[str, Any]:
        value = str(text or "")
        if not value:
            raise IOSBridgeError("type_text requires non-empty text")
        data = self._session_post("/wda/keys", {"value": [value]}, udid=udid)
        return {"ok": True, "value": self._wda_value(data), "session_id": self._session_id}

    def source(self, udid: Optional[str] = None) -> str:
        data = self._session_get("/source", udid=udid)
        value = self._wda_value(data)
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def launch_app(self, bundle_id: str, udid: Optional[str] = None) -> Dict[str, Any]:
        bid = str(bundle_id or "").strip()
        if not bid:
            raise IOSBridgeError("launch_app requires bundle_id")
        data = self._session_post("/wda/apps/launch", {"bundleId": bid}, udid=udid)
        return {"ok": True, "value": self._wda_value(data), "session_id": self._session_id}

    def terminate_app(self, bundle_id: str, udid: Optional[str] = None) -> Dict[str, Any]:
        bid = str(bundle_id or "").strip()
        if not bid:
            raise IOSBridgeError("terminate_app requires bundle_id")
        data = self._session_post("/wda/apps/terminate", {"bundleId": bid}, udid=udid)
        return {"ok": True, "value": self._wda_value(data), "session_id": self._session_id}

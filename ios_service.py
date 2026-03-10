import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ios_bridge import IOSBridge, IOSBridgeError


class IOSServiceError(RuntimeError):
    """HTTP/UI-friendly iOS service error with explicit message."""


class IOSBridgeService:
    """Reusable service wrapper so iOS bridge is callable by UI and HTTP API."""

    def __init__(self, bridge: IOSBridge):
        self.bridge = bridge

    @staticmethod
    def from_config(config: Dict[str, Any]) -> "IOSBridgeService":
        return IOSBridgeService(IOSBridge(config))

    @staticmethod
    def from_config_path(config_path: str = "config.json") -> "IOSBridgeService":
        path = Path(config_path)
        if not path.exists():
            raise IOSServiceError(f"Config file not found: {config_path}")
        try:
            cfg = json.loads(path.read_text())
        except Exception as e:
            raise IOSServiceError(f"Invalid config JSON: {config_path} | error={e}") from e
        return IOSBridgeService.from_config(cfg)

    def call(self, action: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p = dict(payload or {})
        name = str(action or "").strip().lower()

        try:
            if name == "discover":
                return {"ok": True, "devices": self.bridge.discover_devices()}
            if name == "prepare":
                return self.bridge.prepare(udid=p.get("udid"), ensure_session=bool(p.get("ensure_session", True)))
            if name == "health":
                return self.bridge.health_check(udid=p.get("udid"))
            if name == "screenshot":
                return {"ok": True, "path": self.bridge.screenshot(udid=p.get("udid"), out_path=p.get("out_path"))}
            if name == "tap":
                return self.bridge.tap(int(p["x"]), int(p["y"]), udid=p.get("udid"))
            if name == "swipe":
                return self.bridge.swipe(
                    int(p["x1"]),
                    int(p["y1"]),
                    int(p["x2"]),
                    int(p["y2"]),
                    float(p.get("duration", 0.2)),
                    udid=p.get("udid"),
                )
            if name == "type":
                return self.bridge.type_text(str(p.get("text", "")), udid=p.get("udid"))
            if name == "source":
                return {"ok": True, "source": self.bridge.source(udid=p.get("udid"))}
            if name == "launch":
                return self.bridge.launch_app(str(p.get("bundle_id", "")), udid=p.get("udid"))
            if name == "terminate":
                return self.bridge.terminate_app(str(p.get("bundle_id", "")), udid=p.get("udid"))
            if name == "session_create":
                return self.bridge.create_session(udid=p.get("udid"), force_new=bool(p.get("force_new", False)))
            if name == "session_delete":
                return self.bridge.delete_session(ignore_missing=bool(p.get("ignore_missing", True)))
            if name == "shutdown":
                return self.bridge.shutdown(
                    stop_processes=bool(p.get("stop_processes", True)),
                    ignore_errors=bool(p.get("ignore_errors", True)),
                )

            raise IOSServiceError(f"Unsupported iOS action: {name}")
        except KeyError as e:
            raise IOSServiceError(f"Missing required field for action={name}: {e}") from e
        except IOSBridgeError as e:
            raise IOSServiceError(str(e)) from e

    @staticmethod
    def health() -> Dict[str, Any]:
        return {"ok": True, "service": "ios-http-api", "timestamp": int(time.time())}

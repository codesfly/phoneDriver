import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Tuple, Type

from ios_service import IOSBridgeService, IOSServiceError


def _load_config(config_path: str) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise IOSServiceError(f"Config file not found: {config_path}")
    try:
        return json.loads(path.read_text())
    except Exception as e:
        raise IOSServiceError(f"Failed to parse config JSON: {config_path} | error={e}") from e


class IOSApiHandler(BaseHTTPRequestHandler):
    service: IOSBridgeService = None  # set by factory

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise IOSServiceError(f"Invalid JSON body: {e}") from e

    def _send_json(self, code: int, payload: Dict[str, Any]):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> Tuple[int, Dict[str, Any]]:
        if self.path == "/health" and self.command == "GET":
            return 200, IOSBridgeService.health()

        if self.path.startswith("/ios/") and self.command == "POST":
            action = self.path[len("/ios/") :].strip().lower()
            if not action:
                raise IOSServiceError("Missing iOS action in URL path, expected /ios/<action>")
            payload = self._read_json_body()
            result = self.service.call(action, payload)
            return 200, result

        return 404, {"ok": False, "error": f"Not found: {self.command} {self.path}"}

    def do_GET(self):  # noqa: N802
        try:
            code, payload = self._route()
            self._send_json(code, payload)
        except IOSServiceError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"Unhandled server error: {e}"})

    def do_POST(self):  # noqa: N802
        try:
            code, payload = self._route()
            self._send_json(code, payload)
        except IOSServiceError as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            self._send_json(500, {"ok": False, "error": f"Unhandled server error: {e}"})

    def log_message(self, fmt: str, *args):
        logging.info("ios_http_api - " + fmt, *args)


def make_server(host: str, port: int, config_path: str) -> Tuple[ThreadingHTTPServer, Type[IOSApiHandler]]:
    cfg = _load_config(config_path)
    service = IOSBridgeService.from_config(cfg)

    class _Handler(IOSApiHandler):
        pass

    _Handler.service = service
    return ThreadingHTTPServer((host, port), _Handler), _Handler


def main():
    parser = argparse.ArgumentParser(description="PhoneDriver iOS minimal HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    server, _ = make_server(args.host, args.port, args.config)
    logging.info("iOS HTTP API listening on http://%s:%s", args.host, args.port)
    logging.info("health endpoint: GET /health")
    logging.info("action endpoint: POST /ios/<action>")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logging.info("iOS HTTP API stopped")


if __name__ == "__main__":
    main()

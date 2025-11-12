from __future__ import annotations

import atexit
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Tuple
from urllib.parse import urlparse

from .config import ConfigManager
from .controller import FanController
from .ipmi import IpmiInterface
from .sdr import SdrRepository
from .telemetry import SensorReader, TelemetryPoller


class AppState:
    def __init__(self, root_dir: Path) -> None:
        config_path = root_dir / "config.json"
        self.config = ConfigManager.load(str(config_path))
        self.ipmi = IpmiInterface()
        self.sdr = SdrRepository(self.ipmi)
        self.sensor_reader = SensorReader(self.ipmi, self.sdr)
        self.telemetry = TelemetryPoller(
            self.sensor_reader,
            interval_seconds=float(self.config.data["poll_interval_seconds"]),
        )
        self.telemetry.start()
        self.controller = FanController(
            self.ipmi,
            min_pwm=self.config.data["min_pwm"],
            max_pwm=self.config.data["max_pwm"],
            default_pwm=self.config.data["default_pwm"],
        )
        self.static_root = Path(__file__).parent / "web"

    def status_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "controller": self.controller.state(),
            "telemetry": self.telemetry.latest(),
            "config": {
                "min_pwm": self.config.data["min_pwm"],
                "max_pwm": self.config.data["max_pwm"],
                "poll_interval_seconds": self.config.data["poll_interval_seconds"],
            },
            "fan_sensors": [rec.name for rec in self.sensor_reader.fan_sensors],
        }
        return payload


class FanHttpHandler(BaseHTTPRequestHandler):
    server_version = "DellFans/0.1"
    app_state: AppState | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/dellfans", "/dellfans/"):
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/dellfans/app.js":
            self._serve_static("app.js", "application/javascript")
            return
        if path == "/dellfans/app.css":
            self._serve_static("app.css", "text/css")
            return
        if path == "/dellfans/api/status":
            self._write_json(HTTPStatus.OK, self._app().status_payload())
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Unknown path")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/dellfans/api/mode":
            self._handle_mode()
            return
        if path == "/dellfans/api/pwm":
            self._handle_pwm()
            return
        if path == "/dellfans/api/limits":
            self._handle_limits()
            return
        if path == "/dellfans/api/poll_interval":
            self._handle_poll_interval()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Unknown path")

    def _handle_mode(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        mode = str(payload.get("mode", "")).lower()
        state = self._app().controller.state()
        if mode == "auto":
            try:
                self._app().controller.set_auto()
            except Exception as exc:
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
        elif mode == "manual":
            pwm = payload.get("target_pwm", state["target_pwm"])
            try:
                self._app().controller.set_manual(int(pwm))
            except Exception as exc:
                self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
        else:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "mode must be auto or manual"})
            return
        self._write_json(HTTPStatus.OK, self._app().status_payload())

    def _handle_pwm(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        value = payload.get("value")
        if value is None:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "value required"})
            return
        try:
            self._app().controller.set_manual(int(value))
        except Exception as exc:
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        self._write_json(HTTPStatus.OK, self._app().status_payload())

    def _handle_limits(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        min_pwm = payload.get("min_pwm")
        max_pwm = payload.get("max_pwm")
        if min_pwm is None or max_pwm is None:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "min_pwm and max_pwm required"})
            return
        try:
            self._app().config.set_limits(int(min_pwm), int(max_pwm))
            self._app().controller.update_limits(
                self._app().config.data["min_pwm"],
                self._app().config.data["max_pwm"],
            )
        except Exception as exc:
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        self._write_json(HTTPStatus.OK, self._app().status_payload())

    def _handle_poll_interval(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        value = payload.get("seconds")
        if value is None:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "seconds required"})
            return
        try:
            seconds = float(value)
            seconds = self._app().config.set_poll_interval(seconds)
            self._app().telemetry.update_interval(seconds)
        except Exception as exc:
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
            return
        self._write_json(HTTPStatus.OK, self._app().status_payload())

    def _serve_static(self, filename: str, content_type: str) -> None:
        path = self._app().static_root / filename
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "file not found")
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid json: {exc}") from exc

    def _write_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        # Reduce noise by writing to stderr only minimal info.
        return

    @classmethod
    def _app(cls) -> AppState:
        if not cls.app_state:
            raise RuntimeError("AppState not initialized")
        return cls.app_state


def run_server(root_dir: Path) -> Tuple[str, int]:
    app_state = AppState(root_dir)
    FanHttpHandler.app_state = app_state
    atexit.register(_handover_to_idrac, app_state)
    host = str(app_state.config.data["listen_host"])
    port = int(app_state.config.data["listen_port"])
    server = ThreadingHTTPServer((host, port), FanHttpHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return host, port


def _handover_to_idrac(app_state: AppState) -> None:
    try:
        app_state.controller.set_auto()
    except Exception:
        pass

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict


DEFAULT_CONFIG: Dict[str, Any] = {
    "min_pwm": 20,
    "max_pwm": 80,
    "default_pwm": 35,
    "poll_interval_seconds": 2.0,
    "listen_host": "0.0.0.0",
    "listen_port": 6180,
}


@dataclass
class ConfigManager:
    path: str
    data: Dict[str, Any]

    @classmethod
    def load(cls, path: str) -> "ConfigManager":
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                content = json.load(handle)
        else:
            content = {}
        data = {**DEFAULT_CONFIG, **content}
        return cls(path=path, data=data)

    def save(self) -> None:
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, indent=2)
        os.replace(tmp_path, self.path)

    def set_limits(self, min_pwm: int, max_pwm: int) -> None:
        min_pwm = max(0, min(100, int(min_pwm)))
        max_pwm = max(min_pwm, min(100, int(max_pwm)))
        self.data["min_pwm"] = min_pwm
        self.data["max_pwm"] = max_pwm
        self.save()

    def set_default_pwm(self, value: int) -> None:
        self.data["default_pwm"] = max(self.data["min_pwm"], min(self.data["max_pwm"], int(value)))
        self.save()

    def set_poll_interval(self, seconds: float) -> float:
        seconds = max(0.5, float(seconds))
        self.data["poll_interval_seconds"] = seconds
        self.save()
        return seconds

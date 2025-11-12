from __future__ import annotations

import threading
from typing import Dict

from .ipmi import IpmiInterface


class FanController:
    """Wraps the Dell OEM raw commands for manual fan overrides."""

    def __init__(self, ipmi: IpmiInterface, min_pwm: int, max_pwm: int, default_pwm: int) -> None:
        self._ipmi = ipmi
        self._lock = threading.Lock()
        self._min_pwm = max(0, min(100, min_pwm))
        self._max_pwm = max(self._min_pwm, min(100, max_pwm))
        self._target_pwm = self._clamp(default_pwm)
        self._mode = "auto"

    def _clamp(self, value: int) -> int:
        return max(self._min_pwm, min(self._max_pwm, int(value)))

    def update_limits(self, min_pwm: int, max_pwm: int) -> None:
        with self._lock:
            self._min_pwm = max(0, min(100, min_pwm))
            self._max_pwm = max(self._min_pwm, min(100, max_pwm))
            self._target_pwm = self._clamp(self._target_pwm)

    def set_manual(self, pwm: int) -> int:
        pwm_value = self._clamp(pwm)
        with self._lock:
            self._ipmi.send_command(0x30, 0x30, bytes([0x01, 0x00]))
            self._ipmi.send_command(0x30, 0x30, bytes([0x02, 0xFF, pwm_value]))
            self._mode = "manual"
            self._target_pwm = pwm_value
            return pwm_value

    def set_auto(self) -> None:
        with self._lock:
            self._ipmi.send_command(0x30, 0x30, bytes([0x01, 0x01]))
            self._mode = "auto"

    def state(self) -> Dict[str, object]:
        with self._lock:
            return {
                "mode": self._mode,
                "target_pwm": self._target_pwm,
                "min_pwm": self._min_pwm,
                "max_pwm": self._max_pwm,
            }

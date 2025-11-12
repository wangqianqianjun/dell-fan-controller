from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .ipmi import IpmiInterface
from .sdr import FullSensorRecord, SdrRepository


@dataclass
class SensorReading:
    name: str
    value: float
    unit: str
    raw: int
    sensor_number: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "raw": self.raw,
            "sensor_number": self.sensor_number,
        }


def _select_default_targets(repo: SdrRepository) -> Dict[str, Optional[FullSensorRecord]]:
    def by_name(partial: str) -> Optional[FullSensorRecord]:
        target = partial.lower()
        return repo.find(lambda rec: target in rec.name.lower())

    targets: Dict[str, Optional[FullSensorRecord]] = {
        "inlet": by_name("inlet temp"),
        "exhaust": by_name("exhaust temp"),
        "cpu1": repo.find(lambda rec: rec.entity_id == 0x03 and rec.entity_instance == 1),
        "cpu2": repo.find(lambda rec: rec.entity_id == 0x03 and rec.entity_instance == 2),
    }
    return targets


def _discover_fan_sensors(repo: SdrRepository) -> List[FullSensorRecord]:
    fans: List[FullSensorRecord] = []
    for rec in repo.all():
        if rec.unit_code == 0x12 and rec.name.lower().startswith("fan"):
            fans.append(rec)
    fans.sort(key=lambda r: r.name)
    return fans


class SensorReader:
    """Reads sensor values on demand."""

    def __init__(self, ipmi: IpmiInterface, repo: SdrRepository) -> None:
        self._ipmi = ipmi
        self._repo = repo
        self.targets = _select_default_targets(repo)
        self.fan_sensors = _discover_fan_sensors(repo)

    def read_sensor(self, record: FullSensorRecord) -> SensorReading:
        resp = self._ipmi.send_command(0x04, 0x2D, bytes([record.sensor_number]))
        if not resp:
            raise RuntimeError("empty sensor reading")
        raw = resp[0]
        status = resp[1] if len(resp) > 1 else 0
        if status & 0x20:
            raise RuntimeError(f"{record.name} reading unavailable")
        value = record.convert_reading(raw)
        return SensorReading(
            name=record.name,
            value=round(value, 2),
            unit=record.unit_label,
            raw=raw,
            sensor_number=record.sensor_number,
        )

    def capture_snapshot(self) -> Dict[str, object]:
        snapshot: Dict[str, object] = {
            "timestamp": time.time(),
            "cpu": {},
            "temps": {},
            "fans": [],
            "errors": [],
        }
        for key in ("cpu1", "cpu2"):
            record = self.targets.get(key)
            if record:
                try:
                    reading = self.read_sensor(record)
                    snapshot["cpu"][key] = reading.to_dict()
                except Exception as exc:
                    snapshot["errors"].append(f"{record.name}: {exc}")
            else:
                snapshot["errors"].append(f"Missing {key} sensor definition")
        for key in ("inlet", "exhaust"):
            record = self.targets.get(key)
            if record:
                try:
                    reading = self.read_sensor(record)
                    snapshot["temps"][key] = reading.to_dict()
                except Exception as exc:
                    snapshot["errors"].append(f"{record.name}: {exc}")
            else:
                snapshot["errors"].append(f"Missing {key} sensor definition")

        fan_readings: List[Dict[str, object]] = []
        for fan in self.fan_sensors:
            try:
                fan_readings.append(self.read_sensor(fan).to_dict())
            except Exception as exc:
                snapshot["errors"].append(f"{fan.name}: {exc}")
        snapshot["fans"] = fan_readings
        return snapshot


class TelemetryPoller:
    """Background poller that refreshes sensor data."""

    def __init__(self, reader: SensorReader, interval_seconds: float = 2.0) -> None:
        self._reader = reader
        self._interval = max(0.5, interval_seconds)
        self._latest: Dict[str, object] = {"timestamp": None, "cpu": {}, "temps": {}, "fans": [], "errors": ["poller_not_started"]}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def latest(self) -> Dict[str, object]:
        with self._lock:
            return dict(self._latest)

    def update_interval(self, seconds: float) -> float:
        interval = max(0.5, float(seconds))
        with self._lock:
            self._interval = interval
        return interval

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                snapshot = self._reader.capture_snapshot()
            except Exception as exc:
                snapshot = {
                    "timestamp": time.time(),
                    "cpu": {},
                    "temps": {},
                    "fans": [],
                    "errors": [f"polling failed: {exc}"],
                }
            with self._lock:
                self._latest = snapshot
            self._stop_event.wait(self._interval)

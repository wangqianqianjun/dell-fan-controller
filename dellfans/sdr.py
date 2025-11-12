from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

from .ipmi import IpmiCompletionError, IpmiInterface

_ID_STRING_OFFSET = 42

_UNIT_LABELS: Dict[int, str] = {
    0x00: "units",
    0x01: "°C",
    0x02: "°F",
    0x03: "K",
    0x04: "V",
    0x05: "A",
    0x06: "W",
    0x12: "RPM",
    0x19: "%",
}


def _sign_extend(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return value - (1 << bits) if value & sign_bit else value


def _decode_bcd_plus(data: bytes, expected_len: int) -> str:
    table = {
        0x0: "0",
        0x1: "1",
        0x2: "2",
        0x3: "3",
        0x4: "4",
        0x5: "5",
        0x6: "6",
        0x7: "7",
        0x8: "8",
        0x9: "9",
        0xA: " ",
        0xB: "-",
        0xC: ".",
        0xD: ":",
        0xE: ",",
        0xF: "_",
    }
    chars: List[str] = []
    total_nibbles = len(data) * 2
    for idx in range(min(expected_len, total_nibbles)):
        byte = data[idx // 2]
        nibble = (byte >> 4) if idx % 2 == 0 else (byte & 0x0F)
        chars.append(table.get(nibble, "?"))
    return "".join(chars)


_SIX_BIT_TABLE = [
    " ", "!", '"', "#", "$", "%", "&", "'", "(", ")", "*", "+", ",", "-", ".", "/",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", ":", ";", "<", "=", ">", "?",
    "@", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O",
    "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", "[", "\\", "]", "^", "_",
]


def _decode_six_bit_ascii(data: bytes, expected_len: int) -> str:
    bits: List[int] = []
    for byte in data:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    chars: List[str] = []
    for idx in range(expected_len):
        chunk = bits[idx * 6 : (idx + 1) * 6]
        if len(chunk) < 6:
            break
        value = 0
        for bit in chunk:
            value = (value << 1) | bit
        chars.append(_SIX_BIT_TABLE[value] if value < len(_SIX_BIT_TABLE) else "?")
    return "".join(chars)


def _decode_id_string(body: bytes) -> str:
    if len(body) <= _ID_STRING_OFFSET:
        return ""
    info = body[_ID_STRING_OFFSET]
    length = info & 0x3F
    encoding = (info >> 6) & 0x03
    start = _ID_STRING_OFFSET + 1
    raw = body[start : start + length]
    if encoding == 0b11:
        return raw.decode("ascii", errors="ignore").strip()
    if encoding == 0b01:
        return _decode_bcd_plus(raw, length).strip()
    if encoding == 0b10:
        return _decode_six_bit_ascii(raw, length).strip()
    # Best effort fallback.
    return raw.decode("ascii", errors="ignore").strip()


@dataclass(frozen=True)
class FullSensorRecord:
    sensor_number: int
    entity_id: int
    entity_instance: int
    sensor_type: int
    event_reading_type: int
    unit_code: int
    unit_label: str
    m: int
    b: int
    r_exp: int
    b_exp: int
    name: str
    raw: bytes

    def convert_reading(self, raw_value: int) -> float:
        """Convert an 8-bit raw reading into engineering units."""
        value = (self.m * raw_value) + (self.b * (10 ** self.b_exp))
        return value * (10 ** self.r_exp)


class SdrRepository:
    """Loads SDR records to map sensor names and conversion factors."""

    def __init__(self, ipmi: IpmiInterface) -> None:
        self._ipmi = ipmi
        self.records: Dict[int, FullSensorRecord] = {}
        self._load_records()

    def _reserve(self) -> int:
        resp = self._ipmi.send_command(0x0A, 0x22)
        if len(resp) < 2:
            raise RuntimeError("unexpected SDR reserve response")
        return resp[0] | (resp[1] << 8)

    def _get_sdr(self, reservation_id: int, record_id: int) -> bytes:
        data = bytes(
            [
                reservation_id & 0xFF,
                (reservation_id >> 8) & 0xFF,
                record_id & 0xFF,
                (record_id >> 8) & 0xFF,
                0x00,
                0xFF,
            ]
        )
        return self._ipmi.send_command(0x0A, 0x23, data)

    def _load_records(self) -> None:
        reservation = self._reserve()
        record_id = 0x0000
        while True:
            try:
                resp = self._get_sdr(reservation, record_id)
            except IpmiCompletionError as exc:
                if exc.code == 0x81:  # Reservation lost.
                    reservation = self._reserve()
                    continue
                raise

            if len(resp) < 5:
                break

            next_id = resp[0] | (resp[1] << 8)
            record = resp[2:]
            if len(record) < 5:
                break

            record_type = record[3]
            record_len = record[4]
            body = record[5 : 5 + record_len]

            if record_type == 0x01 and len(body) > _ID_STRING_OFFSET:
                parsed = self._parse_full_sensor(body)
                if parsed:
                    self.records[parsed.sensor_number] = parsed

            if next_id == 0xFFFF:
                break
            record_id = next_id

    @staticmethod
    def _parse_full_sensor(body: bytes) -> Optional[FullSensorRecord]:
        try:
            sensor_number = body[2]
            entity_id = body[3]
            entity_instance = body[4] & 0x7F
            sensor_type = body[7]
            event_reading_type = body[8]
            unit_code = body[16]
            unit_label = _UNIT_LABELS.get(unit_code, f"code_0x{unit_code:02x}")
            m_raw = ((body[20] & 0xC0) << 2) | body[19]
            b_raw = ((body[22] & 0xC0) << 2) | body[21]
            r_b_exp = body[24]
            m = _sign_extend(m_raw, 10)
            b = _sign_extend(b_raw, 10)
            r_exp = _sign_extend((r_b_exp >> 4) & 0x0F, 4)
            b_exp = _sign_extend(r_b_exp & 0x0F, 4)
            name = _decode_id_string(body)
            return FullSensorRecord(
                sensor_number=sensor_number,
                entity_id=entity_id,
                entity_instance=entity_instance,
                sensor_type=sensor_type,
                event_reading_type=event_reading_type,
                unit_code=unit_code,
                unit_label=unit_label,
                m=m,
                b=b,
                r_exp=r_exp,
                b_exp=b_exp,
                name=name or f"Sensor {sensor_number}",
                raw=body,
            )
        except (IndexError, ValueError):
            return None

    def find(self, predicate: Callable[[FullSensorRecord], bool]) -> Optional[FullSensorRecord]:
        for record in self.records.values():
            if predicate(record):
                return record
        return None

    def all(self) -> Iterable[FullSensorRecord]:
        return self.records.values()

    def by_number(self, sensor_number: int) -> Optional[FullSensorRecord]:
        return self.records.get(sensor_number)

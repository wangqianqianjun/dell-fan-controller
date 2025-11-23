import ctypes
import ctypes.util
import errno
import os
import re
import select
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


class IpmiError(Exception):
    """Base IPMI error."""


class IpmiCompletionError(IpmiError):
    """Raised when the BMC returns a non-zero completion code."""

    def __init__(self, code: int, message: Optional[str] = None):
        super().__init__(message or f"IPMI completion code 0x{code:02x}")
        self.code = code


class IpmiTimeoutError(IpmiError):
    """Raised when the BMC does not respond in time."""


class _IpmiSystemInterfaceAddr(ctypes.Structure):
    _fields_ = [
        ("addr_type", ctypes.c_int),
        ("channel", ctypes.c_short),
        ("lun", ctypes.c_ubyte),
    ]


class _IpmiMsg(ctypes.Structure):
    _fields_ = [
        ("netfn", ctypes.c_ubyte),
        ("cmd", ctypes.c_ubyte),
        ("data_len", ctypes.c_ushort),
        ("data", ctypes.c_void_p),
    ]


class _IpmiReq(ctypes.Structure):
    _fields_ = [
        ("addr", ctypes.c_void_p),
        ("addr_len", ctypes.c_uint),
        ("msgid", ctypes.c_long),
        ("msg", _IpmiMsg),
    ]


class _IpmiRecv(ctypes.Structure):
    _fields_ = [
        ("recv_type", ctypes.c_int),
        ("addr", ctypes.c_void_p),
        ("addr_len", ctypes.c_uint),
        ("msgid", ctypes.c_long),
        ("msg", _IpmiMsg),
    ]


_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_DIRBITS = 2

_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS

_IOC_WRITE = 1
_IOC_READ = 2

IPMI_IOC_MAGIC = ord("i")


def _IOC(direction: int, type_char: int, number: int, size: int) -> int:
    return (
        (direction << _IOC_DIRSHIFT)
        | (type_char << _IOC_TYPESHIFT)
        | (number << _IOC_NRSHIFT)
        | (size << _IOC_SIZESHIFT)
    )


def _IOR(type_char: int, number: int, struct_type: ctypes.Structure) -> int:
    return _IOC(_IOC_READ, type_char, number, ctypes.sizeof(struct_type))


def _IOWR(type_char: int, number: int, struct_type: ctypes.Structure) -> int:
    return _IOC(_IOC_READ | _IOC_WRITE, type_char, number, ctypes.sizeof(struct_type))


IPMICTL_SEND_COMMAND = _IOR(IPMI_IOC_MAGIC, 13, _IpmiReq)
IPMICTL_RECEIVE_MSG_TRUNC = _IOWR(IPMI_IOC_MAGIC, 11, _IpmiRecv)

IPMI_SYSTEM_INTERFACE_ADDR_TYPE = 0x0C
IPMI_BMC_CHANNEL = 0x0F


class IpmiInterface:
    """Thin wrapper around /dev/ipmi0 for synchronous commands (KCS/open driver)."""

    def __init__(self, device: str = "/dev/ipmi0") -> None:
        self._fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)
        self._addr = _IpmiSystemInterfaceAddr(
            addr_type=IPMI_SYSTEM_INTERFACE_ADDR_TYPE,
            channel=IPMI_BMC_CHANNEL,
            lun=0,
        )
        self._addr_ptr = ctypes.pointer(self._addr)
        self._libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        self._recv_buffer = (ctypes.c_ubyte * 1024)()
        self._addr_buffer = (ctypes.c_ubyte * ctypes.sizeof(_IpmiSystemInterfaceAddr))()
        self._lock = threading.Lock()
        self._msg_id = 1

    def close(self) -> None:
        with self._lock:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def send_command(
        self,
        netfn: int,
        command: int,
        data: bytes | bytearray = b"",
        timeout: float = 1.5,
    ) -> bytes:
        """Send a command and return the raw response payload."""
        payload = bytes(data)
        with self._lock:
            if self._fd < 0:
                raise IpmiError("IPMI device is closed")

            self._msg_id = (self._msg_id + 1) & 0x7FFFFFFF
            if self._msg_id == 0:
                self._msg_id = 1

            payload_buffer = (ctypes.c_ubyte * len(payload))(*payload)
            request = _IpmiReq(
                addr=ctypes.cast(self._addr_ptr, ctypes.c_void_p),
                addr_len=ctypes.sizeof(self._addr),
                msgid=self._msg_id,
                msg=_IpmiMsg(
                    netfn=netfn,
                    cmd=command,
                    data_len=len(payload),
                    data=ctypes.cast(payload_buffer, ctypes.c_void_p),
                ),
            )

            if self._libc.ioctl(self._fd, IPMICTL_SEND_COMMAND, ctypes.byref(request)) != 0:
                err = ctypes.get_errno()
                raise OSError(err, os.strerror(err))

            recv_msg = _IpmiMsg(
                netfn=0,
                cmd=0,
                data_len=len(self._recv_buffer),
                data=ctypes.cast(self._recv_buffer, ctypes.c_void_p),
            )
            recv = _IpmiRecv(
                recv_type=0,
                addr=ctypes.cast(self._addr_buffer, ctypes.c_void_p),
                addr_len=ctypes.sizeof(self._addr_buffer),
                msgid=0,
                msg=recv_msg,
            )

            deadline = time.monotonic() + timeout
            while True:
                rc = self._libc.ioctl(self._fd, IPMICTL_RECEIVE_MSG_TRUNC, ctypes.byref(recv))
                if rc != 0:
                    err = ctypes.get_errno()
                    if err in (errno.EAGAIN, errno.EINTR):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise IpmiTimeoutError("timed out waiting for IPMI response")
                        select.select([self._fd], [], [], min(remaining, 0.1))
                        continue
                    raise OSError(err, os.strerror(err))

                if recv.msgid != self._msg_id:
                    # Response for a previous command; ignore.
                    continue

                raw = bytes(self._recv_buffer[: recv.msg.data_len])
                if not raw:
                    raise IpmiError("empty IPMI response")

                completion = raw[0]
                if completion != 0x00:
                    raise IpmiCompletionError(completion)

                return raw[1:]


class LanIpmiInterface:
    """IPMI over lanplus via ipmitool for platforms that block host/KCS OEM commands."""

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        port: int = 623,
        privilege: str = "ADMIN",
        use_bridge: bool = False,
        bridge_channel: int = 6,
        bridge_target: int = 0x2C,
        ipmitool_path: str = "ipmitool",
    ) -> None:
        self._host = host
        self._user = user
        self._password = password
        self._port = port
        self._privilege = privilege
        self._use_bridge = use_bridge
        self._bridge_channel = bridge_channel
        self._bridge_target = bridge_target
        self._ipmitool_path = ipmitool_path
        self._lock = threading.Lock()

    def close(self) -> None:
        return

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _build_command(self, netfn: int, command: int, payload: bytes) -> list[str]:
        args = [
            self._ipmitool_path,
            "-I",
            "lanplus",
            "-H",
            self._host,
            "-U",
            self._user,
            "-E",  # read password from IPMI_PASSWORD env
            "-p",
            str(self._port),
        ]
        if self._privilege:
            args += ["-L", str(self._privilege)]
        if self._use_bridge:
            args += ["-b", str(self._bridge_channel), "-t", f"0x{self._bridge_target:02x}"]
        args += ["raw", f"0x{netfn:02x}", f"0x{command:02x}"]
        args += [f"0x{byte:02x}" for byte in payload]
        return args

    def _parse_completion_code(self, text: str) -> Optional[int]:
        match = re.search(r"completion code 0x([0-9a-fA-F]{2})", text)
        if match:
            return int(match.group(1), 16)
        return None

    def _parse_output_bytes(self, output: str) -> bytes:
        if not output:
            return b""
        tokens = output.replace("\n", " ").split()
        try:
            return bytes(int(tok, 16) for tok in tokens)
        except ValueError as exc:
            raise IpmiError(f"failed to parse ipmitool output: {output}") from exc

    def send_command(
        self,
        netfn: int,
        command: int,
        data: bytes | bytearray = b"",
        timeout: float = 3.0,
    ) -> bytes:
        payload = bytes(data)
        cmd = self._build_command(netfn, command, payload)
        env = os.environ.copy()
        if self._password:
            env["IPMI_PASSWORD"] = self._password
        with self._lock:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=max(1.0, timeout),
                    env=env,
                )
            except FileNotFoundError as exc:
                raise IpmiError("ipmitool not found; install ipmitool or set transport=kcs") from exc
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            completion = self._parse_completion_code(stderr or stdout)
            if completion is not None:
                raise IpmiCompletionError(completion, stderr or stdout)
            raise IpmiError(f"ipmitool failed (rc={result.returncode}): {stderr or stdout or 'no output'}")
        return self._parse_output_bytes(stdout)


def _read_first_existing(paths: Tuple[str, ...]) -> str:
    for path in paths:
        file_path = Path(path)
        if file_path.exists():
            try:
                return file_path.read_text().strip()
            except OSError:
                continue
    return ""


def _looks_like_modern_idrac(product_name: str) -> bool:
    """Best-effort heuristic: detect 14G/15G (iDRAC9+) platforms."""
    name = product_name.lower()
    if "idrac9" in name or "r940" in name:
        return True
    modern_tokens = (
        "r640",
        "r740",
        "r740xd",
        "r840",
        "r940",
        "r650",
        "r650xs",
        "r750",
        "r750xa",
        "r750xs",
        "r6515",
        "r6525",
        "r7515",
        "r7525",
    )
    if any(token in name for token in modern_tokens):
        return True
    match = re.search(r"r(\d{3,4})", name)
    if match:
        try:
            model_num = int(match.group(1))
            if model_num >= 900:
                return True
        except ValueError:
            pass
    return False


def _as_int(value: Any, default: int, name: str) -> int:
    if value is None or value == "":
        return default
    try:
        return int(str(value), 0)
    except ValueError as exc:
        raise IpmiError(f"{name} must be an integer (got {value!r})") from exc


def create_ipmi_interface(config: Dict[str, Any]) -> Tuple[object, str]:
    """Return an IPMI interface and a short description of the chosen transport."""
    product_name = _read_first_existing(
        (
            "/sys/devices/virtual/dmi/id/product_name",
            "/sys/class/dmi/id/product_name",
        )
    )
    transport_pref = str(config.get("transport", "auto")).lower()
    use_lan = False
    reason = "auto"
    if transport_pref in ("lan", "lanplus"):
        use_lan = True
        reason = "forced to lanplus by config"
    elif transport_pref in ("kcs", "local", "open"):
        use_lan = False
        reason = "forced to kcs by config"
    else:
        use_lan = _looks_like_modern_idrac(product_name)
        reason = "auto-detected modern iDRAC platform" if use_lan else "auto default (kcs)"

    if use_lan:
        host = str(config.get("lan_host", "")).strip() or os.getenv("DELLFANS_IDRAC_HOST", "").strip()
        user = str(config.get("lan_user", "")).strip() or os.getenv("DELLFANS_IDRAC_USER", "").strip()
        password = str(config.get("lan_password", "")).strip() or os.getenv("DELLFANS_IDRAC_PASSWORD", "").strip()
        port = _as_int(config.get("lan_port", 623), 623, "lan_port")
        privilege = str(config.get("lan_privilege", "ADMIN"))
        use_bridge = bool(config.get("use_bridge"))
        bridge_channel = _as_int(config.get("bridge_channel", 6), 6, "bridge_channel")
        bridge_target = _as_int(config.get("bridge_target", 0x2C), 0x2C, "bridge_target")
        if not host or not user or not password:
            raise IpmiError(
                "LAN/lanplus transport selected but credentials are missing. "
                "Set lan_host/lan_user/lan_password in config.json or provide "
                "DELLFANS_IDRAC_HOST/USER/PASSWORD environment variables."
            )
        interface = LanIpmiInterface(
            host=host,
            user=user,
            password=password,
            port=port,
            privilege=privilege,
            use_bridge=use_bridge,
            bridge_channel=bridge_channel,
            bridge_target=bridge_target,
        )
        return interface, f"lanplus ({reason}); product={product_name or 'unknown'}"

    return IpmiInterface(), f"kcs ({reason}); product={product_name or 'unknown'}"

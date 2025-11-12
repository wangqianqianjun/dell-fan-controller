import ctypes
import ctypes.util
import errno
import os
import select
import threading
import time
from typing import Optional


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
    """Thin wrapper around /dev/ipmi0 for synchronous commands."""

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

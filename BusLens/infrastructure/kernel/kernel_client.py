"""Cliente de bajo nivel del driver buslens_filter (Tier 1).

Implementa la interfaz IOCTL con ctypes puro: abre el control device del
driver por device interface o por el symbolic link \\\\.\\BusLensFilter,
envía los IOCTLs de ioctl_codes.py y decodifica las estructuras
(BUSLENS_URB_PACKET / BUSLENS_DRIVER_STATUS).

Todas las llamadas fallan limpiamente si el driver no está presente; el
TierManager interpreta el fallo para degradar al Tier 2 (WinUSB) o
Tier 3 (WMI estándar).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
from typing import Any, Optional

from buslens.infrastructure.kernel.ioctl_codes import (
    DEVICE_INTERFACE_GUID,
    DEVICE_SYMLINK,
    IOCTL_ATTACH,
    IOCTL_CLEAR_LOG,
    IOCTL_DETACH,
    IOCTL_QUERY_LOG,
    IOCTL_QUERY_STATUS,
    IOCTL_READ_PACKETS,
    IOCTL_START_CAPTURE,
    IOCTL_STOP_CAPTURE,
    LOG_CAPACITY,
    MAX_DEVICE_NAME,
    STATUS_SIZE,
    STATUS_STRUCT,
    URB_PACKET_SIZE,
    URB_PACKET_STRUCT,
)
from buslens.infrastructure.usb.setupapi_helper import SetupApi, enumerate_interfaces

logger = logging.getLogger("buslens.infrastructure.kernel")

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class KernelClientError(RuntimeError):
    """Error de comunicación con el driver (incluye el código Win32)."""

    def __init__(self, message: str, win32_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.win32_code = win32_code


class _Kernel32Api:
    """Acceso a las APIs de kernel32 (inyectable en tests)."""

    def __init__(self) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.CreateFileW = self._kernel32.CreateFileW
        self.CreateFileW.restype = ctypes.c_void_p
        self.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                     wt.DWORD, wt.DWORD, ctypes.c_void_p]

        self.DeviceIoControl = self._kernel32.DeviceIoControl
        self.DeviceIoControl.restype = wt.BOOL
        self.DeviceIoControl.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                                         wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                         ctypes.POINTER(wt.DWORD), ctypes.c_void_p]

        self.CloseHandle = self._kernel32.CloseHandle
        self.CloseHandle.restype = wt.BOOL
        self.CloseHandle.argtypes = [ctypes.c_void_p]

    @staticmethod
    def last_error() -> int:
        return ctypes.get_last_error() or 0


class KernelClient:
    """Cliente IOCTL thread-safe del control device del driver."""

    def __init__(self, api: Any = None, setup_api: Optional[SetupApi] = None) -> None:
        self._api = api if api is not None else _Kernel32Api()
        self._setup_api = setup_api
        self._handle: Optional[int] = None
        self._device_path: Optional[str] = None

    # --- ciclo de vida del handle ---

    def open(self, device_path: Optional[str] = None) -> None:
        """Abre el control device: device interface o \\\\.\\BusLensFilter."""
        if self._handle is not None:
            return
        path = device_path or self._resolve_device_path()
        if path is None:
            raise KernelClientError("Driver buslens_filter no está cargado "
                                    "(sin device interface ni symbolic link)")
        handle = self._api.CreateFileW(
            path, GENERIC_READ | GENERIC_WRITE, 0, None,
            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if handle is None or handle == INVALID_HANDLE_VALUE:
            code = self._api.last_error()
            raise KernelClientError(f"No se pudo abrir {path} (Win32 {code})", code)
        self._handle = handle
        self._device_path = path
        logger.info("Control device abierto: %s", path)

    def _resolve_device_path(self) -> Optional[str]:
        paths = enumerate_interfaces(DEVICE_INTERFACE_GUID, api=self._setup_api)
        if paths:
            return paths[0]
        return DEVICE_SYMLINK  # el driver lo crea siempre (WdfDeviceCreateSymbolicLink)

    def close(self) -> None:
        if self._handle is not None:
            self._api.CloseHandle(self._handle)
            self._handle = None
        self._device_path = None

    def __enter__(self) -> "KernelClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- IOCTLs ---

    def _ioctl(self, code: int, in_buffer: bytes = b"", out_size: int = 0) -> bytes:
        if self._handle is None:
            raise KernelClientError("Handle cerrado: abra la device interface primero")
        out_buffer = ctypes.create_string_buffer(max(out_size, 1))
        returned = wt.DWORD(0)
        ok = self._api.DeviceIoControl(
            self._handle, code,
            in_buffer, len(in_buffer),
            out_buffer, max(out_size, 1),
            returned, None)
        if not ok:
            code_ = self._api.last_error()
            raise KernelClientError(f"DeviceIoControl falló (Win32 {code_})", code_)
        return out_buffer.raw[: returned.value]

    def query_status(self) -> dict:
        raw = self._ioctl(IOCTL_QUERY_STATUS, out_size=STATUS_SIZE)
        if len(raw) < STATUS_SIZE:
            raise KernelClientError("Respuesta de query_status demasiado corta")
        version, capturing, attached, log_count, log_capacity, last_error = \
            STATUS_STRUCT.unpack(raw[:STATUS_SIZE])
        return {
            "version": version,
            "capturing": bool(capturing),
            "attached": bool(attached),
            "log_count": log_count,
            "log_capacity": log_capacity,
            "last_error": last_error,
        }

    def start_capture(self) -> None:
        self._ioctl(IOCTL_START_CAPTURE)

    def stop_capture(self) -> None:
        self._ioctl(IOCTL_STOP_CAPTURE)

    def clear_log(self) -> None:
        self._ioctl(IOCTL_CLEAR_LOG)

    def _decode_packets(self, raw: bytes) -> list[dict]:
        count = len(raw) // URB_PACKET_SIZE
        packets: list[dict] = []
        for i in range(count):
            timestamp, urb_function, transfer_flags, transfer_length, ioctl_code, device_index = \
                URB_PACKET_STRUCT.unpack_from(raw, i * URB_PACKET_SIZE)
            packets.append({
                "timestamp_utc": timestamp,
                "urb_function": urb_function,
                "transfer_flags": transfer_flags,
                "transfer_length": transfer_length,
                "ioctl_code": ioctl_code,
                "device_index": device_index,
            })
        return packets

    def read_packets(self, max_packets: int = 64) -> list[dict]:
        """IOCTL_BUSLENS_READ_PACKETS: telemetría URB estructurada."""
        max_packets = max(1, min(int(max_packets), LOG_CAPACITY))
        raw = self._ioctl(IOCTL_READ_PACKETS, out_size=max_packets * URB_PACKET_SIZE)
        return self._decode_packets(raw)

    def query_log(self, max_entries: int = 64) -> list[dict]:
        """Alias de compatibilidad sobre IOCTL_QUERY_LOG (mismo formato)."""
        max_entries = max(1, min(int(max_entries), LOG_CAPACITY))
        raw = self._ioctl(IOCTL_QUERY_LOG, out_size=max_entries * URB_PACKET_SIZE)
        return self._decode_packets(raw)

    # --- adjunto dinámico ---

    def attach(self, device_object_path: str) -> bool:
        """Adjunta el filtro a un stack USB por nombre de device object.

        Acepta rutas de device interface (``\\\\.\\USB#...``) y las
        convierte al namespace NT (``\\??\\USB#...``) que espera el driver.
        """
        path = device_object_path.strip()
        if not path:
            raise KernelClientError("Ruta de adjunto vacía")
        if path.startswith("\\\\.\\"):
            path = "\\??\\" + path[4:]
        encoded = (path + "\x00").encode("utf-16-le")
        if len(encoded) > MAX_DEVICE_NAME * 2:
            raise KernelClientError("Nombre de dispositivo demasiado largo")
        self._ioctl(IOCTL_ATTACH, in_buffer=encoded)
        return bool(self.query_status()["attached"])

    def detach(self) -> None:
        self._ioctl(IOCTL_DETACH)

    def usb_device_paths(self) -> list[str]:
        """Rutas de device interface USB presentes (para adjunto / Tier 2)."""
        from buslens.infrastructure.usb.setupapi_helper import usb_device_paths as _list

        api = self._setup_api
        if api is None and hasattr(self._api, "SetupDiGetClassDevsW"):
            api = self._api
        return _list(api=api)

    def attach_first_usb(self) -> Optional[str]:
        """Best-effort: adjunta al primer dispositivo USB enumerado."""
        for path in self.usb_device_paths():
            try:
                if self.attach(path):
                    return path
            except Exception as exc:
                logger.debug("Adjunto fallido a %s: %s", path, exc)
        return None
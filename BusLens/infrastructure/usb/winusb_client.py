"""Captura USB User-Mode con la API nativa WinUSB (Tier 2).

Cuando el driver KMDF no está disponible, BusLens usa winusb.sys: abre las
device interfaces USB presentes, las inicializa con WinUsb_Initialize y lee
los pipes bulk/interrupt con WinUsb_ReadPipe en un hilo daemon. Requiere que
el dispositivo use el driver WinUSB (la mayoría de los dispositivos con
interfaces WinUSB o instalados con winusb.sys).

Cualquier fallo (dispositivo sin WinUSB, sin permisos) degrada al Tier 3
(WMI estándar) sin lanzar excepciones hacia la UI.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
from typing import Any, Callable, Optional

from BusLens.infrastructure.usb.setupapi_helper import SetupApi, usb_device_paths

logger = logging.getLogger("buslens.infrastructure.usb.winusb")

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

USBD_PIPE_TYPE_CONTROL = 0
USBD_PIPE_TYPE_ISOCHRONOUS = 1
USBD_PIPE_TYPE_BULK = 2
USBD_PIPE_TYPE_INTERRUPT = 3

# PipeId alto = IN (0x80 | endpoint).
USB_ENDPOINT_DIRECTION_MASK = 0x80


class WinUsbError(RuntimeError):
    def __init__(self, message: str, win32_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.win32_code = win32_code


class _USBD_PIPE_INFORMATION(ctypes.Structure):
    """USBD_PIPE_INFORMATION (usb.h), alineación natural del SDK."""

    _fields_ = [
        ("MaximumPacketSize", ctypes.c_ushort),
        ("EndpointAddress", ctypes.c_ubyte),
        ("Interval", ctypes.c_ubyte),
        ("PipeType", ctypes.c_int),
        ("PipeId", ctypes.c_ubyte),
        ("MaximumTransferSize", ctypes.c_ushort),
        ("PipeFlags", ctypes.c_ushort),
    ]


class _WinUsbApi:
    """Acceso a winusb.dll (inyectable en tests)."""

    def __init__(self) -> None:
        self._winusb = ctypes.WinDLL("winusb", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.WinUsb_Initialize = self._winusb.WinUsb_Initialize
        self.WinUsb_Initialize.restype = wt.BOOL
        self.WinUsb_Initialize.argtypes = [ctypes.c_void_p,
                                           ctypes.POINTER(ctypes.c_void_p)]

        self.WinUsb_Free = self._winusb.WinUsb_Free
        self.WinUsb_Free.restype = wt.BOOL
        self.WinUsb_Free.argtypes = [ctypes.c_void_p]

        self.WinUsb_QueryPipe = self._winusb.WinUsb_QueryPipe
        self.WinUsb_QueryPipe.restype = wt.BOOL
        self.WinUsb_QueryPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
                                          ctypes.c_ubyte,
                                          ctypes.POINTER(_USBD_PIPE_INFORMATION)]

        self.WinUsb_ReadPipe = self._winusb.WinUsb_ReadPipe
        self.WinUsb_ReadPipe.restype = wt.BOOL
        self.WinUsb_ReadPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte,
                                         ctypes.c_void_p, ctypes.POINTER(wt.DWORD),
                                         ctypes.c_void_p]

        self.WinUsb_AbortPipe = self._winusb.WinUsb_AbortPipe
        self.WinUsb_AbortPipe.restype = wt.BOOL
        self.WinUsb_AbortPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte]

        self.CreateFileW = self._kernel32.CreateFileW
        self.CreateFileW.restype = ctypes.c_void_p
        self.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                     wt.DWORD, wt.DWORD, ctypes.c_void_p]

        self.CloseHandle = self._kernel32.CloseHandle
        self.CloseHandle.restype = wt.BOOL
        self.CloseHandle.argtypes = [ctypes.c_void_p]

    @staticmethod
    def last_error() -> int:
        return ctypes.get_last_error() or 0


class WinUsbClient:
    """Lector de pipes USB (bulk/interrupt) vía winusb.sys."""

    READ_CHUNK = 4096

    def __init__(
        self,
        api: Any = None,
        setup_api: Optional[SetupApi] = None,
        packet_notifier: Optional[Callable[[int, bytes], None]] = None,
    ) -> None:
        self._api = api if api is not None else _WinUsbApi()
        self._setup_api = setup_api
        self._packet_notifier = packet_notifier or (lambda pipe_id, data: None)
        self._device_handle: Optional[int] = None
        self._interface_handle: Optional[int] = None
        self._device_path: Optional[str] = None
        self._pipes: list[tuple[int, int]] = []  # (pipe_id, pipe_type)
        self._read_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    # --- ciclo de vida ---

    def open_first_available(self) -> bool:
        """Abre la primera device interface USB inicializable con WinUSB."""
        for path in usb_device_paths(api=self._setup_api):
            try:
                if self.open(path):
                    return True
            except Exception as exc:
                logger.debug("WinUSB no disponible en %s: %s", path, exc)
        return False

    def open(self, device_path: str) -> bool:
        handle = self._api.CreateFileW(
            device_path, GENERIC_READ | GENERIC_WRITE, 0, None,
            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if handle is None or handle == INVALID_HANDLE_VALUE:
            code = self._api.last_error()
            raise WinUsbError(f"No se pudo abrir {device_path} (Win32 {code})", code)

        interface_handle = ctypes.c_void_p()
        if not self._api.WinUsb_Initialize(handle, ctypes.byref(interface_handle)):
            code = self._api.last_error()
            self._api.CloseHandle(handle)
            raise WinUsbError(f"WinUsb_Initialize falló en {device_path} (Win32 {code})", code)

        self._device_handle = handle
        self._interface_handle = interface_handle.value
        self._device_path = device_path
        self._pipes = self._query_pipes()
        logger.info("WinUSB activo en %s (%d pipes)", device_path, len(self._pipes))
        return True

    def _query_pipes(self) -> list[tuple[int, int]]:
        pipes: list[tuple[int, int]] = []
        index = 0
        while index < 32:
            info = _USBD_PIPE_INFORMATION()
            if not self._api.WinUsb_QueryPipe(self._interface_handle, 0, index,
                                              ctypes.byref(info)):
                break
            if info.PipeType in (USBD_PIPE_TYPE_BULK, USBD_PIPE_TYPE_INTERRUPT):
                pipes.append((info.PipeId, info.PipeType))
            index += 1
        return pipes

    def start_reading(self) -> None:
        """Arranca el hilo de lectura de pipes IN (bulk/interrupt)."""
        if self._interface_handle is None:
            raise WinUsbError("Cliente WinUSB no abierto")
        if self._read_thread is not None and self._read_thread.is_alive():
            return
        self._stop_event.clear()
        self._read_thread = threading.Thread(
            target=self._read_loop, name="BusLensWinUsbRead", daemon=True
        )
        self._read_thread.start()

    def _read_loop(self) -> None:
        in_pipes = [p for p in self._pipes if p[0] & USB_ENDPOINT_DIRECTION_MASK]
        if not in_pipes:
            logger.info("Sin pipes IN: captura WinUSB sin lecturas activas")
        while not self._stop_event.is_set():
            if in_pipes:
                pipe_id = in_pipes[0][0]
                buffer = ctypes.create_string_buffer(self.READ_CHUNK)
                bytes_read = wt.DWORD(0)
                try:
                    ok = self._api.WinUsb_ReadPipe(
                        self._interface_handle, pipe_id, buffer,
                        ctypes.byref(bytes_read), None)
                except Exception:
                    ok = False
                if ok and bytes_read.value > 0:
                    try:
                        self._packet_notifier(pipe_id, buffer.raw[: bytes_read.value])
                    except Exception as exc:
                        logger.debug("packet notifier falló: %s", exc)
            self._stop_event.wait(0.05)

    def stop_reading(self) -> None:
        self._stop_event.set()
        if self._read_thread is not None:
            self._read_thread.join(timeout=2.0)
        self._read_thread = None

    def close(self) -> None:
        self.stop_reading()
        with self._lock:
            if self._interface_handle is not None:
                try:
                    self._api.WinUsb_AbortPipe(self._interface_handle, 0x80)
                except Exception:
                    pass
                try:
                    self._api.WinUsb_Free(self._interface_handle)
                except Exception:
                    pass
                self._interface_handle = None
            if self._device_handle is not None:
                try:
                    self._api.CloseHandle(self._device_handle)
                except Exception:
                    pass
                self._device_handle = None
            self._device_path = None
            self._pipes = []

    def set_packet_notifier(self, notifier: Optional[Callable[[int, bytes], None]]) -> None:
        """Reemplaza el notificador de paquetes (usado por el TierManager)."""
        self._packet_notifier = notifier or (lambda pipe_id, data: None)

    def __enter__(self) -> "WinUsbClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
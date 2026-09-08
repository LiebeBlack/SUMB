from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterable, Optional

try:
    import wmi
except Exception as e:
    wmi = None  # type: ignore[assignment]
    _WMI_IMPORT_ERROR = e
else:
    _WMI_IMPORT_ERROR = None

logger = logging.getLogger("buslens.infrastructure.wmi")


class WmiClient:
    """Envoltorio de bajo nivel sobre la librería wmi.

    Responsabilidad única: abrir/cerrar conexiones WMI y ejecutar consultas WQL.
    No conoce nada de dispositivos USB ni de UI.
    """

    QUERY_USB_ENTITIES = (
        "SELECT Name, PNPDeviceID, Description, Status, ClassGuid, Manufacturer "
        "FROM Win32_PnPEntity WHERE ClassGuid = '{36fc9e60-c465-11cf-8056-444553540000}'"
    )

    _SUPPORTED_WIN32_ENTITIES = (
        "Win32_PnPEntity",
        "Win32_USBHub",
        "Win32_POTSModem",
        "Win32_TSRemoteControl",
    )

    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._lock = threading.Lock()
        self._closed = False

    @property
    def available(self) -> bool:
        return wmi is not None and _WMI_IMPORT_ERROR is None and not self._closed

    def ensure_connected(self) -> Any:
        if not self.available:
            raise RuntimeError(
                "WMI no disponible. Verifique permisos de admin y existencia de wmi."
            )
        with self._lock:
            if self._client is None and not self._closed:
                self._client = wmi.WMI()
            return self._client

    def query(self, wql: str, max_retries: int = 2) -> Iterable[Any]:
        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                c = self.ensure_connected()
                results = list(c.query(wql))
                if results is None:
                    results = []
                return results
            except Exception as exc:
                last_error = exc
                logger.warning("WMI query attempt %d failed: %s", attempt, exc)
                if attempt < max_retries:
                    time.sleep(0.5)
        raise RuntimeError(f"WMI query falló tras {max_retries} intentos.") from last_error

    def __enter__(self) -> "WmiClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._client is not None:
                try:
                    del self._client
                except Exception:
                    pass
                self._client = None


def parse_pnp_device_id(device_id: str) -> tuple[Optional[str], Optional[str]]:
    """Extrae Vendor ID y Product ID de un PNPDeviceID tipo USB.

    Ejemplo: 'USB\\VID_0781&PID_5583\\...'
    Devuelve (vid, pid) o (None, None) si no coincide.
    """
    vid: Optional[str] = None
    pid: Optional[str] = None
    parts = device_id.split("\\")
    for part in parts:
        part = part.strip()
        if part.upper().startswith("VID_"):
            vid = part[4:8].upper()
        elif part.upper().startswith("PID_"):
            pid = part[4:8].upper()
    return vid, pid

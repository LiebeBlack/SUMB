from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import Any, Callable, Iterable, Iterator, Optional

from buslens.domain.models.usb_device import parse_pnp_device_id

try:
    import wmi
except Exception as e:  # pragma: no cover - entorno sin WMI
    wmi = None  # type: ignore[assignment]
    _WMI_IMPORT_ERROR = e
else:
    _WMI_IMPORT_ERROR = None

logger = logging.getLogger("buslens.infrastructure.wmi")

_WMI_EVENT_WQL_CLASS = "Win32_PnPEntity"
_WMI_EVENT_DELAY_SECS = 2


@contextlib.contextmanager
def _com_scope() -> Iterator[None]:
    """Inicializa COM (MTA) en el hilo actual y lo libera al salir.

    WMI se apoya en COM: toda llamada hecha desde un hilo de trabajo debe ir
    precedida de ``pythoncom.CoInitializeEx`` y seguida de ``CoUninitialize``.
    Este scope es reentrante: solo desinicializa si este hilo no tenía COM
    inicializado de antes. Si pywin32 no está instalado, la operación continúa
    y el fallo real se reporta en la capa WMI.
    """
    owns_com = False
    try:
        import pythoncom

        hr = pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        owns_com = hr == 0  # S_OK: este hilo acaba de inicializar una apartment
    except Exception:
        # pywin32 ausente o COM no disponible: se intenta igual, WMI fallará
        # con un mensaje claro si realmente hace falta.
        pass
    try:
        yield
    finally:
        if owns_com:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                pass


class WmiEventWatcher:
    """Receptor de eventos WMI ``__InstanceOperationEvent``.

    Envuelve el watcher devuelto por ``wmi.WMI().watch_for(...)`` de forma que
    cada recepción ocurra dentro de un scope COM válido en el hilo que la
    ejecuta. ``receive`` nunca lanza por timeout: devuelve ``None``.
    """

    def __init__(self, watcher: Any) -> None:
        self._watcher = watcher
        self._timed_out_type = self._resolve_timeout_exception()

    @staticmethod
    def _resolve_timeout_exception() -> Any:
        if wmi is None:
            return None
        return getattr(wmi, "x_wmi_timed_out", None)

    def receive(self, timeout_ms: int = 1500) -> Any:
        """Espera el siguiente evento o devuelve None si expira el timeout."""
        try:
            with _com_scope():
                return self._watcher(timeout_ms)
        except Exception as exc:
            if self._timed_out_type is not None and isinstance(exc, self._timed_out_type):
                return None
            raise

    def close(self) -> None:
        with _com_scope():
            try:
                del self._watcher
            except Exception:
                pass
            self._watcher = None


class WmiClient:
    """Envoltorio de bajo nivel sobre la librería wmi.

    Responsabilidad única: abrir/cerrar conexiones WMI, ejecutar consultas WQL
    y crear watchers de eventos. No conoce nada de dispositivos USB ni de UI.
    Todas las operaciones COM se aíslan con ``CoInitialize``/``CoUninitialize``
    para poder usarse desde hilos de trabajo.
    """

    QUERY_USB_ENTITIES = (
        "SELECT Name, PNPDeviceID, Description, Status, ClassGuid, Manufacturer "
        "FROM Win32_PnPEntity WHERE ClassGuid = '{36fc9e60-c465-11cf-8056-444553540000}'"
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
                "WMI no disponible. Verifique que la librería 'wmi' esté instalada "
                "y que el usuario tenga permisos para consultar Win32_PnPEntity."
            )
        with self._lock:
            if self._client is None:
                with _com_scope():
                    self._client = wmi.WMI()
            return self._client

    def query(self, wql: str, max_retries: int = 2) -> Iterable[Any]:
        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                c = self.ensure_connected()
                with _com_scope():
                    results = list(c.query(wql))
                return results if results is not None else []
            except Exception as exc:
                last_error = exc
                logger.warning("WMI query attempt %d/%d failed: %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    time.sleep(0.5)
        raise RuntimeError(f"WMI query falló tras {max_retries} intentos.") from last_error

    def watch_usb_events(self) -> Optional[WmiEventWatcher]:
        """Crea un watcher de ``__InstanceOperationEvent`` para Win32_PnPEntity.

        Devuelve ``None`` si la suscripción no se puede establecer (WMI no
        disponible, permisos insuficientes o formato no soportado). El llamador
        debe interpretar ``None`` como señal para cambiar a polling.
        """
        if not self.available:
            return None
        try:
            c = self.ensure_connected()
            with _com_scope():
                watcher = c.watch_for(
                    notification_type="Operation",
                    wmi_class=_WMI_EVENT_WQL_CLASS,
                    delay_secs=_WMI_EVENT_DELAY_SECS,
                )
            logger.info("Suscripción a eventos WMI establecida (__InstanceOperationEvent)")
            return WmiEventWatcher(watcher)
        except Exception as exc:
            logger.warning("Suscripción a eventos WMI no disponible, se usará polling: %s", exc)
            return None

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
                    with _com_scope():
                        del self._client
                except Exception:
                    pass
                self._client = None
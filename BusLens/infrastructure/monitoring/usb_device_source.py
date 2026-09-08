from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable

from BusLens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType
from BusLens.domain.interfaces import IUsbDeviceSource
from BusLens.domain.models.usb_device import UsbDevice, parse_pnp_device_id
from BusLens.infrastructure.wmi.wmi_client import WmiClient, WmiEventWatcher

logger = logging.getLogger("buslens.infrastructure.monitoring")

_POLL_INTERVAL_SECS = 1.5
_EVENT_RECEIVE_TIMEOUT_MS = 1500
_EVENT_RESYNC_INTERVAL_SECS = 5.0


class UsbDeviceSource(IUsbDeviceSource):
    """Proveedor de dispositivos USB usando WMI.

    Responsabilidad única: traducir resultados WMI a entidades de dominio y
    emitir eventos de cambio en segundo plano. Todo el E/S vive fuera del hilo
    de UI y cada acceso a COM queda aislado con CoInitialize/CoUninitialize.

    Estrategia de escucha:
      1. Intenta suscribirse a ``__InstanceOperationEvent`` (hotplug nativo).
      2. Si la suscripción falla o el watcher se rompe, cambia automáticamente
         a un ciclo de polling que mantiene el estado sincronizado.
    """

    def __init__(self, wmi_client: WmiClient | None = None, use_event_subscription: bool = True) -> None:
        self._wmi = wmi_client or WmiClient()
        self._use_event_subscription = use_event_subscription
        self._listen_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._listener: Callable[[DeviceChangedEvent], None] | None = None
        self._known_devices: dict[str, UsbDevice] = {}

    # --- contrato IUsbDeviceSource ---

    def poll_devices(self) -> Iterable[UsbDevice]:
        try:
            raw = self._wmi.query(WmiClient.QUERY_USB_ENTITIES)
        except Exception as exc:
            logger.warning("poll_devices falló: %s", exc)
            return []

        devices: list[UsbDevice] = []
        for item in raw:
            name = getattr(item, "Name", "") or ""
            pid = getattr(item, "PNPDeviceID", "") or ""
            desc = getattr(item, "Description", "") or ""
            status = getattr(item, "Status", "") or ""
            class_guid = getattr(item, "ClassGuid", "") or ""
            manufacturer = getattr(item, "Manufacturer", "") or ""

            vid, prod = parse_pnp_device_id(pid)
            devices.append(
                UsbDevice(
                    pnp_device_id=pid,
                    name=name,
                    vendor_id=vid,
                    product_id=prod,
                    description=desc,
                    status=status,
                    class_guid=class_guid,
                    manufacturer=manufacturer,
                    is_active=status.upper() == "OK",
                )
            )
        return devices

    def start_listening(self, callback: Callable[[DeviceChangedEvent], None]) -> None:
        if self._listen_thread is not None and self._listen_thread.is_alive():
            logger.warning("start_listening ignorado: ya hay un hilo activo")
            return
        self._listener = callback
        self._stop_event.clear()
        self._known_devices = {d.pnp_device_id: d for d in self.poll_devices()}
        self._listen_thread = threading.Thread(
            target=self._listen_loop, name="BusLensUsbMonitor", daemon=True
        )
        self._listen_thread.start()
        logger.info("USB monitoring started (background thread)")

    def stop_listening(self) -> None:
        self._stop_event.set()
        if self._listen_thread is not None:
            self._listen_thread.join(timeout=3.0)
        self._listen_thread = None
        self._listener = None
        self._known_devices = {}
        logger.info("USB monitoring stopped")

    # --- ciclo interno ---

    def _listen_loop(self) -> None:
        watcher: WmiEventWatcher | None = None
        if self._use_event_subscription:
            watcher = self._wmi.watch_usb_events()
            if watcher is not None:
                logger.info("Escuchando hotplug USB vía eventos WMI")
        if watcher is not None:
            try:
                self._event_loop(watcher)
                return
            except Exception as exc:
                logger.warning("Ciclo de eventos WMI falló (%s); cambiando a polling", exc)
            finally:
                try:
                    watcher.close()
                except Exception:
                    pass
        logger.info("Usando polling periódico como mecanismo de monitoreo")
        self._polling_loop()

    def _event_loop(self, watcher: WmiEventWatcher) -> None:
        """Recibe eventos WMI y, además, hace un poll de respaldo periódico."""
        last_sync = 0.0
        while True:
            try:
                raw_event = watcher.receive(_EVENT_RECEIVE_TIMEOUT_MS)
            except Exception as exc:
                logger.warning("Recepción de evento WMI falló: %s", exc)
                break

            now = time.monotonic()
            if raw_event is not None or (now - last_sync) >= _EVENT_RESYNC_INTERVAL_SECS:
                self._sync_once()
                last_sync = now
            if self._stop_event.is_set():
                break

    def _polling_loop(self) -> None:
        """Ciclo de polling: siempre ejecuta al menos un sync por entrada."""
        while True:
            self._sync_once()
            if self._stop_event.wait(_POLL_INTERVAL_SECS):
                break

    def _sync_once(self) -> None:
        """Polla el estado actual y difunde las diferencias contra el snapshot previo."""
        try:
            current = {d.pnp_device_id: d for d in self.poll_devices()}
        except Exception as exc:
            logger.warning("Poll del estado USB falló: %s", exc)
            self._dispatch(
                DeviceChangedEvent(change_type=DeviceChangeType.monitor_error, message=str(exc))
            )
            return

        added = set(current) - set(self._known_devices)
        removed = set(self._known_devices) - set(current)
        common = set(self._known_devices) & set(current)

        for pid in sorted(added):
            self._dispatch(
                DeviceChangedEvent(change_type=DeviceChangeType.device_added, device=current[pid])
            )

        for pid in sorted(removed):
            self._dispatch(
                DeviceChangedEvent(
                    change_type=DeviceChangeType.device_removed, device=self._known_devices[pid]
                )
            )

        for pid in sorted(common):
            prev = self._known_devices[pid]
            curr = current[pid]
            if self._device_changed(prev, curr):
                self._dispatch(
                    DeviceChangedEvent(
                        change_type=DeviceChangeType.device_updated,
                        device=curr,
                        previous_state=self._to_dict(prev),
                    )
                )

        self._known_devices = current

    @staticmethod
    def _device_changed(prev: UsbDevice, curr: UsbDevice) -> bool:
        fields = (
            "name",
            "vendor_id",
            "product_id",
            "description",
            "status",
            "manufacturer",
            "is_active",
        )
        return any(getattr(prev, f) != getattr(curr, f) for f in fields)

    @staticmethod
    def _to_dict(device: UsbDevice) -> dict:
        return {
            "name": device.name,
            "vendor_id": device.vendor_id,
            "product_id": device.product_id,
            "description": device.description,
            "status": device.status,
            "manufacturer": device.manufacturer,
            "is_active": device.is_active,
        }

    def _dispatch(self, event: DeviceChangedEvent) -> None:
        if self._listener is None:
            return
        try:
            self._listener(event)
        except Exception as exc:
            logger.exception("Listener callback error: %s", exc)
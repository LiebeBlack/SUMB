from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Iterable

from buslens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType
from buslens.domain.interfaces import IUsbDeviceSource
from buslens.domain.models.usb_device import UsbDevice
from buslens.infrastructure.wmi.wmi_client import WmiClient, parse_pnp_device_id

logger = logging.getLogger("buslens.infrastructure.monitoring")


class UsbDeviceSource(IUsbDeviceSource):
    """Implementación del proveedor de dispositivos USB usando WMI.

    Responsabilidad única: traducir resultados WMI a entidades de dominio y emitir
    eventos de cambio en segundo plano. Todo el E/S vive fuera del hilo de UI.
    """

    _MODIFIER_PROPERTY_NAME = "Name"

    def __init__(self, wmi_client: WmiClient | None = None) -> None:
        self._wmi = wmi_client or WmiClient()
        self._listen_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._listener: Callable[[DeviceChangedEvent], None] | None = None
        self._known_devices: dict[str, UsbDevice] = {}

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
            device = UsbDevice(
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
            devices.append(device)
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

    def _listen_loop(self) -> None:
        poll_interval = 1.5
        while not self._stop_event.is_set():
            try:
                current = {d.pnp_device_id: d for d in self.poll_devices()}
            except Exception as exc:
                logger.warning("Loop poll error: %s", exc)
                if self._listener is not None:
                    self._listener(
                        DeviceChangedEvent(
                            change_type=DeviceChangeType.monitor_error,
                            message=str(exc),
                        )
                    )
                time.sleep(poll_interval)
                continue

            added = set(current) - set(self._known_devices)
            removed = set(self._known_devices) - set(current)
            common = set(self._known_devices) & set(current)

            for pid in sorted(added):
                ev = DeviceChangedEvent(
                    change_type=DeviceChangeType.device_added, device=current[pid]
                )
                self._dispatch(ev)

            for pid in sorted(removed):
                ev = DeviceChangedEvent(
                    change_type=DeviceChangeType.device_removed, device=self._known_devices[pid]
                )
                self._dispatch(ev)

            for pid in sorted(common):
                prev = self._known_devices[pid]
                curr = current[pid]
                if self._device_changed(prev, curr):
                    ev = DeviceChangedEvent(
                        change_type=DeviceChangeType.device_updated, device=curr, previous_state=self._to_dict(prev)
                    )
                    self._dispatch(ev)

            self._known_devices = current
            self._stop_event.wait(poll_interval)

    def _device_changed(self, prev: UsbDevice, curr: UsbDevice) -> bool:
        fields = (
            "name",
            "vendor_id",
            "product_id",
            "description",
            "status",
            "manufacturer",
            "is_active",
        )
        for f in fields:
            if getattr(prev, f) != getattr(curr, f):
                return True
        return False

    def _to_dict(self, device: UsbDevice) -> dict:
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

from __future__ import annotations

import logging
from typing import Callable

from BusLens.application.services.bus_service import BusService
from BusLens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType
from BusLens.domain.models.usb_device import UsbDevice

logger = logging.getLogger("buslens.application.viewmodels")


class MainViewModel:
    """ViewModel de la vista principal.

    Responsabilidad única: mantener estados de la UI, exponer comandos y
    difundir cambios a los componentes vinculados (patrón Observer: los
    listeners de selección, estado y lista se registran con ``set_on_*``).
    No consulta WMI ni manipula controles XAML.
    """

    def __init__(self, service: BusService) -> None:
        self._service = service
        self._on_selection_changed: Callable[[UsbDevice | None], None] = lambda _: None
        self._on_status_message: Callable[[str], None] = lambda _: None
        self._on_devices_changed: Callable[[list[UsbDevice]], None] = lambda _: None
        self._selected_device: UsbDevice | None = None
        self._device_list: list[UsbDevice] = []
        self._search_text = ""
        self._is_monitoring = True
        self._status_message = "Listando dispositivos USB..."
        self._disposed = False

        service.subscribe(self._on_service_event)
        self._sync_from_service()

    # --- propiedades de estado de UI ---

    @property
    def device_list(self) -> list[UsbDevice]:
        return list(self._device_list)

    @property
    def selected_device(self) -> UsbDevice | None:
        return self._selected_device

    @property
    def search_text(self) -> str:
        return self._search_text

    @property
    def is_monitoring(self) -> bool:
        return self._is_monitoring

    @property
    def status_message(self) -> str:
        return self._status_message

    # --- registro de observers ---

    def set_on_selection_changed(self, callback: Callable[[UsbDevice | None], None]) -> None:
        self._on_selection_changed = callback

    def set_on_status_message(self, callback: Callable[[str], None]) -> None:
        self._on_status_message = callback

    def set_on_devices_changed(self, callback: Callable[[list[UsbDevice]], None]) -> None:
        self._on_devices_changed = callback

    # --- comandos ---

    def select_device(self, device: UsbDevice | None) -> None:
        if self._selected_device == device:
            return
        self._selected_device = device
        self._on_selection_changed(device)

    def set_search(self, query: str) -> None:
        self._search_text = query
        try:
            self._service.set_filter(query)
        except Exception as exc:
            logger.warning("set_filter falló: %s", exc)
        self._sync_from_service()

    def toggle_monitoring(self) -> None:
        if self._is_monitoring:
            self._service.pause()
        else:
            self._service.resume()

    def refresh_now(self) -> None:
        try:
            self._service.refresh()
        except Exception as exc:
            logger.warning("refresh falló: %s", exc)
        self._sync_from_service()

    # --- suscripción interna (eventos de dominio) ---

    def _on_service_event(self, event: DeviceChangedEvent) -> None:
        if self._disposed:
            return
        if event.change_type is DeviceChangeType.device_added:
            self._device_list = self._service.current_devices
            self._sync_selection_presence()
            self._notify_devices_changed()
            self._maybe_update_status("Dispositivo USB detectado")
        elif event.change_type is DeviceChangeType.device_removed:
            if (
                self._selected_device is not None
                and event.device is not None
                and event.device.pnp_device_id == self._selected_device.pnp_device_id
            ):
                self._selected_device = None
            self._device_list = self._service.current_devices
            self._sync_selection_presence()
            self._notify_devices_changed()
            self._maybe_update_status("Dispositivo USB retirado")
        elif event.change_type is DeviceChangeType.device_updated:
            if (
                self._selected_device is not None
                and event.device is not None
                and event.device.pnp_device_id == self._selected_device.pnp_device_id
            ):
                self._selected_device = event.device
            self._device_list = self._service.current_devices
            self._notify_devices_changed()
            self._maybe_update_status("Dispositivo USB actualizado")
        elif event.change_type is DeviceChangeType.monitoring_paused:
            self._is_monitoring = False
            self._maybe_update_status(event.message or "Monitoreo pausado")
        elif event.change_type is DeviceChangeType.monitoring_resumed:
            self._is_monitoring = True
            self._device_list = self._service.current_devices
            self._notify_devices_changed()
            self._maybe_update_status(event.message or "Monitoreo reanudado")
        elif event.change_type is DeviceChangeType.monitor_error:
            self._maybe_update_status(f"Error del monitor: {event.message}")
            logger.warning("Monitor event error: %s", event.message)

    # --- sincronización interna ---

    def _sync_from_service(self) -> None:
        self._device_list = self._service.current_devices
        self._is_monitoring = self._service.is_monitoring
        if self._selected_device is None and self._device_list:
            self._selected_device = self._device_list[0]
        self._sync_selection_presence()
        self._notify_devices_changed()

    def _sync_selection_presence(self) -> None:
        if self._selected_device is not None:
            if self._selected_device in self._device_list:
                return
            self._selected_device = None
        if self._device_list:
            self._selected_device = self._device_list[0]
            self._on_selection_changed(self._selected_device)
        else:
            self._selected_device = None
            self._on_selection_changed(None)

    def _notify_devices_changed(self) -> None:
        try:
            self._on_devices_changed(self.device_list)
        except Exception as exc:
            logger.warning("devices_changed callback failed: %s", exc)

    def _maybe_update_status(self, message: str) -> None:
        self._status_message = message
        try:
            self._on_status_message(message)
        except Exception as exc:
            logger.warning("status callback failed: %s", exc)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        try:
            self._service.unsubscribe(self._on_service_event)
        except Exception as exc:
            logger.warning("unsubscribe failed: %s", exc)
        finally:
            self._on_selection_changed = lambda _: None
            self._on_status_message = lambda _: None
            self._on_devices_changed = lambda _: None
            self._selected_device = None
            self._device_list = []
            self._is_monitoring = False
            self._status_message = "Monitor detenido"
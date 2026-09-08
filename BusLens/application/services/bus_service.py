from __future__ import annotations

import logging
from typing import Callable, Iterable

from buslens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher
from buslens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType
from buslens.domain.interfaces import IObservableBus, IUsbDeviceFilter, IUsbDeviceSource, IMonitoringController
from buslens.domain.models.usb_device import UsbDevice
from buslens.infrastructure.monitoring.usb_device_source import UsbDeviceSource
from buslens.infrastructure.wmi.wmi_client import WmiClient

logger = logging.getLogger("buslens.application.services")


class _InMemoryFilter(IUsbDeviceFilter):
    """Filtro de búsqueda en memoria sin dependencias externas."""

    def __init__(self) -> None:
        self._query = ""

    @property
    def query(self) -> str:
        return self._query

    @query.setter
    def query(self, value: str) -> None:
        self._query = (value or "").strip().lower()

    def apply(self, devices: Iterable[UsbDevice]) -> Iterable[UsbDevice]:
        if not self._query:
            return list(devices)
        out = []
        for d in devices:
            if self.matches_search(d, self._query):
                out.append(d)
        return out

    def matches_search(self, device: UsbDevice, query: str) -> bool:
        haystack = (
            f"{device.name or ''} {device.vendor_id or ''} "
            f"{device.product_id or ''} {device.description or ''} "
            f"{device.manufacturer or ''} {device.pnp_device_id or ''}"
        ).lower()
        return query in haystack


class BusService(IObservableBus, IMonitoringController):
    """Orquestador central que une proveedor WMI con el ViewModel/UI.

    Responsabilidad: mantener el lifecycle del monitor, exponer dispositivos
    filtrados y reemitir eventos de infraestructura con despacho seguro a UI.
    """

    def __init__(
        self,
        source: IUsbDeviceSource | None = None,
        wmi_client: WmiClient | None = None,
    ) -> None:
        if source is None:
            source = UsbDeviceSource(wmi_client=wmi_client or WmiClient())
        self._source = source
        self._filter = _InMemoryFilter()
        self._dispatcher = get_global_dispatcher()
        self._listeners: set[Callable[[DeviceChangedEvent], None]] = set()
        self._paused = False
        self._cached_devices: list[UsbDevice] = []
        self._started = False

    @property
    def is_monitoring(self) -> bool:
        return not self._paused

    def is_paused(self) -> bool:
        return self._paused

    @property
    def current_devices(self) -> list[UsbDevice]:
        return list(self._cached_devices)

    def refresh(self) -> list[UsbDevice]:
        """Sincroniza caché desde el proveedor WMI."""
        try:
            all_devices = list(self._source.poll_devices())
        except Exception as exc:
            logger.warning("refresh falló: %s", exc)
            return list(self._cached_devices)
        self._cached_devices = self._filter.apply(all_devices)
        return self._cached_devices

    def subscribe(self, handler: Callable[[DeviceChangedEvent], None]) -> None:
        self._listeners.add(handler)

    def unsubscribe(self, handler: Callable[[DeviceChangedEvent], None]) -> None:
        self._listeners.discard(handler)

    def pause(self) -> None:
        if self._paused:
            return
        self._paused = True
        try:
            self._source.stop_listening()
        except Exception as exc:
            logger.warning("pause source stop_listening error: %s", exc)
        self._emit(DeviceChangedEvent(change_type=DeviceChangeType.monitoring_paused, message="Monitoreo pausado"))

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        try:
            self._source.start_listening(self._on_infra_event)
        except Exception as exc:
            logger.warning("resume source start_listening error: %s", exc)
            self._paused = True
        self._emit(DeviceChangedEvent(change_type=DeviceChangeType.monitoring_resumed, message="Monitoreo reanudado"))

    def set_filter(self, query: str) -> None:
        self._filter.query = query
        self._cached_devices = self._filter.apply(self._source.poll_devices())

    def start(self) -> None:
        if self._started:
            logger.warning("start ignorado: ya está corriendo")
            return
        self._started = True
        try:
            self._cached_devices = self._filter.apply(self._source.poll_devices())
        except Exception as exc:
            logger.warning("poll inicial falló: %s", exc)
            self._cached_devices = []

        try:
            self._source.start_listening(self._on_infra_event)
        except Exception as exc:
            logger.warning("start listening error: %s", exc)
            self._emit(DeviceChangedEvent(change_type=DeviceChangeType.monitor_error, message=str(exc)))

    def stop(self) -> None:
        self._started = False
        try:
            self._source.stop_listening()
        except Exception as exc:
            logger.warning("stop source error: %s", exc)
        self._emit(DeviceChangedEvent(change_type=DeviceChangeType.monitoring_paused, message="Monitor detenido"))

    def _on_infra_event(self, event: DeviceChangedEvent) -> None:
        if self._paused:
            return
        if event.change_type is DeviceChangeType.device_added:
            self._cached_devices.append(event.device)
        elif event.change_type is DeviceChangeType.device_removed:
            self._cached_devices = [d for d in self._cached_devices if d.pnp_device_id != event.device.pnp_device_id]
        elif event.change_type is DeviceChangeType.device_updated:
            self._cached_devices = [
                event.device if d.pnp_device_id == event.device.pnp_device_id else d
                for d in self._cached_devices
            ]
        elif event.change_type is DeviceChangeType.monitor_error:
            logger.warning("Monitor event error: %s", event.message)
        self._emit(event)

    def _emit(self, event: DeviceChangedEvent) -> None:
        for handler in set(self._listeners):
            try:
                # Usamos closure explícita para evitar closing sobre variables cambiantes.
                self._dispatcher.invoke_main(_make_handler(event, handler))
            except Exception as exc:
                logger.warning("emit dispatcher failed: %s", exc)


def _make_handler(event: DeviceChangedEvent, handler: Callable) -> Callable:
    """Crea un callback que invoca `handler(event)` sin dependence de variables externas."""
    return lambda: handler(event)


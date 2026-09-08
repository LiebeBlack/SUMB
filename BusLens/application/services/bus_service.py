from __future__ import annotations

import logging
import threading
from typing import Callable, Iterable

from BusLens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher
from BusLens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType
from BusLens.domain.interfaces import IObservableBus, IUsbDeviceFilter, IUsbDeviceSource, IMonitoringController
from BusLens.domain.models.usb_device import UsbDevice

logger = logging.getLogger("buslens.application.services")


def _default_device_source() -> IUsbDeviceSource:
    """Crea la fuente WMI por defecto.

    El import es diferido para mantener la capa de Aplicación desacoplada del
    módulo de infraestructura: la dependencia concreta se resuelve aquí, pero
    el resto de la capa solo programa contra ``IUsbDeviceSource``.
    """
    from BusLens.infrastructure.monitoring.usb_device_source import UsbDeviceSource

    return UsbDeviceSource()


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
            if self.matches(d):
                out.append(d)
        return out

    def matches(self, device: UsbDevice) -> bool:
        """Implementación del contrato IUsbDeviceFilter."""
        if not self._query:
            return True
        haystack = (
            f"{device.name or ''} {device.vendor_id or ''} "
            f"{device.product_id or ''} {device.description or ''} "
            f"{device.manufacturer or ''} {device.pnp_device_id or ''}"
        ).lower()
        return self._query in haystack


class BusService(IObservableBus, IMonitoringController):
    """Orquestador central que une el proveedor USB con el ViewModel/UI.

    Responsabilidad: mantener el lifecycle del monitor, exponer dispositivos
    filtrados y reemitir eventos de infraestructura con despacho seguro a la
    UI (``ThreadSafeDispatcher``). El caché es thread-safe porque lo actualiza
    el hilo de monitoreo y lo lee el hilo de UI.
    """

    def __init__(self, source: IUsbDeviceSource | None = None) -> None:
        self._source = source if source is not None else _default_device_source()
        self._filter = _InMemoryFilter()
        self._dispatcher: ThreadSafeDispatcher = get_global_dispatcher()
        self._listeners: set[Callable[[DeviceChangedEvent], None]] = set()
        self._paused = False
        self._started = False
        self._cache_lock = threading.RLock()
        self._cached_devices: list[UsbDevice] = []

    # --- contrato IMonitoringController ---

    @property
    def is_monitoring(self) -> bool:
        return not self._paused

    def is_paused(self) -> bool:
        return self._paused

    def start_monitoring(self) -> None:
        """Alias para start para compatibilidad con IMonitoringController."""
        self.start()

    def stop_monitoring(self) -> None:
        """Alias para stop para compatibilidad con IMonitoringController."""
        self.stop()

    @property
    def current_devices(self) -> list[UsbDevice]:
        with self._cache_lock:
            return list(self._cached_devices)

    # --- contrato IObservableBus ---

    def subscribe(self, handler: Callable[[DeviceChangedEvent], None]) -> None:
        self._listeners.add(handler)

    def unsubscribe(self, handler: Callable[[DeviceChangedEvent], None]) -> None:
        self._listeners.discard(handler)

    def subscribe_device_changes(self, handler: Callable[[DeviceChangedEvent], None]) -> None:
        """Alias para subscribe para compatibilidad con IObservableBus."""
        self.subscribe(handler)

    def unsubscribe_device_changes(self, handler: Callable[[DeviceChangedEvent], None]) -> None:
        """Alias para unsubscribe para compatibilidad con IObservableBus."""
        self.unsubscribe(handler)

    # --- operaciones ---

    def refresh(self) -> list[UsbDevice]:
        """Sincroniza el caché desde el proveedor WMI."""
        try:
            all_devices = list(self._source.poll_devices())
        except Exception as exc:
            logger.warning("refresh falló: %s", exc)
            return self.current_devices
        with self._cache_lock:
            self._cached_devices = self._filter.apply(all_devices)
            return list(self._cached_devices)

    def set_filter(self, query: str) -> None:
        self._filter.query = query
        try:
            all_devices = list(self._source.poll_devices())
        except Exception as exc:
            logger.warning("set_filter: poll falló (%s); filtrando caché existente", exc)
            all_devices = self.current_devices
        with self._cache_lock:
            self._cached_devices = self._filter.apply(all_devices)

    def start(self) -> None:
        if self._started:
            logger.warning("start ignorado: ya está corriendo")
            return
        self._started = True
        try:
            devices = list(self._source.poll_devices())
            with self._cache_lock:
                self._cached_devices = self._filter.apply(devices)
        except Exception as exc:
            logger.warning("poll inicial falló: %s", exc)
            with self._cache_lock:
                self._cached_devices = []

        try:
            self._source.start_listening(self._on_infra_event)
        except Exception as exc:
            logger.warning("start listening error: %s", exc)
            self._emit(
                DeviceChangedEvent(change_type=DeviceChangeType.monitor_error, message=str(exc))
            )

    def stop(self) -> None:
        self._started = False
        try:
            self._source.stop_listening()
        except Exception as exc:
            logger.warning("stop source error: %s", exc)
        self._emit(
            DeviceChangedEvent(change_type=DeviceChangeType.monitoring_paused, message="Monitor detenido")
        )

    def pause(self) -> None:
        if self._paused:
            return
        self._paused = True
        try:
            self._source.stop_listening()
        except Exception as exc:
            logger.warning("pause source stop_listening error: %s", exc)
        self._emit(
            DeviceChangedEvent(change_type=DeviceChangeType.monitoring_paused, message="Monitoreo pausado")
        )

    def resume(self) -> None:
        if not self._paused:
            return
        self._paused = False
        try:
            self._source.start_listening(self._on_infra_event)
        except Exception as exc:
            logger.warning("resume source start_listening error: %s", exc)
            self._paused = True
            self._emit(
                DeviceChangedEvent(change_type=DeviceChangeType.monitor_error, message=str(exc))
            )
            return
        self._emit(
            DeviceChangedEvent(change_type=DeviceChangeType.monitoring_resumed, message="Monitoreo reanudado")
        )

    # --- eventos internos ---

    def _on_infra_event(self, event: DeviceChangedEvent) -> None:
        if self._paused:
            return
        with self._cache_lock:
            if event.change_type is DeviceChangeType.device_added and event.device is not None:
                if event.device not in self._cached_devices:
                    self._cached_devices.append(event.device)
            elif event.change_type is DeviceChangeType.device_removed and event.device is not None:
                self._cached_devices = [
                    d for d in self._cached_devices if d.pnp_device_id != event.device.pnp_device_id
                ]
            elif event.change_type is DeviceChangeType.device_updated and event.device is not None:
                self._cached_devices = [
                    event.device if d.pnp_device_id == event.device.pnp_device_id else d
                    for d in self._cached_devices
                ]
            if self._filter.query:
                self._cached_devices = self._filter.apply(self._cached_devices)
        if event.change_type is DeviceChangeType.monitor_error:
            logger.warning("Monitor event error: %s", event.message)
        self._emit(event)

    def _emit(self, event: DeviceChangedEvent) -> None:
        for handler in set(self._listeners):
            try:
                # Closure explícita para evitar cierres sobre variables cambiantes.
                self._dispatcher.invoke_main(_make_handler(event, handler))
            except Exception as exc:
                logger.warning("emit dispatcher failed: %s", exc)


def _make_handler(event: DeviceChangedEvent, handler: Callable) -> Callable:
    """Crea un callback que invoca ``handler(event)`` sin depender de variables externas."""

    def _dispatch() -> None:
        handler(event)

    return _dispatch
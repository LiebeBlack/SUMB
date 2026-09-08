"""Fakes reutilizables para pruebas sin WMI ni WinRT.

- ``FakeUsbDeviceSource``: implementa el contrato de ``IUsbDeviceSource``
  para probar la capa de Aplicación (BusService/MainViewModel).
- ``FakeWmiClient``: sustituye a ``WmiClient`` para probar la capa de
  Infraestructura (UsbDeviceSource) incluyendo el fallback a polling.
- ``FakeEventWatcher``: watcher de eventos WMI determinista.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Callable, Iterable, Optional

from BusLens.domain.events.usb_events import DeviceChangedEvent


class FakeWmiEntity:
    """Objeto con la forma que WMI devuelve (propiedades PascalCase)."""

    def __init__(
        self,
        pnp_device_id: str,
        name: str = "",
        description: str = "",
        status: str = "OK",
        class_guid: str = "",
        manufacturer: str = "",
    ) -> None:
        self.Name = name
        self.PNPDeviceID = pnp_device_id
        self.Description = description
        self.Status = status
        self.ClassGuid = class_guid
        self.Manufacturer = manufacturer


class FakeUsbDeviceSource:
    """Fuente falsa para pruebas de capa de aplicación sin WMI.

    Implementa el contrato mínimo de ``IUsbDeviceSource`` de forma que el
    ``BusService`` pueda probarse sin depender de WMI.
    """

    def __init__(self, devices: Optional[Iterable[Any]] = None) -> None:
        self._devices = list(devices) if devices else []
        self._listener: Optional[Callable[[DeviceChangedEvent], None]] = None
        self.started = False
        self.stopped = False

    def poll_devices(self) -> list[Any]:
        return list(self._devices)

    def start_listening(self, callback: Callable[[DeviceChangedEvent], None]) -> None:
        self._listener = callback
        self.started = True

    def stop_listening(self) -> None:
        self._listener = None
        self.stopped = True

    def emit(self, event: DeviceChangedEvent) -> None:
        if self._listener is not None:
            self._listener(event)


class FakeEventWatcher:
    """Watcher determinista: devuelve eventos en cola y None cuando se agota."""

    def __init__(self, events: Optional[Iterable[Any]] = None) -> None:
        self._events: deque[Any] = deque(events or [])
        self.closed = False

    def receive(self, timeout_ms: int = 1500) -> Any:
        if self._events:
            return self._events.popleft()
        return None

    def close(self) -> None:
        self.closed = True


class FakeWmiClient:
    """Sustituto de WmiClient para probar UsbDeviceSource sin COM/WMI."""

    def __init__(self, devices: Optional[Iterable[Any]] = None) -> None:
        self._devices = list(devices) if devices else []
        self.watch_result: Optional[FakeEventWatcher] = None
        self.watch_called = False
        self.query_count = 0

    def query(self, wql: str) -> list[Any]:
        self.query_count += 1
        return list(self._devices)

    def watch_usb_events(self) -> Optional[FakeEventWatcher]:
        self.watch_called = True
        return self.watch_result
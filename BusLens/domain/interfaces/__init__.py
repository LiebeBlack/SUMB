from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterable

from buslens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType
from buslens.domain.models.usb_device import UsbDevice


class IUsbDeviceSource(ABC):
    """Contratos que cualquier proveedor de dispositivos USB debe cumplir.

    La infraestructura WMI implementa esta interfaz. La capa de Aplicación
    programa terhadap el contrato, nunca contra WMI directamente.
    """

    @abstractmethod
    def poll_devices(self) -> Iterable[UsbDevice]:
        """Devuelve dispositivos USB detectados en este momento."""

    @abstractmethod
    def start_listening(self, callback: Callable[[DeviceChangedEvent], None]) -> None:
        """Activa la escucha asíncrona de inserción/extracción/actualización de USB."""

    @abstractmethod
    def stop_listening(self) -> None:
        """Detiene la escucha y libera recursos/thread."""


class IMonitoringController(ABC):
    """Control de lifecycle del monitoreo expuesto a la aplicación."""

    @property
    @abstractmethod
    def is_monitoring(self) -> bool:
        ...

    @abstractmethod
    def pause(self) -> None:
        ...

    @abstractmethod
    def resume(self) -> None:
        ...

    @abstractmethod
    def is_paused(self) -> bool:
        ...


class IObservableBus(ABC):
    """Puente hacia la UI: permite suscribirse a eventos del inspector."""

    @abstractmethod
    def subscribe(self, handler: Callable[[DeviceChangedEvent], None]) -> None:
        ...

    @abstractmethod
    def unsubscribe(self, handler: Callable[[DeviceChangedEvent], None]) -> None:
        ...


class IUsbDeviceFilter(ABC):
    """Filtrado aplicado antes de presentar dispositivos en la UI."""

    @abstractmethod
    def apply(self, devices: Iterable[UsbDevice]) -> Iterable[UsbDevice]:
        ...

    @abstractmethod
    def matches_search(self, device: UsbDevice, query: str) -> bool:
        ...

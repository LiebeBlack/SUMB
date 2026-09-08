from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from BusLens.domain.events.usb_events import DeviceChangedEvent
    from BusLens.domain.models.usb_device import UsbDevice


class IUsbDeviceSource(ABC):
    """Contrato para fuentes de dispositivos USB (WMI, driver kernel, etc.)."""

    @abstractmethod
    def poll_devices(self) -> Iterable[UsbDevice]:
        """Retorna la lista actual de dispositivos USB."""
        pass

    @abstractmethod
    def start_listening(self, callback) -> None:
        """Inicia el monitoreo de dispositivos con un callback para eventos."""
        pass

    @abstractmethod
    def stop_listening(self) -> None:
        """Detiene el monitoreo de dispositivos."""
        pass


class IObservableBus(ABC):
    """Contrato para observables que emiten eventos de dispositivos."""

    @abstractmethod
    def subscribe_device_changes(self, callback) -> None:
        """Suscribe un callback para eventos de cambio de dispositivo."""
        pass

    @abstractmethod
    def unsubscribe_device_changes(self, callback) -> None:
        """Desuscribe un callback."""
        pass


class IUsbDeviceFilter(ABC):
    """Contrato para filtros de dispositivos USB."""

    @abstractmethod
    def matches(self, device: UsbDevice) -> bool:
        """Determina si un dispositivo cumple con el criterio del filtro."""
        pass


class IMonitoringController(ABC):
    """Contrato para controlar el monitoreo de dispositivos."""

    @abstractmethod
    def start_monitoring(self) -> None:
        """Inicia el monitoreo."""
        pass

    @abstractmethod
    def stop_monitoring(self) -> None:
        """Detiene el monitoreo."""
        pass

    @abstractmethod
    def is_monitoring(self) -> bool:
        """Verifica si el monitoreo está activo."""
        pass

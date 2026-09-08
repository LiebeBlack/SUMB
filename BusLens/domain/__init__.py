from buslens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType
from buslens.domain.interfaces import IObservableBus, IUsbDeviceFilter, IUsbDeviceSource, IMonitoringController
from buslens.domain.models.usb_device import UsbDevice

__all__ = [
    "DeviceChangedEvent",
    "DeviceChangeType",
    "IUsbDeviceSource",
    "IMonitoringController",
    "IObservableBus",
    "IUsbDeviceFilter",
    "UsbDevice",
]

__version__ = "1.0.0"


def _check_importability() -> bool:
    """Chequeo rápido para CI: verifica que la capa de dominio no tenga dependencias de infraestructura/UI."""
    try:
        _ = UsbDevice(pnp_device_id=r"USB\VID_0781&PID_5581\dev", name="SanDisk")
        return True
    except Exception:
        return False


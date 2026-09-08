from BusLens.domain.models.usb_device import parse_pnp_device_id
from BusLens.infrastructure.monitoring.usb_device_source import UsbDeviceSource
from BusLens.infrastructure.wmi.wmi_client import WmiClient, WmiEventWatcher

__all__ = ["UsbDeviceSource", "WmiClient", "WmiEventWatcher", "parse_pnp_device_id"]

__version__ = "1.0.0"


def _check_importability() -> bool:
    """Chequeo rápido para CI: verifica que esta capa pueda ser importada aunque WMI no esté presente."""
    try:
        from BusLens.infrastructure.wmi.wmi_client import WmiClient
        _ = WmiClient()
        return True
    except Exception:
        return False


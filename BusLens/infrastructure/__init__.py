from buslens.domain.models.usb_device import parse_pnp_device_id
from buslens.infrastructure.monitoring.usb_device_source import UsbDeviceSource
from buslens.infrastructure.wmi.wmi_client import WmiClient, WmiEventWatcher

__all__ = ["UsbDeviceSource", "WmiClient", "WmiEventWatcher", "parse_pnp_device_id"]

__version__ = "1.0.0"


def _check_importability() -> bool:
    """Chequeo rápido para CI: verifica que esta capa pueda ser importada aunque WMI no esté presente."""
    try:
        from buslens.infrastructure.wmi.wmi_client import WmiClient
        _ = WmiClient()
        return True
    except Exception:
        return False


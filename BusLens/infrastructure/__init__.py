from buslens.infrastructure.monitoring.usb_device_source import UsbDeviceSource
from buslens.infrastructure.wmi.wmi_client import WmiClient, parse_pnp_device_id

__all__ = ["UsbDeviceSource", "WmiClient", "parse_pnp_device_id"]

__version__ = "1.0.0"


def _check_importability() -> bool:
    """Chequeo rápido para CI: verifica que esta capa pueda ser importada aunque WMI no esté presente."""
    try:
        from buslens.infrastructure.wmi.wmi_client import WmiClient
        _ = WmiClient()
        return True
    except Exception:
        return False


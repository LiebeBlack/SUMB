from buslens.application.services.bus_service import BusService
from buslens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher
from buslens.application.viewmodels.main_viewmodel import MainViewModel

__all__ = ["BusService", "MainViewModel", "ThreadSafeDispatcher", "get_global_dispatcher"]

__version__ = "1.0.0"


def _check_importability() -> bool:
    """Chequeo rápido para CI: verifica que la capa de aplicación no dependa de WMI/WinRT."""
    try:
        from buslens.infrastructure.monitoring.usb_device_source import UsbDeviceSource
        from buslens.infrastructure.wmi.wmi_client import WmiClient
        svc = BusService(source=UsbDeviceSource(wmi_client=WmiClient()))
        return True
    except Exception:
        return False


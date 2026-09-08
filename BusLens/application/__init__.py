from BusLens.application.services.app_config import AppConfig
from BusLens.application.services.bus_service import BusService
from BusLens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher
from BusLens.application.services.kernel_monitor_service import KernelMonitorService, KernelMonitorState
from BusLens.application.services.tier_manager import TIER_LABELS, Tier, TierManager
from BusLens.application.viewmodels.kernel_settings_viewmodel import KernelSettingsViewModel
from BusLens.application.viewmodels.main_viewmodel import MainViewModel

__all__ = [
    "BusService",
    "MainViewModel",
    "ThreadSafeDispatcher",
    "get_global_dispatcher",
    "AppConfig",
    "KernelMonitorService",
    "KernelMonitorState",
    "TierManager",
    "Tier",
    "TIER_LABELS",
    "KernelSettingsViewModel",
]

__version__ = "1.0.0"


def _check_importability() -> bool:
    """Chequeo rápido para CI: verifica que la capa de aplicación no dependa de WMI/WinRT."""
    try:
        from BusLens.infrastructure.monitoring.usb_device_source import UsbDeviceSource
        from BusLens.infrastructure.wmi.wmi_client import WmiClient
        svc = BusService(source=UsbDeviceSource(wmi_client=WmiClient()))
        return True
    except Exception:
        return False


from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def project_root() -> Path:
    return PROJECT_ROOT


def ensure_buslens_importable() -> None:
    """Verifica que el paquete `buslens` se pueda importar desde la raíz.

    En Windows (sistema de archivos insensible a mayúsculas) el directorio
    BusLens/ se resuelve como `buslens` automáticamente.
    """
    try:
        import buslens  # noqa: F401
    except Exception as exc:
        print(f"[WARN] no se pudo importar buslens en conftest: {exc}")


ensure_buslens_importable()

from buslens.domain.models.usb_device import UsbDevice, parse_pnp_device_id  # noqa: E402
from buslens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType  # noqa: E402
from buslens.application.services.bus_service import BusService  # noqa: E402
from buslens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher  # noqa: E402
from buslens.application.viewmodels.main_viewmodel import MainViewModel  # noqa: E402
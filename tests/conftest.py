from __future__ import annotations

import sys
from pathlib import Path
from importlib.machinery import SourceFileLoader

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def project_root() -> Path:
    return PROJECT_ROOT


def ensure_buslens_importable() -> None:
    """Ajuste defensivo: en algunos entornos el paquete no se resuelve con `import buslens`."""
    init = PROJECT_ROOT / "BusLens" / "__init__.py"
    if init.exists():
        try:
            loader = SourceFileLoader("buslens", str(init))
            loader.load_module("buslens")
        except Exception as exc:
            print(f"[WARN] no se pudo cargar buslens en conftest: {exc}")


def _load_subpackages_sync() -> None:
    subs = {
        "buslens.application": "application",
        "buslens.domain": "domain",
        "buslens.infrastructure": "infrastructure",
        "buslens.presentation": "presentation",
    }
    for full, sub in subs.items():
        init = PROJECT_ROOT / "BusLens" / sub / "__init__.py"
        if init.exists():
            try:
                loader = SourceFileLoader(full, str(init))
                loader.load_module(full)
            except Exception as exc:
                print(f"[WARN] no se pudo cargar {full}: {exc}")


ensure_buslens_importable()
_load_subpackages_sync()

from buslens.domain.models.usb_device import UsbDevice, parse_pnp_device_id
from buslens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType
from buslens.application.services.bus_service import BusService
from buslens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher
from buslens.application.viewmodels.main_viewmodel import MainViewModel

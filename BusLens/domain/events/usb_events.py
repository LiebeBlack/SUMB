from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buslens.domain.models.usb_device import UsbDevice


class DeviceChangeType(Enum):
    """Tipos de eventos que el inspector emite a la aplicación."""

    device_added = auto()
    device_removed = auto()
    device_updated = auto()
    monitor_error = auto()
    monitoring_paused = auto()
    monitoring_resumed = auto()


@dataclass(slots=True)
class DeviceChangedEvent:
    """Payload inmutable para un evento de cambio de dispositivo.

    Usado por la capa de Infraestructura -> Aplicación -> Presentación.
    """

    change_type: DeviceChangeType
    device: "UsbDevice | None" = None
    message: str = ""
    previous_state: "dict | None" = None

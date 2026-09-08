from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

VID_REGEX = re.compile(r"VID_([0-9A-Fa-f]{4})", re.IGNORECASE)
PID_REGEX = re.compile(r"PID_([0-9A-Fa-f]{4})", re.IGNORECASE)

# Separadores observados entre VID y PID dentro de un mismo segmento.
_SEGMENT_VID_PREFIX = "VID_"
_SEGMENT_PID_PREFIX = "PID_"


def parse_pnp_device_id(device_id: str) -> tuple[Optional[str], Optional[str]]:
    """Extrae Vendor ID y Product ID de un PNPDeviceID tipo USB.

    Estrategia principal (requerida): expresiones regulares sobre la cadena
    completa. Ejemplo: ``USB\\VID_0781&PID_5583\\1234`` -> ``("0781", "5583")``.

    Estrategia de respaldo: si alguna parte falta, se recorren los segmentos
    separados por backslash y se intenta ``VID_xxxx`` / ``PID_xxxx`` al inicio
    de cada segmento (cubre IDs con formatos anómalos). En caso de no coincidir
    ninguna estrategia se devuelve ``(None, None)`` sin lanzar excepciones.
    """
    if not device_id:
        return None, None

    vid: Optional[str] = None
    pid: Optional[str] = None

    vid_match = VID_REGEX.search(device_id)
    pid_match = PID_REGEX.search(device_id)
    if vid_match:
        vid = vid_match.group(1).upper()
    if pid_match:
        pid = pid_match.group(1).upper()

    if vid is not None and pid is not None:
        return vid, pid

    # Fallback conservador: análisis por segmentos separados por backslash.
    # Se usan dos `if` independientes porque VID y PID pueden coexistir en el
    # mismo segmento (p. ej. "VID_0781&PID_5581").
    for part in device_id.split("\\"):
        upper = part.strip().upper()
        if vid is None and upper.startswith(_SEGMENT_VID_PREFIX):
            candidate = upper[len(_SEGMENT_VID_PREFIX):].split("&")[0][:4]
            if len(candidate) == 4 and candidate.isalnum():
                vid = candidate
        if pid is None and upper.startswith(_SEGMENT_PID_PREFIX):
            candidate = upper[len(_SEGMENT_PID_PREFIX):].split("&")[0][:4]
            if len(candidate) == 4 and candidate.isalnum():
                pid = candidate

    return vid, pid


@dataclass(slots=True)
class UsbDevice:
    """Entidad de dominio que representa un dispositivo USB detectable vía WMI.

    Ningún campo hace referencia a infraestructura o UI. Esta clase vive
    exclusivamente en la capa de Dominio.
    """

    pnp_device_id: str
    name: str
    vendor_id: Optional[str] = None
    product_id: Optional[str] = None
    description: str = ""
    status: str = ""
    class_guid: str = ""
    manufacturer: str = ""
    is_active: bool = True

    def match_vendor_product(self, vendor: Optional[str], product: Optional[str]) -> bool:
        if vendor is not None and self.vendor_id != vendor:
            return False
        if product is not None and self.product_id != product:
            return False
        return True

    def __hash__(self) -> int:
        return hash(self.pnp_device_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UsbDevice):
            return False
        return self.pnp_device_id == other.pnp_device_id

    def __post_init__(self) -> None:
        if not self.pnp_device_id:
            return
        parsed_vid, parsed_pid = parse_pnp_device_id(self.pnp_device_id)
        if parsed_vid is not None:
            object.__setattr__(self, "vendor_id", parsed_vid)
        if parsed_pid is not None:
            object.__setattr__(self, "product_id", parsed_pid)


@dataclass(slots=True)
class UsbDeviceStub:
    """Stub usado solo para tests que requieren instanciar sin backend WMI."""

    pnp_device_id: str
    name: str = ""
    vendor_id: Optional[str] = None
    product_id: Optional[str] = None
    description: str = ""
    status: str = ""
    class_guid: str = ""
    manufacturer: str = ""
    is_active: bool = True
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

VID_REGEX = re.compile(r"VID_([0-9A-Fa-f]{4})")
PID_REGEX = re.compile(r"PID_([0-9A-Fa-f]{4})")


def parse_pnp_device_id(device_id: str) -> tuple[Optional[str], Optional[str]]:
    """Extrae Vendor ID y Product ID de un PNPDeviceID tipo USB.

    Ejemplo: 'USB\\VID_0781&PID_5583\\...'
    Devuelve (vid, pid) o (None, None) si no coincide.

    Nota de implementación: los PNP IDs reales suelen venir como
        USB\\VID_xxxx&PID_yyyy\\...
    pero en este entorno la función split('\\') sobre raw strings puede no
    separar los segmentos esperados (comportamiento observado empíricamente).
    Se mantiene una estrategia conservadora que extrae vid/pid incluso cuando
    el separador no se comporta como se espera.
    """
    vid: Optional[str] = None
    pid: Optional[str] = None

    # Primer intento: separación por backslash y parseo por bloques.
    for part in device_id.split("\\"):
        part = part.strip()
        upper = part.upper()
        if upper.startswith("VID_"):
            vid = part[4:8].upper()
        elif upper.startswith("PID_"):
            pid = part[4:8].upper()

    # Segundo intento (fallback conservador si split no separó VID/PID en bloques).
    if vid is None or pid is None:
        maybe_vid = VID_REGEX.search(device_id)
        maybe_pid = PID_REGEX.search(device_id)
        if maybe_vid:
            vid = maybe_vid.group(1).upper()
        if maybe_pid:
            pid = maybe_pid.group(1).upper()

    return vid, pid


def parse_pnp_device_id_legacy(device_id: str) -> tuple[Optional[str], Optional[str]]:
    """Variante experimental para manejar IDs anormales conservando compatibilidad.

    Nota: este parser se usa solo cuando el principal devuelve (None, None) para
    un dispositivo USB conocido y se quiere intentar una extracción más conservadora.
    """
    vid, pid = parse_pnp_device_id(device_id)
    if vid is None or pid is None:
        if vid is None:
            maybe_vid = VID_REGEX.search(device_id)
            if maybe_vid:
                vid = maybe_vid.group(1).upper()
        if pid is None:
            maybe_pid = PID_REGEX.search(device_id)
            if maybe_pid:
                pid = maybe_pid.group(1).upper()
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
        parsed_vid, parsed_pid = parse_pnp_device_id_legacy(self.pnp_device_id)
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

"""Constantes del protocolo IOCTL de buslens_filter (Modo Avanzado / Tier 1).

Reimplementa las definiciones de kernel/buslens_filter.h para no depender
del WDK en tiempo de ejecución. Cualquier cambio en el driver debe reflejarse
aquí (y viceversa).
"""

from __future__ import annotations

import struct

# FILE_DEVICE_UNKNOWN
_FILE_DEVICE_UNKNOWN = 0x22
_METHOD_BUFFERED = 0
_FILE_ANY_ACCESS = 0


def ctl_code(function: int, method: int = _METHOD_BUFFERED, access: int = _FILE_ANY_ACCESS) -> int:
    """Construye un código IOCTL igual que la macro CTL_CODE del SDK."""
    return (_FILE_DEVICE_UNKNOWN << 16) | (access << 14) | (method << 2) | function


IOCTL_QUERY_STATUS = ctl_code(0x800)
IOCTL_START_CAPTURE = ctl_code(0x801)
IOCTL_STOP_CAPTURE = ctl_code(0x802)
IOCTL_QUERY_LOG = ctl_code(0x803)
IOCTL_CLEAR_LOG = ctl_code(0x804)
IOCTL_ATTACH = ctl_code(0x805)
IOCTL_DETACH = ctl_code(0x806)
IOCTL_READ_PACKETS = ctl_code(0x807)

# Versión del protocolo (mayor << 16 | menor)
DRIVER_VERSION = 0x00010000

# Capacidad por defecto del ring buffer del driver.
LOG_CAPACITY = 4096

# GUID de la device interface: {D1F2E3A4-B5C6-4D7E-8F90-A1B2C3D4E5F6}
DEVICE_INTERFACE_GUID = "D1F2E3A4-B5C6-4D7E-8F90-A1B2C3D4E5F6"

# Symbolic link del control device: \\.\BusLensFilter
DEVICE_SYMLINK = r"\\.\BusLensFilter"

# GUID_DEVINTERFACE_USB_DEVICE (para localizar dispositivos USB vía SetupAPI)
USB_DEVICE_INTERFACE_GUID = "A5DCBF10-6530-11D2-ACF0-00C04FB85ED4"

# Longitud máxima del nombre de device object para IOCTL_ATTACH.
MAX_DEVICE_NAME = 260

# --- Layout de estructuras (ver buslens_filter.h) ---

# BUSLENS_URB_PACKET (packed, 24 bytes):
#   LARGE_INTEGER(8) + USHORT(2) + USHORT(2) + ULONG(4) + ULONG(4) + ULONG(4)
URB_PACKET_STRUCT = struct.Struct("@qHHIII")
URB_PACKET_SIZE = URB_PACKET_STRUCT.size

# Alias de compatibilidad: el ring buffer del driver es de URB_PACKET.
LOG_ENTRY_STRUCT = URB_PACKET_STRUCT
LOG_ENTRY_SIZE = URB_PACKET_SIZE

# BUSLENS_DRIVER_STATUS (alineación nativa, 20 bytes):
#   ULONG + BOOLEAN + BOOLEAN + ULONG + ULONG + ULONG
STATUS_STRUCT = struct.Struct("@IBBIII")
STATUS_SIZE = STATUS_STRUCT.size

# Códigos de error Win32 relevantes para el flujo de activación.
ERROR_ACCESS_DENIED = 5
ERROR_SERVICE_DOES_NOT_EXIST = 1060
ERROR_SERVICE_NOT_ACTIVE = 1062
ERROR_SERVICE_EXISTS = 1073
ERROR_INVALID_IMAGE_HASH = 577
ERROR_FILE_NOT_FOUND = 2

# Constantes de SCM (advapi32)
SC_MANAGER_ALL_ACCESS = 0xF003F
SERVICE_ALL_ACCESS = 0xF01FF
SERVICE_KERNEL_DRIVER = 0x00000001
SERVICE_DEMAND_START = 0x00000003
SERVICE_ERROR_NORMAL = 0x00000001
SERVICE_CONTROL_STOP = 0x00000001
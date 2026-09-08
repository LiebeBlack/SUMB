"""Helper SetupAPI (ctypes puro) para enumerar device interfaces.

Compartido por el cliente del driver (Tier 1) y por la captura WinUSB
(Tier 2) / enumeración estándar (Tier 3). No requiere pywin32: usa
setupapi.dll directamente.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import uuid
from typing import Optional

DIGCF_PRESENT = 0x2
DIGCF_DEVICEINTERFACE = 0x10
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259


class GUID(ctypes.Structure):
    """Estructura GUID (ctypes.wintypes no la expone en todos los Pythons)."""

    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wt.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wt.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SetupApi:
    """Acceso a las funciones de setupapi.dll (inyectable en tests)."""

    def __init__(self) -> None:
        self._setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

        self.SetupDiGetClassDevsW = self._setupapi.SetupDiGetClassDevsW
        self.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        self.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR,
                                              ctypes.c_void_p, wt.DWORD]

        self.SetupDiEnumDeviceInterfaces = self._setupapi.SetupDiEnumDeviceInterfaces
        self.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
        self.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p,
                                                     ctypes.POINTER(SP_DEVINFO_DATA),
                                                     ctypes.POINTER(GUID), wt.DWORD,
                                                     ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]

        self.SetupDiGetDeviceInterfaceDetailW = self._setupapi.SetupDiGetDeviceInterfaceDetailW
        self.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
        self.SetupDiGetDeviceInterfaceDetailW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
            ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]

        self.SetupDiDestroyDeviceInfoList = self._setupapi.SetupDiDestroyDeviceInfoList
        self.SetupDiDestroyDeviceInfoList.restype = wt.BOOL
        self.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

    @staticmethod
    def last_error() -> int:
        return ctypes.get_last_error() or 0


def guid_struct(guid_str: str) -> GUID:
    """Convierte una cadena GUID a la estructura ctypes GUID."""
    u = uuid.UUID(guid_str)
    guid = GUID()
    guid.Data1 = u.time_low
    guid.Data2 = u.time_mid
    guid.Data3 = u.time_hi_version
    guid.Data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])
    return guid


def enumerate_interfaces(guid_str: str, api: Optional[SetupApi] = None) -> list[str]:
    """Enumeración de device interfaces de un GUID con SetupAPI.

    Devuelve las rutas (\\\\.\\...) presentes. Nunca lanza: los errores de
    SetupAPI devuelven lista vacía (degradación Tier 3).
    """
    setupapi = api if api is not None else SetupApi()
    guid = guid_struct(guid_str)
    dev_info = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if dev_info is None or dev_info == ctypes.c_void_p(-1).value:
        return []

    paths: list[str] = []
    try:
        index = 0
        while index < 1024:  # límite defensivo contra enum degenerada
            if_data = SP_DEVICE_INTERFACE_DATA()
            if_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    dev_info, None, ctypes.byref(guid), index, ctypes.byref(if_data)):
                # Fin de lista (o error transitorio): no se puede avanzar de
                # forma segura sin repetir el mismo fallo.
                break
            required = wt.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                dev_info, ctypes.byref(if_data), None, 0,
                ctypes.byref(required), None)
            if required.value <= 0:
                index += 1
                continue
            detail = ctypes.create_string_buffer(required.value)
            cb_size = ctypes.c_ulong(ctypes.sizeof(ctypes.c_ulong))
            ctypes.memmove(detail, ctypes.byref(cb_size), ctypes.sizeof(cb_size))
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                    dev_info, ctypes.byref(if_data), detail, required.value,
                    ctypes.byref(required), None):
                index += 1
                continue
            path = ctypes.wstring_at(ctypes.addressof(detail) + ctypes.sizeof(ctypes.c_ulong))
            if path:
                paths.append(path)
            index += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(dev_info)
    return paths


def usb_device_paths(api: Optional[SetupApi] = None) -> list[str]:
    """Device interfaces de dispositivos USB presentes (GUID USB_DEVICE)."""
    from BusLens.infrastructure.kernel.ioctl_codes import USB_DEVICE_INTERFACE_GUID

    return enumerate_interfaces(USB_DEVICE_INTERFACE_GUID, api=api)
"""Puente dinámico kernel<->usuario (Tier 1) con SCM nativo (advapi32).

DynamicKernelBridge monta buslens_filter.sys bajo demanda SIN persistencia:
  - Copia el .sys a %TEMP%\\BusLens\\ (no toca System32\\drivers).
  - Registra un servicio kernel temporal con CreateServiceW
    (SERVICE_DEMAND_START) y lo inicia con StartServiceW.
  - Abre el dispositivo con CreateFileW en \\\\.\\BusLensFilter.
  - Al desmontar: cierra el handle, ControlService(SERVICE_CONTROL_STOP) y
    DeleteService; borra el archivo temporal. Cero persistencia en boot.

Implementado con ctypes puro (advapi32.dll + kernel32.dll), sin subprocess.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Optional

from buslens.infrastructure.kernel.ioctl_codes import (
    DEVICE_SYMLINK,
    ERROR_ACCESS_DENIED,
    ERROR_SERVICE_DOES_NOT_EXIST,
    ERROR_SERVICE_EXISTS,
    ERROR_INVALID_IMAGE_HASH,
    SC_MANAGER_ALL_ACCESS,
    SERVICE_ALL_ACCESS,
    SERVICE_CONTROL_STOP,
    SERVICE_DEMAND_START,
    SERVICE_ERROR_NORMAL,
    SERVICE_KERNEL_DRIVER,
)

logger = logging.getLogger("buslens.infrastructure.kernel.bridge")

SERVICE_NAME = "buslens_filter"
DISPLAY_NAME = "BusLens Kernel Filter (Beta)"
SYS_FILENAME = "buslens_filter.sys"
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class BridgeError(RuntimeError):
    def __init__(self, message: str, win32_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.win32_code = win32_code


class BridgeNeedsElevationError(BridgeError):
    """SCM rechazó por permisos: hace falta elevación UAC."""


class BridgeTestSigningError(BridgeError):
    """El kernel rechazó la imagen del driver (Test-Signing deshabilitado)."""


class _SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wt.DWORD),
        ("dwCurrentState", wt.DWORD),
        ("dwControlsAccepted", wt.DWORD),
        ("dwWin32ExitCode", wt.DWORD),
        ("dwServiceSpecificExitCode", wt.DWORD),
        ("dwCheckPoint", wt.DWORD),
        ("dwWaitHint", wt.DWORD),
    ]


class _AdvApi32:
    """Acceso a las APIs de SCM (inyectable en tests)."""

    def __init__(self) -> None:
        self._advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.OpenSCManagerW = self._advapi32.OpenSCManagerW
        self.OpenSCManagerW.restype = ctypes.c_void_p
        self.OpenSCManagerW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]

        self.CreateServiceW = self._advapi32.CreateServiceW
        self.CreateServiceW.restype = ctypes.c_void_p
        self.CreateServiceW.argtypes = [
            ctypes.c_void_p, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, wt.DWORD,
            wt.DWORD, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p,
            wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR]

        self.OpenServiceW = self._advapi32.OpenServiceW
        self.OpenServiceW.restype = ctypes.c_void_p
        self.OpenServiceW.argtypes = [ctypes.c_void_p, wt.LPCWSTR, wt.DWORD]

        self.StartServiceW = self._advapi32.StartServiceW
        self.StartServiceW.restype = wt.BOOL
        self.StartServiceW.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]

        self.ControlService = self._advapi32.ControlService
        self.ControlService.restype = wt.BOOL
        self.ControlService.argtypes = [ctypes.c_void_p, wt.DWORD,
                                        ctypes.POINTER(_SERVICE_STATUS)]

        self.DeleteService = self._advapi32.DeleteService
        self.DeleteService.restype = wt.BOOL
        self.DeleteService.argtypes = [ctypes.c_void_p]

        self.CloseServiceHandle = self._advapi32.CloseServiceHandle
        self.CloseServiceHandle.restype = wt.BOOL
        self.CloseServiceHandle.argtypes = [ctypes.c_void_p]

        self.CreateFileW = self._kernel32.CreateFileW
        self.CreateFileW.restype = ctypes.c_void_p
        self.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                     wt.DWORD, wt.DWORD, ctypes.c_void_p]

        self.CloseHandle = self._kernel32.CloseHandle
        self.CloseHandle.restype = wt.BOOL
        self.CloseHandle.argtypes = [ctypes.c_void_p]

    @staticmethod
    def last_error() -> int:
        return ctypes.get_last_error() or 0


class DynamicKernelBridge:
    """Montaje/desmontaje dinámico del driver con SCM nativo."""

    def __init__(self, api: Any = None, sys_source: Optional[Path] = None) -> None:
        self._api = api if api is not None else _AdvApi32()
        self._sys_source = Path(sys_source) if sys_source else None
        self._temp_sys: Optional[Path] = None
        self._sc_manager: Optional[int] = None
        self._service: Optional[int] = None
        self._device_handle: Optional[int] = None
        self.mounted = False

    # --- rutas ---

    @staticmethod
    def temp_dir() -> Path:
        base = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
        return Path(base) / "BusLens"

    def default_sys_source(self) -> Optional[Path]:
        """Ubicaciones probables del .sys: junto al exe, dev, ya copiado."""
        candidates: list[Path] = []
        if getattr(ctypes.pythonapi, "Py_GetPath", None) is not None:
            import sys as _sys

            if getattr(_sys, "frozen", False):
                exe_dir = Path(_sys.executable).resolve().parent
                candidates.append(exe_dir / "kernel" / SYS_FILENAME)
            else:
                project_root = Path(__file__).resolve().parent.parent.parent.parent
                candidates.append(project_root / "kernel" / SYS_FILENAME)
        candidates.append(self.temp_dir() / SYS_FILENAME)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    # --- montaje (Tier 1) ---

    def mount(self, sys_source: Optional[Path] = None) -> bool:
        """Copia el .sys a TEMP, registra e inicia el servicio, abre el device.

        Devuelve True si el dispositivo quedó abierto. Lanza
        BridgeNeedsElevationError o BridgeTestSigningError en los fallos
        esperados para que el TierManager decida el fallback.
        """
        source = Path(sys_source) if sys_source else (self._sys_source or self.default_sys_source())
        if source is None or not source.exists():
            raise BridgeError(f"No se encontró {SYS_FILENAME} (compílelo con el WDK)")

        self._temp_sys = self.temp_dir() / SYS_FILENAME
        self._temp_sys.parent.mkdir(parents=True, exist_ok=True)
        if self._temp_sys != source:
            shutil.copy2(source, self._temp_sys)

        self._open_scm()
        try:
            self._ensure_service_registered()
            self._start_service()
        except Exception:
            self._close_scm()
            raise

        self._device_handle = self._api.CreateFileW(
            DEVICE_SYMLINK, GENERIC_READ | GENERIC_WRITE, 0, None,
            OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if self._device_handle is None or self._device_handle == INVALID_HANDLE_VALUE:
            code = self._api.last_error()
            self.unmount(remove_service=False)
            raise BridgeError(f"No se pudo abrir {DEVICE_SYMLINK} (Win32 {code})", code)

        self.mounted = True
        logger.info("Tier 1 montado: %s", DEVICE_SYMLINK)
        return True

    def _open_scm(self) -> None:
        self._sc_manager = self._api.OpenSCManagerW(None, None, SC_MANAGER_ALL_ACCESS)
        if self._sc_manager is None or self._sc_manager == 0:
            code = self._api.last_error()
            if code == ERROR_ACCESS_DENIED:
                raise BridgeNeedsElevationError(
                    "Se requieren privilegios de administrador para montar el driver (Win32 5)", code)
            raise BridgeError(f"OpenSCManagerW falló (Win32 {code})", code)

    def _ensure_service_registered(self) -> None:
        self._service = self._api.OpenServiceW(
            self._sc_manager, SERVICE_NAME, SERVICE_ALL_ACCESS)
        if self._service is not None and self._service != 0:
            return
        self._service = self._api.CreateServiceW(
            self._sc_manager,
            SERVICE_NAME,
            DISPLAY_NAME,
            SERVICE_ALL_ACCESS,
            SERVICE_KERNEL_DRIVER,
            SERVICE_DEMAND_START,
            SERVICE_ERROR_NORMAL,
            str(self._temp_sys),
            None, None, None, None, None)
        if self._service is None or self._service == 0:
            code = self._api.last_error()
            if code == ERROR_SERVICE_EXISTS:
                self._service = self._api.OpenServiceW(
                    self._sc_manager, SERVICE_NAME, SERVICE_ALL_ACCESS)
                return
            if code == ERROR_ACCESS_DENIED:
                raise BridgeNeedsElevationError(
                    "Se requieren privilegios de administrador para crear el servicio (Win32 5)", code)
            raise BridgeError(f"CreateServiceW falló (Win32 {code})", code)

    def _start_service(self) -> None:
        if not self._api.StartServiceW(self._service, 0, None):
            code = self._api.last_error()
            if code == ERROR_INVALID_IMAGE_HASH:
                raise BridgeTestSigningError(
                    "El kernel rechazó buslens_filter.sys (ERROR_INVALID_IMAGE_HASH). "
                    "Activa Test-Signing (bcdedit /set testsigning on) o firma el driver.", code)
            if code == ERROR_ACCESS_DENIED:
                raise BridgeNeedsElevationError(
                    "Se requieren privilegios de administrador para iniciar el servicio (Win32 5)", code)
            # ERROR_SERVICE_ALREADY_RUNNING (1056) o arranques concurrentes: ok.
            if code != 1056:
                raise BridgeError(f"StartServiceW falló (Win32 {code})", code)

    # --- desmontaje / purge ---

    def unmount(self, remove_service: bool = True) -> None:
        """Cierra el handle, detiene el servicio y (opcional) lo elimina."""
        if self._device_handle is not None:
            try:
                self._api.CloseHandle(self._device_handle)
            except Exception:
                pass
            self._device_handle = None

        if self._service is not None and self._sc_manager is not None:
            try:
                status = _SERVICE_STATUS()
                self._api.ControlService(self._service, SERVICE_CONTROL_STOP,
                                         ctypes.byref(status))
            except Exception:
                pass
            if remove_service:
                try:
                    self._api.DeleteService(self._service)
                except Exception:
                    pass

        self._close_scm()
        self._remove_temp_sys()
        self.mounted = False
        logger.info("Tier 1 desmontado%s", " y purgado" if remove_service else "")

    def purge(self) -> None:
        """Desmontaje completo: servicio eliminado y archivo temporal borrado."""
        self.unmount(remove_service=True)

    def _close_scm(self) -> None:
        if self._service is not None:
            try:
                self._api.CloseServiceHandle(self._service)
            except Exception:
                pass
            self._service = None
        if self._sc_manager is not None:
            try:
                self._api.CloseServiceHandle(self._sc_manager)
            except Exception:
                pass
            self._sc_manager = None

    def _remove_temp_sys(self) -> None:
        if self._temp_sys is not None:
            try:
                if self._temp_sys.exists():
                    self._temp_sys.unlink()
            except OSError:
                pass
            self._temp_sys = None

    def is_mounted(self) -> bool:
        return self.mounted
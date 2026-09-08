"""Gestión del servicio kernel buslens_filter (Modo Avanzado Beta).

Operaciones con SCM (sc.exe): instalar (copia del .sys + create), iniciar,
detener y eliminar. La instalación/arranque requieren administrador: si el
proceso actual no tiene privilegios, se eleva UAC lanzando el propio
ejecutable con ``--kernel-admin`` y se verifica el resultado después.

Todos los errores de SCM se mapean a excepciones tipadas para que la capa
de aplicación pueda degradar limpiamente (fallback al Modo Estándar).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

from buslens.infrastructure.kernel.elevation import elevate_kernel_operation
from buslens.infrastructure.kernel.ioctl_codes import (
    ERROR_ACCESS_DENIED,
    ERROR_INVALID_IMAGE_HASH,
    ERROR_SERVICE_DOES_NOT_EXIST,
    ERROR_SERVICE_NOT_ACTIVE,
)

logger = logging.getLogger("buslens.infrastructure.kernel")

SERVICE_NAME = "buslens_filter"
DISPLAY_NAME = "BusLens Kernel Filter (Beta)"
SYS_FILENAME = "buslens_filter.sys"
DRIVERS_DIR = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers"


class DriverManagerError(RuntimeError):
    def __init__(self, message: str, win32_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.win32_code = win32_code


class NeedsElevationError(DriverManagerError):
    """El SCM rechazó la operación por permisos; hay que elevar UAC."""


class TestSigningDisabledError(DriverManagerError):
    """El kernel rechazó la imagen: firma de pruebas deshabilitada."""


def _sc_exit_code(exc: BaseException) -> Optional[int]:
    """Extrae el código de error Win32 de una excepción de subprocess."""
    if exc.returncode is not None and exc.returncode != 0:
        return exc.returncode
    return None


class DriverManager:
    """Operaciones sobre el servicio del driver con fallback a elevación."""

    def __init__(self, runner: Optional[Callable[[Sequence[str]], subprocess.CompletedProcess]] = None) -> None:
        self._runner = runner or self._default_runner

    @staticmethod
    def _default_runner(args: Sequence[str]) -> subprocess.CompletedProcess:
        return subprocess.run(list(args), capture_output=True, text=True)

    # --- fuentes del .sys ---

    def sys_source_candidates(self) -> list[Path]:
        """Ubicaciones probables del .sys: instalado, junto al exe, dev."""
        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidates.append(exe_dir / "kernel" / SYS_FILENAME)
        else:
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            candidates.append(project_root / "kernel" / SYS_FILENAME)
        candidates.append(DRIVERS_DIR / SYS_FILENAME)
        return candidates

    def find_sys(self) -> Optional[Path]:
        for candidate in self.sys_source_candidates():
            if candidate.exists():
                return candidate
        return None

    # --- consultas ---

    def is_installed(self) -> bool:
        result = self._run(["sc.exe", "query", SERVICE_NAME])
        if result.returncode == 0:
            return "SERVICE_NAME" in (result.stdout or "")
        code = _sc_exit_code(result)  # noqa: F841 - usado en debug
        return False

    def is_running(self) -> bool:
        result = self._run(["sc.exe", "query", SERVICE_NAME])
        if result.returncode == 0:
            output = result.stdout or ""
            return "RUNNING" in output.upper()
        return False

    def driver_state(self) -> dict:
        """Estado observable del servicio (sin privilegios especiales)."""
        result = self._run(["sc.exe", "query", SERVICE_NAME])
        if result.returncode != 0:
            return {"installed": False, "running": False}
        output = (result.stdout or "").upper()
        return {
            "installed": "SERVICE_NAME" in (result.stdout or ""),
            "running": "RUNNING" in output,
        }

    # --- operaciones (intento directo + elevación) ---

    def _run(self, args: Sequence[str]) -> subprocess.CompletedProcess:
        try:
            return self._runner(args)
        except FileNotFoundError:
            raise DriverManagerError("sc.exe no está disponible en este sistema") from None

    def _raise_mapped(self, operation: str, result: subprocess.CompletedProcess) -> None:
        code = _sc_exit_code(result)
        message = (result.stderr or result.stdout or "").strip() or f"sc {operation} falló"
        if code == ERROR_ACCESS_DENIED:
            raise NeedsElevationError(f"{message} (se requiere elevación)", code)
        if code == ERROR_INVALID_IMAGE_HASH:
            raise TestSigningDisabledError(
                "El kernel rechazó buslens_filter.sys (ERROR_INVALID_IMAGE_HASH). "
                "Activa el Test-Signing con: bcdedit /set testsigning on (requiere reinicio).",
                code,
            )
        raise DriverManagerError(f"{message} (Win32 {code})", code)

    def _install_and_start_now(self) -> None:
        sys_path = self.find_sys()
        if sys_path is None:
            raise DriverManagerError(
                f"No se encontró {SYS_FILENAME}. Compílelo con el WDK "
                "(kernel/buslens_filter.vcxproj) o inclúyalo junto a la aplicación."
            )
        dest = DRIVERS_DIR / SYS_FILENAME
        if sys_path != dest:
            shutil.copy2(sys_path, dest)
            logger.info(".sys copiado a %s", dest)
        if not self.is_installed():
            result = self._run([
                "sc.exe", "create", SERVICE_NAME,
                "type=", "kernel",
                "start=", "demand",
                "error=", "normal",
                "binPath=", rf"\SystemRoot\System32\drivers\{SYS_FILENAME}",
                "DisplayName=", DISPLAY_NAME,
            ])
            if result.returncode != 0:
                self._raise_mapped("create", result)
        result = self._run(["sc.exe", "start", SERVICE_NAME])
        if result.returncode != 0:
            code = _sc_exit_code(result)
            if code in (ERROR_SERVICE_NOT_ACTIVE, ERROR_SERVICE_DOES_NOT_EXIST):
                pass  # casos límite: se re-verifica después
            else:
                self._raise_mapped("start", result)

    def ensure_installed_and_started(self) -> None:
        """Instala e inicia el servicio, elevando UAC si hace falta."""
        try:
            self._install_and_start_now()
        except NeedsElevationError:
            logger.info("Se requiere elevación para el servicio del driver")
            if not elevate_kernel_operation("install-start"):
                raise DriverManagerError("La elevación UAC fue rechazada por el usuario") from None
            if not self.is_running():
                raise DriverManagerError("El driver no arrancó tras la elevación")

    def stop_and_remove(self) -> None:
        """Detiene y elimina el servicio (eleva UAC si hace falta)."""
        try:
            self._stop_now()
            self._delete_now()
        except NeedsElevationError:
            logger.info("Se requiere elevación para detener/eliminar el servicio")
            if not elevate_kernel_operation("stop-delete"):
                raise DriverManagerError("La elevación UAC fue rechazada por el usuario") from None
            if self.is_installed():
                raise DriverManagerError("El servicio sigue presente tras la elevación")

    def _stop_now(self) -> None:
        result = self._run(["sc.exe", "stop", SERVICE_NAME])
        if result.returncode != 0:
            code = _sc_exit_code(result)
            if code == ERROR_SERVICE_DOES_NOT_EXIST:
                return
            if code == ERROR_SERVICE_NOT_ACTIVE:
                return
            self._raise_mapped("stop", result)

    def _delete_now(self) -> None:
        result = self._run(["sc.exe", "delete", SERVICE_NAME])
        if result.returncode != 0:
            code = _sc_exit_code(result)
            if code == ERROR_SERVICE_DOES_NOT_EXIST:
                return
            self._raise_mapped("delete", result)
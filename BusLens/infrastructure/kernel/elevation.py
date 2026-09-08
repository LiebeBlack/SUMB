"""Elevación UAC puntual para operaciones del driver.

Lanza el propio ejecutable con ``--kernel-admin <operación>`` mediante
ShellExecuteW("runas"). Funciona tanto en desarrollo (python run.py) como
en el ejecutable compilado (BusLens.exe). La elevación solo ocurre cuando
el usuario activa/desactiva el Modo Avanzado.
"""

from __future__ import annotations

import ctypes
import logging
import sys

logger = logging.getLogger("buslens.infrastructure.kernel")

_SW_HIDE = 0
_SE_ERR_SUCCESS_THRESHOLD = 32


def _elevation_command(operation: str) -> tuple[str, str]:
    """Devuelve (exe, argumentos) para el proceso elevado."""
    if getattr(sys, "frozen", False):
        return sys.executable, f"--kernel-admin {operation}"
    return sys.executable, f'"{sys.argv[0]}" --kernel-admin {operation}'


def elevate_kernel_operation(operation: str) -> bool:
    """Solicita UAC para ejecutar una operación del driver.

    Devuelve True si el usuario aceptó (no indica que la operación
    tuviera éxito: el llamador debe verificar el estado después).
    """
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteW.restype = ctypes.c_intptr_t
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int,
        ]
        exe, args = _elevation_command(operation)
        result = shell32.ShellExecuteW(None, "runas", exe, args, None, _SW_HIDE)
    except Exception as exc:  # pragma: no cover - entorno sin shell32
        logger.warning("No se pudo solicitar elevación: %s", exc)
        return False
    if result <= _SE_ERR_SUCCESS_THRESHOLD:
        logger.info("Elevación rechazada o fallida (código %d)", result)
        return False
    logger.info("Elevación aceptada para operación: %s", operation)
    return True
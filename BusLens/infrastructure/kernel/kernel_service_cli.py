"""CLI elevada del driver (invocada con ``--kernel-admin``).

Se lanza desde la propia aplicación con UAC (ver elevation.py) y ejecuta
las operaciones de servicio que requieren administrador:

    <app> --kernel-admin install-start   copia el .sys, crea e inicia el servicio
    <app> --kernel-admin stop-delete     detiene y elimina el servicio
    <app> --kernel-admin status          imprime el estado (no requiere admin)

Códigos de salida: 0 éxito, 1 error.
"""

from __future__ import annotations

import logging
import sys
from typing import Sequence

from buslens.infrastructure.kernel.driver_manager import (
    DriverManager,
    DriverManagerError,
    NeedsElevationError,
    TestSigningDisabledError,
)

logger = logging.getLogger("buslens.infrastructure.kernel.cli")


def run(argv: Sequence[str]) -> int:
    """Ejecuta la operación indicada en argv[2] (tras --kernel-admin)."""
    operation = argv[2] if len(argv) > 2 else "status"
    manager = DriverManager()
    try:
        if operation == "install-start":
            manager._install_and_start_now()
            print(f"[OK] Servicio {manager.SERVICE_NAME} instalado e iniciado")
        elif operation == "stop-delete":
            manager._stop_now()
            manager._delete_now()
            print(f"[OK] Servicio {manager.SERVICE_NAME} detenido y eliminado")
        elif operation == "status":
            state = manager.driver_state()
            print(f"installed={state['installed']} running={state['running']}")
        else:
            print(f"[ERROR] operación desconocida: {operation}")
            return 1
        return 0
    except TestSigningDisabledError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except NeedsElevationError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except DriverManagerError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensivo
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
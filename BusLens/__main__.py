from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(name)s %(levelname)s: %(message)s",
)

logger = logging.getLogger("buslens")


def _ensure_project_root_on_path() -> None:
    """Se asegura de que la raíz del proyecto sea importable como `buslens`.

    Preferimos instalar el paquete (pip install -e . / pip install .) o ejecutar
    desde la raíz. Este fallback es solo para desarrollo local sin instalación.
    """
    project_root = Path(__file__).resolve().parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


def main() -> None:
    _ensure_project_root_on_path()

    # Modo elevado del driver (lanzado por UAC con --kernel-admin <operación>).
    if "--kernel-admin" in sys.argv:
        from buslens.infrastructure.kernel.kernel_service_cli import run as run_kernel_cli

        raise SystemExit(run_kernel_cli(sys.argv))

    try:
        from buslens.presentation.winrt.winrt_app import BusLensApp
        from buslens.presentation.winrt.winrt_types import WinRtUnavailableError
    except Exception as exc:
        logger.exception("No se pudo cargar la capa de presentación: %s", exc)
        logger.info("Esta aplicación está diseñada para Windows 10/11 con WinUI 3 / PyWinRT.")
        sys.exit(1)

    sys.excepthook = _fatal_exception_hook

    app = BusLensApp()
    try:
        app.run()
    except WinRtUnavailableError as exc:
        logger.info("%s — esta aplicación requiere Windows 10/11 con PyWinRT (winrt-Windows).", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("BusLens interrumpido por el usuario")
    except Exception as exc:
        logger.exception("BusLens terminó con error: %s", exc)
        raise
    finally:
        try:
            app.shutdown()
        except Exception as exc:
            logger.warning("shutdown final falló: %s", exc)


def _fatal_exception_hook(exc_type, exc_value, exc_tb) -> None:
    """Hook de última línea: registra cualquier excepción no capturada antes de abortar."""
    logger.error(
        "Excepción fatal no capturada:\n%r",
        exc_value,
        exc_info=(exc_type, exc_value, exc_tb),
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("BusLens interrumpido por el usuario")
    except SystemExit:
        raise
    except Exception as exc:
        logger.exception("BusLens terminó con error: %s", exc)
        raise


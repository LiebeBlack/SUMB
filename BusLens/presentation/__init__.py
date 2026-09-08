from buslens.presentation.winrt.winrt_app import BusLensApp

__all__ = ["BusLensApp"]

__version__ = "1.0.0"


def _check_importability() -> bool:
    """La capa de presentación depende de WinRT y es opcional hasta ejecución."""
    try:
        from buslens.presentation.winrt.winrt_types import WinUiTypes
        _ = WinUiTypes()
        return True
    except Exception:
        # WinRT no disponible en este entorno: aceptable para dev/test sin UI.
        return False


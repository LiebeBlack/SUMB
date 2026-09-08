from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s: %(message)s")
logger = logging.getLogger("buslens.presentation.winrt")

# Tipos típicos que necesitamos de WinUI 3/PyWinRT
REQUIRED_TYPES: Tuple[str, ...] = (
    "Window",
    "AppBarButton",
    "CommandBar",
    "Grid",
    "ListView",
    "TextBox",
    "StackPanel",
    "TextBlock",
    "ScrollViewer",
    "ObservableCollection",
    "SolidColorBrush",
    "Colors",
    "ApplicationView",
    "Thickness",
    "SymbolIcon",
    "Symbol",
)

OPTIONAL_TYPES: Tuple[str, ...] = (
    "MicaBackdrop",
    "AcrylicBackdrop",
)


def _try_import_winrt() -> Dict[str, Any]:
    """Resuelve tipos PyWinRT bajo demanda con mensajes de diagnóstico claros."""

    from winrt.windows.ui.xaml import Window  # type: ignore[assignment]
    from winrt.windows.ui.xaml.controls import (
        AppBarButton,
        CommandBar,
        Grid,
        ListView,
        TextBox,
        StackPanel,
        TextBlock,
        ScrollViewer,
        SymbolIcon,
        Symbol,
    )
    from winrt.windows.ui.xaml.data import ObservableCollection
    from winrt.windows.ui.xaml.media import SolidColorBrush
    from winrt.windows.ui.xaml.media.xaml_media import Colors
    from winrt.windows.ui.viewmanagement import ApplicationView
    from winrt.windows.ui.xaml import Thickness  # type: ignore[assignment]

    types: Dict[str, Any] = {
        "Window": Window,
        "AppBarButton": AppBarButton,
        "CommandBar": CommandBar,
        "Grid": Grid,
        "ListView": ListView,
        "TextBox": TextBox,
        "StackPanel": StackPanel,
        "TextBlock": TextBlock,
        "ScrollViewer": ScrollViewer,
        "ObservableCollection": ObservableCollection,
        "SolidColorBrush": SolidColorBrush,
        "Colors": Colors,
        "ApplicationView": ApplicationView,
        "Thickness": Thickness,
        "SymbolIcon": SymbolIcon,
        "Symbol": Symbol,
    }

    try:
        from winrt.windows.ui.xaml.media.dwarka.mica_backdrop import MicaBackdrop  # type: ignore[assignment]
        types["MicaBackdrop"] = MicaBackdrop
        logger.info("MicaBackdrop disponible (WinUI 3 / Windows 11)")
    except Exception as exc:
        logger.warning("MicaBackdrop no disponible: %s", exc)
        types["MicaBackdrop"] = None

    try:
        from winrt.windows.ui.xaml.media.dwarka.acrylic_backdrop import AcrylicBackdrop  # type: ignore[assignment]
        types["AcrylicBackdrop"] = AcrylicBackdrop
    except Exception:
        types["AcrylicBackdrop"] = None

    return types


class WinUiTypes:
    """Resolución lazy de tipos PyWinRT.

    Si se solicita un tipo opcional que no esté disponible, devuelve None en lugar de
    fallar. Los tipos requeridos fallan temprano durante `ensure()`.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}
        self._loaded = False

    def ensure(self) -> None:
        if self._loaded:
            return
        try:
            self._cache.update(_try_import_winrt())
        except Exception as exc:
            logger.exception("Error al cargar tipos WinRT: %s", exc)
            raise
        self._loaded = True

    def __getitem__(self, name: str) -> Any:
        self.ensure()
        if name not in self._cache:
            available = ", ".join(self._cache.keys())
            raise KeyError(f"Tipo WinRT no disponible: {name}. Disponibles: {available}")
        return self._cache[name]

    def get(self, name: str, default: Any = None) -> Any:
        self.ensure()
        return self._cache.get(name, default)

    @property
    def has_mica(self) -> bool:
        return self._cache.get("MicaBackdrop") is not None

    @property
    def has_acrylic(self) -> bool:
        return self._cache.get("AcrylicBackdrop") is not None

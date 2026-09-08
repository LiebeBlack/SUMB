from __future__ import annotations

import importlib
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("buslens.presentation.winrt")


class WinRtUnavailableError(RuntimeError):
    """Se lanza cuando PyWinRT no está disponible en el entorno.

    Los entry points la capturan para salir limpio (código 1) sin traceback.
    """

# Tipos típicos que necesitamos de WinUI 3/PyWinRT
REQUIRED_TYPES: Tuple[str, ...] = (
    "Window",
    "AppBarButton",
    "CommandBar",
    "Grid",
    "RowDefinition",
    "ColumnDefinition",
    "ListView",
    "TextBox",
    "StackPanel",
    "TextBlock",
    "ScrollViewer",
    "Rectangle",
    "ObservableCollection",
    "SolidColorBrush",
    "Colors",
    "ApplicationView",
    "Thickness",
    "SymbolIcon",
    "Symbol",
    "Orientation",
    "SelectionMode",
    "TextTrimming",
    "ScrollBarVisibility",
    "VerticalAlignment",
    "FontFamily",
    "ToggleSwitch",
    "Visibility",
)

OPTIONAL_TYPES: Tuple[str, ...] = (
    "MicaBackdrop",
    "AcrylicBackdrop",
)

# Namespaces XAML soportados, en orden de preferencia:
#   1. Windows SDK (winrt.windows.ui.xaml) — proyección estándar de PyWinRT.
#   2. WinUI 3 (winrt.microsoft.ui.xaml) — namespace nativo de WinUI 3.
_XAML_PREFIXES: Tuple[str, ...] = ("windows.ui.xaml", "microsoft.ui.xaml")

_BACKDROP_CANDIDATE_MODULES: Tuple[str, ...] = (
    "winrt.microsoft.ui.xaml.media",
    "winrt.windows.ui.xaml.media",
)


def _import_candidate(module_names: Tuple[str, ...]) -> Optional[Any]:
    """Importa el primer módulo disponible de la lista y lo devuelve."""
    for name in module_names:
        try:
            return importlib.import_module(name)
        except Exception:
            continue
    return None


def _resolve_xaml_prefix() -> Optional[str]:
    for prefix in _XAML_PREFIXES:
        module = _import_candidate((f"winrt.{prefix}.controls",))
        if module is not None:
            logger.info("Namespace XAML resuelto: winrt.%s", prefix)
            return prefix
    return None


def _try_import_winrt() -> Dict[str, Any]:
    """Resuelve tipos PyWinRT bajo demanda con mensajes de diagnóstico claros."""
    prefix = _resolve_xaml_prefix()
    if prefix is None:
        raise WinRtUnavailableError(
            "PyWinRT no está disponible en este entorno (se buscó "
            "winrt.windows.ui.xaml y winrt.microsoft.ui.xaml)."
        )

    xaml = importlib.import_module(f"winrt.{prefix}")
    controls = importlib.import_module(f"winrt.{prefix}.controls")
    data = importlib.import_module(f"winrt.{prefix}.data")
    media = importlib.import_module(f"winrt.{prefix}.media")
    viewmanagement = importlib.import_module("winrt.windows.ui.viewmanagement")

    types: Dict[str, Any] = {
        "Window": getattr(xaml, "Window", None),
        "AppBarButton": getattr(controls, "AppBarButton", None),
        "CommandBar": getattr(controls, "CommandBar", None),
        "Grid": getattr(controls, "Grid", None),
        "RowDefinition": getattr(controls, "RowDefinition", None),
        "ColumnDefinition": getattr(controls, "ColumnDefinition", None),
        "ListView": getattr(controls, "ListView", None),
        "TextBox": getattr(controls, "TextBox", None),
        "StackPanel": getattr(controls, "StackPanel", None),
        "TextBlock": getattr(controls, "TextBlock", None),
        "ScrollViewer": getattr(controls, "ScrollViewer", None),
        "Rectangle": getattr(controls, "Rectangle", None),
        "ObservableCollection": getattr(data, "ObservableCollection", None),
        "SolidColorBrush": getattr(media, "SolidColorBrush", None),
        "ApplicationView": getattr(viewmanagement, "ApplicationView", None),
        "Thickness": getattr(xaml, "Thickness", None),
        "SymbolIcon": getattr(controls, "SymbolIcon", None),
        "Symbol": getattr(controls, "Symbol", None),
        "Orientation": getattr(controls, "Orientation", None),
        "SelectionMode": getattr(controls, "SelectionMode", None),
        "TextTrimming": getattr(controls, "TextTrimming", None),
        "ScrollBarVisibility": getattr(controls, "ScrollBarVisibility", None),
        "VerticalAlignment": getattr(controls, "VerticalAlignment", None),
        "FontFamily": getattr(media, "FontFamily", None),
        "ToggleSwitch": getattr(controls, "ToggleSwitch", None),
        "Visibility": getattr(xaml, "Visibility", None),
        "_xaml_prefix": prefix,
    }

    # Tipos opcionales adicionales (InfoBar de WinUI, si existe).
    types.setdefault("InfoBar", getattr(controls, "InfoBar", None))

    # Windows.UI.Colors vive en winrt.windows.ui; en WinUI 3 es winrt.microsoft.ui.
    colors_module = _import_candidate(("winrt.windows.ui", "winrt.microsoft.ui"))
    types["Colors"] = getattr(colors_module, "Colors", None) if colors_module is not None else None

    # --- Tipos opcionales (degradación elegante si no existen) ---
    for name in OPTIONAL_TYPES:
        types[name] = None
    for module_name in _BACKDROP_CANDIDATE_MODULES:
        module = _import_candidate((module_name,))
        if module is None:
            continue
        for name in OPTIONAL_TYPES:
            if types.get(name) is None:
                types[name] = getattr(module, name, None)
    if types.get("MicaBackdrop") is not None:
        logger.info("MicaBackdrop disponible (WinUI 3 / Windows 11)")
    if types.get("AcrylicBackdrop") is not None:
        logger.info("AcrylicBackdrop disponible")

    missing = [name for name in REQUIRED_TYPES if types.get(name) is None]
    if missing:
        raise RuntimeError(f"Tipos WinRT requeridos no disponibles: {', '.join(missing)}")
    return types


class WinUiTypes:
    """Resolución lazy de tipos PyWinRT.

    Si se solicita un tipo opcional que no esté disponible, devuelve None en
    lugar de fallar. Los tipos requeridos fallan temprano durante ``ensure()``.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Any] = {}
        self._loaded = False

    def ensure(self) -> None:
        if self._loaded:
            return
        try:
            self._cache.update(_try_import_winrt())
        except WinRtUnavailableError as exc:
            # Caso esperado (entorno sin PyWinRT): mensaje claro, sin traceback.
            logger.warning("%s", exc)
            raise
        except Exception as exc:
            logger.exception("Error al cargar tipos WinRT: %s", exc)
            raise
        self._loaded = True

    @property
    def xaml_prefix(self) -> str:
        self.ensure()
        return str(self._cache.get("_xaml_prefix", "windows.ui.xaml"))

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
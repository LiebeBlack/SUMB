from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, Iterable, List, Optional, Sequence

from buslens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher

logger = logging.getLogger("buslens.presentation.winrt")


# ---------------------------------------------------------------------------
# Dispatcher thread-safe para WinUI 3
# ---------------------------------------------------------------------------

def setup_thread_safe_dispatcher_for_winui(invoke_from_thread_callback: Callable[[Callable], None]) -> ThreadSafeDispatcher:
    """Conecta el dispatcher global con el mecanismo de despacho principal de WinUI 3."""
    dispatcher = get_global_dispatcher()
    dispatcher.register_main_invoker(invoke_from_thread_callback)
    return dispatcher


def shutdown_thread_safe_dispatcher_for_winui(invoke_from_thread_callback: Callable[[Callable], None]) -> None:
    """Desregistra el invoker del dispatcher global para evitar fugas al cerrar."""
    dispatcher = get_global_dispatcher()
    dispatcher.unregister_main_invoker(invoke_from_thread_callback)


def safe_invoke_ui(callback: Callable) -> None:
    """Wrapper que usa el dispatcher global para invocar actualizaciones de UI.

    Si no hay invokers registrados, el callback se ejecuta en el hilo actual
    (útil en desarrollo/headless).
    """
    get_global_dispatcher().invoke_main(callback)


def current_dispatcher_queue() -> Optional[Any]:
    """Devuelve la DispatcherQueue del hilo actual, o None si no existe.

    Soporta tanto Windows.System.DispatcherQueue (Windows SDK) como
    Microsoft.UI.Dispatching.DispatcherQueue (WinUI 3).
    """
    for module_name in ("winrt.windows.system", "winrt.microsoft.ui.dispatching"):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        cls = getattr(module, "DispatcherQueue", None)
        if cls is None:
            continue
        getter = getattr(cls, "get_for_current_thread", None) or getattr(cls, "GetForCurrentThread", None)
        if getter is None:
            continue
        try:
            queue = getter()
        except Exception:
            continue
        if queue is not None:
            return queue
    return None


def create_winui_invoker() -> Callable[[Callable], None]:
    """Crea un invoker que ejecuta callbacks en la DispatcherQueue del hilo de UI.

    Degradación elegante: si no hay DispatcherQueue disponible (entorno
    headless o sin UI), el callback se ejecuta directamente en el hilo actual.
    """

    def invoke_main(callback: Callable) -> None:
        queue = current_dispatcher_queue()
        if queue is not None:
            try:
                enqueue = getattr(queue, "try_enqueue", None) or getattr(queue, "TryEnqueue", None)
                if enqueue is not None and enqueue(callback):
                    return
            except Exception as exc:
                logger.warning("Falló el enqueue en DispatcherQueue: %s", exc)
        try:
            callback()
        except Exception as exc:
            logger.warning("Invoker fallback ejecutó el callback con error: %s", exc)

    return invoke_main


# ---------------------------------------------------------------------------
# Nombres de miembros (pywinrt usa snake_case; algunos wrappers usan PascalCase)
# ---------------------------------------------------------------------------

def _member_candidates(name: str) -> List[str]:
    """Genera las variantes de nombre plausibles para un miembro WinRT."""
    candidates: List[str] = []
    # snake_case
    snake = "".join(f"_{c.lower()}" if c.isupper() else c for c in name).lstrip("_")
    candidates.append(snake)
    # UPPER_SNAKE
    candidates.append(snake.upper())
    # PascalCase / original
    candidates.append(name)
    # camelCase
    candidates.append(name[:1].lower() + name[1:])
    # todo en minúsculas
    candidates.append(name.lower())
    return list(dict.fromkeys(candidates))  # sin duplicados


def resolve_member(owner: Any, name: str, default: Any = None) -> Any:
    """Resuelve un miembro (propiedad/enum/método) probando variantes de nombre."""
    if owner is None:
        return default
    for candidate in _member_candidates(name):
        try:
            value = getattr(owner, candidate)
        except AttributeError:
            continue
        if value is not None:
            return value
    return default


def get_prop(obj: Any, name: str, default: Any = None) -> Any:
    return resolve_member(obj, name, default)


def set_prop(obj: Any, value: Any, *names: str) -> bool:
    """Asigna una propiedad probando varias variantes de nombre.

    Devuelve True si alguna variante se asignó sin excepción.
    """
    if obj is None:
        return False
    for name in names:
        for candidate in _member_candidates(name):
            try:
                setattr(obj, candidate, value)
                return True
            except Exception:
                continue
    logger.debug("No se pudo asignar ninguna variante de %s", names)
    return False


def call_method(obj: Any, name: str, *args: Any) -> Optional[Any]:
    """Invoca un método probando variantes de nombre; None si ninguna existe."""
    if obj is None:
        return None
    for candidate in _member_candidates(name):
        try:
            method = getattr(obj, candidate)
            if callable(method):
                return method(*args)
        except Exception as exc:
            logger.debug("call_method %s.%s falló: %s", type(obj).__name__, candidate, exc)
    return None


def resolve_enum_value(enum_type: Any, name: str, default: Any = None) -> Any:
    """Resuelve un miembro de enumeración probando UPPER_SNAKE/Pascal/snake."""
    return resolve_member(enum_type, name, default)


# ---------------------------------------------------------------------------
# Adjuntos (attached properties) — p.ej. Grid.Row / Grid.Column
# ---------------------------------------------------------------------------

def set_attached(T: Any, owner_name: str, element: Any, prop_name: str, value: Any) -> bool:
    """Asigna una attached property probando las APIs de PyWinRT.

    Estrategias:
      1. ``Owner.set_row(element, value)`` (snake_case)
      2. ``Owner.SetRow(element, value)`` (PascalCase)
      3. ``element.SetValue(Owner.RowProperty, value)``
    """
    if element is None:
        return False
    owner = T.get(owner_name) if T is not None else None
    if owner is None:
        return False
    for candidate in _member_candidates(f"set_{prop_name}"):
        try:
            method = getattr(owner, candidate)
            method(element, value)
            return True
        except Exception:
            continue
    for candidate in _member_candidates(f"{prop_name}Property"):
        try:
            prop = getattr(owner, candidate)
        except AttributeError:
            continue
        for set_candidate in _member_candidates("SetValue"):
            try:
                method = getattr(element, set_candidate)
                method(prop, value)
                return True
            except Exception:
                continue
    logger.debug("No se pudo asignar attached property %s.%s", owner_name, prop_name)
    return False


# ---------------------------------------------------------------------------
# Colecciones y eventos
# ---------------------------------------------------------------------------

def add_to_collection(collection: Any, item: Any) -> bool:
    """Agrega un item a una colección IVector (append/add/insert)."""
    if collection is None:
        return False
    for name in ("append", "add", "Insert", "insert"):
        try:
            method = getattr(collection, name, None)
            if method is not None:
                method(item)
                return True
        except Exception:
            continue
    return False


def add_event(obj: Any, event_name: str, handler: Callable) -> bool:
    """Conecta un manejador a un evento WinRT probando las API disponibles.

    Estrategias: ``obj.event += handler``, ``obj.add_event(handler)``,
    ``setattr(obj, event, handler)``.
    """
    if obj is None:
        return False
    for candidate in _member_candidates(event_name):
        try:
            event = getattr(obj, candidate)
        except AttributeError:
            continue
        if event is None:
            continue
        # Estrategia 1: operador += (soporte nativo de pywinrt)
        try:
            event += handler  # type: ignore[operator]
            return True
        except Exception:
            pass
        # Estrategia 2: add_<name>(handler)
        for add_candidate in _member_candidates(f"add_{candidate}"):
            try:
                add_method = getattr(obj, add_candidate, None)
                if add_method is not None:
                    add_method(handler)
                    return True
            except Exception:
                continue
    # Estrategia 3: asignación directa (último recurso)
    for candidate in _member_candidates(event_name):
        try:
            setattr(obj, candidate, handler)
            return True
        except Exception:
            continue
    return False


def make_items_source(T: Any, items: Iterable[Any]) -> Any:
    """Convierte una lista Python en un ItemsSource adecuado.

    Prefiere ``ObservableCollection`` (notificaciones nativas); si no está
    disponible devuelve la lista tal cual para que WinUI la proyecte.
    """
    oc_type = T.get("ObservableCollection") if T is not None else None
    if oc_type is None:
        return list(items)
    try:
        oc = oc_type()
        for item in items:
            if not add_to_collection(oc, item):
                oc = None
                break
        if oc is not None:
            return oc
    except Exception as exc:
        logger.warning("No se pudo crear ObservableCollection: %s", exc)
    return list(items)


def refresh_items_source(T: Any, current: Any, items: Sequence[Any]) -> Any:
    """Reemplaza el contenido de un ItemsSource conservando la colección si es mutable."""
    if current is not None:
        for name in ("clear", "Clear"):
            try:
                method = getattr(current, name, None)
                if method is not None:
                    method()
                    break
            except Exception:
                continue
        ok = True
        for item in items:
            if not add_to_collection(current, item):
                ok = False
                break
        if ok:
            return current
    return make_items_source(T, items)


# ---------------------------------------------------------------------------
# Tipos auxiliares (Thickness, FontWeight, colores)
# ---------------------------------------------------------------------------

def make_thickness(T: Any, *values: int) -> Optional[Any]:
    """Crea un Thickness probando constructores de 1 y 4 argumentos."""
    cls = T.get("Thickness") if T is not None else None
    if cls is None:
        return None
    if len(values) == 1:
        try:
            return cls(values[0])
        except Exception:
            pass
    try:
        return cls(*values)
    except Exception:
        pass
    try:
        return cls(0, 0, 0, 0)
    except Exception:
        return None


def resolve_font_weight(T: Any, name: str) -> Optional[Any]:
    """Resuelve un FontWeight desde Windows.UI.Text / Microsoft.UI.Text."""
    for module_name in ("winrt.windows.ui.text", "winrt.microsoft.ui.text"):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        weights = getattr(module, "FontWeights", None)
        if weights is None:
            continue
        value = resolve_member(weights, name)
        if value is not None:
            return value
    return None


def resolve_color(T: Any, name: str) -> Optional[Any]:
    """Resuelve un color estático (Colors.DarkGray, etc.)."""
    colors = T.get("Colors") if T is not None else None
    if colors is None:
        return None
    return resolve_member(colors, name)


def make_brush(T: Any, color_name: str) -> Optional[Any]:
    """Crea un SolidColorBrush desde un nombre de color estático, si es posible."""
    brush_type = T.get("SolidColorBrush") if T is not None else None
    if brush_type is None:
        return None
    color = resolve_color(T, color_name)
    if color is None:
        return None
    try:
        return brush_type(color)
    except Exception as exc:
        logger.debug("No se pudo crear SolidColorBrush(%s): %s", color_name, exc)
        return None
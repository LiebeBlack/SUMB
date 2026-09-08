from __future__ import annotations

from typing import Callable

from buslens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher


def setup_thread_safe_dispatcher_for_winui(invoke_from_thread_callback: Callable[[Callable], None]) -> ThreadSafeDispatcher:
    """Conecta el dispatcher global con el mecanismo de despacho principal de WinUI 3.

    Esta función es idempotente: registrar el mismo callback dos veces no duplica
    invocaciones porque `invoke_main` usa conjunto interno.
    """
    dispatcher = get_global_dispatcher()
    dispatcher.register_main_invoker(invoke_from_thread_callback)
    return dispatcher


def shutdown_thread_safe_dispatcher_for_winui(invoke_from_thread_callback: Callable[[Callable], None]) -> None:
    """Desregistra el invoker del dispatcher global para evitar fugas al cerrar."""
    dispatcher = get_global_dispatcher()
    dispatcher.unregister_main_invoker(invoke_from_thread_callback)


def safe_invoke_ui(callback: Callable) -> None:
    """Wrapper que usa el dispatcher global para invocar actualizaciones de UI.

    Si no hay invokers, el callback se ejecuta en el hilo actual.
    """
    get_global_dispatcher().invoke_main(callback)

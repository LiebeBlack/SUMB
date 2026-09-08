"""Controlador de ciclo de vida de la ventana WinUI 3.

Todo el acceso a la ventana es defensivo: si una API concreta de PyWinRT no
existe, se registra y la operación se omite sin romper la aplicación.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from buslens.presentation.winrt.winrt_helpers import (
    call_method,
    safe_invoke_ui,
    set_prop,
    setup_thread_safe_dispatcher_for_winui,
    shutdown_thread_safe_dispatcher_for_winui,
)

logger = logging.getLogger("buslens.presentation.window_helpers")


class WindowController:
    """Controlador de ventana que permite despachar y controlar el ciclo de vida."""

    def __init__(self) -> None:
        self._window: Any = None
        self._invoker: Optional[Callable[[Callable], None]] = None

    @property
    def window(self) -> Any:
        return self._window

    def attach(self, window: Any) -> None:
        self._window = window

    def detach(self) -> None:
        self._window = None

    def set_title(self, title: str) -> None:
        if self._window is None:
            return
        set_prop(self._window, title, "Title", "title")

    def set_size(self, width: int, height: int) -> None:
        if self._window is None:
            return
        set_prop(self._window, width, "Width", "width")
        set_prop(self._window, height, "Height", "height")

    def activate(self) -> None:
        if self._window is None:
            return
        call_method(self._window, "Activate")

    def close(self) -> None:
        if self._window is None:
            return
        try:
            call_method(self._window, "Close")
        except Exception as exc:
            logger.warning("close de ventana falló: %s", exc)

    def connect_dispatcher(self, invoke_from_thread_callback: Callable[[Callable], None]) -> None:
        """Conecta un invoker del hilo de UI con el dispatcher global."""
        self._invoker = invoke_from_thread_callback
        setup_thread_safe_dispatcher_for_winui(invoke_from_thread_callback)

    def disconnect_dispatcher(self) -> None:
        if self._invoker is not None:
            shutdown_thread_safe_dispatcher_for_winui(self._invoker)
            self._invoker = None

    def invoke_ui(self, callback: Callable) -> None:
        """Despacha un callback al hilo de UI de forma segura."""
        safe_invoke_ui(callback)
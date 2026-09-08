from __future__ import annotations

import logging
from typing import Callable

from buslens.presentation.winrt.winrt_helpers import (
    safe_invoke_ui,
    setup_thread_safe_dispatcher_for_winui,
    shutdown_thread_safe_dispatcher_for_winui,
)

logger = logging.getLogger("buslens.presentation.window_helpers")


class WindowController:
    """Controlador de ventana que permite despachar y controlar el ciclo de vida."""

    def __init__(self) -> None:
        self._window = None

    def attach(self, window) -> None:
        self._window = window

    def detach(self) -> None:
        self._window = None

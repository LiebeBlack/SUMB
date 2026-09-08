from __future__ import annotations

import threading
from typing import Callable, Optional


class ThreadSafeDispatcher:
    """Despacha invocaciones de forma segura sobre un hilo arbitrario.

    La UI de WinUI 3 debe invocar sus callbacks exclusivamente desde el hilo
    principal. Esta clase permite que trabajadores (WMI, asyncio, threads)
    operen sin conocer la UI y despachen mediante `invoke_main`.
    """

    def __init__(self) -> None:
        self._main_callbacks: list[Callable[[Callable], None]] = []
        self._lock = threading.Lock()

    def register_main_invoker(self, invoker: Callable[[Callable], None]) -> None:
        """Registra un invoker del hilo principal (ej. dispatch de WinUI 3)."""
        with self._lock:
            self._main_callbacks.append(invoker)

    def unregister_main_invoker(self, invoker: Callable[[Callable], None]) -> None:
        with self._lock:
            try:
                self._main_callbacks.remove(invoker)
            except ValueError:
                pass

    def invoke_main(self, callback: Callable) -> None:
        """Ejecuta callback desde el hilo principal si está disponible.

        Si no hay invokers registrados (entornos sin UI), cae al hilo actual.
        """
        with self._lock:
            callbacks = list(self._main_callbacks)
        if not callbacks:
            # Caída segura: ejecutar en el hilo actual (útil para desarrollo local).
            try:
                callback()
            except Exception as exc:
                print(f"[BusLens] dispatcher fallback callback failed: {exc}")
            return
        for cb in callbacks:
            try:
                cb(callback)
            except Exception as exc:
                print(f"[BusLens] dispatcher main invoker fell: {exc}")


_global_dispatcher = ThreadSafeDispatcher()


def get_global_dispatcher() -> ThreadSafeDispatcher:
    return _global_dispatcher

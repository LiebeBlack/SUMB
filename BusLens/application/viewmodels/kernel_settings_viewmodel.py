"""ViewModel de los Ajustes del Modo Avanzado (Beta).

Expone el estado del inspector de kernel y los comandos del ToggleSwitch al
panel de Ajustes, difundiendo cambios a la UI mediante callbacks Observer
(``set_on_state_changed``, ``set_on_urb_count_changed``).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from buslens.application.services.app_config import AppConfig
from buslens.application.services.kernel_monitor_service import (
    KernelMonitorService,
    KernelMonitorState,
)

logger = logging.getLogger("buslens.application.viewmodels")


class KernelSettingsViewModel:
    def __init__(self, service: KernelMonitorService, config: AppConfig) -> None:
        self._service = service
        self._config = config
        self._on_state_changed: Callable[[KernelMonitorState, str], None] = lambda s, m: None
        self._on_urb_count_changed: Callable[[int], None] = lambda n: None
        self._urb_count = 0

        # El toggle refleja la última elección guardada, pero el servicio
        # arranca desactivado (no se auto-activa el driver al abrir la app).
        self._service.set_notifier(self._on_state_changed)
        self._service.set_log_notifier(self._on_log_entries)

    # --- registro de observers ---

    def set_on_state_changed(self, callback: Callable[[KernelMonitorState, str], None]) -> None:
        self._on_state_changed = callback
        self._service.set_notifier(callback)

    def set_on_urb_count_changed(self, callback: Callable[[int], None]) -> None:
        self._on_urb_count_changed = callback

    # --- estado expuesto a la UI ---

    @property
    def kernel_enabled(self) -> bool:
        return self._service.is_enabled

    @property
    def last_saved_enabled(self) -> bool:
        """Valor guardado del toggle (para restaurar la posición visual)."""
        return self._config.kernel_inspector_enabled

    @property
    def state(self) -> KernelMonitorState:
        return self._service.state

    @property
    def status_label(self) -> str:
        labels = {
            KernelMonitorState.disabled: "Inspector de hardware: desactivado",
            KernelMonitorState.enabling: "Inspector de hardware: activando...",
            KernelMonitorState.enabled: (
                "Inspector de hardware: activo (Beta) · " + self._service.tier_label
            ),
            KernelMonitorState.disabling: "Inspector de hardware: desactivando...",
            KernelMonitorState.error_fallback: "Inspector de hardware: no disponible (Modo Estándar)",
        }
        return labels.get(self._service.state, "Inspector de hardware: desconocido")

    @property
    def tier_label(self) -> str:
        """Etiqueta del tier activo (Kernel KMDF / WinUSB / WMI estándar)."""
        return self._service.tier_label

    @property
    def last_message(self) -> str:
        return self._config.kernel_last_message or self.status_label

    @property
    def urb_count(self) -> int:
        return self._service.urb_count

    # --- comandos ---

    def set_kernel_enabled(self, enabled: bool) -> None:
        logger.info("Toggle del inspector de kernel -> %s", enabled)
        self._service.set_enabled(enabled)
        self._on_urb_count_changed(self._service.urb_count)

    def refresh_status(self) -> None:
        self._on_state_changed(self._service.state, self.last_message)

    # --- reenvío de eventos del servicio ---

    def _on_log_entries(self, entries: list) -> None:
        self._urb_count = self._service.urb_count
        try:
            self._on_urb_count_changed(self._urb_count)
        except Exception as exc:
            logger.warning("urb_count callback falló: %s", exc)

    def dispose(self) -> None:
        try:
            self._service.dispose()
        except Exception as exc:
            logger.warning("dispose del servicio kernel falló: %s", exc)
"""Servicio del Modo Avanzado (Beta) de BusLens: inspector de hardware.

Orquesta la cascada de tiers a través de TierManager:

  Tier 1  Driver KMDF dinámico (URBs/IRPs en ring 0, elevación UAC al activar).
  Tier 2  Captura WinUSB user-space (winusb.sys + SetupAPI).
  Tier 3  Modo Estándar (WMI) — la app sigue funcionando sin colapsar.

Cualquier fallo degrada automáticamente al siguiente tier notificando al
observador. La activación/desactivación se persiste en AppConfig y el cierre
(dispose) purga el servicio del driver sin dejar recursos en el kernel.
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum, auto
from typing import Any, Callable, Optional

from buslens.application.services.app_config import AppConfig
from buslens.application.services.tier_manager import Tier, TierManager

logger = logging.getLogger("buslens.application.services.kernel")


class KernelMonitorState(Enum):
    disabled = auto()
    enabling = auto()
    enabled = auto()
    disabling = auto()
    error_fallback = auto()


_LOG_POLL_INTERVAL_SECS = 1.0
_LOG_POLL_BATCH = 64


class KernelMonitorService:
    """Ciclo de vida del inspector de hardware con degradación elegante.

    Dependencias inyectables (TierManager, config) para poder probar toda la
    lógica sin driver real.
    """

    def __init__(
        self,
        config: AppConfig,
        tier_manager: Optional[TierManager] = None,
        notifier: Optional[Callable[[KernelMonitorState, str], None]] = None,
        log_notifier: Optional[Callable[[list[dict]], None]] = None,
    ) -> None:
        self._config = config
        self._tier_manager = tier_manager if tier_manager is not None else TierManager()
        self._tier_manager.set_packet_notifier(self._on_winusb_packet)
        self._notifier = notifier or (lambda state, msg: None)
        self._log_notifier = log_notifier or (lambda entries: None)
        self._state = KernelMonitorState.disabled
        self._client: Any = None
        self._log_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._urb_count = 0
        self._attached_path: Optional[str] = None
        self._lock = threading.Lock()

    # --- estado observable ---

    def set_notifier(self, notifier: Callable[[KernelMonitorState, str], None]) -> None:
        self._notifier = notifier

    def set_log_notifier(self, notifier: Callable[[list[dict]], None]) -> None:
        self._log_notifier = notifier

    @property
    def state(self) -> KernelMonitorState:
        return self._state

    @property
    def is_enabled(self) -> bool:
        return self._state in (KernelMonitorState.enabling, KernelMonitorState.enabled)

    @property
    def active_tier(self) -> Tier:
        return self._tier_manager.active_tier

    @property
    def tier_label(self) -> str:
        return self._tier_manager.tier_label

    @property
    def last_tier_error(self) -> Optional[str]:
        return self._tier_manager.last_error

    @property
    def urb_count(self) -> int:
        return self._urb_count

    @property
    def attached_path(self) -> Optional[str]:
        return self._attached_path

    def _set_state(self, state: KernelMonitorState, message: str) -> None:
        with self._lock:
            self._state = state
        self._config.kernel_last_message = message
        logger.info("KernelMonitor %s: %s", state.name, message)
        try:
            self._notifier(state, message)
        except Exception as exc:
            logger.warning("notifier falló: %s", exc)

    # --- activación / desactivación ---

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            if self._state in (KernelMonitorState.enabling, KernelMonitorState.enabled):
                return
            self._enable()
        else:
            if self._state in (KernelMonitorState.disabling, KernelMonitorState.disabled):
                return
            self._disable()

    def _enable(self) -> None:
        self._set_state(KernelMonitorState.enabling, "Activando el inspector de hardware...")
        try:
            tier = self._tier_manager.activate()
        except Exception as exc:
            self._fail_cleanly(exc)
            return

        self._attached_path = self._tier_manager.attached_path
        if tier is Tier.tier1_kernel:
            self._client = self._tier_manager.client
            self._config.kernel_inspector_enabled = True
            suffix = f" adjunto a {self._attached_path}" if self._attached_path else ""
            self._set_state(
                KernelMonitorState.enabled,
                "Inspector de kernel activo (Tier 1 · KMDF)" + suffix,
            )
            self._start_log_poller()
        elif tier is Tier.tier2_winusb:
            self._config.kernel_inspector_enabled = True
            self._set_state(
                KernelMonitorState.enabled,
                "Captura USB activa (Tier 2 · WinUSB user-space)",
            )
        else:
            self._config.kernel_inspector_enabled = False
            detail = f": {self._tier_manager.last_error}" if self._tier_manager.last_error else ""
            self._set_state(
                KernelMonitorState.error_fallback,
                "Modo Estándar activo (Tier 3 · WMI)" + detail,
            )

    def _disable(self) -> None:
        self._set_state(KernelMonitorState.disabling, "Deteniendo el inspector de hardware...")
        self._stop_log_poller()
        self._tier_manager.deactivate()
        self._client = None
        self._urb_count = 0
        self._attached_path = None
        self._config.kernel_inspector_enabled = False
        self._set_state(KernelMonitorState.disabled, "Inspector de hardware desactivado")

    def _fail_cleanly(self, exc: Exception) -> None:
        """Degradación elegante: libera lo que haya y notifica el fallback."""
        logger.warning("Modo Avanzado falló (%s); degradando al Modo Estándar", exc)
        self._tier_manager.deactivate()
        self._client = None
        self._attached_path = None
        self._config.kernel_inspector_enabled = False
        message = f"Modo Estándar activo (el inspector de hardware no está disponible): {exc}"
        self._set_state(KernelMonitorState.error_fallback, message)

    # --- captura de paquetes WinUSB (Tier 2) ---

    def _on_winusb_packet(self, pipe_id: int, data: bytes) -> None:
        self._urb_count += 1
        try:
            self._log_notifier([{
                "timestamp_utc": time.time_ns(),
                "pipe_id": pipe_id,
                "size": len(data),
                "tier": "winusb",
            }])
        except Exception as exc:
            logger.debug("log_notifier falló: %s", exc)

    # --- poller del ring buffer (Tier 1) ---

    def _start_log_poller(self) -> None:
        if self._log_thread is not None and self._log_thread.is_alive():
            return
        self._stop_event.clear()
        self._log_thread = threading.Thread(
            target=self._log_loop, name="BusLensKernelLog", daemon=True
        )
        self._log_thread.start()

    def _stop_log_poller(self) -> None:
        self._stop_event.set()
        if self._log_thread is not None:
            self._log_thread.join(timeout=2.0)
        self._log_thread = None

    def _log_loop(self) -> None:
        while not self._stop_event.is_set():
            self._poll_once()
            self._stop_event.wait(_LOG_POLL_INTERVAL_SECS)

    def _poll_once(self) -> None:
        """Consulta el ring buffer del driver una vez y difunde los eventos."""
        try:
            if self._client is not None:
                entries = self._client.query_log(_LOG_POLL_BATCH)
                if entries:
                    self._urb_count += len(entries)
                    self._log_notifier(entries)
        except Exception as exc:
            logger.debug("query_log falló (el driver puede haberse detenido): %s", exc)

    # --- cierre de la aplicación ---

    def dispose(self) -> None:
        """Cierre seguro: detiene captura, cierra clientes y purga el driver.

        El servicio del driver se elimina (DeleteService) y el .sys temporal
        se borra: cero recursos colgados en el kernel y cero persistencia.
        """
        self._stop_log_poller()
        try:
            self._tier_manager.deactivate()
        except Exception as exc:
            logger.warning("Purge final del inspector de hardware falló: %s", exc)
        self._client = None
        self._urb_count = 0
        self._attached_path = None
        if self._state is KernelMonitorState.enabled:
            self._state = KernelMonitorState.disabled
        logger.info("Inspector de hardware detenido (recursos purgados)")
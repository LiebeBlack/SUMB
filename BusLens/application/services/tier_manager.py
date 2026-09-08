"""Gestor de tiers (cascada T1 -> T2 -> T3) del inspector de hardware.

Estrategia de máxima capacidad primero con degradación transparente:

  Tier 1 (Kernel KMDF dinámico):  monta buslens_filter.sys bajo demanda con
      SCM nativo (advapi32), sin persistencia en boot. Si el proceso no tiene
      privilegios de administrador, solicita una elevación UAC puntual y
      reintenta. Proporciona captura de URBs/IRPs en ring 0.

  Tier 2 (WinUSB / user-space):   si el driver no puede cargarse (Test-Signing
      deshabilitado, Secure Boot, firma, permisos), captura los pipes
      bulk/interrupt de los dispositivos USB mediante la API nativa WinUSB
      (winusb.sys) a través de SetupAPI.

  Tier 3 (WMI estándar):          si nada de lo anterior está disponible, el
      Modo Estándar (Win32_PnPEntity + hotplug WMI) sigue activo sin elevación.
      La aplicación nunca colapsa: cada tier falla cerrando sus recursos.

El desmontaje (deactivate) cierra el handle del dispositivo, detiene la
captura, detiene el servicio (ControlService SERVICE_CONTROL_STOP) y lo
elimina (DeleteService): cero servicios huérfanos y cero persistencia en boot.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Any, Callable, Optional

logger = logging.getLogger("buslens.application.services.tiers")


class Tier(Enum):
    """Nivel de capacidad activo del inspector de hardware."""

    tier1_kernel = auto()
    tier2_winusb = auto()
    tier3_wmi = auto()


TIER_LABELS: dict[Tier, str] = {
    Tier.tier1_kernel: "Kernel (KMDF)",
    Tier.tier2_winusb: "WinUSB (user-space)",
    Tier.tier3_wmi: "Estándar (WMI)",
}


class TierActivationError(RuntimeError):
    """Error fatal del tier activo (no degradable por sí mismo)."""


class TierManager:
    """Orquestador de la cascada T1 -> T2 -> T3 con limpieza garantizada.

    Dependencias inyectables (bridge, cliente IOCTL, cliente WinUSB,
    elevador UAC) para poder probar toda la lógica sin hardware real.
    """

    def __init__(
        self,
        bridge_factory: Optional[Callable[[], Any]] = None,
        client_factory: Optional[Callable[[], Any]] = None,
        winusb_factory: Optional[Callable[[], Any]] = None,
        elevator: Optional[Callable[[str], bool]] = None,
        packet_notifier: Optional[Callable[[int, bytes], None]] = None,
    ) -> None:
        self._bridge_factory = bridge_factory or self._default_bridge_factory
        self._client_factory = client_factory or self._default_client_factory
        self._winusb_factory = winusb_factory or self._default_winusb_factory
        self._elevator = elevator or self._default_elevator
        self._packet_notifier = packet_notifier or (lambda pipe_id, data: None)

        self.active_tier = Tier.tier3_wmi
        self._bridge: Any = None
        self._client: Any = None
        self._winusb: Any = None
        self._attached_path: Optional[str] = None
        self._elevated_service = False
        self.last_error: Optional[str] = None

    # --- fábricas por defecto (resolución diferida para no importar WMI/WinRT) ---

    @staticmethod
    def _default_bridge_factory() -> Any:
        from BusLens.infrastructure.kernel.dynamic_kernel_bridge import DynamicKernelBridge

        return DynamicKernelBridge()

    @staticmethod
    def _default_client_factory() -> Any:
        from BusLens.infrastructure.kernel.kernel_client import KernelClient

        return KernelClient()

    @staticmethod
    def _default_winusb_factory() -> Any:
        from BusLens.infrastructure.usb.winusb_client import WinUsbClient

        return WinUsbClient()

    @staticmethod
    def _default_elevator(operation: str) -> bool:
        from BusLens.infrastructure.kernel.elevation import elevate_kernel_operation

        return elevate_kernel_operation(operation)

    # --- observación ---

    @property
    def client(self) -> Any:
        """Cliente IOCTL activo del Tier 1 (None si no aplica)."""
        return self._client

    @property
    def bridge(self) -> Any:
        return self._bridge

    @property
    def attached_path(self) -> Optional[str]:
        return self._attached_path

    @property
    def tier_label(self) -> str:
        return TIER_LABELS.get(self.active_tier, "Desconocido")

    def set_packet_notifier(self, notifier: Callable[[int, bytes], None]) -> None:
        self._packet_notifier = notifier or (lambda pipe_id, data: None)
        if self._winusb is not None:
            try:
                self._winusb.set_packet_notifier(self._packet_notifier)
            except Exception:
                pass

    # --- activación en cascada ---

    def activate(self) -> Tier:
        """Intenta T1 (KMDF), luego T2 (WinUSB), y degrada a T3 (WMI).

        Devuelve el tier activo. Nunca lanza hacia la UI: cualquier fallo
        cierra los recursos abiertos y cae al siguiente tier.
        """
        if self.activate_tier1():
            return self.active_tier
        if self.activate_tier2():
            return self.active_tier
        return self.activate_tier3()

    def activate_tier1(self) -> bool:
        """Carga dinámica del driver KMDF (máxima capacidad).

        Si el SCM rechaza por permisos, solicita elevación UAC puntual y
        reintenta abriendo el dispositivo contra el servicio ya instalado.
        """
        bridge = None
        client = None
        try:
            bridge = self._bridge_factory()
            bridge.mount()
            client = self._client_factory()
            client.open()
            self._elevated_service = False
        except Exception as exc:
            logger.info("Tier 1 (KMDF) en proceso no disponible: %s", exc)
            self.last_error = f"Tier 1 (KMDF): {exc}"
            # Intentar con elevación UAC puntual (solo si el montaje en
            # proceso falló por permisos; otros fallos no mejoran con UAC).
            from BusLens.infrastructure.kernel.dynamic_kernel_bridge import (
                BridgeNeedsElevationError,
            )

            needs_elevation = isinstance(exc, BridgeNeedsElevationError)
            if needs_elevation:
                if self._elevator("install-start"):
                    try:
                        client = self._client_factory()
                        client.open()
                        self._elevated_service = True
                    except Exception as exc2:
                        logger.warning("Tier 1 tras elevación falló: %s", exc2)
                        self.last_error = f"Tier 1 (KMDF elevado): {exc2}"
                        self._teardown(None, client)
                        return False
                else:
                    self.last_error = "Tier 1 (KMDF): elevación UAC rechazada"
                    self._teardown(bridge, None)
                    return False
            else:
                self._teardown(bridge, client)
                return False

        # Driver conectado: preparar la captura.
        try:
            status = client.query_status()
            logger.info("Driver conectado: versión 0x%08x, captura previa=%s",
                        status.get("version", 0), status.get("capturing"))
            client.clear_log()
            client.start_capture()
            self._attached_path = None
            try:
                self._attached_path = client.attach_first_usb()
            except Exception as exc:
                logger.warning("Adjunto USB best-effort falló: %s", exc)
                self._attached_path = None
        except Exception as exc:
            logger.warning("Preparación de captura Tier 1 falló: %s", exc)
            self.last_error = f"Tier 1 (KMDF): {exc}"
            self._teardown(bridge, client)
            return False

        self._bridge = bridge
        self._client = client
        self.active_tier = Tier.tier1_kernel
        self.last_error = None
        logger.info("Tier 1 activo (KMDF)%s",
                    f" adjunto a {self._attached_path}" if self._attached_path else "")
        return True

    def activate_tier2(self) -> bool:
        """Captura USB user-space con la API nativa WinUSB (winusb.sys)."""
        winusb = None
        try:
            winusb = self._winusb_factory()
            winusb.set_packet_notifier(self._packet_notifier)
            if winusb.open_first_available():
                winusb.start_reading()
                self._winusb = winusb
                self.active_tier = Tier.tier2_winusb
                self.last_error = None
                logger.info("Tier 2 activo (WinUSB)")
                return True
            self.last_error = "Tier 2 (WinUSB): ningún dispositivo USB con interfaz WinUSB"
        except Exception as exc:
            logger.info("Tier 2 (WinUSB) no disponible: %s", exc)
            self.last_error = f"Tier 2 (WinUSB): {exc}"
        if winusb is not None:
            try:
                winusb.close()
            except Exception:
                pass
        return False

    def activate_tier3(self) -> Tier:
        """Modo Estándar (WMI): no requiere nada adicional del kernel.

        Conserva ``last_error`` (el motivo del fallo de T1/T2) para que la
        UI pueda informar al usuario por qué se degradó.
        """
        self.active_tier = Tier.tier3_wmi
        self._attached_path = None
        logger.info("Tier 3 activo (WMI estándar); fallo previo: %s", self.last_error)
        return self.active_tier

    # --- desactivación / purge ---

    def deactivate(self) -> None:
        """Cierra todos los recursos y purga el servicio del driver.

        Orden seguro: lector WinUSB -> cliente IOCTL (stop + close) ->
        puente SCM (stop del servicio + DeleteService + borrado del .sys
        temporal). Si el servicio lo instaló la CLI elevada, solicita la
        elevación de limpieza (best-effort, nunca lanza hacia la UI).
        """
        if self._winusb is not None:
            try:
                self._winusb.close()
            except Exception as exc:
                logger.warning("Cierre de WinUSB falló: %s", exc)
            self._winusb = None

        client, self._client = self._client, None
        if client is not None:
            try:
                client.stop_capture()
            except Exception as exc:
                logger.warning("stop_capture falló: %s", exc)
            try:
                client.close()
            except Exception as exc:
                logger.warning("close del cliente IOCTL falló: %s", exc)

        bridge, self._bridge = self._bridge, None
        if bridge is not None:
            try:
                bridge.unmount(remove_service=True)
            except Exception as exc:
                logger.warning("Purge del driver falló: %s", exc)

        if self._elevated_service:
            self._elevated_service = False
            try:
                if not self._elevator("stop-delete"):
                    logger.warning("Limpieza del servicio elevado rechazada por el usuario")
            except Exception as exc:
                logger.warning("Limpieza del servicio elevado falló: %s", exc)

        self._attached_path = None
        self.active_tier = Tier.tier3_wmi

    # --- utilidades ---

    def _teardown(self, bridge: Any, client: Any) -> None:
        """Cierre defensivo de recursos parcialmente abiertos (Tier 1)."""
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if bridge is not None:
            try:
                bridge.unmount(remove_service=True)
            except Exception:
                pass

    def dispose(self) -> None:
        self.deactivate()
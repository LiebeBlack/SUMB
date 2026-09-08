"""Configuración central de la aplicación BusLens.

Única fuente de verdad para el nombre/versión, las dimensiones de la ventana
y los intervalos de monitoreo compartidos entre capas.
"""

from __future__ import annotations

APP_NAME = "BusLens"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "BusLens — Monitor USB"

# Dimensiones de la ventana (DIPs)
DEFAULT_WINDOW_WIDTH = 1100
DEFAULT_WINDOW_HEIGHT = 720
MIN_WINDOW_WIDTH = 800
MIN_WINDOW_HEIGHT = 520

# Monitoreo
DEFAULT_POLL_INTERVAL_SECS = 1.5
EVENT_RESYNC_INTERVAL_SECS = 5.0
WMI_EVENT_DELAY_SECS = 2
WMI_EVENT_RECEIVE_TIMEOUT_MS = 1500

# WMI
USB_CLASS_GUID = "{36fc9e60-c465-11cf-8056-444553540000}"
WMI_PNP_ENTITY_CLASS = "Win32_PnPEntity"


def app_name() -> str:
    return APP_NAME


def app_version() -> str:
    return APP_VERSION
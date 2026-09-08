"""Persistencia de configuración de BusLens en JSON local.

Guarda el estado del toggle "Habilitar Inspector Kernel (Beta)" y otros
valores en %APPDATA%\\BusLens\\config.json. Thread-safe (un lock), con
recuperación ante archivos corruptos (defaults).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("buslens.application.config")

DEFAULTS: dict[str, Any] = {
    "kernel_inspector_enabled": False,
    "kernel_last_message": "",
    "kernel_urb_count": 0,
}


def default_config_path() -> Path:
    """Ruta por defecto: %APPDATA%\\BusLens\\config.json."""
    base = os.environ.get("APPDATA")
    if not base:
        base = tempfile.gettempdir()
    return Path(base) / "BusLens" / "config.json"


class AppConfig:
    """Configuración JSON con carga diferida y guardado atómico."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else default_config_path()
        self._lock = threading.Lock()
        self._values: dict[str, Any] = dict(DEFAULTS)
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    # --- carga / guardado ---

    def load(self) -> "AppConfig":
        """Carga el archivo; si falta o está corrupto, usa defaults."""
        with self._lock:
            self._values = dict(DEFAULTS)
            self._loaded = True
            if not self._path.exists():
                return self
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key, value in data.items():
                        if key in DEFAULTS:
                            self._values[key] = value
                        else:
                            self._values[key] = value
            except (OSError, ValueError) as exc:
                logger.warning("config.json corrupto (%s); usando defaults", exc)
                try:
                    backup = self._path.with_suffix(".json.corrupt")
                    self._path.rename(backup)
                except OSError:
                    pass
            return self

    def save(self) -> None:
        """Guarda con escritura atómica (temp + rename)."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._values, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self._path)

    # --- acceso ---

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._values[key] = value

    def reset(self) -> None:
        with self._lock:
            self._values = dict(DEFAULTS)
        try:
            if self._path.exists():
                self._path.unlink()
        except OSError:
            pass

    # --- accesos tipados del Modo Avanzado ---

    @property
    def kernel_inspector_enabled(self) -> bool:
        return bool(self.get("kernel_inspector_enabled", False))

    @kernel_inspector_enabled.setter
    def kernel_inspector_enabled(self, value: bool) -> None:
        self.set("kernel_inspector_enabled", bool(value))
        self.save()

    @property
    def kernel_last_message(self) -> str:
        return str(self.get("kernel_last_message", "") or "")

    @kernel_last_message.setter
    def kernel_last_message(self, value: str) -> None:
        self.set("kernel_last_message", str(value))
        self.save()
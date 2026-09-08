"""
Chequeo de sintaxis y dependencias mínimas para BusLens.

Se ejecuta desde la raíz del proyecto con:

    python scripts/check_syntax.py
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CWD = Path.cwd()

if PROJECT_ROOT != CWD:
    os.chdir(PROJECT_ROOT)

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PYTHON_FILES = sorted(PROJECT_ROOT.glob("**/*.py"))


def check_syntax(path: Path) -> bool:
    try:
        ast.parse(path.read_text(encoding="utf-8"))
        return True
    except SyntaxError as exc:
        print(f"[SYNTAX ERROR] {path}: {exc}")
        return False


def check_external_dependencies(path: Path) -> None:
    """Informa (sin fallar) de módulos externos que cada archivo importa."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in {"wmi", "winrt", "pythoncom"}:
                        print(f"[INFO] {path} depende de librería externa: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.split(".")[0] in {"wmi", "winrt", "pythoncom"}:
                    print(f"[INFO] {path} depende de librería externa: {module}")
    except Exception:
        pass


def check_layer_importability() -> bool:
    """Verifica que cada capa se pueda importar (WMI/WinRT son opcionales)."""
    checks = [
        ("buslens", "paquete raíz"),
        ("buslens.domain", "capa de dominio"),
        ("buslens.application", "capa de aplicación"),
        ("buslens.infrastructure", "capa de infraestructura"),
        ("buslens.infrastructure.wmi", "cliente WMI"),
    ]
    ok = True
    for module_name, label in checks:
        try:
            __import__(module_name)
            print(f"[OK] {label} importable ({module_name})")
        except Exception as exc:
            print(f"[ERROR] no se pudo importar {module_name}: {exc}")
            ok = False

    try:
        from buslens.infrastructure.wmi.wmi_client import WmiClient
        client = WmiClient()
        print(f"[INFO] WmiClient creado. WMI disponible: {client.available}")
    except Exception as exc:
        print(f"[WARN] WmiClient no pudo instanciarse: {exc}")

    try:
        from buslens.domain.models.usb_device import UsbDevice
        d = UsbDevice(pnp_device_id=r"USB\VID_1234&PID_5678\dev", name="TestDevice")
        print(f"[OK] UsbDevice parseado: vendor={d.vendor_id}, product={d.product_id}")
    except Exception as exc:
        print(f"[ERROR] UsbDevice: {exc}")
        ok = False

    try:
        from buslens.application.services.bus_service import BusService
        from buslens.application.viewmodels.main_viewmodel import MainViewModel
        svc = BusService()
        vm = MainViewModel(svc)
        svc.stop()
        vm.dispose()
        print("[OK] BusService + MainViewModel creados y liberados")
    except Exception as exc:
        print(f"[ERROR] capa de aplicación: {exc}")
        ok = False

    return ok


def main() -> int:
    print(f"Escaneando raíz: {PROJECT_ROOT}")
    print(f"CWD: {CWD}")
    print(f"sys.path (inicio): {sys.path[0]!r}")

    total = 0
    ok = 0
    for path in PYTHON_FILES:
        total += 1
        if check_syntax(path):
            ok += 1
        check_external_dependencies(path)

    print(f"Archivos Python: {total} — sintaxis válida: {ok}")
    if ok != total:
        print("ERROR: archivos con sintaxis inválida presentes.")
        return 1

    if not check_layer_importability():
        print("[ERROR] importabilidad de capas falló.")
        return 1

    print("Síntesis: estructura y capas válidas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
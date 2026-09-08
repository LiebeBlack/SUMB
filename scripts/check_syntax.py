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

PYTHON_FILES = list(PROJECT_ROOT.glob("**/*.py"))


def check_syntax(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return True
    except SyntaxError as exc:
        print(f"[SYNTAX ERROR] {path}: {exc}")
        return False


def check_imports(path: Path) -> bool:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"wmi", "winrt"}:
                        print(f"[INFO] {path} depende de librería externa: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.split(".")[0] in {"wmi", "winrt"}:
                    print(f"[INFO] {path} depende de librería externa: {module}")
        return True
    except Exception as exc:
        print(f"[IMPORT CHECK ERROR] {path}: {exc}")
        return False


def main() -> int:
    print(f"Escaneando raíz: {PROJECT_ROOT}")
    print(f"CWD: {CWD}")
    print(f"sys.path (inicio): {sys.path[0]!r}")

    total = 0
    ok = 0
    for path in sorted(PYTHON_FILES):
        total += 1
        if check_syntax(path):
            ok += 1
        check_imports(path)

    print(f"Archivos Python: {total} — sintaxis válida: {ok}")
    if ok != total:
        print("ERROR: archivos con sintaxis inválida presentes.")
        return 1

    try:
        from importlib.machinery import SourceFileLoader

        pkg_dir = PROJECT_ROOT / "BusLens"
        init = pkg_dir / "__init__.py"
        if init.exists():
            loader = SourceFileLoader("buslens", str(init))
            mod = loader.load_module("buslens")
            print(f"Paquete buslens importado manualmente: {mod.__version__}")
            for sub in ("application", "domain", "infrastructure", "presentation"):
                sub_init = pkg_dir / sub / "__init__.py"
                if sub_init.exists():
                    sub_loader = SourceFileLoader(f"buslens.{sub}", str(sub_init))
                    sub_mod = sub_loader.load_module(f"buslens.{sub}")
                    print(f"  subpaquete {sub}: {getattr(sub_mod, '__version__', 'ok')}")
        else:
            print("[ERROR] no existe BusLens/__init__.py")
            return 1
    except Exception as exc:
        print(f"[ERROR] importación de paquetes: {exc}")
        return 1

    print("[OK] paquetes raiz disponibles")

    try:
        from importlib.machinery import SourceFileLoader

        def _load(pkg: str, subpath: str):
            init = PROJECT_ROOT / "BusLens" / subpath / "__init__.py"
            if not init.exists():
                raise FileNotFoundError(str(init))
            return SourceFileLoader(pkg, str(init)).load_module(pkg)

        _ = _load("buslens.infrastructure.wmi", "infrastructure/wmi")
        from buslens.infrastructure.wmi.wmi_client import WmiClient
        client = WmiClient()
        print(f"WmiClient creado. Disponible: {client.available}")

        _ = _load("buslens.domain.models", "domain/models")
        from buslens.domain.models.usb_device import UsbDevice
        d = UsbDevice(pnp_device_id=r"USB\VID_1234&PID_5678\dev", name="TestDevice")
        print(f"UsbDevice parseado: vendor={d.vendor_id}, product={d.product_id}, name={d.name}")

        _ = _load("buslens.application.services", "application/services")
        from buslens.application.services.bus_service import BusService
        svc = BusService()
        print("BusService creado correctamente")

        _ = _load("buslens.application.viewmodels", "application/viewmodels")
        from buslens.application.viewmodels.main_viewmodel import MainViewModel
        vm = MainViewModel(BusService())
        print("MainViewModel creado correctamente")
    except Exception as exc:
        print(f"[ERROR] importación de capas: {exc}")
        return 1

    print("Síntesis: estructura y capas válidas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

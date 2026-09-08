"""Compilación del ejecutable autocontenido de BusLens con PyInstaller.

Uso:
    python build_exe.py                # ejecutable único (onefile)
    python build_exe.py --onedir       # modo carpeta (arranque más rápido)
    python build_exe.py --name BusLens --clean

Qué hace:
    1. Genera assets/buslens.ico si no existe (scripts/make_icon.py).
    2. Genera packaging/version_info.txt con la versión de app_config.
    3. Genera installer/version.iss para que Inno Setup use la misma versión.
    4. Compila con PyInstaller incluyendo:
       - Todas las proyecciones winrt-* instaladas (pyd/DLL nativos).
       - Los namespaces WinRT usados dinámicamente (hidden-imports).
       - pythoncom/pywintypes/win32com (bindings COM) y wmi.
       - El manifest de Windows (DPI PerMonitorV2, UTF-8, Win10/11).
       - La información de versión del ejecutable.

Salida: dist/BusLens.exe (onefile) o dist/BusLens/BusLens.exe (onedir).
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY_SCRIPT = ROOT / "run.py"
ASSETS_DIR = ROOT / "assets"
ICON_PATH = ASSETS_DIR / "buslens.ico"
MANIFEST_PATH = ROOT / "packaging" / "buslens.manifest"
VERSION_INFO_TEMPLATE = """\
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({filevers}),
    prodvers=({filevers}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          '040904B0',
          [
            StringStruct('CompanyName', 'BusLens'),
            StringStruct('FileDescription', 'Monitor e Inspector de Protocolos USB / Hardware'),
            StringStruct('FileVersion', '{version}'),
            StringStruct('InternalName', 'BusLens'),
            StringStruct('LegalCopyright', 'Copyright (c) BusLens. MIT License.'),
            StringStruct('OriginalFilename', 'BusLens.exe'),
            StringStruct('ProductName', 'BusLens'),
            StringStruct('ProductVersion', '{version}')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

# Namespaces WinRT que winrt_types.py y winrt_helpers.py importan en tiempo
# de ejecución con importlib: PyInstaller no puede verlos estáticamente.
WINRT_NAMESPACES = (
    "winrt.windows.ui.xaml",
    "winrt.windows.ui.xaml.controls",
    "winrt.windows.ui.xaml.data",
    "winrt.windows.ui.xaml.markup",
    "winrt.windows.ui.xaml.media",
    "winrt.windows.ui.xaml.media.xaml_media",
    "winrt.windows.ui.viewmanagement",
    "winrt.windows.ui",
    "winrt.windows.ui.text",
    "winrt.windows.system",
    "winrt.microsoft.ui",
    "winrt.microsoft.ui.xaml",
    "winrt.microsoft.ui.xaml.controls",
    "winrt.microsoft.ui.xaml.media",
    "winrt.microsoft.ui.dispatching",
)

EXCLUDE_MODULES = (
    "tests",
    "scratch",
    "pip_check",
    "tkinter",
    "unittest",
    "pydoc",
)

HIDDEN_IMPORTS = (
    "pythoncom",
    "pywintypes",
    "win32com",
    "win32com.client",
    "wmi",
)


def app_version() -> str:
    """Lee la versión desde buslens.presentation.winrt.app_config."""
    try:
        from BusLens.presentation.winrt.app_config import APP_VERSION

        return APP_VERSION
    except Exception:
        return "1.0.0"


def ensure_icon() -> Path:
    """Genera el icono si no existe y lo devuelve."""
    if not ICON_PATH.exists():
        import subprocess

        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make_icon.py")],
            check=True,
            cwd=str(ROOT),
        )
    return ICON_PATH


def write_version_info(version: str, dest: Path) -> Path:
    """Escribe el archivo de versión que PyInstaller incorpora al exe."""
    parts = (version.split(".") + ["0", "0", "0", "0"])[:4]
    filevers = ", ".join(parts)
    dest.write_text(
        VERSION_INFO_TEMPLATE.format(filevers=filevers, version=version),
        encoding="ascii",
    )
    return dest


def write_inno_version(version: str) -> Path:
    """Escribe installer/version.iss con la versión para Inno Setup."""
    dest = ROOT / "installer" / "version.iss"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        f'; Generado por build_exe.py — no editar a mano.\n'
        f'#define MyAppVersion "{version}"\n',
        encoding="utf-8",
    )
    return dest


def collect_winrt_args() -> list[str]:
    """Args de PyInstaller para incluir las proyecciones winrt-* instaladas.

    Si existe el paquete base `winrt` (pip install winrt-Windows), se recolecta
    completo. En caso contrario se recorren las distribuciones winrt-* de
    importlib.metadata y se recogen sus módulos top-level.
    """
    args: list[str] = []
    if importlib.util.find_spec("winrt") is not None:
        args += ["--collect-all", "winrt"]
    else:
        try:
            from importlib.metadata import distributions

            for dist in distributions():
                name = (dist.metadata.get("Name") or "").lower()
                if not name.startswith("winrt-"):
                    continue
                top_level = dist.read_text("top_level.txt") or ""
                for module in top_level.split():
                    if module.strip():
                        args += ["--collect-all", module.strip()]
        except Exception as exc:  # pragma: no cover - entorno inusual
            print(f"[WARN] no se pudieron enumerar distribuciones winrt: {exc}")
    return args


def build(
    name: str,
    onefile: bool,
    clean: bool,
    console: bool,
    output_dir: Path,
) -> Path:
    version = app_version()
    print(f"[1/4] Versión detectada: {version}")

    icon = ensure_icon()
    print(f"[2/4] Icono: {icon}")

    with tempfile.TemporaryDirectory(prefix="buslens_version_") as tmp:
        version_file = write_version_info(version, Path(tmp) / "version_info.txt")
        write_inno_version(version)

        pyinstaller_args: list[str] = [
            "--noconfirm",
            "--noupx",
            "--clean" if clean else "--no-cache",
            "--name", name,
            "--distpath", str(output_dir),
            "--workpath", str(ROOT / "build" / "pyinstaller"),
            "--specpath", str(ROOT / "build"),
            "--icon", str(icon),
            "--manifest", str(MANIFEST_PATH),
            "--version-file", str(version_file),
        ]
        pyinstaller_args += ["--onefile"] if onefile else ["--onedir"]
        pyinstaller_args += ["--windowed"] if not console else ["--console"]

        for module in EXCLUDE_MODULES:
            pyinstaller_args += ["--exclude-module", module]
        for module in HIDDEN_IMPORTS:
            pyinstaller_args += ["--hidden-import", module]
        for module in WINRT_NAMESPACES:
            pyinstaller_args += ["--hidden-import", module]
        pyinstaller_args += collect_winrt_args()

        # Si el driver del Modo Avanzado (Beta) está compilado, se incluye
        # junto al exe para que DriverManager lo pueda instalar.
        kernel_sys = ROOT / "kernel" / "buslens_filter.sys"
        kernel_inf = ROOT / "kernel" / "buslens_filter.inf"
        for kernel_file in (kernel_sys, kernel_inf):
            if kernel_file.exists():
                pyinstaller_args += ["--add-data", f"{kernel_file};kernel"]

        pyinstaller_args.append(str(ENTRY_SCRIPT))

        print("[3/4] Compilando con PyInstaller...")
        print("      pyinstaller " + " ".join(pyinstaller_args))

        try:
            from PyInstaller.__main__ import run as pyinstaller_run
        except ImportError as exc:
            raise SystemExit(
                "PyInstaller no está instalado. Ejecute: pip install -r requirements-dev.txt"
            ) from exc

        pyinstaller_run(pyinstaller_args)

    exe = output_dir / f"{name}.exe" if onefile else output_dir / name / f"{name}.exe"
    if not exe.exists():
        raise SystemExit(f"[ERROR] no se encontró el ejecutable esperado: {exe}")
    print(f"[4/4] Ejecutable generado: {exe} ({exe.stat().st_size / 1024 / 1024:.1f} MiB)")
    return exe


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compila BusLens a ejecutable autocontenido con PyInstaller"
    )
    parser.add_argument("--name", default="BusLens", help="nombre del ejecutable (default: BusLens)")
    parser.add_argument("--onedir", action="store_true", help="modo carpeta en vez de onefile")
    parser.add_argument("--console", action="store_true", help="mantener consola (debug)")
    parser.add_argument("--clean", action="store_true", help="limpiar caché de PyInstaller")
    parser.add_argument("--output", type=Path, default=ROOT / "dist", help="directorio de salida")
    args = parser.parse_args()

    if shutil.which("pyinstaller") is None:
        try:
            import PyInstaller  # noqa: F401
        except ImportError:
            print(
                "[ERROR] PyInstaller no está instalado. Ejecute: pip install -r requirements-dev.txt"
            )
            return 1

    build(
        name=args.name,
        onefile=not args.onedir,
        clean=args.clean,
        console=args.console,
        output_dir=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
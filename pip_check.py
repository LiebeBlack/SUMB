from __future__ import annotations

import sys
from pathlib import Path
from importlib.machinery import SourceFileLoader

root = Path(".").resolve()
print("root_abs:", root)
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

print("sys.path[0]:", sys.path[0])


def probe():
    pkg_dir = root / "buslens"
    init = pkg_dir / "__init__.py"
    print("init existe:", init.exists())
    if not init.exists():
        print("no existe init")
        return False

    loader = SourceFileLoader("buslens", str(init))
    mod = loader.load_module("buslens")
    print("importado manual:", mod)
    print("__version__:", getattr(mod, "__version__", None))

    # Intenta subpaquetes
    for sub in ("application", "domain", "infrastructure", "presentation"):
        sub_pkg = f"buslens.{sub}"
        loader2 = SourceFileLoader(sub_pkg, str(pkg_dir / sub / "__init__.py"))
        m2 = loader2.load_module(sub_pkg)
        print(f"  importado {sub_pkg}:", getattr(m2, "__version__", None))
    return True


if __name__ == "__main__":
    ok = probe()
    raise SystemExit(0 if ok else 2)

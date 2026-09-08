from __future__ import annotations

import sys
from pathlib import Path
from importlib.machinery import SourceFileLoader

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

init = ROOT / "buslens" / "__init__.py"
loader = SourceFileLoader("buslens", str(init))
loader.load_module("buslens")

from BusLens.domain.models.usb_device import parse_pnp_device_id

input_id = r"USB\VID_0781&PID_5581\1234"
print("input repr:", repr(input_id))
print("split by chr(92):", input_id.split(chr(92)))
print("parsed:", parse_pnp_device_id(input_id))

print("\n--- pid logic sobre PARTÍCULAS SEPARADAS ---")
pid_candidate = None
for part in input_id.split(chr(92)):
    part = part.strip()
    upper = part.upper()
    print(f"  part={repr(part)} upper={repr(upper)}")
    if upper.startswith("PID_"):
        rest = part[4:]
        print(f"    rest after PID_={repr(rest)}")
        if "&" in rest:
            before_amp = rest.split("&", 1)[0]
            print(f"    before amp={repr(before_amp)}")
            pid_candidate = before_amp.strip().upper()
            if pid_candidate and len(pid_candidate) >= 4:
                pid_candidate = pid_candidate[:4].upper()
            print(f"    pid_candidate(final via & split)={repr(pid_candidate)}")
        else:
            pid_candidate = rest.strip().upper()
            if pid_candidate and len(pid_candidate) >= 4:
                pid_candidate = pid_candidate[:4].upper()
            print(f"    pid_candidate(nosplit &)={repr(pid_candidate)}")
    elif upper.startswith("VID_"):
        rest = part[4:]
        print(f"    VID rest={repr(rest)}")
print("\nfinal pid candidate:", pid_candidate)

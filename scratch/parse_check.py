from __future__ import annotations

import sys
from pathlib import Path
from importlib.machinery import SourceFileLoader

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

init = ROOT / "BusLens" / "__init__.py"
loader = SourceFileLoader("buslens", str(init))
loader.load_module("buslens")

from buslens.domain.models.usb_device import parse_pnp_device_id

input_id = r"USB\VID_0781&PID_5581\1234"
print("input repr:", repr(input_id))
print("split by backslash:", input_id.split("\\"))
print("parsed:", parse_pnp_device_id(input_id))

from buslens.domain.models.usb_device import UsbDevice
d = UsbDevice(pnp_device_id=r"USB\VID_0781&PID_5581\1234", name="SanDisk")
print("UsbDevice vid:", repr(d.vendor_id), "pid:", repr(d.product_id))

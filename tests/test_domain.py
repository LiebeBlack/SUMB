"""
Tests unitarios de la capa de Dominio.
Ejecutar: python -m unittest tests.test_domain -v
"""

from __future__ import annotations

import unittest

from tests.conftest import ensure_buslens_importable

ensure_buslens_importable()

from buslens.domain.models.usb_device import UsbDevice, parse_pnp_device_id
from buslens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType


class TestParsePnpDeviceId(unittest.TestCase):
    def test_Parse_Vendor_Product_Standard(self):
        vid, pid = parse_pnp_device_id(r"USB\VID_0781&PID_5581\1234")
        self.assertEqual(vid, "0781")
        self.assertEqual(pid, "5581")

    def test_Parse_WithRevisionAndInstance(self):
        vid, pid = parse_pnp_device_id(r"USB\VID_046D&PID_C52B\6&2e1a3f4&0&2")
        self.assertEqual(vid, "046D")
        self.assertEqual(pid, "C52B")

    def test_Parse_CaseInsensitive(self):
        vid, pid = parse_pnp_device_id(r"usb\vid_abcd&pid_1234\x")
        self.assertEqual(vid, "ABCD")
        self.assertEqual(pid, "1234")

    def test_Parse_CompositeParent(self):
        vid, pid = parse_pnp_device_id(r"USB\VID_045E&PID_073A\7&2347C8C2&0&0000")
        self.assertEqual(vid, "045E")
        self.assertEqual(pid, "073A")

    def test_Parse_VidOnly_ReturnsNonePid(self):
        vid, pid = parse_pnp_device_id(r"USB\VID_1234\instance")
        self.assertEqual(vid, "1234")
        self.assertIsNone(pid)

    def test_Missing_Vendor_Product(self):
        vid, pid = parse_pnp_device_id(r"SOME_NON_USB_DEVICE")
        self.assertIsNone(vid)
        self.assertIsNone(pid)

    def test_Empty_Input(self):
        self.assertEqual(parse_pnp_device_id(""), (None, None))

    def test_Regex_Primary_Strategy(self):
        # El regex es la estrategia principal y debe funcionar aunque el ID
        # contenga segmentos adicionales (REV, instancias, etc.).
        vid, pid = parse_pnp_device_id("USB" + chr(92) + "VID_0781&PID_5581" + chr(92) + "REV_0100")
        self.assertEqual(vid, "0781")
        self.assertEqual(pid, "5581")


class TestUsbDevice(unittest.TestCase):
    def test_Parsing_Vendor_Product(self):
        device = UsbDevice(
            pnp_device_id=r"USB\VID_0781&PID_5581\1234",
            name="SanDisk",
            description="Memoria USB",
            status="OK",
            manufacturer="SanDisk",
        )
        # Nota: este test expone la falla actual del parser en este entorno.
        self.assertIsNotNone(device.vendor_id)
        self.assertIsNotNone(device.product_id)

    def test_Parsing_Missing_Vendor_Product(self):
        device = UsbDevice(pnp_device_id=r"SOME_NON_USB_DEVICE", name="NoUSB")
        self.assertIsNone(device.vendor_id)
        self.assertIsNone(device.product_id)

    def test_Match_Filtering(self):
        device = UsbDevice(
            pnp_device_id=r"USB\VID_0781&PID_5581\disk",
            vendor_id="0781",
            product_id="5581",
            name="Disk",
        )
        self.assertTrue(device.match_vendor_product("0781", None))
        self.assertTrue(device.match_vendor_product(None, "5581"))
        self.assertTrue(device.match_vendor_product("0781", "5581"))
        self.assertFalse(device.match_vendor_product("0000", None))

    def test_Equality_And_Hash(self):
        a = UsbDevice(pnp_device_id="A", name="A")
        b = UsbDevice(pnp_device_id="A", name="B")
        c = UsbDevice(pnp_device_id="C", name="C")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        s = {a, b, c}
        self.assertEqual(len(s), 2)


class TestDeviceChangedEvent(unittest.TestCase):
    def test_Constructor_Default(self):
        ev = DeviceChangedEvent(change_type=DeviceChangeType.device_added)
        self.assertEqual(ev.change_type, DeviceChangeType.device_added)
        self.assertIsNone(ev.device)
        self.assertEqual(ev.message, "")

    def test_WithDevice(self):
        d = UsbDevice(pnp_device_id="X", name="X")
        ev = DeviceChangedEvent(change_type=DeviceChangeType.device_removed, device=d, message="retirado")
        self.assertEqual(ev.change_type, DeviceChangeType.device_removed)
        self.assertIsNotNone(ev.device)
        self.assertEqual(ev.message, "retirado")

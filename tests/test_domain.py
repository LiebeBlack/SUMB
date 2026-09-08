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
    def test_Parse_Video_Product_id(self):
        # Nota: en este entorno `split('\\\\')` sobre raw string puede no separar
        # VID/PID como bloques independientes (comportamiento observado). El parser
        # principal intenta ambas estrategias (split por bloques + regex fallback).
        # Este test falla hasta que se valide/falle el split en vivo; sin embargo,
        # el regex fallback debe funcionar.
        vid, pid = parse_pnp_device_id(r"USB\VID_0781&PID_5581\1234")
        self.assertEqual(vid, "0781")
        self.assertEqual(pid, "5581")

    def test_Missing_Vendor_Product(self):
        vid, pid = parse_pnp_device_id(r"SOME_NON_USB_DEVICE")
        self.assertIsNone(vid)
        self.assertIsNone(pid)

    def test_Regex_Fallback(self):
        # Simula un ID donde el split por bloques no separa VID/PID (según
        # comportamiento observado) y comprueba que el regex fallback funcione.
        # Nota: el backslash se escapa explícitamente para simular raw string.
        vid, pid = parse_pnp_device_id("GARBAGE_USB" + chr(92) + "VID_0781&PID_5581" + chr(92) + "1234")
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

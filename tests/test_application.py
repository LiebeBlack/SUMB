"""
Tests unitarios de la capa de Aplicación y servicios.
Ejecutar: python -m unittest tests.test_application -v
"""

from __future__ import annotations

import threading
import unittest
from typing import Any

# imports manuales garantizados por conftest en este entorno
from buslens.application.services.bus_service import BusService
from buslens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher
from buslens.domain.models.usb_device import UsbDevice
from buslens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType


class FakeUsbDeviceSource:
    """Fuente falsa para pruebas de capa de aplicación sin WMI.

    Implementa el contrato mínimo de IUsbDeviceSource para que el BusService
    pueda ser probado sin depender de WMI.
    """

    def __init__(self, devices=None):
        self._devices = list(devices) if devices else []
        self._listener = None

    def poll_devices(self):
        return list(self._devices)

    def start_listening(self, callback):
        self._listener = callback

    def stop_listening(self):
        self._listener = None

    def emit(self, event):
        if self._listener:
            self._listener(event)


class TestBusServiceFiltering(unittest.TestCase):
    def test_EmptyQuery_ReturnsAll(self):
        src = FakeUsbDeviceSource([
            UsbDevice(pnp_device_id="1", name="A"),
            UsbDevice(pnp_device_id="2", name="B"),
        ])
        svc = BusService(source=src)
        svc.start()
        self.assertEqual(len(svc.current_devices), 2)

    def test_Search_Filtering(self):
        src = FakeUsbDeviceSource([
            UsbDevice(pnp_device_id="1", name="USB Disk A"),
            UsbDevice(pnp_device_id="2", name="USB Disk B"),
            UsbDevice(pnp_device_id="3", name="Mouse"),
        ])
        svc = BusService(source=src)
        svc.start()
        svc.set_filter("Disk")
        self.assertEqual(len(svc.current_devices), 2)

    def test_Search_CaseInsensitive(self):
        src = FakeUsbDeviceSource([
            UsbDevice(pnp_device_id="1", name="USB DISK"),
        ])
        svc = BusService(source=src)
        svc.start()
        svc.set_filter("disk")
        self.assertEqual(len(svc.current_devices), 1)


class TestBusServiceLifecycle(unittest.TestCase):
    def test_Pause_Then_Resume_EmitsCorrectEvents(self):
        src = FakeUsbDeviceSource([UsbDevice(pnp_device_id="1", name="D1")])
        svc = BusService(source=src)
        events = []
        def record(ev):
            events.append(ev)
        svc.subscribe(record)
        svc.start()
        svc.pause()
        svc.resume()
        types = {e.change_type for e in events}
        self.assertIn(DeviceChangeType.monitoring_paused, types)
        self.assertIn(DeviceChangeType.monitoring_resumed, types)
        self.assertTrue(svc.is_monitoring)
        self.assertFalse(svc.is_paused())


class TestThreadSafeDispatcher(unittest.TestCase):
    def test_InvokeWithoutRegisteredInvoker_FallsThrough(self):
        d = ThreadSafeDispatcher()
        hit = []
        d.invoke_main(lambda: hit.append(True))
        self.assertEqual(hit, [True])

    def test_RegisterAndInvoke_MainThreadFallback(self):
        d = ThreadSafeDispatcher()
        results = []
        def invoker(cb):
            results.append("invoked")
            cb()
        d.register_main_invoker(invoker)
        d.invoke_main(lambda: results.append("callback"))
        self.assertEqual(results, ["invoked", "callback"])
        d.unregister_main_invoker(invoker)
        results2 = []
        d.invoke_main(lambda: results2.append("fallback"))
        self.assertEqual(results2, ["fallback"])


class TestGlobalDispatcherSingleton(unittest.TestCase):
    def test_SameInstance(self):
        a = get_global_dispatcher()
        b = get_global_dispatcher()
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)

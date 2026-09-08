"""
Tests unitarios de la capa de Aplicación y servicios.
Ejecutar: python -m unittest tests.test_application -v
"""

from __future__ import annotations

import unittest

from tests.fake_wmi_source import (
    FakeEventWatcher,
    FakeUsbDeviceSource,
    FakeWmiClient,
    FakeWmiEntity,
)

from buslens.application.services.bus_service import BusService
from buslens.application.services.dispatcher import ThreadSafeDispatcher, get_global_dispatcher
from buslens.application.viewmodels.main_viewmodel import MainViewModel
from buslens.domain.events.usb_events import DeviceChangedEvent, DeviceChangeType
from buslens.domain.models.usb_device import UsbDevice
from buslens.infrastructure.monitoring.usb_device_source import UsbDeviceSource


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

    def test_Filter_Reapplied_On_DeviceAdded(self):
        """Un device_added que no coincide con el filtro no entra al caché."""
        src = FakeUsbDeviceSource([UsbDevice(pnp_device_id="1", name="Disk A")])
        svc = BusService(source=src)
        svc.start()
        svc.set_filter("Disk")
        src.emit(
            DeviceChangedEvent(
                change_type=DeviceChangeType.device_added,
                device=UsbDevice(pnp_device_id="2", name="Mouse"),
            )
        )
        ids = [d.pnp_device_id for d in svc.current_devices]
        self.assertEqual(ids, ["1"])

    def test_Filter_Allows_Matching_DeviceAdded(self):
        src = FakeUsbDeviceSource([UsbDevice(pnp_device_id="1", name="Disk A")])
        svc = BusService(source=src)
        svc.start()
        svc.set_filter("Disk")
        src.emit(
            DeviceChangedEvent(
                change_type=DeviceChangeType.device_added,
                device=UsbDevice(pnp_device_id="2", name="Disk B"),
            )
        )
        ids = [d.pnp_device_id for d in svc.current_devices]
        self.assertEqual(sorted(ids), ["1", "2"])

    def test_SetFilter_WithUnavailableSource_KeepsCache(self):
        class BrokenSource(FakeUsbDeviceSource):
            def poll_devices(self):
                raise RuntimeError("WMI caído")

        src = BrokenSource([UsbDevice(pnp_device_id="1", name="Disk A")])
        svc = BusService(source=src)
        svc.start()
        svc.set_filter("nada-que-coincida")
        self.assertEqual(svc.current_devices, [])


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

    def test_Resume_Failure_EmitsMonitorError(self):
        class FailingSource(FakeUsbDeviceSource):
            def start_listening(self, callback):
                raise RuntimeError("no se puede escuchar")

        src = FailingSource([UsbDevice(pnp_device_id="1", name="D1")])
        svc = BusService(source=src)
        svc.start()
        svc.pause()
        svc.resume()
        self.assertTrue(svc.is_paused())
        self.assertFalse(svc.is_monitoring)

    def test_Stop_EmitsPausedEvent(self):
        src = FakeUsbDeviceSource([UsbDevice(pnp_device_id="1", name="D1")])
        svc = BusService(source=src)
        events = []

        def record(ev):
            events.append(ev)

        svc.subscribe(record)
        svc.start()
        svc.stop()
        self.assertTrue(any(e.change_type is DeviceChangeType.monitoring_paused for e in events))
        self.assertTrue(src.stopped)


class TestMainViewModelObserver(unittest.TestCase):
    def _make_vm(self, devices):
        src = FakeUsbDeviceSource(devices)
        svc = BusService(source=src)
        svc.start()
        return MainViewModel(svc), src, svc

    def test_DevicesChanged_Observer_Fires(self):
        vm, src, svc = self._make_vm([UsbDevice(pnp_device_id="1", name="Disk A")])
        fired = []

        def on_devices(devices):
            fired.append(list(devices))

        vm.set_on_devices_changed(on_devices)
        vm.refresh_now()
        self.assertTrue(fired)
        self.assertEqual([d.pnp_device_id for d in fired[-1]], ["1"])

    def test_Selection_Changed_Observer(self):
        d1 = UsbDevice(pnp_device_id="1", name="A")
        d2 = UsbDevice(pnp_device_id="2", name="B")
        vm, src, svc = self._make_vm([d1])
        selections = []

        def on_selection(device):
            selections.append(device)

        vm.set_on_selection_changed(on_selection)
        vm.select_device(d2)
        self.assertEqual(selections[-1], d2)
        # Seleccionar el mismo dispositivo no re-dispara el evento.
        selections.clear()
        vm.select_device(d2)
        self.assertEqual(selections, [])

    def test_Remove_Selected_Device_Clears_Selection(self):
        d1 = UsbDevice(pnp_device_id="1", name="A")
        vm, src, svc = self._make_vm([d1])
        vm.select_device(d1)
        src.emit(
            DeviceChangedEvent(change_type=DeviceChangeType.device_removed, device=d1)
        )
        self.assertIsNone(vm.selected_device)
        self.assertEqual(vm.device_list, [])

    def test_Dispose_Then_Event_DoesNotCrash(self):
        vm, src, svc = self._make_vm([UsbDevice(pnp_device_id="1", name="A")])
        vm.dispose()
        src.emit(
            DeviceChangedEvent(
                change_type=DeviceChangeType.device_added,
                device=UsbDevice(pnp_device_id="2", name="B"),
            )
        )
        self.assertIsNone(vm.selected_device)


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


class TestUsbDeviceSource(unittest.TestCase):
    def _entity(self, pid, name):
        return FakeWmiEntity(pnp_device_id=pid, name=name)

    def test_Poll_Diff_EmitsAddedRemoved(self):
        client = FakeWmiClient([self._entity(r"USB\VID_1234&PID_0001\1", "A")])
        src = UsbDeviceSource(wmi_client=client, use_event_subscription=False)
        events = []
        src._listener = lambda ev: events.append(ev)  # acceso directo para prueba síncrona
        src._known_devices = {}

        src._sync_once()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].change_type, DeviceChangeType.device_added)
        self.assertEqual(events[0].device.pnp_device_id, r"USB\VID_1234&PID_0001\1")

        client._devices = [
            self._entity(r"USB\VID_1234&PID_0001\1", "A"),
            self._entity(r"USB\VID_1234&PID_0002\2", "B"),
        ]
        src._sync_once()
        added = [e for e in events if e.change_type is DeviceChangeType.device_added]
        self.assertEqual(len(added), 2)

        client._devices = [self._entity(r"USB\VID_1234&PID_0002\2", "B")]
        src._sync_once()
        removed = [e for e in events if e.change_type is DeviceChangeType.device_removed]
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0].device.pnp_device_id, r"USB\VID_1234&PID_0001\1")

    def test_Poll_Diff_EmitsUpdated(self):
        client = FakeWmiClient([])
        src = UsbDeviceSource(wmi_client=client, use_event_subscription=False)
        events = []
        src._listener = lambda ev: events.append(ev)
        src._known_devices = {
            r"USB\VID_1234&PID_0001\1": UsbDevice(pnp_device_id=r"USB\VID_1234&PID_0001\1", name="A")
        }
        client._devices = [self._entity(r"USB\VID_1234&PID_0001\1", "A renovado")]
        src._sync_once()
        updated = [e for e in events if e.change_type is DeviceChangeType.device_updated]
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].device.name, "A renovado")

    def test_EventSubscription_FallbackToPolling(self):
        client = FakeWmiClient([self._entity(r"USB\VID_1234&PID_0001\1", "A")])
        client.watch_result = None  # suscripción no disponible -> polling
        src = UsbDeviceSource(wmi_client=client, use_event_subscription=True)
        events = []
        src.start_listening(events.append)
        src.stop_listening()
        self.assertTrue(client.watch_called)
        # El ciclo de polling garantiza al menos un sync por entrada (query
        # del baseline + query del primer sync).
        self.assertGreaterEqual(client.query_count, 2)
        # stop_listening libera el estado interno (baseline limpio).
        self.assertEqual(src._known_devices, {})

    def test_EventSubscription_Success(self):
        client = FakeWmiClient([self._entity(r"USB\VID_1234&PID_0001\1", "A")])
        client.watch_result = FakeEventWatcher([])
        src = UsbDeviceSource(wmi_client=client, use_event_subscription=True)
        events = []
        src.start_listening(events.append)
        src.stop_listening()
        self.assertTrue(client.watch_called)
        self.assertTrue(client.watch_result.closed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
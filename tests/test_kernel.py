"""
Tests del Modo Avanzado (Beta): AppConfig, protocolo IOCTL, cliente,
gestor del servicio y monitor kernel con degradación elegante.

No requieren driver real: todas las dependencias de sistema se sustituyen
por fakes (API Win32, runner de sc.exe).
"""

from __future__ import annotations

import ctypes
import subprocess
import tempfile
import unittest
from pathlib import Path

from BusLens.application.services.app_config import AppConfig
from BusLens.application.services.kernel_monitor_service import (
    KernelMonitorService,
    KernelMonitorState,
)
from BusLens.application.services.tier_manager import TIER_LABELS, Tier, TierManager
from BusLens.infrastructure.kernel import (
    DriverManager,
    DriverManagerError,
    NeedsElevationError,
    TestSigningDisabledError,
)
from BusLens.infrastructure.kernel import ioctl_codes as ic
from BusLens.infrastructure.kernel.kernel_client import KernelClient, KernelClientError


class TestIoctlCodes(unittest.TestCase):
    def test_CtlCode_MatchesCTL_MACRO(self):
        # CTL_CODE(0x22, 0x800, METHOD_BUFFERED=0, FILE_ANY_ACCESS=0)
        self.assertEqual(ic.IOCTL_QUERY_STATUS, (0x22 << 16) | 0x800)
        self.assertEqual(ic.IOCTL_START_CAPTURE, (0x22 << 16) | 0x801)
        self.assertEqual(ic.IOCTL_STOP_CAPTURE, (0x22 << 16) | 0x802)
        self.assertEqual(ic.IOCTL_QUERY_LOG, (0x22 << 16) | 0x803)
        self.assertEqual(ic.IOCTL_CLEAR_LOG, (0x22 << 16) | 0x804)
        self.assertEqual(ic.IOCTL_ATTACH, (0x22 << 16) | 0x805)
        self.assertEqual(ic.IOCTL_DETACH, (0x22 << 16) | 0x806)
        self.assertEqual(ic.IOCTL_READ_PACKETS, (0x22 << 16) | 0x807)

    def test_StructSizes_MatchHeader(self):
        # BUSLENS_LOG_ENTRY: 8 + 4*4 = 24 ; BUSLENS_DRIVER_STATUS: 20
        self.assertEqual(ic.LOG_ENTRY_SIZE, 24)
        self.assertEqual(ic.STATUS_SIZE, 20)


class FakeWin32Api:
    """Sustituye _Win32Api capturando las llamadas relevantes."""

    def __init__(self) -> None:
        self.opened_paths: list[str] = []
        self.closed = 0
        self.ioctls: list[tuple[int, bytes, int]] = []
        self._handle_counter = 0
        self.last_handle: int | None = None
        self.ioctl_error: int | None = None
        self.fail_open = False

    def CreateFileW(self, path, access, share, sec, disp, flags, tpl):
        if self.fail_open:
            return None
        self._handle_counter += 1
        self.last_handle = self._handle_counter
        self.opened_paths.append(path)
        return self.last_handle

    def DeviceIoControl(self, handle, code, in_buf, in_len, out_buf, out_len, returned, ovl):
        if self.ioctl_error is not None:
            return 0
        self.ioctls.append((code, bytes(in_buf)[:in_len] if in_buf else b"", out_len))
        if code == ic.IOCTL_QUERY_STATUS:
            payload = ic.STATUS_STRUCT.pack(ic.DRIVER_VERSION, 1, 1, 3, ic.LOG_CAPACITY, 0)
            ctypes_memmove(out_buf, payload, len(payload))
            returned.value = len(payload)
        elif code in (ic.IOCTL_QUERY_LOG, ic.IOCTL_READ_PACKETS):
            payload = ic.LOG_ENTRY_STRUCT.pack(0, 0x0008, 0x0000, 64, 27, 0) * 2
            ctypes_memmove(out_buf, payload, len(payload))
            returned.value = len(payload)
        return 1

    def CloseHandle(self, handle):
        self.closed += 1
        return 1

    @staticmethod
    def last_error() -> int:
        return ctypes.get_last_error() or 0


def ctypes_memmove(dst, src, size):
    if dst is None or not size:
        return
    import ctypes

    ctypes.memmove(dst, bytes(src), size)


class FakeDeviceIoApi(FakeWin32Api):
    """API con SetupAPI falsa: device interfaces fijas."""

    def __init__(self, interfaces: list[str]) -> None:
        super().__init__()
        self._interfaces = interfaces

    def SetupDiGetClassDevsW(self, guid, enumerator, hwnd, flags):
        return 0x1234

    def SetupDiEnumDeviceInterfaces(self, dev, data, guid, index, out):
        if index < len(self._interfaces):
            addr = ctypes.cast(out, ctypes.c_void_p).value
            (ctypes.c_ulong * 1).from_address(addr)[0] = 1  # cbSize
            return 1
        ctypes.set_last_error(259)  # ERROR_NO_MORE_ITEMS
        return 0

    def SetupDiGetDeviceInterfaceDetailW(self, dev, data, detail, size, required, devdata):
        if detail is None or size == 0:
            (ctypes.c_ulong * 1).from_address(
                ctypes.cast(required, ctypes.c_void_p).value)[0] = 256
            return 0

        path = self._interfaces[0]
        raw = path.encode("utf-16-le") + b"\x00\x00"
        offset = ctypes.sizeof(ctypes.c_ulong)
        dest = ctypes.cast(detail, ctypes.c_void_p).value
        ctypes.memmove(dest + offset, raw, len(raw))
        (ctypes.c_ulong * 1).from_address(
            ctypes.cast(required, ctypes.c_void_p).value)[0] = offset + len(raw)
        return 1

    def SetupDiDestroyDeviceInfoList(self, dev):
        return 1


class TestKernelClient(unittest.TestCase):
    def test_ReadPackets_DecodesAndSendsIOCTL(self):
        api = FakeWin32Api()
        client = KernelClient(api=api)
        client.open("\\\\.\\BusLensFilter")
        packets = client.read_packets(max_packets=2)
        self.assertEqual(len(packets), 2)
        self.assertIn(ic.IOCTL_READ_PACKETS, [c[0] for c in api.ioctls])
        self.assertEqual(packets[0]["urb_function"], 0x0008)
        self.assertEqual(packets[0]["transfer_length"], 64)

    def test_Open_QueryStatus_Close(self):
        api = FakeWin32Api()
        client = KernelClient(api=api)
        client.open("\\\\.\\BusLensFilter")
        status = client.query_status()
        self.assertTrue(status["capturing"])
        self.assertTrue(status["attached"])
        self.assertEqual(status["log_count"], 3)
        client.close()
        self.assertEqual(api.closed, 1)

    def test_Open_Failure_Raises(self):
        api = FakeWin32Api()
        api.fail_open = True
        client = KernelClient(api=api)
        with self.assertRaises(KernelClientError):
            client.open("\\\\.\\BusLensFilter")

    def test_Ioctl_WithoutOpen_Raises(self):
        client = KernelClient(api=FakeWin32Api())
        with self.assertRaises(KernelClientError):
            client.query_status()

    def test_StartStopCapture_SendsCodes(self):
        api = FakeWin32Api()
        client = KernelClient(api=api)
        client.open("\\\\.\\BusLensFilter")
        client.start_capture()
        client.stop_capture()
        codes = [c[0] for c in api.ioctls]
        self.assertIn(ic.IOCTL_START_CAPTURE, codes)
        self.assertIn(ic.IOCTL_STOP_CAPTURE, codes)

    def test_Attach_ConvertsWinPathToNt(self):
        api = FakeWin32Api()
        client = KernelClient(api=api)
        client.open("\\\\.\\BusLensFilter")
        attached = client.attach("\\\\.\\USB#VID_1234&PID_5678#abc")
        self.assertTrue(attached)
        attach_ioctl = [c for c in api.ioctls if c[0] == ic.IOCTL_ATTACH]
        self.assertEqual(len(attach_ioctl), 1)
        payload = attach_ioctl[0][1].decode("utf-16-le")
        self.assertTrue(payload.startswith("\\??\\USB#VID_1234&PID_5678#abc"))

    def test_QueryLog_DecodesEntries(self):
        api = FakeWin32Api()
        client = KernelClient(api=api)
        client.open("\\\\.\\BusLensFilter")
        entries = client.query_log()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["urb_function"], 0x0008)
        self.assertEqual(entries[0]["transfer_length"], 64)

    def test_EnumerateUsbPaths(self):
        api = FakeDeviceIoApi(["\\\\.\\USB#VID_1234&PID_5678#abc"])
        client = KernelClient(api=api)
        paths = client.usb_device_paths()
        self.assertEqual(paths, ["\\\\.\\USB#VID_1234&PID_5678#abc"])

    def test_AttachFirstUsb_BestEffort(self):
        api = FakeDeviceIoApi(["\\\\.\\USB#VID_1234&PID_5678#abc"])
        client = KernelClient(api=api)
        client.open("\\\\.\\BusLensFilter")
        path = client.attach_first_usb()
        self.assertIsNotNone(path)


class FakeRunner:
    """Sustituye a subprocess para simular sc.exe."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.installed = False
        self.running = False
        self.fail_with: int | None = None

    def __call__(self, args) -> subprocess.CompletedProcess:
        self.commands.append(list(args))
        cmd = args[1] if len(args) > 1 else ""
        if self.fail_with is not None:
            return subprocess.CompletedProcess(list(args), self.fail_with,
                                               stdout="", stderr="access denied")
        if cmd == "query":
            if not self.installed:
                return subprocess.CompletedProcess(list(args), 1060, stdout="", stderr="")
            state = "RUNNING" if self.running else "STOPPED"
            return subprocess.CompletedProcess(list(args), 0,
                                               stdout=f"SERVICE_NAME: x\nSTATE: 4 {state}\n", stderr="")
        if cmd == "create":
            self.installed = True
            return subprocess.CompletedProcess(list(args), 0, stdout="SUCCESS", stderr="")
        if cmd == "start":
            if not self.installed:
                return subprocess.CompletedProcess(list(args), 1060, stdout="", stderr="")
            self.running = True
            return subprocess.CompletedProcess(list(args), 0, stdout="SUCCESS", stderr="")
        if cmd == "stop":
            self.running = False
            return subprocess.CompletedProcess(list(args), 0, stdout="SUCCESS", stderr="")
        if cmd == "delete":
            self.installed = False
            return subprocess.CompletedProcess(list(args), 0, stdout="SUCCESS", stderr="")
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")


class TestDriverManager(unittest.TestCase):
    def test_InstallAndStart(self):
        runner = FakeRunner()
        manager = DriverManager(runner=runner)
        with tempfile.TemporaryDirectory() as tmp:
            sys_file = Path(tmp) / "buslens_filter.sys"
            sys_file.write_bytes(b"MZ")
            manager.sys_source_candidates = lambda: [sys_file]
            manager.ensure_installed_and_started()
        self.assertTrue(runner.installed)
        self.assertTrue(runner.running)
        self.assertTrue(manager.is_running())

    def test_AccessDenied_MapsToNeedsElevation(self):
        runner = FakeRunner()
        runner.fail_with = 5
        manager = DriverManager(runner=runner)
        with tempfile.TemporaryDirectory() as tmp:
            sys_file = Path(tmp) / "buslens_filter.sys"
            sys_file.write_bytes(b"MZ")
            manager.sys_source_candidates = lambda: [sys_file]
            with self.assertRaises(NeedsElevationError):
                manager._install_and_start_now()

    def test_InvalidImageHash_MapsToTestSigning(self):
        runner = FakeRunner()
        manager = DriverManager(runner=runner)
        runner.installed = True
        runner.fail_with = 577
        with self.assertRaises(TestSigningDisabledError):
            manager._install_and_start_now()

    def test_StopAndRemove(self):
        runner = FakeRunner()
        runner.installed = True
        runner.running = True
        manager = DriverManager(runner=runner)
        manager._stop_now()
        manager._delete_now()
        self.assertFalse(runner.installed)
        self.assertFalse(manager.is_installed())

    def test_MissingSys_RaisesDriverError(self):
        manager = DriverManager(runner=FakeRunner())
        manager.sys_source_candidates = lambda: []
        with self.assertRaises(DriverManagerError):
            manager._install_and_start_now()


class FakeKernelClient:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.capture_started = False
        self.capture_stopped = False
        self.entries: list[dict] = []

    def open(self, path=None):
        self.opened = True

    def close(self):
        self.closed = True

    def query_status(self):
        return {"version": ic.DRIVER_VERSION, "capturing": False, "attached": False,
                "log_count": 0, "log_capacity": ic.LOG_CAPACITY, "last_error": 0}

    def clear_log(self):
        pass

    def start_capture(self):
        self.capture_started = True

    def stop_capture(self):
        self.capture_stopped = True

    def attach_first_usb(self):
        return "\\??\\USB#VID_1234&PID_5678#abc"

    def query_log(self, max_entries=64):
        return list(self.entries)


class FakeBridge:
    """Sustituye a DynamicKernelBridge en los tests de cascada."""

    def __init__(self, fail_mount=None) -> None:
        self.fail_mount = fail_mount  # excepción a lanzar en mount()
        self.mounted = False
        self.unmount_calls: list[bool] = []

    def mount(self):
        if self.fail_mount is not None:
            raise self.fail_mount
        self.mounted = True
        return True

    def unmount(self, remove_service=True):
        self.unmount_calls.append(remove_service)
        self.mounted = False


class FakeWinUsb:
    """Sustituye a WinUsbClient en los tests de cascada."""

    def __init__(self, available=False) -> None:
        self.available = available
        self.opened = False
        self.reading = False
        self.closed = False
        self.notifier = None

    def set_packet_notifier(self, notifier):
        self.notifier = notifier

    def open_first_available(self):
        self.opened = self.available
        return self.available

    def start_reading(self):
        self.reading = True

    def close(self):
        self.closed = True
        self.reading = False


class FakeTierManager:
    """TierManager falso para probar el servicio sin cascada real."""

    def __init__(self, tier=Tier.tier1_kernel, attached=None, last_error=None) -> None:
        self.tier = tier
        self.attached = attached
        self.last_error = last_error
        self.activate_calls = 0
        self.deactivate_calls = 0
        self.client = None
        self._packet_notifier = None

    @property
    def active_tier(self):
        return self.tier

    @property
    def tier_label(self):
        return TIER_LABELS.get(self.tier, "?")

    @property
    def attached_path(self):
        return self.attached

    def set_packet_notifier(self, notifier):
        self._packet_notifier = notifier

    def activate(self):
        self.activate_calls += 1
        return self.tier

    def deactivate(self):
        self.deactivate_calls += 1
        self.tier = Tier.tier3_wmi


class TestKernelMonitorService(unittest.TestCase):
    def _make(self, tier=Tier.tier1_kernel, attached="\\??\\USB#VID_1234&PID_5678#abc",
              last_error=None):
        tmp = Path(tempfile.mkdtemp()) / "config.json"
        config = AppConfig(path=tmp).load()
        notifications: list[tuple[KernelMonitorState, str]] = []
        tiers = FakeTierManager(tier=tier, attached=attached, last_error=last_error)

        def notifier(state, message):
            notifications.append((state, message))

        service = KernelMonitorService(
            config=config,
            tier_manager=tiers,
            notifier=notifier,
        )
        return service, config, notifications, tiers

    def test_Enable_Tier1_ReachesEnabledState(self):
        service, config, notifications, tiers = self._make()
        service.set_enabled(True)
        self.assertEqual(service.state, KernelMonitorState.enabled)
        self.assertTrue(config.kernel_inspector_enabled)
        self.assertTrue(service.is_enabled)
        self.assertEqual(service.active_tier, Tier.tier1_kernel)
        self.assertEqual(tiers.activate_calls, 1)
        states = [s for s, _ in notifications]
        self.assertIn(KernelMonitorState.enabling, states)
        self.assertIn(KernelMonitorState.enabled, states)

    def test_Enable_Tier2_ReachesEnabledState(self):
        service, config, notifications, tiers = self._make(tier=Tier.tier2_winusb)
        service.set_enabled(True)
        self.assertEqual(service.state, KernelMonitorState.enabled)
        self.assertTrue(config.kernel_inspector_enabled)
        self.assertIn("Tier 2", next(m for s, m in notifications if s is KernelMonitorState.enabled))

    def test_Enable_Tier3_FallsBackToStandard(self):
        service, config, notifications, tiers = self._make(
            tier=Tier.tier3_wmi, attached=None, last_error="sin driver")
        service.set_enabled(True)
        self.assertEqual(service.state, KernelMonitorState.error_fallback)
        self.assertFalse(config.kernel_inspector_enabled)
        self.assertIn("Tier 3", next(m for s, m in notifications if s is KernelMonitorState.error_fallback))

    def test_Disable_DeactivatesTiers(self):
        service, config, notifications, tiers = self._make()
        service.set_enabled(True)
        service.set_enabled(False)
        self.assertEqual(service.state, KernelMonitorState.disabled)
        self.assertFalse(config.kernel_inspector_enabled)
        self.assertEqual(tiers.deactivate_calls, 1)

    def test_Dispose_DeactivatesTiers(self):
        service, config, notifications, tiers = self._make()
        service.set_enabled(True)
        service.dispose()
        self.assertEqual(tiers.deactivate_calls, 1)
        self.assertEqual(service.state, KernelMonitorState.disabled)

    def test_LogPoller_CountsUrbs(self):
        service, config, notifications, tiers = self._make()
        client = FakeKernelClient()
        service._client = client
        client.entries = [{"urb_function": 8, "transfer_length": 64}] * 3
        service._urb_count = 0
        service._poll_once()
        self.assertEqual(service._urb_count, 3)

    def test_WinusbPacket_CountsAndForwards(self):
        service, config, notifications, tiers = self._make(tier=Tier.tier2_winusb)
        received: list[list[dict]] = []
        service.set_log_notifier(received.append)
        service._urb_count = 0
        tiers._packet_notifier(0x81, b"\x00\x01")
        self.assertEqual(service._urb_count, 1)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0]["pipe_id"], 0x81)
        self.assertEqual(received[0][0]["size"], 2)


class TestTierManager(unittest.TestCase):
    def _make(self, bridge_fail=None, winusb_available=False):
        bridges: list[FakeBridge] = []
        clients: list[FakeKernelClient] = []
        winusbs: list[FakeWinUsb] = []
        elevator_calls: list[str] = []

        def bridge_factory():
            bridge = FakeBridge(fail_mount=bridge_fail)
            bridges.append(bridge)
            return bridge

        def client_factory():
            client = FakeKernelClient()
            clients.append(client)
            return client

        def winusb_factory():
            winusb = FakeWinUsb(available=winusb_available)
            winusbs.append(winusb)
            return winusb

        def elevator(operation):
            elevator_calls.append(operation)
            return True

        manager = TierManager(
            bridge_factory=bridge_factory,
            client_factory=client_factory,
            winusb_factory=winusb_factory,
            elevator=elevator,
        )
        return manager, bridges, clients, winusbs, elevator_calls

    def test_Tier1_Success_MountsAndCaptures(self):
        manager, bridges, clients, _, _ = self._make()
        tier = manager.activate()
        self.assertEqual(tier, Tier.tier1_kernel)
        self.assertTrue(bridges[0].mounted)
        self.assertTrue(clients[0].opened)
        self.assertTrue(clients[0].capture_started)
        self.assertEqual(manager.attached_path, "\\??\\USB#VID_1234&PID_5678#abc")

    def test_Tier1_Fails_Tier2_Success(self):
        manager, bridges, clients, winusbs, _ = self._make(
            bridge_fail=RuntimeError("sin .sys"), winusb_available=True)
        tier = manager.activate()
        self.assertEqual(tier, Tier.tier2_winusb)
        self.assertTrue(winusbs[0].opened)
        self.assertTrue(winusbs[0].reading)
        self.assertIsNone(manager.client)

    def test_AllTiersFail_Tier3(self):
        manager, bridges, clients, winusbs, _ = self._make(
            bridge_fail=RuntimeError("sin .sys"), winusb_available=False)
        tier = manager.activate()
        self.assertEqual(tier, Tier.tier3_wmi)
        self.assertEqual(manager.tier_label, TIER_LABELS[Tier.tier3_wmi])
        self.assertIsNotNone(manager.last_error)

    def test_Tier1_NeedsElevation_ElevatesAndRetries(self):
        from BusLens.infrastructure.kernel.dynamic_kernel_bridge import (
            BridgeNeedsElevationError,
        )

        manager, bridges, clients, _, elevator_calls = self._make(
            bridge_fail=BridgeNeedsElevationError("permisos", 5))
        tier = manager.activate()
        self.assertEqual(tier, Tier.tier1_kernel)
        self.assertEqual(elevator_calls, ["install-start"])
        self.assertTrue(clients[0].opened)
        self.assertTrue(clients[0].capture_started)

    def test_Deactivate_PurgesBridgeAndClosesClient(self):
        manager, bridges, clients, _, _ = self._make()
        manager.activate()
        manager.deactivate()
        self.assertEqual(bridges[0].unmount_calls, [True])
        self.assertTrue(clients[0].capture_stopped)
        self.assertTrue(clients[0].closed)
        self.assertEqual(manager.active_tier, Tier.tier3_wmi)

    def test_Deactivate_ClosesWinUsb(self):
        manager, _, _, winusbs, _ = self._make(winusb_available=True)
        manager.activate_tier2()
        manager.deactivate()
        self.assertTrue(winusbs[0].closed)

    def test_Deactivate_ElevatedService_RequestsCleanup(self):
        from BusLens.infrastructure.kernel.dynamic_kernel_bridge import (
            BridgeNeedsElevationError,
        )

        manager, bridges, clients, _, elevator_calls = self._make(
            bridge_fail=BridgeNeedsElevationError("permisos", 5))
        manager.activate()
        manager.deactivate()
        self.assertIn("stop-delete", elevator_calls)


class TestAppConfig(unittest.TestCase):
    def test_Defaults_WhenMissing(self):
        config = AppConfig(path=Path(tempfile.mkdtemp()) / "nope.json")
        config.load()
        self.assertFalse(config.kernel_inspector_enabled)
        self.assertEqual(config.kernel_last_message, "")

    def test_RoundTrip(self):
        path = Path(tempfile.mkdtemp()) / "config.json"
        config = AppConfig(path=path).load()
        config.kernel_inspector_enabled = True
        config.kernel_last_message = "activo"
        config2 = AppConfig(path=path).load()
        self.assertTrue(config2.kernel_inspector_enabled)
        self.assertEqual(config2.kernel_last_message, "activo")

    def test_CorruptFile_UsesDefaults(self):
        path = Path(tempfile.mkdtemp()) / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{esto no es json", encoding="utf-8")
        config = AppConfig(path=path).load()
        self.assertFalse(config.kernel_inspector_enabled)
        self.assertTrue(path.exists() or path.with_suffix(".json.corrupt").exists())

    def test_SetGet(self):
        config = AppConfig(path=Path(tempfile.mkdtemp()) / "c.json").load()
        config.set("kernel_inspector_enabled", True)
        self.assertTrue(config.get("kernel_inspector_enabled"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
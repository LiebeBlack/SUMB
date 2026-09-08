from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from buslens.application.services.bus_service import BusService
from buslens.application.viewmodels.main_viewmodel import MainViewModel
from buslens.domain.models.usb_device import UsbDevice
from buslens.presentation.winrt.winrt_types import WinUiTypes
from buslens.presentation.winrt.winrt_helpers import (
    safe_invoke_ui,
    setup_thread_safe_dispatcher_for_winui,
    shutdown_thread_safe_dispatcher_for_winui,
)

logging.basicConfig(level=logging.INFO, format="%(name)s [%(threadName)s] %(message)s")
logger = logging.getLogger("buslens.presentation")

TYPES = WinUiTypes()


class BusLensApp:
    """Aplicación WinUI 3 compatible con Windows 10/11.

    Responsabilidad única de esta capa: construir la ventana, conectar el ViewModel y
    despachar actualizaciones a la UI de forma thread-safe.
    """

    def __init__(self) -> None:
        self._viewmodel: Optional[MainViewModel] = None
        self._service: Optional[BusService] = None
        self._window = None
        self._main_grid = None
        self._device_list_box = None
        self._detail_panel = None
        self._status_text = None
        self._search_box = None
        self._dispatcher_callback: Optional[Callable[[Callable], None]] = None

    def run(self) -> None:
        T = TYPES
        T.ensure()

        self._service = BusService()
        self._viewmodel = MainViewModel(self._service)
        self._bind_viewmodel_callbacks()
        self._ensure_dispatcher()

        # El monitoreo arranca antes de construir la UI para no retrasar la ventana,
        # pero la UI no recibe eventos hasta que la ventana está lista.
        try:
            self._service.start()
        except Exception as exc:
            logger.warning("start service antes de UI falló: %s", exc)

        self._build_ui()
        self._show_window()
        logger.info("BusLensApp.run() completado, ventana visible")

    def _bind_viewmodel_callbacks(self) -> None:
        if self._viewmodel is None:
            return
        self._viewmodel.set_on_selection_changed(self._on_selection_changed)
        self._viewmodel.set_on_status_message(self._on_status_message)
        logger.info("Viewmodel callbacks vinculados")

    def _ensure_dispatcher(self) -> None:
        if self._dispatcher_callback is not None:
            return
        T = TYPES

        def invoke_main(cb: Callable) -> None:
            try:
                window = T["Window"].GetForCurrentThread()
                core = getattr(window, "CoreWindow", None)
                dispatcher = getattr(core, "Dispatcher", None) if core is not None else None
                if dispatcher is not None:
                    from winrt.windows.ui.core import CoreDispatcherPriority
                    dispatcher.ProcessEvents(CoreDispatcherPriority.normal)
                    dispatcher.RunAsync(CoreDispatcherPriority.normal, lambda: cb())
                    return
            except Exception:
                pass
            cb()

        self._dispatcher_callback = invoke_main
        setup_thread_safe_dispatcher_for_winui(invoke_main)

    def _build_ui(self) -> None:
        T = TYPES
        self._window = T["Window"]()
        self._window.Title = "BusLens — Monitor USB"
        self._window.Width = 1100
        self._window.Height = 720
        self._window.MinWidth = 800
        self._window.MinHeight = 520
        logger.info("Ventana creada: %dx%d", self._window.Width, self._window.Height)

        self._apply_backdrop_gracefully(T)

        root = T["Grid"]()
        root.RowDefinitions.Add(self._row_definition(T, Height="Auto"))
        root.RowDefinitions.Add(self._row_definition(T, Height="*"))
        self._main_grid = root
        logger.debug("Root Grid creado")

        try:
            self._window.Content = root
        except Exception as exc:
            logger.warning("Fallo al asignar Content a la ventana: %s", exc)

        self._build_command_bar(T, root)
        self._build_body(T, root)

    def _apply_backdrop_gracefully(self, T) -> None:
        """Aplica el backdrop nativo disponible o registra que no está presente."""
        if self._window is None:
            return

        try:
            if T.has_mica:
                backdrop = T["MicaBackdrop"]()
                T["Window"].SetBackdrop(self._window, backdrop)
                logger.info("Backdrop: MicaBackdrop")
                return
        except Exception as exc:
            logger.warning("Fallo al aplicar MicaBackdrop: %s", exc)

        try:
            if T.has_acrylic:
                backdrop = T["AcrylicBackdrop"]()
                T["Window"].SetBackdrop(self._window, backdrop)
                logger.info("Backdrop: AcrylicBackdrop")
                return
        except Exception as exc:
            logger.warning("Fallo al aplicar AcrylicBackdrop: %s", exc)

        logger.info("Sin backdrop nativo (Win10 o entorno sin Dwarka)")

    def _build_command_bar(self, T, root) -> None:
        try:
            toolbar = T["CommandBar"]()
            toolbar.IsOpen = False

            pause_btn = T["AppBarButton"]()
            pause_btn.Label = "Pausar"
            pause_btn.Icon = self._make_symbol_icon(T, "Pause")
            pause_btn.Click = lambda sender, e: self._on_pause_clicked()

            refresh_btn = T["AppBarButton"]()
            refresh_btn.Label = "Actualizar"
            refresh_btn.Icon = self._make_symbol_icon(T, "Refresh")
            refresh_btn.Click = lambda sender, e: self._on_refresh_clicked()

            toolbar.PrimaryCommands.Add(refresh_btn)
            toolbar.PrimaryCommands.Add(pause_btn)
            root.Children.Add(toolbar)
            Grid.SetRow(toolbar, 0)
        except Exception as exc:
            logger.warning("Fallo al construir CommandBar: %s", exc)

    def _make_symbol_icon(self, T, name: str):
        try:
            Symbol = T["Symbol"]
            sym = getattr(Symbol, name, None)
            if sym is None:
                sym = getattr(Symbol, "Refresh", Symbol.Refresh)
            return T["SymbolIcon"](sym)
        except Exception as exc:
            logger.warning("Fallo al crear icono de símbolo %s: %s", name, exc)
            return None

    def _build_body(self, T, root) -> None:
        try:
            body = T["Grid"]()
            body.ColumnDefinitions.Add(self._column_definition(T, Width="260"))
            body.ColumnDefinitions.Add(self._column_definition(T, Width="*"))
            Grid.SetRow(body, 1)
            self._main_grid.Children.Add(body)

            self._build_left_panel(T, body)
            self._build_right_panel(T, body)

            sep = T["Grid"]["Rectangle"]()
            sep.Fill = T["SolidColorBrush"](T["Colors"]["LightGray"])
            sep.Width = 1
            Grid.SetColumn(sep, 1)
            body.Children.Add(sep)
        except Exception as exc:
            logger.warning("Fallo al construir el body: %s", exc)

    def _build_left_panel(self, T, body) -> None:
        try:
            left = T["Grid"]()
            left.RowDefinitions.Add(self._row_definition(T, Height="Auto"))
            left.RowDefinitions.Add(self._row_definition(T, Height="*"))
            body.Children.Add(left)
            Grid.SetColumn(left, 0)

            search_row = T["StackPanel"]()
            search_row.Orientation = T["StackPanel"]["Orientation"]["Vertical"]
            search_row.Margin = T["Thickness"](Left=12, Top=12, Right=12, Bottom=8)

            self._search_box = T["TextBox"]()
            self._search_box.PlaceholderText = "Buscar dispositivo..."
            self._search_box.TextChanged = lambda sender, e: self._on_search_changed()
            self._search_box.Text = self._viewmodel.search_text
            search_row.Children.Add(self._search_box)

            tip = T["TextBlock"]()
            tip.Text = "Dispositivos USB detectados"
            tip.FontSize = 13
            tip.FontWeight = "SemiBold"
            tip.Margin = T["Thickness"](Left=12, Top=4, Bottom=6)
            search_row.Children.Add(tip)
            left.Children.Add(search_row)
            Grid.SetRow(search_row, 0)

            listbox = T["ListView"]()
            listbox.SelectionMode = "Single"
            listbox.IsItemClickEnabled = True
            listbox.ItemsSource = self._viewmodel.device_list
            listbox.SelectionChanged = lambda sender, e: self._on_selection_changed_from_ui()
            listbox.ItemTemplate = self._device_list_item_template(T)
            listbox.Margin = T["Thickness"](Left=8, Top=4, Right=8, Bottom=8)
            self._device_list_box = listbox
            left.Children.Add(listbox)
            Grid.SetRow(listbox, 1)
        except Exception as exc:
            logger.warning("Fallo al construir el panel izquierdo: %s", exc)

    def _build_right_panel(self, T, body) -> None:
        try:
            right = T["ScrollViewer"]()
            right.VerticalScrollBarVisibility = "Auto"
            right.HorizontalScrollBarVisibility = "Disabled"
            body.Children.Add(right)
            Grid.SetColumn(right, 1)

            stack = T["StackPanel"]()
            stack.Margin = T["Thickness"](Left=24, Top=20, Right=24, Bottom=20)
            right.Content = stack

            title = T["TextBlock"]()
            title.Text = "Inspector de Dispositivo USB"
            title.FontSize = 22
            title.FontWeight = "SemiBold"
            title.Margin = T["Thickness"](Bottom=16)
            stack.Children.Add(title)

            self._detail_panel = T["StackPanel"]()
            self._detail_panel.Margin = T["Thickness"](Bottom=20)
            stack.Children.Add(self._detail_panel)

            status_label = T["TextBlock"]()
            status_label.Text = "Estado del Monitor"
            status_label.FontSize = 13
            status_label.FontWeight = "SemiBold"
            status_label.Margin = T["Thickness"](Top=8, Bottom=4)
            stack.Children.Add(status_label)

            self._status_text = T["TextBlock"]()
            self._status_text.Text = self._viewmodel.status_message
            self._status_text.FontSize = 14
            self._status_text.FontWeight = "Normal"
            if T["Colors"].DarkGray:
                self._status_text.Foreground = T["SolidColorBrush"](T["Colors"]["DarkGray"])
            stack.Children.Add(self._status_text)
        except Exception as exc:
            logger.warning("Fallo al construir el panel derecho: %s", exc)

    def _device_list_item_template(self, T):
        try:
            stack = T["StackPanel"]()
            stack.Orientation = T["StackPanel"]["Orientation"]["Horizontal"]
            stack.VerticalAlignment = "Center"
            stack.Margin = T["Thickness"](Left=6, Right=6, Top=4, Bottom=4)

            icon = T["TextBlock"]()
            icon.Text = "\uE8D7"
            icon.FontFamily = "Segoe MDL2 Assets"
            icon.FontSize = 14
            icon.Margin = T["Thickness"](Right=8)
            stack.Children.Add(icon)

            name = T["TextBlock"]()
            name.Text = "..."
            name.FontSize = 13
            name.TextTrimming = "CharacterEllipsis"
            name.MaxWidth = 220
            stack.Children.Add(name)

            return stack
        except Exception as exc:
            logger.warning("Fallo al crear plantilla de item: %s", exc)
            return None

    def _show_window(self) -> None:
        if self._window is None:
            return

        try:
            self._window.Activate()
            logger.info("Ventana activada")
        except Exception as exc:
            logger.warning("Fallo al activar la ventana: %s", exc)

        try:
            view = TYPES["ApplicationView"].GetForCurrentView()
            view.TitleBar.ExtendViewIntoTitleBar = False
        except Exception:
            pass

    # --- handlers de UI ---

    def _on_selection_changed_from_ui(self) -> None:
        box = self._device_list_box
        if box is None:
            return
        item = box.SelectedItem
        if item is None:
            self._viewmodel.select_device(None)
            return
        if isinstance(item, UsbDevice):
            self._viewmodel.select_device(item)

    def _on_selection_changed(self, device: UsbDevice | None) -> None:
        safe_invoke_ui(lambda: self._render_device_detail(device))

    def _on_status_message(self, message: str) -> None:
        safe_invoke_ui(lambda: setattr(self._status_text, "Text", message) if self._status_text else None)

    def _on_search_changed(self) -> None:
        box = self._search_box
        if box is None:
            return
        self._viewmodel.set_search(box.Text)

    def _on_pause_clicked(self) -> None:
        self._viewmodel.toggle_monitoring()

    def _on_refresh_clicked(self) -> None:
        self._viewmodel.refresh_now()

    def _render_device_detail(self, device: UsbDevice | None) -> None:
        if self._detail_panel is None:
            return
        panel = self._detail_panel
        panel.Children.Clear()

        if device is None:
            blank = TYPES["TextBlock"]()
            blank.Text = "Selecciona un dispositivo USB para inspeccionar."
            blank.FontSize = 14
            blank.Foreground = TYPES["SolidColorBrush"](TYPES["Colors"]["DarkGray"])
            panel.Children.Add(blank)
            return

        fields = [
            ("Nombre", device.name),
            ("Descripción", device.description),
            ("ID de dispositivo (PNP)", device.pnp_device_id),
            ("ID de Proveedor (Vendor ID)", device.vendor_id or "\u2014"),
            ("ID de Producto (Product ID)", device.product_id or "\u2014"),
            ("Fabricante", device.manufacturer or "\u2014"),
            ("Clase GUID", device.class_guid or "\u2014"),
            ("Estado", device.status or "\u2014"),
            ("Activo", "Sí" if device.is_active else "No"),
        ]

        for label, value in fields:
            row = self._detail_field_row(TYPES, label, value)
            panel.Children.Add(row)

    def _detail_field_row(self, T, label: str, value: str):
        try:
            row = T["StackPanel"]()
            row.Orientation = T["StackPanel"]["Orientation"]["Horizontal"]
            row.Margin = T["Thickness"](Bottom=6)

            lbl = T["TextBlock"]()
            lbl.Text = label
            lbl.FontSize = 13
            lbl.FontWeight = "SemiBold"
            lbl.Width = 200
            lbl.TextTrimming = "CharacterEllipsis"
            row.Children.Add(lbl)

            val = T["TextBlock"]()
            val.Text = value
            val.FontSize = 13
            val.FontWeight = "Normal"
            val.TextTrimming = "CharacterEllipsis"
            val.Foreground = T["SolidColorBrush"](T["Colors"]["Black"])
            row.Children.Add(val)

            return row
        except Exception as exc:
            logger.warning("Fallo al construir campo de detalle: %s", exc)
            return None

    def shutdown(self) -> None:
        if self._dispatcher_callback is not None:
            try:
                shutdown_thread_safe_dispatcher_for_winui(self._dispatcher_callback)
            except Exception:
                pass
            self._dispatcher_callback = None

        if self._viewmodel is not None:
            try:
                self._viewmodel.dispose()
            except Exception:
                pass
            self._viewmodel = None

        if self._service is not None:
            try:
                self._service.stop()
            except Exception:
                pass
            self._service = None

        self._window = None
        self._main_grid = None


def main() -> None:
    app = BusLensApp()
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("BusLens interrumpido por el usuario")
    except Exception as exc:
        logger.exception("BusLens terminó con error: %s", exc)
        raise
    finally:
        app.shutdown()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("BusLens interrumpido por el usuario")
    except Exception as exc:
        logger.exception("BusLens terminó con error: %s", exc)
        raise

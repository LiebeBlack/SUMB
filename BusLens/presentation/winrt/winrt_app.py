from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from buslens.application.services.app_config import AppConfig
from buslens.application.services.bus_service import BusService
from buslens.application.services.kernel_monitor_service import (
    KernelMonitorService,
    KernelMonitorState,
)
from buslens.application.viewmodels.kernel_settings_viewmodel import KernelSettingsViewModel
from buslens.application.viewmodels.main_viewmodel import MainViewModel
from buslens.domain.models.usb_device import UsbDevice
from buslens.infrastructure.monitoring.usb_device_source import UsbDeviceSource
from buslens.presentation.winrt.app_config import (
    APP_NAME,
    APP_VERSION,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    WINDOW_TITLE,
)
from buslens.presentation.winrt.winrt_helpers import (
    add_event,
    add_to_collection,
    call_method,
    create_winui_invoker,
    get_prop,
    make_brush,
    make_items_source,
    make_thickness,
    refresh_items_source,
    resolve_enum_value,
    resolve_font_weight,
    safe_invoke_ui,
    set_attached,
    set_prop,
    setup_thread_safe_dispatcher_for_winui,
    shutdown_thread_safe_dispatcher_for_winui,
)
from buslens.presentation.winrt.winrt_types import WinRtUnavailableError, WinUiTypes

logging.basicConfig(level=logging.INFO, format="%(name)s [%(threadName)s] %(message)s")
logger = logging.getLogger("buslens.presentation")

TYPES = WinUiTypes()


class BusLensApp:
    """Aplicación WinUI 3 compatible con Windows 10/11.

    Responsabilidad única de esta capa: construir la ventana (composition
    root), conectar el ViewModel y despachar actualizaciones a la UI de forma
    thread-safe. No contiene lógica WMI ni parsing: solo orquesta capas.
    """

    def __init__(self) -> None:
        self._viewmodel: Optional[MainViewModel] = None
        self._service: Optional[BusService] = None
        self._window: Any = None
        self._main_grid: Any = None
        self._device_list_box: Any = None
        self._device_items: Any = None
        self._detail_panel: Any = None
        self._status_text: Any = None
        self._search_box: Any = None
        self._invoker: Optional[Callable[[Callable], None]] = None
        self._kernel_vm: Optional[KernelSettingsViewModel] = None
        self._settings_panel: Any = None
        self._kernel_switch: Any = None
        self._kernel_state_text: Any = None
        self._kernel_message_text: Any = None
        self._kernel_urb_text: Any = None
        self._settings_visible = False
        self._suppress_kernel_toggle = False

    # --- ciclo de vida ---

    def run(self) -> None:
        TYPES.ensure()

        # Composition root: la presentación ensambla infraestructura + aplicación.
        self._service = BusService(source=UsbDeviceSource())
        self._viewmodel = MainViewModel(self._service)
        self._bind_viewmodel_callbacks()
        self._ensure_dispatcher()

        try:
            self._service.start()
        except Exception as exc:
            logger.warning("start service antes de UI falló: %s", exc)

        self._build_ui()
        self._show_window()

        # Sincroniza la caché inicial con la lista (el constructor corrió antes del start).
        self._viewmodel.refresh_now()
        self._init_kernel_settings()
        logger.info("%s v%s: ventana visible", APP_NAME, APP_VERSION)

    def shutdown(self) -> None:
        if self._kernel_vm is not None:
            try:
                # Cierre seguro: detiene captura y cierra el cliente IOCTL.
                self._kernel_vm.dispose()
            except Exception:
                pass
            self._kernel_vm = None

        if self._invoker is not None:
            try:
                shutdown_thread_safe_dispatcher_for_winui(self._invoker)
            except Exception:
                pass
            self._invoker = None

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
        self._device_list_box = None
        self._device_items = None

    # --- enlaces ViewModel -> UI (patrón Observer) ---

    def _bind_viewmodel_callbacks(self) -> None:
        if self._viewmodel is None:
            return
        self._viewmodel.set_on_selection_changed(self._on_selection_changed)
        self._viewmodel.set_on_status_message(self._on_status_message)
        self._viewmodel.set_on_devices_changed(self._on_devices_changed)
        logger.info("Viewmodel callbacks vinculados")

    def _ensure_dispatcher(self) -> None:
        if self._invoker is not None:
            return
        self._invoker = create_winui_invoker()
        setup_thread_safe_dispatcher_for_winui(self._invoker)

    # --- construcción de la UI (degradación elegante por sección) ---

    def _build_ui(self) -> None:
        T = TYPES
        self._window = T["Window"]()
        set_prop(self._window, WINDOW_TITLE, "Title", "title")
        set_prop(self._window, DEFAULT_WINDOW_WIDTH, "Width", "width")
        set_prop(self._window, DEFAULT_WINDOW_HEIGHT, "Height", "height")
        set_prop(self._window, MIN_WINDOW_WIDTH, "MinWidth", "min_width")
        set_prop(self._window, MIN_WINDOW_HEIGHT, "MinHeight", "min_height")
        logger.info("Ventana creada: %s", WINDOW_TITLE)

        self._apply_backdrop_gracefully(T)

        root = T["Grid"]()
        row_auto = T["RowDefinition"]()
        set_prop(row_auto, "Auto", "Height", "height")
        row_star = T["RowDefinition"]()
        set_prop(row_star, "*", "Height", "height")
        add_to_collection(root.RowDefinitions, row_auto)
        add_to_collection(root.RowDefinitions, row_star)
        self._main_grid = root

        if not set_prop(self._window, root, "Content", "content"):
            logger.warning("No se pudo asignar el contenido raíz a la ventana")

        self._build_command_bar(T, root)
        self._build_body(T, root)
        self._build_settings_panel(T, root)

    def _apply_backdrop_gracefully(self, T) -> None:
        """Aplica el backdrop nativo disponible o registra que no está presente."""
        if self._window is None:
            return
        if T.has_mica:
            try:
                backdrop = T["MicaBackdrop"]()
                call_method(T["Window"], "SetBackdrop", self._window, backdrop)
                logger.info("Backdrop: MicaBackdrop")
                return
            except Exception as exc:
                logger.warning("Fallo al aplicar MicaBackdrop: %s", exc)
        if T.has_acrylic:
            try:
                backdrop = T["AcrylicBackdrop"]()
                call_method(T["Window"], "SetBackdrop", self._window, backdrop)
                logger.info("Backdrop: AcrylicBackdrop")
                return
            except Exception as exc:
                logger.warning("Fallo al aplicar AcrylicBackdrop: %s", exc)
        logger.info("Sin backdrop nativo (Win10 o entorno sin Dwarka)")

    def _build_command_bar(self, T, root) -> None:
        try:
            toolbar = T["CommandBar"]()
            set_prop(toolbar, False, "IsOpen", "is_open")

            pause_btn = T["AppBarButton"]()
            set_prop(pause_btn, "Pausar", "Label", "label")
            set_prop(pause_btn, self._make_symbol_icon(T, "Pause"), "Icon", "icon")
            add_event(pause_btn, "Click", lambda sender, e: self._on_pause_clicked())

            refresh_btn = T["AppBarButton"]()
            set_prop(refresh_btn, "Actualizar", "Label", "label")
            set_prop(refresh_btn, self._make_symbol_icon(T, "Refresh"), "Icon", "icon")
            add_event(refresh_btn, "Click", lambda sender, e: self._on_refresh_clicked())

            settings_btn = T["AppBarButton"]()
            set_prop(settings_btn, "Ajustes", "Label", "label")
            set_prop(settings_btn, self._make_symbol_icon(T, "Setting"), "Icon", "icon")
            add_event(settings_btn, "Click", lambda sender, e: self._on_settings_clicked())

            add_to_collection(toolbar.PrimaryCommands, refresh_btn)
            add_to_collection(toolbar.PrimaryCommands, pause_btn)
            add_to_collection(toolbar.PrimaryCommands, settings_btn)
            add_to_collection(root.Children, toolbar)
            set_attached(T, "Grid", toolbar, "Row", 0)
        except Exception as exc:
            logger.warning("Fallo al construir CommandBar: %s", exc)

    def _make_symbol_icon(self, T, name: str) -> Any:
        try:
            symbol = resolve_enum_value(T["Symbol"], name)
            if symbol is None:
                symbol = resolve_enum_value(T["Symbol"], "Refresh")
            if symbol is None:
                return None
            return T["SymbolIcon"](symbol)
        except Exception as exc:
            logger.warning("Fallo al crear icono de símbolo %s: %s", name, exc)
            return None

    def _build_body(self, T, root) -> None:
        try:
            body = T["Grid"]()
            col_left = T["ColumnDefinition"]()
            set_prop(col_left, 260, "Width", "width")
            col_right = T["ColumnDefinition"]()
            set_prop(col_right, "*", "Width", "width")
            add_to_collection(body.ColumnDefinitions, col_left)
            add_to_collection(body.ColumnDefinitions, col_right)
            add_to_collection(root.Children, body)
            set_attached(T, "Grid", body, "Row", 1)

            self._build_left_panel(T, body)
            self._build_right_panel(T, body)

            if T.get("Rectangle") is not None:
                sep = T["Rectangle"]()
                brush = make_brush(T, "LightGray")
                if brush is not None:
                    set_prop(sep, brush, "Fill", "fill")
                set_prop(sep, 1, "Width", "width")
                add_to_collection(body.Children, sep)
                set_attached(T, "Grid", sep, "Column", 1)
        except Exception as exc:
            logger.warning("Fallo al construir el body: %s", exc)

    def _build_left_panel(self, T, body) -> None:
        try:
            left = T["Grid"]()
            row_auto = T["RowDefinition"]()
            set_prop(row_auto, "Auto", "Height", "height")
            row_star = T["RowDefinition"]()
            set_prop(row_star, "*", "Height", "height")
            add_to_collection(left.RowDefinitions, row_auto)
            add_to_collection(left.RowDefinitions, row_star)
            add_to_collection(body.Children, left)
            set_attached(T, "Grid", left, "Column", 0)

            search_row = T["StackPanel"]()
            orientation = resolve_enum_value(T["Orientation"], "Vertical")
            if orientation is not None:
                set_prop(search_row, orientation, "Orientation", "orientation")
            set_prop(search_row, make_thickness(T, 12, 12, 12, 8), "Margin", "margin")

            self._search_box = T["TextBox"]()
            set_prop(self._search_box, "Buscar dispositivo...", "PlaceholderText", "placeholder_text")
            add_event(self._search_box, "TextChanged", lambda sender, e: self._on_search_changed())
            set_prop(self._search_box, self._viewmodel.search_text, "Text", "text")
            add_to_collection(search_row.Children, self._search_box)

            tip = T["TextBlock"]()
            set_prop(tip, "Dispositivos USB detectados", "Text", "text")
            set_prop(tip, 13, "FontSize", "font_size")
            weight = resolve_font_weight(T, "SemiBold")
            if weight is not None:
                set_prop(tip, weight, "FontWeight", "font_weight")
            set_prop(tip, make_thickness(T, 12, 4, 0, 6), "Margin", "margin")
            add_to_collection(search_row.Children, tip)
            add_to_collection(left.Children, search_row)
            set_attached(T, "Grid", search_row, "Row", 0)

            listbox = T["ListView"]()
            selection_mode = resolve_enum_value(T["SelectionMode"], "Single")
            if selection_mode is not None:
                set_prop(listbox, selection_mode, "SelectionMode", "selection_mode")
            set_prop(listbox, True, "IsItemClickEnabled", "is_item_click_enabled")
            self._device_items = make_items_source(T, self._viewmodel.device_list)
            set_prop(listbox, self._device_items, "ItemsSource", "items_source")
            add_event(listbox, "SelectionChanged", lambda sender, e: self._on_selection_changed_from_ui())
            add_event(listbox, "ContainerContentChanging", self._on_container_content_changing)
            set_prop(listbox, self._device_list_item_template(T), "ItemTemplate", "item_template")
            set_prop(listbox, make_thickness(T, 8, 4, 8, 8), "Margin", "margin")
            self._device_list_box = listbox
            add_to_collection(left.Children, listbox)
            set_attached(T, "Grid", listbox, "Row", 1)
        except Exception as exc:
            logger.warning("Fallo al construir el panel izquierdo: %s", exc)

    def _build_right_panel(self, T, body) -> None:
        try:
            right = T["ScrollViewer"]()
            v_visibility = resolve_enum_value(T["ScrollBarVisibility"], "Auto")
            h_visibility = resolve_enum_value(T["ScrollBarVisibility"], "Disabled")
            if v_visibility is not None:
                set_prop(right, v_visibility, "VerticalScrollBarVisibility", "vertical_scroll_bar_visibility")
            if h_visibility is not None:
                set_prop(right, h_visibility, "HorizontalScrollBarVisibility", "horizontal_scroll_bar_visibility")
            add_to_collection(body.Children, right)
            set_attached(T, "Grid", right, "Column", 1)

            stack = T["StackPanel"]()
            set_prop(stack, make_thickness(T, 24, 20, 24, 20), "Margin", "margin")
            set_prop(right, stack, "Content", "content")

            title = T["TextBlock"]()
            set_prop(title, "Inspector de Dispositivo USB", "Text", "text")
            set_prop(title, 22, "FontSize", "font_size")
            weight = resolve_font_weight(T, "SemiBold")
            if weight is not None:
                set_prop(title, weight, "FontWeight", "font_weight")
            set_prop(title, make_thickness(T, 0, 0, 0, 16), "Margin", "margin")
            add_to_collection(stack.Children, title)

            self._detail_panel = T["StackPanel"]()
            set_prop(self._detail_panel, make_thickness(T, 0, 0, 0, 20), "Margin", "margin")
            add_to_collection(stack.Children, self._detail_panel)

            status_label = T["TextBlock"]()
            set_prop(status_label, "Estado del Monitor", "Text", "text")
            set_prop(status_label, 13, "FontSize", "font_size")
            if weight is not None:
                set_prop(status_label, weight, "FontWeight", "font_weight")
            set_prop(status_label, make_thickness(T, 0, 8, 0, 4), "Margin", "margin")
            add_to_collection(stack.Children, status_label)

            self._status_text = T["TextBlock"]()
            set_prop(self._status_text, self._viewmodel.status_message, "Text", "text")
            set_prop(self._status_text, 14, "FontSize", "font_size")
            brush = make_brush(T, "DarkGray")
            if brush is not None:
                set_prop(self._status_text, brush, "Foreground", "foreground")
            add_to_collection(stack.Children, self._status_text)
        except Exception as exc:
            logger.warning("Fallo al construir el panel derecho: %s", exc)

    def _build_settings_panel(self, T, root) -> None:
        """Panel de Ajustes: ToggleSwitch del Modo Avanzado (Beta) + estado."""
        try:
            panel = T["Grid"]()
            add_to_collection(root.Children, panel)
            set_attached(T, "Grid", panel, "Row", 1)
            set_prop(panel, "Collapsed", "Visibility", "visibility")
            self._settings_panel = panel

            stack = T["StackPanel"]()
            set_prop(stack, make_thickness(T, 28, 24, 28, 24), "Margin", "margin")
            add_to_collection(panel.Children, stack)

            title = T["TextBlock"]()
            set_prop(title, "Ajustes", "Text", "text")
            set_prop(title, 22, "FontSize", "font_size")
            weight = resolve_font_weight(T, "SemiBold")
            if weight is not None:
                set_prop(title, weight, "FontWeight", "font_weight")
            set_prop(title, make_thickness(T, 0, 0, 0, 16), "Margin", "margin")
            add_to_collection(stack.Children, title)

            section = T["TextBlock"]()
            set_prop(section, "Modo Avanzado / Inspector de Kernel (Beta)", "Text", "text")
            set_prop(section, 15, "FontSize", "font_size")
            if weight is not None:
                set_prop(section, weight, "FontWeight", "font_weight")
            set_prop(section, make_thickness(T, 0, 8, 0, 4), "Margin", "margin")
            add_to_collection(stack.Children, section)

            description = T["TextBlock"]()
            set_prop(description,
                     "Captura URBs e IRPs USB en tiempo real mediante el driver "
                     "buslens_filter.sys. Al activarlo se solicita elevación UAC "
                     "una sola vez; si el driver no puede cargarse, la app vuelve "
                     "automáticamente al Modo Estándar (WMI).",
                     "Text", "text")
            set_prop(description, 13, "FontSize", "font_size")
            set_prop(description, make_thickness(T, 0, 0, 0, 12), "Margin", "margin")
            add_to_collection(stack.Children, description)

            self._kernel_switch = T["ToggleSwitch"]()
            set_prop(self._kernel_switch, "Habilitar Inspector Kernel (Beta)", "Header", "header")
            set_prop(self._kernel_switch, False, "IsOn", "is_on")
            add_event(self._kernel_switch, "Toggled", self._on_kernel_toggled)
            add_to_collection(stack.Children, self._kernel_switch)

            self._kernel_state_text = T["TextBlock"]()
            set_prop(self._kernel_state_text, "", "Text", "text")
            set_prop(self._kernel_state_text, 13, "FontSize", "font_size")
            set_prop(self._kernel_state_text, make_thickness(T, 0, 8, 0, 0), "Margin", "margin")
            add_to_collection(stack.Children, self._kernel_state_text)

            self._kernel_message_text = T["TextBlock"]()
            set_prop(self._kernel_message_text, "", "Text", "text")
            set_prop(self._kernel_message_text, 13, "FontSize", "font_size")
            set_prop(self._kernel_message_text, make_thickness(T, 0, 4, 0, 0), "Margin", "margin")
            add_to_collection(stack.Children, self._kernel_message_text)

            self._kernel_urb_text = T["TextBlock"]()
            set_prop(self._kernel_urb_text, "URBs capturados: 0", "Text", "text")
            set_prop(self._kernel_urb_text, 13, "FontSize", "font_size")
            set_prop(self._kernel_urb_text, make_thickness(T, 0, 4, 0, 0), "Margin", "margin")
            add_to_collection(stack.Children, self._kernel_urb_text)
        except Exception as exc:
            logger.warning("Fallo al construir el panel de ajustes: %s", exc)

    def _init_kernel_settings(self) -> None:
        """Composition root del Modo Avanzado: servicio + ViewModel + estado inicial."""
        try:
            config = AppConfig().load()
            service = KernelMonitorService(config=config)
            self._kernel_vm = KernelSettingsViewModel(service, config)
            self._kernel_vm.set_on_state_changed(self._on_kernel_state_changed)
            self._kernel_vm.set_on_urb_count_changed(self._on_kernel_urb_count_changed)
            if self._kernel_switch is not None:
                # Restaura la posición visual guardada sin activar el driver.
                self._suppress_kernel_toggle = True
                set_prop(self._kernel_switch, self._kernel_vm.last_saved_enabled,
                         "IsOn", "is_on")
                self._suppress_kernel_toggle = False
            self._render_kernel_state(self._kernel_vm.state, self._kernel_vm.last_message)
            logger.info("Modo Avanzado (Beta) inicializado")
        except Exception as exc:
            logger.warning("No se pudo inicializar el Modo Avanzado: %s", exc)

    def _on_settings_clicked(self) -> None:
        self._settings_visible = not self._settings_visible
        if self._settings_panel is not None and self._main_grid is not None:
            visibility = resolve_enum_value(TYPES["Visibility"],
                                            "Visible" if self._settings_visible else "Collapsed")
            if visibility is not None:
                set_prop(self._settings_panel, visibility, "Visibility", "visibility")
        if self._kernel_vm is not None and self._settings_visible:
            self._kernel_vm.refresh_status()

    def _on_kernel_toggled(self, sender: Any, e: Any) -> None:
        if self._suppress_kernel_toggle or self._kernel_vm is None:
            return
        is_on = bool(get_prop(sender, "IsOn", False))
        self._kernel_vm.set_kernel_enabled(is_on)

    def _on_kernel_state_changed(self, state: KernelMonitorState, message: str) -> None:
        safe_invoke_ui(lambda: self._render_kernel_state(state, message))

    def _on_kernel_urb_count_changed(self, count: int) -> None:
        safe_invoke_ui(lambda: self._render_kernel_urb_count(count))

    def _render_kernel_state(self, state: KernelMonitorState, message: str) -> None:
        if self._kernel_vm is not None:
            self._suppress_kernel_toggle = True
            set_prop(self._kernel_switch, self._kernel_vm.kernel_enabled, "IsOn", "is_on")
            self._suppress_kernel_toggle = False
        if self._kernel_state_text is not None:
            set_prop(self._kernel_state_text, self._kernel_vm.status_label if self._kernel_vm else "",
                     "Text", "text")
        if self._kernel_message_text is not None:
            set_prop(self._kernel_message_text, message, "Text", "text")
            color_name = "OrangeRed" if state is KernelMonitorState.error_fallback else "DarkGray"
            brush = make_brush(TYPES, color_name)
            if brush is not None:
                set_prop(self._kernel_message_text, brush, "Foreground", "foreground")

    def _render_kernel_urb_count(self, count: int) -> None:
        if self._kernel_urb_text is not None:
            set_prop(self._kernel_urb_text, f"URBs capturados: {count}", "Text", "text")

    def _device_list_item_template(self, T) -> Any:
        try:
            stack = T["StackPanel"]()
            orientation = resolve_enum_value(T["Orientation"], "Horizontal")
            if orientation is not None:
                set_prop(stack, orientation, "Orientation", "orientation")
            v_alignment = resolve_enum_value(T["VerticalAlignment"], "Center")
            if v_alignment is not None:
                set_prop(stack, v_alignment, "VerticalAlignment", "vertical_alignment")
            set_prop(stack, make_thickness(T, 6, 4, 6, 4), "Margin", "margin")

            icon = T["TextBlock"]()
            set_prop(icon, "\uE8D7", "Text", "text")
            family = T.get("FontFamily")
            if family is not None:
                try:
                    set_prop(icon, family("Segoe MDL2 Assets"), "FontFamily", "font_family")
                except Exception:
                    pass
            set_prop(icon, 14, "FontSize", "font_size")
            set_prop(icon, make_thickness(T, 0, 0, 8, 0), "Margin", "margin")
            add_to_collection(stack.Children, icon)

            name = T["TextBlock"]()
            set_prop(name, "", "Text", "text")
            set_prop(name, 13, "FontSize", "font_size")
            trimming = resolve_enum_value(T["TextTrimming"], "CharacterEllipsis")
            if trimming is not None:
                set_prop(name, trimming, "TextTrimming", "text_trimming")
            set_prop(name, 220, "MaxWidth", "max_width")
            add_to_collection(stack.Children, name)
            return stack
        except Exception as exc:
            logger.warning("Fallo al crear plantilla de item: %s", exc)
            return None

    def _show_window(self) -> None:
        if self._window is None:
            return
        call_method(self._window, "Activate")
        logger.info("Ventana activada")
        try:
            view = call_method(TYPES["ApplicationView"], "GetForCurrentView")
            title_bar = get_prop(view, "TitleBar")
            if title_bar is not None:
                set_prop(title_bar, False, "ExtendViewIntoTitleBar", "extend_view_into_title_bar")
        except Exception:
            pass

    # --- handlers de UI ---

    def _on_container_content_changing(self, sender: Any, e: Any) -> None:
        """Rellena el TextBlock del nombre por cada item materializado por la ListView."""
        try:
            if get_prop(e, "InRecycleQueue", False):
                return
            container = get_prop(e, "ItemContainer")
            root = get_prop(container, "ContentTemplateRoot") if container is not None else None
            item = get_prop(e, "Item")
            if root is None:
                return
            name = item.name if isinstance(item, UsbDevice) else (str(item) if item is not None else "")
            children = get_prop(root, "Children")
            if children is not None and get_prop(children, "Size", 0) > 1:
                name_block = call_method(children, "GetAt", 1)
                if name_block is not None:
                    set_prop(name_block, name, "Text", "text")
        except Exception as exc:
            logger.debug("ContainerContentChanging falló: %s", exc)

    def _on_selection_changed_from_ui(self) -> None:
        box = self._device_list_box
        if box is None or self._viewmodel is None:
            return
        item = get_prop(box, "SelectedItem")
        if isinstance(item, UsbDevice):
            self._viewmodel.select_device(item)
        else:
            self._viewmodel.select_device(None)

    def _on_selection_changed(self, device: UsbDevice | None) -> None:
        safe_invoke_ui(lambda: self._render_device_detail(device))

    def _on_status_message(self, message: str) -> None:
        safe_invoke_ui(lambda: self._render_status(message))

    def _on_devices_changed(self, devices: list[UsbDevice]) -> None:
        safe_invoke_ui(lambda: self._refresh_device_items(devices))

    def _on_search_changed(self) -> None:
        box = self._search_box
        if box is None or self._viewmodel is None:
            return
        self._viewmodel.set_search(get_prop(box, "Text", ""))

    def _on_pause_clicked(self) -> None:
        if self._viewmodel is not None:
            self._viewmodel.toggle_monitoring()

    def _on_refresh_clicked(self) -> None:
        if self._viewmodel is not None:
            self._viewmodel.refresh_now()

    # --- render ---

    def _refresh_device_items(self, devices: list[UsbDevice]) -> None:
        box = self._device_list_box
        if box is None:
            return
        self._device_items = refresh_items_source(TYPES, self._device_items, devices)
        set_prop(box, self._device_items, "ItemsSource", "items_source")

    def _render_status(self, message: str) -> None:
        if self._status_text is not None:
            set_prop(self._status_text, message, "Text", "text")

    def _render_device_detail(self, device: UsbDevice | None) -> None:
        if self._detail_panel is None:
            return
        panel = self._detail_panel
        call_method(panel.Children, "Clear")

        if device is None:
            blank = TYPES["TextBlock"]()
            set_prop(blank, "Selecciona un dispositivo USB para inspeccionar.", "Text", "text")
            set_prop(blank, 14, "FontSize", "font_size")
            brush = make_brush(TYPES, "DarkGray")
            if brush is not None:
                set_prop(blank, brush, "Foreground", "foreground")
            add_to_collection(panel.Children, blank)
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
            if row is not None:
                add_to_collection(panel.Children, row)

    def _detail_field_row(self, T, label: str, value: str) -> Any:
        try:
            row = T["StackPanel"]()
            orientation = resolve_enum_value(T["Orientation"], "Horizontal")
            if orientation is not None:
                set_prop(row, orientation, "Orientation", "orientation")
            set_prop(row, make_thickness(T, 0, 0, 0, 6), "Margin", "margin")

            lbl = T["TextBlock"]()
            set_prop(lbl, label, "Text", "text")
            set_prop(lbl, 13, "FontSize", "font_size")
            weight = resolve_font_weight(T, "SemiBold")
            if weight is not None:
                set_prop(lbl, weight, "FontWeight", "font_weight")
            set_prop(lbl, 200, "Width", "width")
            trimming = resolve_enum_value(T["TextTrimming"], "CharacterEllipsis")
            if trimming is not None:
                set_prop(lbl, trimming, "TextTrimming", "text_trimming")
            add_to_collection(row.Children, lbl)

            val = T["TextBlock"]()
            set_prop(val, value, "Text", "text")
            set_prop(val, 13, "FontSize", "font_size")
            if trimming is not None:
                set_prop(val, trimming, "TextTrimming", "text_trimming")
            brush = make_brush(T, "Black")
            if brush is not None:
                set_prop(val, brush, "Foreground", "foreground")
            add_to_collection(row.Children, val)
            return row
        except Exception as exc:
            logger.warning("Fallo al construir campo de detalle: %s", exc)
            return None


def main() -> None:
    app = BusLensApp()
    try:
        app.run()
    except WinRtUnavailableError as exc:
        logger.info("%s — esta aplicación requiere Windows 10/11 con PyWinRT (winrt-Windows).", exc)
        raise SystemExit(1)
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
# BusLens — Monitor e Inspector de Protocolos de Hardware / USB

BusLens es una aplicación Windows 10/11 escrita en Python que monitorea dispositivos USB mediante WMI y presenta una interfaz nativa con WinUI 3 (PyWinRT).

## Arquitectura (Clean Architecture)

```
.
├── requirements.txt
├── run.py                     # entry point: python run.py
├── setup.py                   # empaquetado básico (pip install . / pip install -e .)
├── README.md
├── scripts/
│   └── check_syntax.py        # verificación de sintaxis + importabilidad
└── tests/
    ├── conftest.py
    ├── fake_wmi_source.py     # fakes reutilizables (sin WMI real)
    ├── test_application.py
    └── test_domain.py

buslens/                      # capas estrictamente separadas
├── __main__.py               # entry point: python -m buslens
├── domain/                   # modelos + contratos (sin WMI/WinRT)
│   ├── __init__.py
│   ├── events/usb_events.py
│   ├── interfaces/__init__.py
│   └── models/usb_device.py  # parser VID/PID con regex + fallback
├── application/              # ViewModel + orchestrator + dispatcher thread-safe
│   ├── __init__.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── bus_service.py
│   │   └── dispatcher.py
│   └── viewmodels/
│       ├── __init__.py
│       └── main_viewmodel.py
├── infrastructure/           # WMI (WQL) + listener en hilo + watcher de eventos
│   ├── __init__.py
│   ├── monitoring/
│   │   ├── __init__.py
│   │   └── usb_device_source.py
│   └── wmi/
│       ├── __init__.py
│       └── wmi_client.py     # scope COM por hilo + watcher __InstanceOperationEvent
└── presentation/             # WinUI 3 via PyWinRT (MicaBackdrop, Grid, ListView, CommandBar)
    ├── __init__.py
    └── winrt/
        ├── __init__.py
        ├── app_config.py
        ├── window_helpers.py
        ├── winrt_app.py
        ├── winrt_helpers.py
        └── winrt_types.py
```

## Requisitos

- Windows 10 o Windows 11
- Python 3.9 o superior
- Permisos suficientes para consultar WMI (normalmente el usuario actual tiene acceso a `Win32_PnPEntity`)

Instalar dependencias:

```bash
pip install -r requirements.txt
```

## Cómo ejecutar

### Desde la raíz del proyecto (desarrollo)

```bash
python run.py
# o bien
python -m buslens
```

### Como paquete instalado

```bash
pip install .
# o en modo editable:
pip install -e .

# luego, desde cualquier directorio:
buslens
```

## Compatibilidad y degradación elegante

- **WinUI 3 / PyWinRT**: se intenta primero el namespace `winrt.windows.ui.xaml`
  (Windows SDK) y, si no existe, `winrt.microsoft.ui.xaml` (WinUI 3 nativo).
- **Backdrops**: `MicaBackdrop` y `AcrylicBackdrop` se detectan en tiempo de
  ejecución. Si no están disponibles (Windows 10 o entorno sin Dwarka), la app
  abre sin backdrop y sin excepción.
- **Sin WinRT en el entorno**: la aplicación solo hace logging y sale con
  código 1, sin excepción fatal.
- **WMI caído o sin permisos**: el monitoreo continúa en modo degradado
  (errores reportados como eventos `monitor_error`, la UI sigue operativa).

## Hotplug: eventos WMI con fallback a polling

1. Al arrancar la escucha, se intenta suscribir a
   `__InstanceOperationEvent` sobre `Win32_PnPEntity` (notificaciones nativas
   de inserción/extracción).
2. Si la suscripción falla (WMI no disponible, permisos insuficientes, formato
   no soportado) **o** el watcher se rompe en caliente, se cambia
   automáticamente a un ciclo de polling (1.5 s) que compara el snapshot
   anterior y difunde `device_added` / `device_removed` / `device_updated`.
3. Incluso con eventos activos, un poll de respaldo cada 5 s mantiene el
   estado sincronizado si algún evento se pierde.

## Concurrencia y thread-safety

- Todas las llamadas WMI/COM desde hilos de trabajo se aíslan con
  `pythoncom.CoInitializeEx(COINIT_MULTITHREADED)` / `CoUninitialize`
  (ver `_com_scope` en `wmi_client.py`).
- El hilo de monitoreo (`BusLensUsbMonitor`, daemon) nunca toca la UI: los
  eventos se reenvían por `BusService` y se despachan con
  `ThreadSafeDispatcher.invoke_main`, que los ejecuta en la
  `DispatcherQueue` del hilo principal de WinUI 3.
- El caché de dispositivos de `BusService` está protegido por un `RLock`
  (lo escribe el hilo de monitoreo y lo lee el hilo de UI).

## Empaquetado: ejecutable autocontenido e instalador

### Requisitos de build

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

### 1. Ejecutable único (PyInstaller)

```bash
python scripts/make_icon.py        # genera assets/buslens.ico (sin dependencias)
python build_exe.py --clean        # produce dist/BusLens.exe (onefile)
# opciones: --onedir (carpeta), --console (debug), --name <nombre>
```

`build_exe.py` hace todo automáticamente:

- Genera el icono si falta y escribe `packaging/version_info.txt` con la
  versión de `app_config.py` (incrustada como metadatos del exe).
- Escribe `installer/version.iss` para que Inno Setup use la misma versión.
- Recoge todas las proyecciones PyWinRT instaladas (`winrt-*`: pyd/DLL
  nativos) y añade hidden-imports para los namespaces que la app importa
  dinámicamente (`winrt.windows.ui.xaml`, `winrt.microsoft.ui.xaml`, etc.).
- Incluye `pythoncom`/`pywintypes`/`win32com` (bindings COM) y `wmi`.
- Aplica `packaging/buslens.manifest`: DPI PerMonitorV2, código de página
  UTF-8, `supportedOS` de Windows 10/11 y `asInvoker`.

### 2. Instalador profesional (Inno Setup 6.3+)

```bash
ISCC.exe installer/buslens_setup.iss /DMyAppVersion=1.0.0
```

El instalador (`dist/BusLens_Setup_v1.0.0.exe`) incluye:

- Arquitectura 64-bit (`x64compatible`), `WizardStyle=modern` de Windows 10/11.
- Acceso directo en el menú Inicio y escritorio (opcional, tarea marcable).
- Registro de versión en `HKLM\Software\BusLens` y `App Paths`.
- Elevación correcta para WMI (administrador, con opción de diálogo per-user).
- Desinstalación limpia: cierra el proceso si está en ejecución, elimina
  accesos directos, claves de registro y datos de usuario (`%APPDATA%\BusLens`).

## Modo Avanzado / Inspector de Kernel (Beta)

BusLens incluye un modo experimental que captura URBs (USB Request Blocks) e
IRPs de dispositivos USB en tiempo real mediante un driver kernel KMDF.

### Arquitectura del modo

| Capa | Componente | Responsabilidad |
|------|------------|-----------------|
| Kernel | `kernel/buslens_filter.c` (KMDF) | Device interface, ring buffer, IOCTLs, adjunto no destructivo a stacks USB |
| Infraestructura | `buslens/infrastructure/kernel/` | Cliente IOCTL (ctypes + SetupAPI), gestión del servicio (sc.exe), elevación UAC |
| Aplicación | `KernelMonitorService` + `KernelSettingsViewModel` | Estados, fallback automático, observador de la UI |
| UI | Panel Ajustes → ToggleSwitch | Activación/desactivación con elevación puntual |
| Config | `AppConfig` (`%APPDATA%\BusLens\config.json`) | Persistencia del estado del toggle |

### Flujo de activación (cascada multi-tier)

Al activar el ToggleSwitch se ejecuta la estrategia *máxima capacidad primero*
con degradación transparente (`TierManager`):

1. **Tier 1 — Kernel KMDF dinámico** (`DynamicKernelBridge` + `KernelClient`):
   se copia `buslens_filter.sys` a `%TEMP%\BusLens\`, se registra un servicio
   kernel *temporal* con `CreateServiceW` (`SERVICE_DEMAND_START`), se inicia
   con `StartServiceW` y se abre `\\.\BusLensFilter`. Si el SCM rechaza la
   operación por permisos, se solicita elevación UAC **una sola vez**
   (`--kernel-admin install-start`) y se reintenta. Luego se limpia el log,
   se inicia la captura y se adjunta el filtro al primer dispositivo USB
   (best-effort, no bloqueante). Un poller consulta el ring buffer en segundo
   plano y muestra el contador de URBs.
2. **Tier 2 — WinUSB user-space** (`WinUsbClient`): si el driver no puede
   cargarse (Test-Signing deshabilitado, Secure Boot, firma o permisos), la
   captura pasa a `winusb.sys` vía SetupAPI: abre las device interfaces USB,
   enumera los pipes bulk/interrupt con `WinUsb_QueryPipe` y lee los paquetes
   con `WinUsb_ReadPipe` en un hilo daemon.
3. **Tier 3 — WMI estándar**: si tampoco hay dispositivos WinUSB, la app
   sigue funcionando con `Win32_PnPEntity` + hotplug WMI, notificando el
   motivo del fallo en el panel sin colapsar.

### Desmontaje y purge (cero persistencia)

Al desactivar el toggle o cerrar la aplicación, `TierManager.deactivate()`
cierra el lector WinUSB, detiene la captura, cierra el handle del dispositivo,
detiene el servicio (`ControlService SERVICE_CONTROL_STOP`) y lo elimina
(`DeleteService`) además de borrar el `.sys` temporal. Si el servicio lo
instaló la CLI elevada, se solicita su limpieza (`--kernel-admin stop-delete`).
No se modifican flags de boot (BCD) ni hay persistencia de arranque.

### Degradación elegante

- Si el driver no puede cargarse (p. ej. Test-Signing deshabilitado,
  `ERROR_INVALID_IMAGE_HASH`), la app degrada a WinUSB y, si hace falta, al
  Modo Estándar (WMI), mostrando la notificación en el panel sin colapsar.
- Si el usuario rechaza el UAC, la app permanece en el mejor tier posible.
- El toggle guarda su última posición en `config.json`, pero **no** se
  auto-activa al abrir la aplicación (evita elevaciones sorpresa).

### Seguridad del driver (sin BSOD por diseño)

- El filtro se adjunta solo bajo demanda del cliente (IOCTL_ATTACH) y nunca
  modifica ni completa IRPs ajenos: siempre passthrough con completion.
- La lectura de URBs está protegida con `__try/__except`; el ring buffer es
  non-paged con spinlock.
- La captura se detiene al cerrar el último handle; el adjunto se deshace en
  detach, remove-device y DriverUnload. Cero recursos colgados.
- El INF **no** toca las claves UpperFilters/LowerFilters de la clase USB.

### Compilar el driver (requiere WDK)

```bash
# En Visual Studio Developer Command Prompt con el WDK instalado:
msbuild kernel\buslens_filter.vcxproj /p:Configuration=Release /p:Platform=x64
# Resultado: kernel\x64\Release\buslens_filter.sys
```

Para cargarlo en desarrollo:

```bat
bcdedit /set testsigning on   :: requiere reinicio (Test-Signing)
```

El CI compila el driver con el WDK y, si tiene éxito, el instalador incluye
`buslens_filter.sys` en `{app}\kernel`.

## CI/CD (GitHub Actions)

`.github/workflows/build.yml` automatiza el pipeline completo:

1. **test** (push y PR, Windows y Linux): `check_syntax.py` + unittest.
2. **build** (push a `main`): PyInstaller onefile + Inno Setup, sube
   `BusLens.exe` y `BusLens_Setup_vX.Y.Z.exe` como artefactos.
3. **publish** (tag `v*`): publica ambos artefactos en GitHub Releases.

## Verificación local

```bash
# sintaxis + importabilidad de capas
python scripts/check_syntax.py

# tests (no requieren WMI real)
python -m unittest tests.test_domain -v
python -m unittest tests.test_application -v
```

## Notas técnicas

- Cero lógica de hardware en la capa de presentación: `MainViewModel` solo
  orquesta y difunde eventos (patrón Observer).
- `parse_pnp_device_id` extrae `VID_xxxx`/`PID_yyyy` por expresión regular
  sobre el `PNPDeviceID` (p. ej. `USB\VID_0781&PID_5583\...`), con fallback
  por segmentos para IDs anómalos.
- No requiere C#, C++, ni WebViews.
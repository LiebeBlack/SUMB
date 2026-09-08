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
    ├── fake_wmi_source.py
    ├── test_application.py
    └── test_domain.py

BusLens/                      # capas estrictamente separadas
├── __main__.py               # entry point: python -m buslens
├── domain/                   # modelos + contratos (sin WMI/WinRT)
│   ├── __init__.py
│   ├── events/usb_events.py
│   ├── interfaces/__init__.py
│   └── models/usb_device.py
├── application/              # ViewModel + orchestrator + dispatcher thread-safe
│   ├── __init__.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── bus_service.py
│   │   └── dispatcher.py
│   └── viewmodels/
│       ├── __init__.py
│       └── main_viewmodel.py
├── infrastructure/           # WMI (WQL) + listener en hilo + parser USB
│   ├── __init__.py
│   ├── monitoring/
│   │   ├── __init__.py
│   │   └── usb_device_source.py
│   └── wmi/
│       ├── __init__.py
│       └── wmi_client.py
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

## Compatibilidad

- Windows 10 y Windows 11.
- Detección en tiempo de ejecución de `MicaBackdrop`, `AcrylicBackdrop` y API de `Window.SetBackdrop`. Si no está disponible (Win10 o entorno sin Dwarka), la app abre sin backdrop y sin excepción.
- Si WinRT no está disponible en el entorno, la aplicación solo hace logging y sale con código 1, sin excepción fatal.

## Verificación local

```bash
# sintaxis + importabilidad
python scripts/check_syntax.py

# tests (no requieren WMI real)
python -m unittest tests.test_domain -v
python -m unittest tests.test_application -v
```

## Lifecycle del monitoreo

- El escáner USB corre en un hilo daemon (`BusLensUsbMonitor`).
- Todos los eventos de inserción/extracción/actualización se reenvían por `BusService` y despachan a la UI con `ThreadSafeDispatcher.invoke_main`.
- Al cerrar, `BusLensApp.shutdown()` detiene el servicio, despacha el ViewModel y elimina los invokers del dispatcher.

## Notas técnicas

- Cero lógica de hardware en la capa de presentación: `MainViewModel` solo orquesta.
- `WmiClient` reutiliza la conexión y hace retry; `parse_pnp_device_id` extrae VID/PID de `USB\VID_xxxx&PID_yyyy`.
- No requiere C#, C++, ni WebViews.

/*++

    buslens_filter.c — Driver KMDF de BusLens (Modo Avanzado / Tier 1).

    Estructura del driver:
      1. Control device (DriverEntry, no PnP):
         - Nombre NT        \Device\BusLensFilter
         - Symbolic link    \DosDevices\BusLensFilter  (la app abre \\.\BusLensFilter)
         - Device interface {D1F2E3A4-B5C6-4D7E-8F90-A1B2C3D4E5F6}
         - Cola secuencial de IOCTLs METHOD_BUFFERED:
           QUERY_STATUS, START/STOP_CAPTURE, QUERY_LOG, CLEAR_LOG,
           ATTACH/DETACH (adjunto dinámico no destructivo) y
           READ_PACKETS (telemetría URB estructurada).
      2. Filter FDO PnP (EvtDeviceAdd, cuando el INF lo registra como
         Upper Filter de la clase USB): WdfFdoInitSetFilter + preprocess
         de IRP_MJ_INTERNAL_DEVICE_CONTROL que observa URBs
         (IOCTL_USB_SUBMIT_URB) y los vuelca al ring buffer compartido.

    Seguridad (sin BSOD por diseño):
      - Todo IRP ajeno se reenvía sin modificar ni completar
        (WdfDeviceWdmDispatchPreprocessedIrp / passthrough clásico).
      - La lectura de URBs se protege con __try/__except.
      - El ring buffer es non-paged con spinlock.
      - EvtDriverUnload libera pool, symlink (KMDF) y detaches sin
        requerir reinicio ni tocar BCD.

--*/

/* INITGUID materializa las definiciones de GUID (DEFINE_GUID de
   buslens_filter.h). Sin el, DEFINE_GUID solo emite una declaracion extern
   y el link falla con LNK2001 (BUSLENS_DEVICE_INTERFACE_GUID sin definir).
   Mismo patron que los samples oficiales de KMDF (echo, toaster). */
#define INITGUID
#include <ntddk.h>
#include <wdf.h>
#include <usb.h>
#include <usbioctl.h>
#include "buslens_filter.h"

#define BUSLENS_POOL_TAG 'nslB'

/* ------------------------------------------------------------------ */
/* Contextos                                                          */
/* ------------------------------------------------------------------ */

/* Contexto del control device: ring buffer compartido + estado. */
typedef struct _BUSLENS_DEVICE_CONTEXT {
    WDFSPINLOCK Lock;
    BUSLENS_URB_PACKET* Entries;
    ULONG Capacity;
    ULONG WriteIndex;
    ULONG TotalWritten;
    BOOLEAN Capturing;
    BOOLEAN Attached;
    PDEVICE_OBJECT FilterDevice;
    PDEVICE_OBJECT LowerDevice;
    PFILE_OBJECT TargetFileObject;
    LONG HandleCount;
    ULONG LastError;
    ULONG DeviceIndexCounter;
} BUSLENS_DEVICE_CONTEXT, *PBUSLENS_DEVICE_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(BUSLENS_DEVICE_CONTEXT, BusLensGetDeviceContext)

/* Contexto del driver: control device + driver handle. */
typedef struct _BUSLENS_DRIVER_CONTEXT {
    WDFDRIVER Driver;
    WDFDEVICE ControlDevice;
} BUSLENS_DRIVER_CONTEXT, *PBUSLENS_DRIVER_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(BUSLENS_DRIVER_CONTEXT, BusLensGetDriverContext)

/* Contexto de cada filter FDO PnP (Upper Filter de clase USB). */
typedef struct _BUSLENS_FILTER_FDO_CONTEXT {
    PBUSLENS_DEVICE_CONTEXT Shared;  /* ring buffer + lock del control device */
    ULONG DeviceIndex;
} BUSLENS_FILTER_FDO_CONTEXT, *PBUSLENS_FILTER_FDO_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(BUSLENS_FILTER_FDO_CONTEXT, BusLensGetFilterContext)

/* Extensión del device object usado en el adjunto dinámico (IOCTL_ATTACH). */
typedef struct _BUSLENS_FILTER_EXTENSION {
    PBUSLENS_DEVICE_CONTEXT OwnerContext;
    PDEVICE_OBJECT LowerDevice;
} BUSLENS_FILTER_EXTENSION, *PBUSLENS_FILTER_EXTENSION;

/* Driver object del adjunto dinámico (IoCreateDriver), single-instance. */
static PDRIVER_OBJECT g_FilterDriverObject = NULL;

/* ------------------------------------------------------------------ */
/* Prototipos                                                         */
/* ------------------------------------------------------------------ */

DRIVER_INITIALIZE DriverEntry;
DRIVER_INITIALIZE FilterDriverInitialize;
EVT_WDF_DRIVER_DEVICE_ADD EvtDeviceAdd;
EVT_WDF_DRIVER_UNLOAD EvtDriverUnload;
EVT_WDF_IO_QUEUE_IO_DEVICE_CONTROL EvtIoDeviceControl;
EVT_WDF_FILE_CREATE EvtFileCreate;
EVT_WDF_FILE_CLOSE EvtFileClose;
WDF_WDM_IRP_PREPROCESS_CALLBACK EvtPreprocessInternalDeviceControl;

DRIVER_DISPATCH FilterPassThrough;
DRIVER_DISPATCH FilterInternalDeviceControl;
IO_COMPLETION_ROUTINE FilterPassThroughCompletion;
IO_COMPLETION_ROUTINE FilterPnpCompletion;

NTSTATUS BusLensCreateControlDevice(WDFDRIVER Driver, WDFDEVICE* Device);
VOID BusLensLogEvent(PBUSLENS_DEVICE_CONTEXT Context,
                     USHORT UrbFunction, USHORT TransferFlags,
                     ULONG TransferBufferLength, ULONG IoControlCode,
                     ULONG DeviceIndex);
VOID BusLensCopyLog(PBUSLENS_DEVICE_CONTEXT Context, PVOID Output,
                    size_t OutputLength, size_t* BytesCopied);
NTSTATUS BusLensAttach(PBUSLENS_DEVICE_CONTEXT Context, PCWSTR TargetName);
VOID BusLensDetach(PBUSLENS_DEVICE_CONTEXT Context);

/* ------------------------------------------------------------------ */
/* DriverEntry                                                        */
/* ------------------------------------------------------------------ */

NTSTATUS
DriverEntry(
    _In_ PDRIVER_OBJECT DriverObject,
    _In_ PUNICODE_STRING RegistryPath
    )
{
    WDF_DRIVER_CONFIG config;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDFDRIVER driver;
    PBUSLENS_DRIVER_CONTEXT driverContext;
    WDFDEVICE controlDevice = NULL;
    NTSTATUS status;

    /* Control driver + filter PnP: EvtDeviceAdd se invoca solo cuando el
       INF registra el driver como Upper Filter de la clase USB. */
    WDF_DRIVER_CONFIG_INIT(&config, EvtDeviceAdd);
    config.EvtDriverUnload = EvtDriverUnload;

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ContextTypeInfo = BUSLENS_DRIVER_CONTEXT_INFO;

    status = WdfDriverCreate(DriverObject,
                             RegistryPath,
                             &attributes,
                             &config,
                             &driver);
    if (!NT_SUCCESS(status)) {
        KdPrint(("BusLens: WdfDriverCreate falló 0x%08x\n", status));
        return status;
    }

    driverContext = BusLensGetDriverContext(driver);
    driverContext->Driver = driver;
    driverContext->ControlDevice = NULL;

    status = BusLensCreateControlDevice(driver, &controlDevice);
    if (!NT_SUCCESS(status)) {
        KdPrint(("BusLens: no se pudo crear el control device 0x%08x\n", status));
        return status;
    }

    KdPrint(("BusLens: driver cargado (versión 0x%08x)\n", BUSLENS_DRIVER_VERSION));
    return STATUS_SUCCESS;
}

/* ------------------------------------------------------------------ */
/* EvtDeviceAdd: filter FDO PnP (Upper Filter de la clase USB)        */
/* ------------------------------------------------------------------ */

NTSTATUS
EvtDeviceAdd(
    _In_ WDFDRIVER Driver,
    _In_ PWDFDEVICE_INIT DeviceInit
    )
{
    WDF_OBJECT_ATTRIBUTES attributes;
    WDFDEVICE device;
    PBUSLENS_DRIVER_CONTEXT driverContext;
    PBUSLENS_DEVICE_CONTEXT shared = NULL;
    PBUSLENS_FILTER_FDO_CONTEXT filterContext;
    NTSTATUS status;

    driverContext = BusLensGetDriverContext(Driver);
    if (driverContext->ControlDevice != NULL) {
        shared = BusLensGetDeviceContext(driverContext->ControlDevice);
    }
    if (shared == NULL) {
        return STATUS_DEVICE_NOT_READY;
    }

    /* Este FDO es un filter del stack del dispositivo destino. */
    WdfFdoInitSetFilter(DeviceInit, TRUE);

    /* Observación de IRPs de clase USB (URBs) antes del despacho KMDF. */
    status = WdfDeviceInitSetWdmIrpPreprocessCallback(
        DeviceInit,
        EvtPreprocessInternalDeviceControl,
        IRP_MJ_INTERNAL_DEVICE_CONTROL);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ContextTypeInfo = BUSLENS_FILTER_FDO_CONTEXT_INFO;

    status = WdfDeviceCreate(&DeviceInit, &attributes, &device);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    filterContext = BusLensGetFilterContext(device);
    filterContext->Shared = shared;
    filterContext->DeviceIndex =
        InterlockedIncrement(&shared->DeviceIndexCounter);

    KdPrint(("BusLens: filter FDO %d creado sobre el stack USB\n",
             filterContext->DeviceIndex));
    return STATUS_SUCCESS;
}

/* ------------------------------------------------------------------ */
/* EvtDriverUnload: libera todo (sin recursos colgados, sin reboot)   */
/* ------------------------------------------------------------------ */

VOID
EvtDriverUnload(
    _In_ WDFDRIVER Driver
    )
{
    PBUSLENS_DRIVER_CONTEXT driverContext = BusLensGetDriverContext(Driver);
    PBUSLENS_DEVICE_CONTEXT deviceContext = NULL;

    if (driverContext->ControlDevice != NULL) {
        deviceContext = BusLensGetDeviceContext(driverContext->ControlDevice);

        /* Detach dinámico si seguía adjunto (IOCTL_ATTACH). */
        BusLensDetach(deviceContext);

        if (deviceContext->Entries != NULL) {
            ExFreePoolWithTag(deviceContext->Entries, BUSLENS_POOL_TAG);
            deviceContext->Entries = NULL;
        }

        /* KMDF elimina el symlink \DosDevices\BusLensFilter junto con el
           device object de control. */
        WdfObjectDelete(driverContext->ControlDevice);
        driverContext->ControlDevice = NULL;
    }

    KdPrint(("BusLens: driver descargado\n"));
}

/* ------------------------------------------------------------------ */
/* Creación del control device (nombre NT + symlink + device interface)*/
/* ------------------------------------------------------------------ */

NTSTATUS
BusLensCreateControlDevice(
    _In_ WDFDRIVER Driver,
    _Out_ WDFDEVICE* Device
    )
{
    WDFDEVICE_INIT* init = NULL;
    WDF_OBJECT_ATTRIBUTES attributes;
    WDF_IO_QUEUE_CONFIG queueConfig;
    WDFQUEUE queue;
    WDF_FILEOBJECT_CONFIG fileConfig;
    PBUSLENS_DEVICE_CONTEXT deviceContext;
    UNICODE_STRING sddl;
    UNICODE_STRING ntDeviceName;
    UNICODE_STRING dosDeviceName;
    NTSTATUS status;

    *Device = NULL;

    /* SYSTEM + Administradores con acceso total; usuarios interactivos con
       lectura/escritura (consulta de estado sin elevación). */
    RtlInitUnicodeString(&sddl,
        L"D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)");
    RtlInitUnicodeString(&ntDeviceName, BUSLENS_NT_DEVICE_NAME);
    RtlInitUnicodeString(&dosDeviceName, BUSLENS_DOS_DEVICE_NAME);

    init = WdfControlDeviceInitAllocate(Driver, &sddl);
    if (init == NULL) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    /* Nombre NT del dispositivo de control. */
    status = WdfDeviceInitAssignName(init, &ntDeviceName);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    /* IOCTLs con METHOD_BUFFERED para máxima seguridad. */
    WdfDeviceInitSetIoType(init, WdfDeviceIoBuffered);
    WdfDeviceInitSetCharacteristics(init, FILE_DEVICE_UNKNOWN, FALSE);

    /* Conteo de handles: EvtFileCreate/EvtFileClose. */
    WDF_FILEOBJECT_CONFIG_INIT(&fileConfig, EvtFileCreate, EvtFileClose, NULL);
    WdfDeviceInitSetFileObjectConfig(init, &fileConfig, WDF_NO_OBJECT_ATTRIBUTES);

    WDF_OBJECT_ATTRIBUTES_INIT(&attributes);
    attributes.ContextTypeInfo = BUSLENS_DEVICE_CONTEXT_INFO;

    status = WdfDeviceCreate(&init, &attributes, Device);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    /* Symbolic link de usuario: \\.\BusLensFilter. */
    status = WdfDeviceCreateSymbolicLink(*Device, &dosDeviceName);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    /* Device interface para enumeración con SetupAPI. */
    status = WdfDeviceCreateDeviceInterface(*Device,
                                            &BUSLENS_DEVICE_INTERFACE_GUID,
                                            NULL);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    deviceContext = BusLensGetDeviceContext(*Device);
    deviceContext->Capturing = FALSE;
    deviceContext->Attached = FALSE;
    deviceContext->FilterDevice = NULL;
    deviceContext->LowerDevice = NULL;
    deviceContext->TargetFileObject = NULL;
    deviceContext->HandleCount = 0;
    deviceContext->LastError = 0;
    deviceContext->WriteIndex = 0;
    deviceContext->TotalWritten = 0;
    deviceContext->DeviceIndexCounter = 0;
    deviceContext->Capacity = BUSLENS_LOG_CAPACITY;

    deviceContext->Entries = (BUSLENS_URB_PACKET*)ExAllocatePool2(
        POOL_FLAG_NON_PAGED,
        sizeof(BUSLENS_URB_PACKET) * deviceContext->Capacity,
        BUSLENS_POOL_TAG);
    if (deviceContext->Entries == NULL) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    status = WdfSpinLockCreate(WDF_NO_OBJECT_ATTRIBUTES, &deviceContext->Lock);
    if (!NT_SUCCESS(status)) {
        ExFreePoolWithTag(deviceContext->Entries, BUSLENS_POOL_TAG);
        deviceContext->Entries = NULL;
        return status;
    }

    /* Cola de IOCTLs (secuencial: un IOCTL a la vez). */
    WDF_IO_QUEUE_CONFIG_INIT(&queueConfig, WdfIoQueueDispatchSequential);
    queueConfig.EvtIoDeviceControl = EvtIoDeviceControl;
    status = WdfIoQueueCreate(*Device, &queueConfig, WDF_NO_OBJECT_ATTRIBUTES, &queue);
    if (!NT_SUCCESS(status)) {
        return status;
    }
    status = WdfDeviceSetDefaultIoQueue(*Device, queue);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    WdfControlFinishInitializingDevice(*Device);

    KdPrint(("BusLens: control device \\Device\\BusLensFilter creado\n"));
    return STATUS_SUCCESS;
}

/* ------------------------------------------------------------------ */
/* Handles de cliente                                                 */
/* ------------------------------------------------------------------ */

VOID
EvtFileCreate(
    _In_ WDFFILEOBJECT FileObject
    )
{
    WDFDEVICE device = WdfFileObjectGetDevice(FileObject);
    PBUSLENS_DEVICE_CONTEXT context = BusLensGetDeviceContext(device);
    InterlockedIncrement(&context->HandleCount);
}

VOID
EvtFileClose(
    _In_ WDFFILEOBJECT FileObject
    )
{
    WDFDEVICE device = WdfFileObjectGetDevice(FileObject);
    PBUSLENS_DEVICE_CONTEXT context = BusLensGetDeviceContext(device);

    if (InterlockedDecrement(&context->HandleCount) <= 0) {
        /* Último cliente: detener la captura para no alimentar el ring
           buffer sin consumidores. */
        context->Capturing = FALSE;
    }
}

/* ------------------------------------------------------------------ */
/* Ring buffer (BUSLENS_URB_PACKET, non-paged, spinlock)             */
/* ------------------------------------------------------------------ */

VOID
BusLensLogEvent(
    _In_ PBUSLENS_DEVICE_CONTEXT Context,
    _In_ USHORT UrbFunction,
    _In_ USHORT TransferFlags,
    _In_ ULONG TransferBufferLength,
    _In_ ULONG IoControlCode,
    _In_ ULONG DeviceIndex
    )
{
    BUSLENS_URB_PACKET* entry;
    ULONG index;

    if (Context == NULL || Context->Entries == NULL || !Context->Capturing) {
        return;
    }

    WdfSpinLockAcquire(Context->Lock);

    if (Context->TotalWritten < Context->Capacity) {
        index = Context->TotalWritten;
    } else {
        index = Context->WriteIndex;
        Context->WriteIndex = (Context->WriteIndex + 1) % Context->Capacity;
    }

    entry = &Context->Entries[index];
    entry->Timestamp = KeQuerySystemTimePrecise();
    entry->UrbFunction = UrbFunction;
    entry->TransferFlags = TransferFlags;
    entry->TransferBufferLength = TransferBufferLength;
    entry->IoControlCode = IoControlCode;
    entry->DeviceIndex = DeviceIndex;
    Context->TotalWritten++;

    WdfSpinLockRelease(Context->Lock);
}

VOID
BusLensCopyLog(
    _In_ PBUSLENS_DEVICE_CONTEXT Context,
    _In_ PVOID Output,
    _In_ size_t OutputLength,
    _Out_ size_t* BytesCopied
    )
{
    ULONG available;
    ULONG startIndex;
    size_t bytesToCopy;

    *BytesCopied = 0;
    if (Output == NULL || OutputLength < sizeof(BUSLENS_URB_PACKET)) {
        return;
    }

    WdfSpinLockAcquire(Context->Lock);

    available = (ULONG)(Context->TotalWritten < Context->Capacity
                            ? Context->TotalWritten
                            : Context->Capacity);
    startIndex = 0;
    if (available > 0 && Context->TotalWritten >= Context->Capacity) {
        startIndex = Context->WriteIndex;
    }
    bytesToCopy = (size_t)available * sizeof(BUSLENS_URB_PACKET);
    if (bytesToCopy > OutputLength) {
        bytesToCopy = OutputLength -
                      (OutputLength % sizeof(BUSLENS_URB_PACKET));
    }
    if (bytesToCopy > 0) {
        RtlCopyMemory(Output, &Context->Entries[startIndex], bytesToCopy);
        *BytesCopied = bytesToCopy;
    }

    WdfSpinLockRelease(Context->Lock);
}

/* ------------------------------------------------------------------ */
/* IOCTLs del control device                                          */
/* ------------------------------------------------------------------ */

VOID
EvtIoDeviceControl(
    _In_ WDFQUEUE Queue,
    _In_ WDFREQUEST Request,
    _In_ size_t OutputBufferLength,
    _In_ size_t InputBufferLength,
    _In_ ULONG IoControlCode
    )
{
    WDFDEVICE device = WdfIoQueueGetDevice(Queue);
    PBUSLENS_DEVICE_CONTEXT context = BusLensGetDeviceContext(device);
    NTSTATUS status = STATUS_SUCCESS;
    PVOID input = NULL;
    PVOID output = NULL;
    size_t inputLength = 0;
    size_t outputLength = 0;
    size_t bytesCopied = 0;
    WCHAR targetName[BUSLENS_MAX_DEVICE_NAME];
    size_t nameChars = 0;

    switch (IoControlCode) {

    case BUSLENS_IOCTL_QUERY_STATUS:
    {
        BUSLENS_DRIVER_STATUS* driverStatus = NULL;
        status = WdfRequestRetrieveOutputBuffer(Request,
                                               sizeof(BUSLENS_DRIVER_STATUS),
                                               &output,
                                               &outputLength);
        if (!NT_SUCCESS(status) || outputLength < sizeof(BUSLENS_DRIVER_STATUS)) {
            status = STATUS_BUFFER_TOO_SMALL;
            break;
        }
        driverStatus = (BUSLENS_DRIVER_STATUS*)output;
        driverStatus->Version = BUSLENS_DRIVER_VERSION;
        driverStatus->Capturing = context->Capturing;
        driverStatus->Attached = context->Attached;
        driverStatus->LogCount = (ULONG)(context->TotalWritten < context->Capacity
                                             ? context->TotalWritten
                                             : context->Capacity);
        driverStatus->LogCapacity = context->Capacity;
        driverStatus->LastError = context->LastError;
        WdfRequestSetInformation(Request, sizeof(BUSLENS_DRIVER_STATUS));
        break;
    }

    case BUSLENS_IOCTL_START_CAPTURE:
        context->Capturing = TRUE;
        BusLensLogEvent(context, 0, 0, 0, IoControlCode, 0);
        break;

    case BUSLENS_IOCTL_STOP_CAPTURE:
        context->Capturing = FALSE;
        BusLensLogEvent(context, 0, 0, 0, IoControlCode, 0);
        break;

    case BUSLENS_IOCTL_CLEAR_LOG:
        WdfSpinLockAcquire(context->Lock);
        context->WriteIndex = 0;
        context->TotalWritten = 0;
        WdfSpinLockRelease(context->Lock);
        break;

    case BUSLENS_IOCTL_QUERY_LOG:
    case BUSLENS_IOCTL_READ_PACKETS:
    {
        /* Ambos devuelven telemetría URB estructurada del ring buffer;
           READ_PACKETS es el canal preferido del cliente. */
        status = WdfRequestRetrieveOutputBuffer(Request, 1, &output, &outputLength);
        if (!NT_SUCCESS(status)) {
            break;
        }
        BusLensCopyLog(context, output, outputLength, &bytesCopied);
        WdfRequestSetInformation(Request, bytesCopied);
        break;
    }

    case BUSLENS_IOCTL_ATTACH:
    {
        status = WdfRequestRetrieveInputBuffer(Request,
                                               sizeof(WCHAR),
                                               &input,
                                               &inputLength);
        if (!NT_SUCCESS(status) || inputLength < sizeof(WCHAR) * 2) {
            status = STATUS_INVALID_PARAMETER;
            break;
        }
        nameChars = inputLength / sizeof(WCHAR);
        if (nameChars > BUSLENS_MAX_DEVICE_NAME) {
            nameChars = BUSLENS_MAX_DEVICE_NAME;
        }
        RtlCopyMemory(targetName, input, nameChars * sizeof(WCHAR));
        targetName[nameChars - 1] = L'\0';
        status = BusLensAttach(context, targetName);
        if (!NT_SUCCESS(status)) {
            context->LastError = (ULONG)RtlNtStatusToDosError(status);
        } else {
            context->LastError = 0;
            BusLensLogEvent(context, 0, 0, 0, IoControlCode, 0);
        }
        break;
    }

    case BUSLENS_IOCTL_DETACH:
        BusLensDetach(context);
        context->LastError = 0;
        BusLensLogEvent(context, 0, 0, 0, IoControlCode, 0);
        break;

    default:
        status = STATUS_INVALID_DEVICE_REQUEST;
        break;
    }

    WdfRequestComplete(Request, status);
}

/* ------------------------------------------------------------------ */
/* Preprocess de IRP_MJ_INTERNAL_DEVICE_CONTROL (filter FDO PnP)     */
/* ------------------------------------------------------------------ */

NTSTATUS
EvtPreprocessInternalDeviceControl(
    _In_ WDFDEVICE Device,
    _In_ PIRP Irp
    )
{
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    PBUSLENS_FILTER_FDO_CONTEXT filterContext = BusLensGetFilterContext(Device);
    PBUSLENS_DEVICE_CONTEXT shared =
        (filterContext != NULL) ? filterContext->Shared : NULL;
    ULONG code = stack->Parameters.DeviceIoControl.IoControlCode;

    /* Observación de URBs: solo lectura del encabezado, protegida. */
    if (code == IOCTL_USB_SUBMIT_URB && shared != NULL) {
        PVOID urb = stack->Parameters.DeviceIoControl.Type3InputBuffer;
        USHORT urbFunction = 0;
        USHORT transferFlags = 0;
        ULONG transferLength = 0;
        __try {
            if (urb != NULL) {
                USB_URB_HEADER header = {0};
                RtlCopyMemory(&header,
                              urb,
                              min(sizeof(header),
                                  stack->Parameters.DeviceIoControl.InputBufferLength));
                urbFunction = header.Function;
                transferLength = header.Length;
                /* Flags de dirección: bit 7 del pipe de la transferencia. */
                if (urbFunction == URB_FUNCTION_BULK_OR_INTERRUPT_TRANSFER) {
                    USB_URB_BULK_OR_INTERRUPT_TRANSFER* bulk =
                        (USB_URB_BULK_OR_INTERRUPT_TRANSFER*)urb;
                    transferFlags = (USHORT)(bulk->Pipe.PipeFlags &
                                             USBD_PIPE_DIRECTION_IN ? 1 : 0);
                }
            }
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            urbFunction = 0;
            transferFlags = 0;
            transferLength = 0;
        }
        BusLensLogEvent(shared, urbFunction, transferFlags, transferLength,
                        code, filterContext->DeviceIndex);
    }

    /* Passthrough: KMDF reenvía el IRP al siguiente elemento del stack
       (los filters nunca completan IRPs ajenos). */
    return WdfDeviceWdmDispatchPreprocessedIrp(Device, Irp);
}

/* ------------------------------------------------------------------ */
/* Adjunto dinámico no destructivo (IOCTL_ATTACH)                     */
/* ------------------------------------------------------------------ */

NTSTATUS
FilterDriverInitialize(
    _In_ PDRIVER_OBJECT DriverObject,
    _In_ PUNICODE_STRING RegistryPath
    )
{
    ULONG i;
    UNREFERENCED_PARAMETER(RegistryPath);

    for (i = 0; i < IRP_MJ_MAXIMUM_FUNCTION; i++) {
        DriverObject->MajorFunction[i] = FilterPassThrough;
    }
    DriverObject->MajorFunction[IRP_MJ_INTERNAL_DEVICE_CONTROL] =
        FilterInternalDeviceControl;
    DriverObject->MajorFunction[IRP_MJ_PNP] = FilterPassThrough;
    DriverObject->MajorFunction[IRP_MJ_POWER] = FilterPassThrough;

    g_FilterDriverObject = DriverObject;
    return STATUS_SUCCESS;
}

NTSTATUS
BusLensAttach(
    _In_ PBUSLENS_DEVICE_CONTEXT Context,
    _In_ PCWSTR TargetName
    )
{
    UNICODE_STRING target;
    PDEVICE_OBJECT targetDevice = NULL;
    PFILE_OBJECT targetFile = NULL;
    PDEVICE_OBJECT filterDevice = NULL;
    PBUSLENS_FILTER_EXTENSION extension = NULL;
    NTSTATUS status;

    if (Context == NULL || TargetName == NULL || TargetName[0] == L'\0') {
        return STATUS_INVALID_PARAMETER;
    }

    BusLensDetach(Context);

    RtlInitUnicodeString(&target, TargetName);

    status = IoGetDeviceObjectPointer(&target,
                                      FILE_ALL_ACCESS,
                                      &targetFile,
                                      &targetDevice);
    if (!NT_SUCCESS(status)) {
        KdPrint(("BusLens: no se pudo abrir %wZ (0x%08x)\n", &target, status));
        return status;
    }

    g_FilterDriverObject = NULL;
    status = IoCreateDriver(NULL, FilterDriverInitialize);
    if (!NT_SUCCESS(status) || g_FilterDriverObject == NULL) {
        ObDereferenceObject(targetFile);
        return NT_SUCCESS(status) ? STATUS_UNSUCCESSFUL : status;
    }

    status = IoCreateDevice(g_FilterDriverObject,
                            sizeof(BUSLENS_FILTER_EXTENSION),
                            NULL,
                            FILE_DEVICE_UNKNOWN,
                            FILE_DEVICE_SECURE_OPEN,
                            FALSE,
                            &filterDevice);
    if (!NT_SUCCESS(status)) {
        IoDeleteDriver(g_FilterDriverObject);
        g_FilterDriverObject = NULL;
        ObDereferenceObject(targetFile);
        return status;
    }

    extension = (PBUSLENS_FILTER_EXTENSION)filterDevice->DeviceExtension;
    extension->OwnerContext = Context;
    extension->LowerDevice = NULL;

    status = IoAttachDevice(filterDevice, &target, &extension->LowerDevice);
    if (!NT_SUCCESS(status)) {
        IoDeleteDevice(filterDevice);
        IoDeleteDriver(g_FilterDriverObject);
        g_FilterDriverObject = NULL;
        ObDereferenceObject(targetFile);
        return status;
    }

    Context->FilterDevice = filterDevice;
    Context->LowerDevice = extension->LowerDevice;
    Context->TargetFileObject = targetFile;
    Context->Attached = TRUE;

    KdPrint(("BusLens: adjuntado a %wZ\n", &target));
    return STATUS_SUCCESS;
}

VOID
BusLensDetach(
    _In_ PBUSLENS_DEVICE_CONTEXT Context
    )
{
    if (Context == NULL || !Context->Attached) {
        return;
    }

    if (Context->LowerDevice != NULL) {
        IoDetachDevice(Context->LowerDevice);
        Context->LowerDevice = NULL;
    }
    if (Context->FilterDevice != NULL) {
        IoDeleteDevice(Context->FilterDevice);
        Context->FilterDevice = NULL;
    }
    if (Context->TargetFileObject != NULL) {
        ObDereferenceObject(Context->TargetFileObject);
        Context->TargetFileObject = NULL;
    }
    if (g_FilterDriverObject != NULL) {
        IoDeleteDriver(g_FilterDriverObject);
        g_FilterDriverObject = NULL;
    }
    Context->Attached = FALSE;
    KdPrint(("BusLens: adjunto retirado\n"));
}

/* ------------------------------------------------------------------ */
/* Dispatch del device object del adjunto dinámico                    */
/* ------------------------------------------------------------------ */

NTSTATUS
FilterInternalDeviceControl(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PIRP Irp
    )
{
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    PBUSLENS_FILTER_EXTENSION extension =
        (PBUSLENS_FILTER_EXTENSION)DeviceObject->DeviceExtension;
    ULONG code = stack->Parameters.DeviceIoControl.IoControlCode;
    PBUSLENS_DEVICE_CONTEXT context =
        (extension != NULL) ? extension->OwnerContext : NULL;

    if (code == IOCTL_USB_SUBMIT_URB) {
        PVOID urb = stack->Parameters.DeviceIoControl.Type3InputBuffer;
        USHORT urbFunction = 0;
        ULONG urbLength = 0;
        __try {
            if (urb != NULL) {
                USB_URB_HEADER header = {0};
                RtlCopyMemory(&header,
                              urb,
                              min(sizeof(header),
                                  stack->Parameters.DeviceIoControl.InputBufferLength));
                urbFunction = header.Function;
                urbLength = header.Length;
            }
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            urbFunction = 0;
            urbLength = 0;
        }
        BusLensLogEvent(context, urbFunction, 0, urbLength, code, 0);
    }

    IoCopyCurrentIrpStackLocationToNext(Irp);
    IoSetCompletionRoutine(Irp,
                           FilterPassThroughCompletion,
                           DeviceObject,
                           TRUE,
                           TRUE,
                           TRUE);
    return IoCallDriver(extension->LowerDevice, Irp);
}

NTSTATUS
FilterPassThrough(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PIRP Irp
    )
{
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    PBUSLENS_FILTER_EXTENSION extension =
        (PBUSLENS_FILTER_EXTENSION)DeviceObject->DeviceExtension;
    IO_COMPLETION_ROUTINE completion = FilterPassThroughCompletion;

    if (stack->MajorFunction == IRP_MJ_PNP) {
        completion = FilterPnpCompletion;
    }

    IoCopyCurrentIrpStackLocationToNext(Irp);
    IoSetCompletionRoutine(Irp, completion, DeviceObject, TRUE, TRUE, TRUE);
    return IoCallDriver(extension->LowerDevice, Irp);
}

NTSTATUS
FilterPassThroughCompletion(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PIRP Irp,
    _In_ PVOID Context
    )
{
    UNREFERENCED_PARAMETER(DeviceObject);
    UNREFERENCED_PARAMETER(Context);

    if (Irp->PendingReturned) {
        IoMarkIrpPending(Irp);
    }
    return STATUS_MORE_PROCESSING_REQUIRED;
}

NTSTATUS
FilterPnpCompletion(
    _In_ PDEVICE_OBJECT DeviceObject,
    _In_ PIRP Irp,
    _In_ PVOID Context
    )
{
    PIO_STACK_LOCATION stack = IoGetCurrentIrpStackLocation(Irp);
    PBUSLENS_FILTER_EXTENSION extension =
        (PBUSLENS_FILTER_EXTENSION)DeviceObject->DeviceExtension;

    UNREFERENCED_PARAMETER(Context);

    if (stack->MinorFunction == IRP_MN_REMOVE_DEVICE &&
        extension != NULL && extension->LowerDevice != NULL) {
        PBUSLENS_DEVICE_CONTEXT owner = extension->OwnerContext;
        IoDetachDevice(extension->LowerDevice);
        extension->LowerDevice = NULL;
        if (owner != NULL) {
            owner->LowerDevice = NULL;
            owner->Attached = FALSE;
            if (owner->TargetFileObject != NULL) {
                ObDereferenceObject(owner->TargetFileObject);
                owner->TargetFileObject = NULL;
            }
        }
    }

    if (Irp->PendingReturned) {
        IoMarkIrpPending(Irp);
    }
    return STATUS_MORE_PROCESSING_REQUIRED;
}
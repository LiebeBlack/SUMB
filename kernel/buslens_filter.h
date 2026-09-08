/*++

    buslens_filter.h — Definiciones compartidas del driver de BusLens.

    Este encabezado lo comparten el driver (C) y, de forma documentada,
    el cliente Python (buslens/infrastructure/kernel/ioctl_codes.py),
    que reimplementa las mismas constantes para no depender de cabeceras
    del WDK en tiempo de ejecución.

    El driver expone:
      - Device object de control:  \Device\BusLensFilter
      - Symbolic link de usuario:  \DosDevices\BusLensFilter  ->  \\.\BusLensFilter
      - Device interface (clase): {D1F2E3A4-B5C6-4D7E-8F90-A1B2C3D4E5F6}
      - IOCTLs METHOD_BUFFERED (ver BUSLENS_FUNCTION_*).

--*/

#ifndef BUSLENS_FILTER_H
#define BUSLENS_FILTER_H

#include <wdm.h>

/* Nombre NT y symbolic link del dispositivo de control. */
#define BUSLENS_NT_DEVICE_NAME  L"\\Device\\BusLensFilter"
#define BUSLENS_DOS_DEVICE_NAME L"\\DosDevices\\BusLensFilter"

/* GUID de la device interface: {D1F2E3A4-B5C6-4D7E-8F90-A1B2C3D4E5F6} */
DEFINE_GUID(BUSLENS_DEVICE_INTERFACE_GUID,
    0xD1F2E3A4, 0xB5C6, 0x4D7E, 0x8F, 0x90, 0xA1, 0xB2, 0xC3, 0xD4, 0xE5, 0xF6);

/* Versión del protocolo IOCTL (parte alta = mayor, parte baja = menor). */
#define BUSLENS_DRIVER_VERSION 0x00010000UL

/* Capacidad por defecto del ring buffer de eventos. */
#define BUSLENS_LOG_CAPACITY 4096UL

/* Longitud máxima del nombre de device object destino del adjunto. */
#define BUSLENS_MAX_DEVICE_NAME 260UL

/* Funciones de IOCTL (FILE_DEVICE_UNKNOWN = 0x22, METHOD_BUFFERED). */
#define BUSLENS_FUNCTION_QUERY_STATUS 0x800
#define BUSLENS_FUNCTION_START_CAPTURE 0x801
#define BUSLENS_FUNCTION_STOP_CAPTURE 0x802
#define BUSLENS_FUNCTION_QUERY_LOG 0x803
#define BUSLENS_FUNCTION_CLEAR_LOG 0x804
#define BUSLENS_FUNCTION_ATTACH 0x805
#define BUSLENS_FUNCTION_DETACH 0x806
#define BUSLENS_FUNCTION_READ_PACKETS 0x807

#define BUSLENS_IOCTL_QUERY_STATUS \
    CTL_CODE(FILE_DEVICE_UNKNOWN, BUSLENS_FUNCTION_QUERY_STATUS, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define BUSLENS_IOCTL_START_CAPTURE \
    CTL_CODE(FILE_DEVICE_UNKNOWN, BUSLENS_FUNCTION_START_CAPTURE, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define BUSLENS_IOCTL_STOP_CAPTURE \
    CTL_CODE(FILE_DEVICE_UNKNOWN, BUSLENS_FUNCTION_STOP_CAPTURE, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define BUSLENS_IOCTL_QUERY_LOG \
    CTL_CODE(FILE_DEVICE_UNKNOWN, BUSLENS_FUNCTION_QUERY_LOG, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define BUSLENS_IOCTL_CLEAR_LOG \
    CTL_CODE(FILE_DEVICE_UNKNOWN, BUSLENS_FUNCTION_CLEAR_LOG, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define BUSLENS_IOCTL_ATTACH \
    CTL_CODE(FILE_DEVICE_UNKNOWN, BUSLENS_FUNCTION_ATTACH, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define BUSLENS_IOCTL_DETACH \
    CTL_CODE(FILE_DEVICE_UNKNOWN, BUSLENS_FUNCTION_DETACH, METHOD_BUFFERED, FILE_ANY_ACCESS)
#define BUSLENS_IOCTL_READ_PACKETS \
    CTL_CODE(FILE_DEVICE_UNKNOWN, BUSLENS_FUNCTION_READ_PACKETS, METHOD_BUFFERED, FILE_ANY_ACCESS)

/* ------------------------------------------------------------------ */
/* Paquete de telemetría URB (canal kernel -> user, empaquetado).     */
/* ------------------------------------------------------------------ */

#pragma pack(push, 1)

typedef struct _BUSLENS_URB_PACKET {
    LARGE_INTEGER Timestamp;        /* KeQuerySystemTimePrecise (8 bytes)  */
    USHORT UrbFunction;             /* función URB (URB_FUNCTION_*)         */
    USHORT TransferFlags;           /* USBD_TRANSFER_DIRECTION_*            */
    ULONG TransferBufferLength;     /* longitud de la transferencia         */
    ULONG IoControlCode;            /* código IOCTL observado               */
    ULONG DeviceIndex;              /* índice del filter FDO (0 = control)  */
} BUSLENS_URB_PACKET;               /* 24 bytes en total (packed)           */

#pragma pack(pop)

/* ------------------------------------------------------------------ */
/* Estado consultable del driver (20 bytes, alineación natural).      */
/* ------------------------------------------------------------------ */

typedef struct _BUSLENS_DRIVER_STATUS {
    ULONG Version;      /* BUSLENS_DRIVER_VERSION */
    BOOLEAN Capturing;  /* captura activa */
    BOOLEAN Attached;   /* hay un adjunto dinámico a un stack USB */
    ULONG LogCount;     /* entradas disponibles en el ring buffer */
    ULONG LogCapacity;  /* capacidad del ring buffer */
    ULONG LastError;    /* último NTSTATUS en formato Win32, 0 si ninguno */
} BUSLENS_DRIVER_STATUS;

#endif /* BUSLENS_FILTER_H */
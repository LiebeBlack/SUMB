"""Genera assets/buslens.ico con el icono de BusLens (lente de inspección).

No requiere dependencias externas: construye un ICO válido con imágenes
BMP (DIB) sin comprimir para tamaños < 256 y PNG embebido para 256 px.

Uso:
    python scripts/make_icon.py [--output assets/buslens.ico]
"""

from __future__ import annotations

import argparse
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Paleta del icono (RGBA)
EDGE_COLOR = (59, 130, 246, 255)      # azul borde exterior
RING_COLOR = (240, 244, 250, 255)     # anillo interior claro
PUPIL_COLOR = (23, 37, 84, 255)       # pupila azul profundo
FOCUS_COLOR = (147, 197, 253, 255)    # reflejo del lente

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_pixels(size: int) -> list[tuple[int, int, int, int]]:
    """Renderiza la lente de inspección: borde, anillo, pupila y reflejo."""
    center = (size - 1) / 2.0
    outer_r = size * 0.48
    ring_r = size * 0.34
    pupil_r = size * 0.19
    focus_x = center - size * 0.13
    focus_y = center - size * 0.14
    focus_r = size * 0.09

    pixels: list[tuple[int, int, int, int]] = []
    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center
            dist = (dx * dx + dy * dy) ** 0.5

            # Borde suave (anti-aliasing) en el límite exterior.
            if dist >= outer_r:
                alpha = max(0.0, min(1.0, (outer_r - dist) * (size / 3.0)))
                if alpha <= 0.0:
                    pixels.append((0, 0, 0, 0))
                    continue
                pixels.append((*EDGE_COLOR[:3], int(alpha * 255)))
                continue
            if dist >= ring_r:
                pixels.append(EDGE_COLOR)
                continue
            if dist >= pupil_r:
                pixels.append(RING_COLOR)
                continue

            # Reflejo especular (pequeño círculo claro arriba-izquierda).
            fx = x - focus_x
            fy = y - focus_y
            if (fx * fx + fy * fy) ** 0.5 <= focus_r:
                pixels.append(FOCUS_COLOR)
            else:
                pixels.append(PUPIL_COLOR)
    return pixels


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def encode_png(size: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    """Codifica un PNG RGBA de 8 bits sin filtrar."""
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filtro None
        for x in range(size):
            raw.extend(pixels[y * size + x])
    idat = zlib.compress(bytes(raw), 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def encode_dib(size: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    """Codifica un DIB (BMP dentro de ICO) de 32bpp con filas de abajo hacia arriba."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    xor_data = bytearray()
    for y in range(size - 1, -1, -1):  # filas invertidas
        for x in range(size):
            r, g, b, a = pixels[y * size + x]
            xor_data.extend((b, g, r, a))
    # AND mask: 1 bit por píxel, filas invertidas, padded a múltiplo de 32 bits.
    row_bits = (size + 31) // 32 * 32
    and_data = bytearray()
    for y in range(size - 1, -1, -1):
        row = bytearray(row_bits // 8)
        for x in range(size):
            if pixels[y * size + x][3] == 0:
                row[x // 8] |= 0x80 >> (x % 8)
        and_data.extend(row)
    return header + bytes(xor_data) + bytes(and_data)


def build_ico(output: Path) -> None:
    images: list[tuple[int, bytes]] = []
    for size in SIZES:
        pixels = render_pixels(size)
        if size == 256:
            images.append((size, encode_png(size, pixels)))
        else:
            images.append((size, encode_dib(size, pixels)))

    header = struct.pack("<HHH", 0, 1, len(images))
    entries = bytearray()
    offset = 6 + 16 * len(images)
    for size, data in images:
        w = 0 if size == 256 else size
        entries.extend(
            struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(data), offset)
        )
        offset += len(data)

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fh:
        fh.write(header)
        fh.write(bytes(entries))
        for _, data in images:
            fh.write(data)
    print(f"[OK] Icono generado: {output} ({len(images)} tamaños, {output.stat().st_size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera el icono de BusLens")
    parser.add_argument("--output", type=Path, default=ROOT / "assets" / "buslens.ico")
    args = parser.parse_args()
    build_ico(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
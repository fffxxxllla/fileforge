#!/usr/bin/env python3
"""FileForge - create blank, openable files with an exact logical size.

The project intentionally uses only Python's standard library.  Most formats are
real minimal documents/containers.  Bytes that do not carry user content are
used as padding, and large binary payloads are written as sparse zero ranges
where the platform supports sparse files.
"""

from __future__ import annotations

import argparse
import base64
import bz2
import gzip
import lzma
import os
import re
import sqlite3
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO, Callable, Iterable


APP_NAME = "FileForge"
VERSION = "2.0.1"
ZERO_BLOCK = b"\0" * (1024 * 1024)
SPACE_BLOCK = b" " * (1024 * 1024)


class FileForgeError(Exception):
    """Base error shown to users without a traceback."""


class SizeTooSmallError(FileForgeError):
    def __init__(self, fmt: str, requested: int, minimum: int):
        super().__init__(
            f"{fmt.upper()} 至少需要 {minimum} 字节，当前只指定了 {requested} 字节。"
        )
        self.format = fmt
        self.requested = requested
        self.minimum = minimum


Generator = Callable[[Path, int], None]


@dataclass(frozen=True)
class FormatSpec:
    name: str
    category: str
    description: str
    extensions: tuple[str, ...]
    generator: Generator
    minimum: int = 0


def parse_size(value: str) -> int:
    """Parse a size using Windows-friendly binary units by default.

    KB/MB/GB/TB intentionally use powers of 1024 because Windows Explorer
    displays those values with the short labels.  The IEC spellings (KiB,
    MiB...) are equivalent.  Explicit decimal units use the ``1000`` suffix,
    for example ``10MB1000``.
    """
    match = re.fullmatch(
        r"\s*((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))\s*([a-z0-9]+)?\s*",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        raise FileForgeError(f"无法识别大小：{value!r}。示例：512B、10MB、1.5GiB")
    try:
        number = Decimal(match.group(1))
    except InvalidOperation as exc:
        raise FileForgeError(f"无法识别大小：{value!r}") from exc
    unit = (match.group(2) or "B").upper()
    binary_units = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "KIB": 1024,
        "M": 1024**2,
        "MB": 1024**2,
        "MIB": 1024**2,
        "G": 1024**3,
        "GB": 1024**3,
        "GIB": 1024**3,
        "T": 1024**4,
        "TB": 1024**4,
        "TIB": 1024**4,
        "P": 1024**5,
        "PB": 1024**5,
        "PIB": 1024**5,
        "E": 1024**6,
        "EB": 1024**6,
        "EIB": 1024**6,
    }
    decimal_units = {
        "KB1000": 1000,
        "MB1000": 1000**2,
        "GB1000": 1000**3,
        "TB1000": 1000**4,
        "PB1000": 1000**5,
        "EB1000": 1000**6,
    }
    multiplier = binary_units.get(unit, decimal_units.get(unit))
    if multiplier is None:
        raise FileForgeError(f"不支持的大小单位：{unit}")
    result = number * multiplier
    # A file can only contain an integer number of bytes. Accept arbitrary
    # decimal input and round to the nearest byte instead of rejecting values
    # whose decimal denominator does not divide the selected unit.
    size = int(result.to_integral_value(rounding=ROUND_HALF_UP))
    if number > 0 and size == 0:
        size = 1
    if size < 0:
        raise FileForgeError("文件大小不能为负数")
    return size


def human_size(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024 or unit == "PiB":
            return f"{size} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size} B"


def _write_repeated(stream: BinaryIO, data: bytes, count: int) -> None:
    while count:
        chunk = data if count >= len(data) else data[:count]
        stream.write(chunk)
        count -= len(chunk)


def _write_sparse_zeros(stream: BinaryIO, count: int) -> None:
    if count <= 0:
        return
    if os.name == "nt":
        # Seeking alone creates a hole on Unix.  NTFS additionally needs the
        # sparse-file flag; failure is harmless and falls back to normal zeros.
        try:
            import ctypes
            import msvcrt

            returned = ctypes.c_ulong()
            ctypes.windll.kernel32.DeviceIoControl(
                msvcrt.get_osfhandle(stream.fileno()),
                0x000900C4,  # FSCTL_SET_SPARSE
                None,
                0,
                None,
                0,
                ctypes.byref(returned),
                None,
            )
        except (AttributeError, OSError):
            pass
    stream.seek(count, os.SEEK_CUR)


def _finish_sparse_file(stream: BinaryIO) -> None:
    """Materialize the byte before EOF after a final sparse seek."""
    position = stream.tell()
    if position:
        stream.seek(position - 1)
        stream.write(b"\0")
        stream.seek(position)


def _pad_after(path: Path, target: int, initial: bytes, pad: bytes = b"\0") -> None:
    if target < len(initial):
        raise SizeTooSmallError(path.suffix.lstrip("."), target, len(initial))
    with path.open("wb") as stream:
        stream.write(initial)
        remaining = target - len(initial)
        if remaining:
            if pad == b"\0":
                _write_sparse_zeros(stream, remaining)
                _finish_sparse_file(stream)
            else:
                _write_repeated(stream, pad, remaining)


def _text_generator(prefix: bytes = b"", suffix: bytes = b"", pad: bytes = b" ") -> Generator:
    minimum = len(prefix) + len(suffix)

    def generate(path: Path, target: int) -> None:
        if target < minimum:
            raise SizeTooSmallError(path.suffix.lstrip("."), target, minimum)
        with path.open("wb") as stream:
            stream.write(prefix)
            _write_repeated(stream, pad, target - minimum)
            stream.write(suffix)

    return generate


def _generic(path: Path, target: int) -> None:
    with path.open("wb") as stream:
        _write_sparse_zeros(stream, target)
        _finish_sparse_file(stream)


def _png_chunk_header(kind: bytes, length: int) -> bytes:
    return struct.pack(">I", length) + kind


def _crc32_repeated_zeros(count: int, initial: int = 0) -> int:
    crc = initial
    while count:
        take = min(count, len(ZERO_BLOCK))
        crc = zlib.crc32(ZERO_BLOCK[:take], crc)
        count -= take
    return crc & 0xFFFFFFFF


def _png(path: Path, target: int) -> None:
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    start = signature + chunk(b"IHDR", ihdr_data) + chunk(b"IDAT", zlib.compress(b"\0\0\0\0\0"))
    end = chunk(b"IEND", b"")
    minimum = len(start) + len(end)
    if target < minimum:
        raise SizeTooSmallError("png", target, minimum)
    remaining = target - minimum
    with path.open("wb") as stream:
        stream.write(start)
        while remaining >= 12:
            payload_size = min(remaining - 12, 0x7FFFFFFF)
            kind = b"fpAD"
            stream.write(_png_chunk_header(kind, payload_size))
            _write_sparse_zeros(stream, payload_size)
            crc = _crc32_repeated_zeros(payload_size, zlib.crc32(kind))
            stream.write(struct.pack(">I", crc))
            remaining -= payload_size + 12
        stream.write(end)
        if remaining:
            _write_sparse_zeros(stream, remaining)
            _finish_sparse_file(stream)


JPEG_1X1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAAR"
    "CAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAA"
    "AAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhED"
    "EQA/AL+AD//Z"
)


GIF_1X1 = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00"
    b"\x02\x02D\x01\x00;"
)


WEBP_1X1 = base64.b64decode("UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4HAA==")


def _bmp(path: Path, target: int) -> None:
    minimum = 58
    if target < minimum:
        raise SizeTooSmallError("bmp", target, minimum)
    declared = min(target, 0xFFFFFFFF)
    header = b"BM" + struct.pack("<IHHI", declared, 0, 0, 54)
    dib = struct.pack("<IiiHHIIiiII", 40, 1, 1, 1, 24, 0, 4, 2835, 2835, 0, 0)
    _pad_after(path, target, header + dib + b"\xff\xff\xff\x00")


def _tiff(path: Path, target: int) -> None:
    entries = [
        (256, 4, 1, 1),
        (257, 4, 1, 1),
        (258, 3, 1, 8),
        (259, 3, 1, 1),
        (262, 3, 1, 1),
        (273, 4, 1, 8 + 2 + 9 * 12 + 4),
        (277, 3, 1, 1),
        (278, 4, 1, 1),
        (279, 4, 1, 1),
    ]
    data = bytearray(b"II*\x00\x08\x00\x00\x00")
    data.extend(struct.pack("<H", len(entries)))
    for tag, typ, count, value in entries:
        if typ == 3 and count == 1:
            packed_value = struct.pack("<H", value) + b"\0\0"
        else:
            packed_value = struct.pack("<I", value)
        data.extend(struct.pack("<HHI", tag, typ, count) + packed_value)
    data.extend(b"\0\0\0\0\xff")
    _pad_after(path, target, bytes(data))


def _ico(path: Path, target: int) -> None:
    resource_size = 48
    icon_dir = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 1, 1, 0, 0, 1, 32, resource_size, 22)
    dib = struct.pack("<IiiHHIIiiII", 40, 1, 2, 1, 32, 0, 4, 0, 0, 0, 0)
    _pad_after(path, target, icon_dir + entry + dib + b"\0" * 8)


def _portable_bitmap(kind: str) -> Generator:
    samples = {"ppm": b"\xff\xff\xff", "pgm": b"\xff", "pbm": b"\x00"}
    magic = {"ppm": b"P6", "pgm": b"P5", "pbm": b"P4"}
    header = magic[kind] + (b"\n1 1\n" if kind == "pbm" else b"\n1 1\n255\n")
    initial = header + samples[kind]

    def generate(path: Path, target: int) -> None:
        _pad_after(path, target, initial)

    return generate


def _wav(path: Path, target: int) -> None:
    minimum = 44
    if target < minimum:
        raise SizeTooSmallError("wav", target, minimum)
    if target - 8 > 0xFFFFFFFF:
        raise FileForgeError("标准 WAV 的 RIFF 长度上限约为 4 GiB；请减小文件或使用通用 BIN。")
    data_size = target - minimum
    header = (
        b"RIFF"
        + struct.pack("<I", target - 8)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 8000, 16000, 2, 16)
        + b"data"
        + struct.pack("<I", data_size)
    )
    _pad_after(path, target, header)


def _aiff(path: Path, target: int) -> None:
    minimum = 54
    if target < minimum:
        raise SizeTooSmallError("aiff", target, minimum)
    if target - 8 > 0xFFFFFFFF:
        raise FileForgeError("标准 AIFF 的 FORM 长度上限约为 4 GiB。")
    sample_bytes = target - minimum
    frames = min(sample_bytes // 2, 0xFFFFFFFF)
    extended_8000 = bytes.fromhex("400BFA00000000000000")
    header = (
        b"FORM"
        + struct.pack(">I", target - 8)
        + b"AIFFCOMM"
        + struct.pack(">IhIh", 18, 1, frames, 16)
        + extended_8000
        + b"SSND"
        + struct.pack(">III", 8 + sample_bytes, 0, 0)
    )
    _pad_after(path, target, header)


def _flac(path: Path, target: int) -> None:
    stream_info = struct.pack(">HH", 4096, 4096) + b"\0" * 6
    packed_audio_info = (8000 << 44) | (15 << 36)
    stream_info += packed_audio_info.to_bytes(8, "big") + b"\0" * 16
    base_size = 4 + 4 + len(stream_info)
    if target < base_size:
        raise SizeTooSmallError("flac", target, base_size)
    remaining = target - base_size
    with path.open("wb") as stream:
        stream.write(b"fLaC")
        stream.write(bytes([0x80 if remaining < 4 else 0x00]) + len(stream_info).to_bytes(3, "big"))
        stream.write(stream_info)
        while remaining >= 4:
            payload = min(remaining - 4, 0xFFFFFF)
            will_remain = remaining - 4 - payload
            is_last = will_remain < 4
            stream.write(bytes([0x81 if is_last else 0x01]) + payload.to_bytes(3, "big"))
            _write_sparse_zeros(stream, payload)
            remaining = will_remain
        if remaining:
            _write_sparse_zeros(stream, remaining)
        _finish_sparse_file(stream)


def _syncsafe(value: int) -> bytes:
    if value > 0x0FFFFFFF:
        raise FileForgeError("单个 ID3v2 标签不能超过 256 MiB。")
    return bytes((value >> 21 & 0x7F, value >> 14 & 0x7F, value >> 7 & 0x7F, value & 0x7F))


def _mp3(path: Path, target: int) -> None:
    # MPEG-1 Layer III, 128 kbps, 44.1 kHz. A zeroed frame decodes as silence.
    frame = b"\xff\xfb\x90\x64" + b"\0" * 413
    if target < len(frame):
        raise SizeTooSmallError("mp3", target, len(frame))
    if target >= len(frame) + 10:
        padding = target - len(frame) - 10
        if padding <= 0x0FFFFFFF:
            with path.open("wb") as stream:
                stream.write(b"ID3\x04\x00\x00" + _syncsafe(padding))
                _write_sparse_zeros(stream, padding)
                stream.write(frame)
                _finish_sparse_file(stream)
            return
    _pad_after(path, target, frame)


def _midi(path: Path, target: int) -> None:
    midi = b"MThd\0\0\0\x06\0\0\0\x01\0\x60MTrk\0\0\0\x04\0\xff/\0"
    _pad_after(path, target, midi)


def _mp4_box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _mp4_initial(brand: bytes) -> bytes:
    compatible = b"isomiso2mp41" if brand != b"qt  " else b"qt  "
    ftyp = _mp4_box(b"ftyp", brand + struct.pack(">I", 0x200) + compatible)
    matrix = struct.pack(">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)
    mvhd_payload = (
        b"\0\0\0\0"
        + struct.pack(">IIII", 0, 0, 1000, 0)
        + struct.pack(">I", 0x00010000)
        + struct.pack(">H", 0x0100)
        + b"\0" * 10
        + matrix
        + b"\0" * 24
        + struct.pack(">I", 1)
    )
    return ftyp + _mp4_box(b"moov", _mp4_box(b"mvhd", mvhd_payload))


def _mp4(brand: bytes) -> Generator:
    initial = _mp4_initial(brand)

    def generate(path: Path, target: int) -> None:
        if target < len(initial):
            raise SizeTooSmallError(path.suffix.lstrip("."), target, len(initial))
        remaining = target - len(initial)
        with path.open("wb") as stream:
            stream.write(initial)
            if remaining >= 8:
                if remaining <= 0xFFFFFFFF:
                    stream.write(struct.pack(">I4s", remaining, b"free"))
                    _write_sparse_zeros(stream, remaining - 8)
                    remaining = 0
                elif remaining >= 16:
                    stream.write(struct.pack(">I4sQ", 1, b"free", remaining))
                    _write_sparse_zeros(stream, remaining - 16)
                    remaining = 0
            if remaining:
                _write_sparse_zeros(stream, remaining)
            _finish_sparse_file(stream)

    return generate


def _avi(path: Path, target: int) -> None:
    avih = struct.pack("<14I", 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0)
    hdrl_payload = b"hdrl" + b"avih" + struct.pack("<I", 56) + avih
    initial_body = b"AVI " + b"LIST" + struct.pack("<I", len(hdrl_payload)) + hdrl_payload
    movi = b"LIST\x04\x00\x00\x00movi"
    minimum = 8 + len(initial_body) + len(movi)
    if target < minimum:
        raise SizeTooSmallError("avi", target, minimum)
    if target - 8 > 0xFFFFFFFF:
        raise FileForgeError("标准 AVI 的 RIFF 长度上限约为 4 GiB。")
    remaining = target - minimum
    with path.open("wb") as stream:
        stream.write(b"RIFF" + struct.pack("<I", target - 8) + initial_body)
        if remaining >= 8:
            payload = remaining - 8
            stream.write(b"JUNK" + struct.pack("<I", payload))
            _write_sparse_zeros(stream, payload)
            remaining = 0
        stream.write(movi)
        if remaining:
            _write_sparse_zeros(stream, remaining)
        _finish_sparse_file(stream)


def _pdf_layout(filler: int) -> tuple[bytes, bytes]:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << >> /Contents 4 0 R >>",
    ]
    prefix = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(prefix))
        prefix.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    offsets.append(len(prefix))
    prefix.extend(f"4 0 obj\n<< /Length {filler} >>\nstream\n".encode())
    xref_offset = len(prefix) + filler + len(b"\nendstream\nendobj\n")
    suffix = bytearray(b"\nendstream\nendobj\n")
    suffix.extend(b"xref\n0 5\n0000000000 65535 f \n")
    for offset in offsets:
        suffix.extend(f"{offset:010d} 00000 n \n".encode())
    suffix.extend(
        b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    return bytes(prefix), bytes(suffix)


def _pdf(path: Path, target: int) -> None:
    filler = max(0, target - 512)
    for _ in range(20):
        prefix, suffix = _pdf_layout(filler)
        new_filler = target - len(prefix) - len(suffix)
        if new_filler == filler:
            break
        filler = new_filler
    if filler < 0:
        prefix0, suffix0 = _pdf_layout(0)
        raise SizeTooSmallError("pdf", target, len(prefix0) + len(suffix0))
    prefix, suffix = _pdf_layout(filler)
    if len(prefix) + filler + len(suffix) != target:
        raise FileForgeError("无法为这个临界大小构造 PDF，请把大小增加几个字节。")
    with path.open("wb") as stream:
        stream.write(prefix)
        _write_sparse_zeros(stream, filler)
        stream.write(suffix)


def _sqlite(path: Path, target: int) -> None:
    minimum = 4096
    if target < minimum:
        raise SizeTooSmallError("sqlite", target, minimum)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA page_size=4096")
        connection.execute("VACUUM")
    finally:
        connection.close()
    current = path.stat().st_size
    if current > target:
        raise SizeTooSmallError("sqlite", target, current)
    with path.open("r+b") as stream:
        stream.seek(0, os.SEEK_END)
        _write_sparse_zeros(stream, target - current)
        _finish_sparse_file(stream)


def _tar(path: Path, target: int) -> None:
    minimum = 1024
    if target < minimum:
        raise SizeTooSmallError("tar", target, minimum)
    with path.open("wb") as stream:
        stream.write(b"\0" * minimum)
        _write_sparse_zeros(stream, target - minimum)
        _finish_sparse_file(stream)


def _compressed_empty(kind: str) -> Generator:
    if kind == "gz":
        initial = gzip.compress(b"", mtime=0)
    elif kind == "bz2":
        initial = bz2.compress(b"")
    else:
        initial = lzma.compress(b"", format=lzma.FORMAT_XZ)

    def generate(path: Path, target: int) -> None:
        _pad_after(path, target, initial)

    return generate


def _xz(path: Path, target: int) -> None:
    """Write concatenated empty XZ streams so strict readers accept large padding.

    XZ streams and their permitted stream padding are always a multiple of four
    bytes.  For a non-multiple-of-four target, the final 1-3 bytes are benign
    trailing padding; exact common KB/MB/GiB sizes remain fully strict-valid.
    """
    empty_stream = lzma.compress(b"", format=lzma.FORMAT_XZ)
    minimum = len(empty_stream)
    if target < minimum:
        raise SizeTooSmallError("xz", target, minimum)
    stream_count, remainder = divmod(target, minimum)
    strict_padding = remainder - (remainder % 4)
    trailing = remainder - strict_padding
    repeated_block = empty_stream * max(1, min(32768, stream_count))
    with path.open("wb") as stream:
        left = stream_count
        while left:
            take = min(left, len(repeated_block) // minimum)
            stream.write(repeated_block[: take * minimum])
            left -= take
        if strict_padding:
            stream.write(b"\0" * strict_padding)
        if trailing:
            stream.write(b"\0" * trailing)


# ---------- exact-size stored ZIP writer ----------


@dataclass
class _ZipEntry:
    name: bytes
    data: bytes | None
    size: int
    crc: int
    offset: int = 0
    local_extra: bytes = b""
    central_extra: bytes = b""


def _zip_entries_layout(files: list[tuple[str, bytes]], filler_size: int) -> tuple[list[_ZipEntry], int, int, bool]:
    entries = [
        _ZipEntry(name.encode("utf-8"), data, len(data), zlib.crc32(data) & 0xFFFFFFFF)
        for name, data in files
    ]
    # The CRC is irrelevant for layout and can be expensive for a large filler;
    # calculate it once only after the final filler size has been solved.
    entries.append(_ZipEntry(b"__fileforge_padding__.bin", None, filler_size, 0))
    position = 0
    for entry in entries:
        entry.offset = position
        if entry.size >= 0xFFFFFFFF:
            entry.local_extra = struct.pack("<HHQQ", 0x0001, 16, entry.size, entry.size)
        position += 30 + len(entry.name) + len(entry.local_extra) + entry.size
    central_offset = position
    for entry in entries:
        extra_values = bytearray()
        if entry.size >= 0xFFFFFFFF:
            extra_values.extend(struct.pack("<QQ", entry.size, entry.size))
        if entry.offset >= 0xFFFFFFFF:
            extra_values.extend(struct.pack("<Q", entry.offset))
        if extra_values:
            entry.central_extra = struct.pack("<HH", 0x0001, len(extra_values)) + extra_values
        position += 46 + len(entry.name) + len(entry.central_extra)
    central_size = position - central_offset
    zip64 = (
        len(entries) >= 0xFFFF
        or central_offset >= 0xFFFFFFFF
        or central_size >= 0xFFFFFFFF
        or any(entry.size >= 0xFFFFFFFF or entry.offset >= 0xFFFFFFFF for entry in entries)
    )
    end_size = 22 + (76 if zip64 else 0)
    return entries, central_offset, central_size, zip64


def _zip_total_size(files: list[tuple[str, bytes]], filler_size: int) -> int:
    entries, central_offset, central_size, zip64 = _zip_entries_layout(files, filler_size)
    return central_offset + central_size + 22 + (76 if zip64 else 0)


def _solve_zip_size(files: list[tuple[str, bytes]], target: int) -> tuple[int, int]:
    minimum = _zip_total_size(files, 0)
    if target < minimum:
        raise SizeTooSmallError("zip", target, minimum)
    low, high = 0, target
    while low < high:
        mid = (low + high + 1) // 2
        if _zip_total_size(files, mid) <= target:
            low = mid
        else:
            high = mid - 1
    filler = low
    comment_size = target - _zip_total_size(files, filler)
    if comment_size > 0xFFFF:
        raise FileForgeError("无法精确填充此 ZIP 临界大小，请把目标大小增加 128 字节。")
    return filler, comment_size


def _write_exact_zip(path: Path, target: int, files: list[tuple[str, bytes]]) -> None:
    filler_size, comment_size = _solve_zip_size(files, target)
    entries, central_offset, central_size, zip64 = _zip_entries_layout(files, filler_size)
    entries[-1].crc = _crc32_repeated_zeros(filler_size)
    with path.open("wb") as stream:
        for entry in entries:
            large = entry.size >= 0xFFFFFFFF
            version = 45 if large else 20
            size_field = 0xFFFFFFFF if large else entry.size
            stream.write(
                struct.pack(
                    "<IHHHHHIIIHH",
                    0x04034B50,
                    version,
                    0,
                    0,
                    0,
                    0x21,
                    entry.crc,
                    size_field,
                    size_field,
                    len(entry.name),
                    len(entry.local_extra),
                )
            )
            stream.write(entry.name)
            stream.write(entry.local_extra)
            if entry.data is None:
                _write_sparse_zeros(stream, entry.size)
            else:
                stream.write(entry.data)
        for entry in entries:
            large_size = entry.size >= 0xFFFFFFFF
            large_offset = entry.offset >= 0xFFFFFFFF
            version = 45 if (large_size or large_offset) else 20
            size_field = 0xFFFFFFFF if large_size else entry.size
            offset_field = 0xFFFFFFFF if large_offset else entry.offset
            stream.write(
                struct.pack(
                    "<IHHHHHHIIIHHHHHII",
                    0x02014B50,
                    version,
                    version,
                    0,
                    0,
                    0,
                    0x21,
                    entry.crc,
                    size_field,
                    size_field,
                    len(entry.name),
                    len(entry.central_extra),
                    0,
                    0,
                    0,
                    0o600 << 16,
                    offset_field,
                )
            )
            stream.write(entry.name)
            stream.write(entry.central_extra)
        if zip64:
            zip64_offset = stream.tell()
            stream.write(
                struct.pack(
                    "<IQHHIIQQQQ",
                    0x06064B50,
                    44,
                    45,
                    45,
                    0,
                    0,
                    len(entries),
                    len(entries),
                    central_size,
                    central_offset,
                )
            )
            stream.write(struct.pack("<IIQI", 0x07064B50, 0, zip64_offset, 1))
        stream.write(
            struct.pack(
                "<IHHHHIIH",
                0x06054B50,
                0,
                0,
                min(len(entries), 0xFFFF),
                min(len(entries), 0xFFFF),
                min(central_size, 0xFFFFFFFF),
                min(central_offset, 0xFFFFFFFF),
                comment_size,
            )
        )
        if comment_size:
            stream.write(b"\0" * comment_size)
        _finish_sparse_file(stream)


def _xml(value: str) -> bytes:
    return value.strip().encode("utf-8")


CONTENT_TYPES_HEAD = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">"""


def _docx_files() -> list[tuple[str, bytes]]:
    return [
        ("[Content_Types].xml", _xml(CONTENT_TYPES_HEAD + """
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
        </Types>""")),
        ("_rels/.rels", _xml("""<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
        </Relationships>""")),
        ("word/document.xml", _xml("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:p/><w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body>
        </w:document>""")),
    ]


def _xlsx_files() -> list[tuple[str, bytes]]:
    return [
        ("[Content_Types].xml", _xml(CONTENT_TYPES_HEAD + """
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
          <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
        </Types>""")),
        ("_rels/.rels", _xml("""<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
        </Relationships>""")),
        ("xl/workbook.xml", _xml("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
          <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
        </workbook>""")),
        ("xl/_rels/workbook.xml.rels", _xml("""<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
          <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
        </Relationships>""")),
        ("xl/worksheets/sheet1.xml", _xml("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>""")),
    ]


def _pptx_files() -> list[tuple[str, bytes]]:
    content_types = CONTENT_TYPES_HEAD + """
      <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
      <Default Extension="xml" ContentType="application/xml"/>
      <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
      <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>
      <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
      <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
      <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
    </Types>"""
    sp_tree = """<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree>"""
    relationships_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    return [
        ("[Content_Types].xml", _xml(content_types)),
        ("_rels/.rels", _xml(f"""<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="{relationships_ns}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>""")),
        ("ppt/presentation.xml", _xml("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="rId2"/></p:sldIdLst><p:sldSz cx="9144000" cy="5143500"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>""")),
        ("ppt/_rels/presentation.xml.rels", _xml(f"""<?xml version="1.0" encoding="UTF-8"?>
        <Relationships xmlns="{relationships_ns}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/></Relationships>""")),
        ("ppt/slides/slide1.xml", _xml(f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld>{sp_tree}</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>""")),
        ("ppt/slides/_rels/slide1.xml.rels", _xml(f"""<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{relationships_ns}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>""")),
        ("ppt/slideMasters/slideMaster1.xml", _xml(f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld>{sp_tree}</p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>""")),
        ("ppt/slideMasters/_rels/slideMaster1.xml.rels", _xml(f"""<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{relationships_ns}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>""")),
        ("ppt/slideLayouts/slideLayout1.xml", _xml(f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank"><p:cSld name="Blank">{sp_tree}</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>""")),
        ("ppt/slideLayouts/_rels/slideLayout1.xml.rels", _xml(f"""<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{relationships_ns}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>""")),
        ("ppt/theme/theme1.xml", _xml("""<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Blank"><a:themeElements><a:clrScheme name="Blank"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="000000"/></a:dk2><a:lt2><a:srgbClr val="FFFFFF"/></a:lt2><a:accent1><a:srgbClr val="4472C4"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme><a:fontScheme name="Blank"><a:majorFont/><a:minorFont/></a:fontScheme><a:fmtScheme name="Blank"><a:fillStyleLst/><a:lnStyleLst/><a:effectStyleLst/><a:bgFillStyleLst/></a:fmtScheme></a:themeElements></a:theme>""")),
    ]


def _odf_files(mime: str, root_tag: str) -> list[tuple[str, bytes]]:
    return [
        ("mimetype", mime.encode("ascii")),
        ("META-INF/manifest.xml", _xml(f"""<?xml version="1.0" encoding="UTF-8"?>
        <manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2"><manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/><manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/></manifest:manifest>""")),
        ("content.xml", _xml(f"""<?xml version="1.0" encoding="UTF-8"?>
        <office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:{root_tag}="urn:oasis:names:tc:opendocument:xmlns:{root_tag}:1.0" office:version="1.2"><office:body><office:{root_tag}/></office:body></office:document-content>""")),
    ]


def _epub_files() -> list[tuple[str, bytes]]:
    return [
        ("mimetype", b"application/epub+zip"),
        ("META-INF/container.xml", _xml("""<?xml version="1.0" encoding="UTF-8"?>
        <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>""")),
        ("OEBPS/content.opf", _xml("""<?xml version="1.0" encoding="UTF-8"?>
        <package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">fileforge-blank</dc:identifier><dc:title>Blank</dc:title><dc:language>zh</dc:language><meta property="dcterms:modified">2020-01-01T00:00:00Z</meta></metadata><manifest><item id="page" href="index.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="page"/></spine></package>""")),
        ("OEBPS/index.xhtml", _xml("""<?xml version="1.0" encoding="UTF-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title></title></head><body></body></html>""")),
    ]


def _archive_generator(kind: str) -> Generator:
    def generate(path: Path, target: int) -> None:
        if kind == "docx":
            files = _docx_files()
        elif kind == "xlsx":
            files = _xlsx_files()
        elif kind == "pptx":
            files = _pptx_files()
        elif kind == "odt":
            files = _odf_files("application/vnd.oasis.opendocument.text", "text")
        elif kind == "ods":
            files = _odf_files("application/vnd.oasis.opendocument.spreadsheet", "spreadsheet")
        elif kind == "odp":
            files = _odf_files("application/vnd.oasis.opendocument.presentation", "presentation")
        elif kind == "epub":
            files = _epub_files()
        elif kind == "jar":
            files = [("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\r\nCreated-By: FileForge\r\n\r\n")]
        else:
            files = []
        _write_exact_zip(path, target, files)

    return generate


def _vcard(path: Path, target: int) -> None:
    _text_generator(b"BEGIN:VCARD\r\nVERSION:4.0\r\n", b"END:VCARD\r\n")(path, target)


def _ical(path: Path, target: int) -> None:
    _text_generator(b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//FileForge//Blank//EN\r\n", b"END:VCALENDAR\r\n")(path, target)


def _specs() -> list[FormatSpec]:
    whitespace = _text_generator()
    pdf_minimum = sum(map(len, _pdf_layout(0)))
    zip_minimum = _zip_total_size([], 0)
    docx_minimum = _zip_total_size(_docx_files(), 0)
    xlsx_minimum = _zip_total_size(_xlsx_files(), 0)
    pptx_minimum = _zip_total_size(_pptx_files(), 0)
    odt_minimum = _zip_total_size(_odf_files("application/vnd.oasis.opendocument.text", "text"), 0)
    ods_minimum = _zip_total_size(_odf_files("application/vnd.oasis.opendocument.spreadsheet", "spreadsheet"), 0)
    odp_minimum = _zip_total_size(_odf_files("application/vnd.oasis.opendocument.presentation", "presentation"), 0)
    epub_minimum = _zip_total_size(_epub_files(), 0)
    jar_minimum = _zip_total_size(
        [("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\r\nCreated-By: FileForge\r\n\r\n")], 0
    )
    return [
        FormatSpec("generic", "通用", "空白二进制（任意扩展名）", ("bin", "dat", "raw"), _generic, 0),
        FormatSpec("text", "文本与代码", "纯文本/代码空白文件", ("txt", "log", "md", "markdown", "csv", "tsv", "py", "pyw", "js", "mjs", "cjs", "ts", "tsx", "jsx", "css", "scss", "less", "sql", "java", "c", "h", "cpp", "hpp", "cs", "go", "rs", "php", "rb", "sh", "ps1", "bat", "cmd", "toml"), whitespace, 0),
        FormatSpec("json", "文本与代码", "空 JSON 对象", ("json",), _text_generator(b"{}"), 2),
        FormatSpec("jsonl", "文本与代码", "空 JSON Lines", ("jsonl", "ndjson"), _text_generator(b"{}\n"), 3),
        FormatSpec("xml", "文本与代码", "空 XML 文档", ("xml", "plist", "kml"), _text_generator(b'<?xml version="1.0" encoding="UTF-8"?><root>', b"</root>"), 51),
        FormatSpec("yaml", "文本与代码", "空 YAML 文档", ("yaml", "yml"), _text_generator(b"---\n{}\n"), 7),
        FormatSpec("ini", "文本与代码", "空 INI/配置文件", ("ini", "cfg", "conf"), _text_generator(b"[empty]\n"), 8),
        FormatSpec("html", "文本与代码", "空 HTML 页面", ("html", "htm"), _text_generator(b'<!doctype html><html><head><meta charset="utf-8"><title></title></head><body>', b"</body></html>"), 91),
        FormatSpec("svg", "图片", "空白 SVG 图片", ("svg",), _text_generator(b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">', b"</svg>"), 68),
        FormatSpec("rtf", "文档", "空白 RTF 文档", ("rtf",), _text_generator(b"{\\rtf1\\ansi ", b"}"), 14),
        FormatSpec("postscript", "文档", "空白 PostScript/EPS", ("ps", "eps"), _text_generator(b"%!PS-Adobe-3.0\n%%Pages: 0\n", b"%%EOF\n"), 34),
        FormatSpec("vcard", "数据交换", "空白 vCard", ("vcf",), _vcard, 44),
        FormatSpec("ical", "数据交换", "空白 iCalendar", ("ics",), _ical, 82),
        FormatSpec("png", "图片", "1×1 透明 PNG", ("png",), _png, 68),
        FormatSpec("jpeg", "图片", "1×1 JPEG", ("jpg", "jpeg", "jpe"), lambda p, s: _pad_after(p, s, JPEG_1X1), len(JPEG_1X1)),
        FormatSpec("gif", "图片", "1×1 GIF", ("gif",), lambda p, s: _pad_after(p, s, GIF_1X1), len(GIF_1X1)),
        FormatSpec("webp", "图片", "1×1 WebP", ("webp",), lambda p, s: _pad_after(p, s, WEBP_1X1), len(WEBP_1X1)),
        FormatSpec("bmp", "图片", "1×1 BMP", ("bmp",), _bmp, 58),
        FormatSpec("tiff", "图片", "1×1 TIFF", ("tif", "tiff"), _tiff, 123),
        FormatSpec("ico", "图片", "1×1 透明图标", ("ico",), _ico, 70),
        FormatSpec("ppm", "图片", "1×1 PPM", ("ppm",), _portable_bitmap("ppm"), 14),
        FormatSpec("pgm", "图片", "1×1 PGM", ("pgm",), _portable_bitmap("pgm"), 12),
        FormatSpec("pbm", "图片", "1×1 PBM", ("pbm",), _portable_bitmap("pbm"), 8),
        FormatSpec("pdf", "文档", "单页空白 PDF", ("pdf",), _pdf, pdf_minimum),
        FormatSpec("docx", "Office", "空白 Word 文档", ("docx",), _archive_generator("docx"), docx_minimum),
        FormatSpec("xlsx", "Office", "空白 Excel 工作簿", ("xlsx",), _archive_generator("xlsx"), xlsx_minimum),
        FormatSpec("pptx", "Office", "空白 PowerPoint 演示文稿", ("pptx",), _archive_generator("pptx"), pptx_minimum),
        FormatSpec("odt", "Office", "空白 OpenDocument 文本文档", ("odt",), _archive_generator("odt"), odt_minimum),
        FormatSpec("ods", "Office", "空白 OpenDocument 表格", ("ods",), _archive_generator("ods"), ods_minimum),
        FormatSpec("odp", "Office", "空白 OpenDocument 演示文稿", ("odp",), _archive_generator("odp"), odp_minimum),
        FormatSpec("epub", "文档", "空白 EPUB 电子书", ("epub",), _archive_generator("epub"), epub_minimum),
        FormatSpec("zip", "压缩与归档", "有效空 ZIP", ("zip", "cbz"), _archive_generator("zip"), zip_minimum),
        FormatSpec("jar", "压缩与归档", "含清单的 JAR", ("jar",), _archive_generator("jar"), jar_minimum),
        FormatSpec("tar", "压缩与归档", "有效空 TAR", ("tar",), _tar, 1024),
        FormatSpec("gzip", "压缩与归档", "有效空 GZIP（尾部填充）", ("gz",), _compressed_empty("gz"), 20),
        FormatSpec("bzip2", "压缩与归档", "有效空 BZIP2（尾部填充）", ("bz2",), _compressed_empty("bz2"), len(bz2.compress(b""))),
        FormatSpec("xz", "压缩与归档", "有效空 XZ（拼接空数据流）", ("xz",), _xz, len(lzma.compress(b"", format=lzma.FORMAT_XZ))),
        FormatSpec("wav", "音频", "PCM 静音 WAV", ("wav", "wave"), _wav, 44),
        FormatSpec("aiff", "音频", "PCM 静音 AIFF", ("aif", "aiff", "aifc"), _aiff, 54),
        FormatSpec("flac", "音频", "零时长 FLAC", ("flac",), _flac, 42),
        FormatSpec("mp3", "音频", "单帧静音 MP3", ("mp3",), _mp3, 417),
        FormatSpec("midi", "音频", "空白 MIDI", ("mid", "midi"), _midi, 26),
        FormatSpec("mp4", "视频/媒体", "零时长 MP4 容器", ("mp4", "m4v"), _mp4(b"isom"), len(_mp4_initial(b"isom"))),
        FormatSpec("m4a", "视频/媒体", "零时长 M4A 容器", ("m4a",), _mp4(b"M4A "), len(_mp4_initial(b"M4A "))),
        FormatSpec("mov", "视频/媒体", "零时长 QuickTime 容器", ("mov", "qt"), _mp4(b"qt  "), len(_mp4_initial(b"qt  "))),
        FormatSpec("avi", "视频/媒体", "零帧 AVI 容器", ("avi",), _avi, 100),
        FormatSpec("sqlite", "数据库", "空 SQLite 数据库", ("sqlite", "sqlite3", "db"), _sqlite, 4096),
    ]


SPECS = _specs()
SPEC_BY_NAME = {spec.name: spec for spec in SPECS}
SPEC_BY_EXTENSION = {ext: spec for spec in SPECS for ext in spec.extensions}


def resolve_format(output: Path, requested: str | None) -> FormatSpec:
    key = requested.lower().lstrip(".") if requested else output.suffix.lower().lstrip(".")
    if not key:
        raise FileForgeError("输出文件没有扩展名，请使用 --format 指定格式。")
    spec = SPEC_BY_NAME.get(key) or SPEC_BY_EXTENSION.get(key)
    if spec is None:
        if requested and requested.lower() in {"generic", "binary"}:
            return SPEC_BY_NAME["generic"]
        raise FileForgeError(
            f"暂不支持格式 {key!r}。可使用 --format generic 生成通用空白文件，"
            "或运行 --list-formats 查看支持列表。"
        )
    return spec


def create_file(output: str | os.PathLike[str], size: int, fmt: str | None = None, overwrite: bool = False) -> tuple[Path, FormatSpec]:
    output_path = Path(output).expanduser().resolve()
    if size < 0:
        raise FileForgeError("文件大小不能为负数")
    spec = resolve_format(output_path, fmt)
    if size < spec.minimum:
        raise SizeTooSmallError(spec.name, size, spec.minimum)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not overwrite:
        raise FileForgeError(f"文件已存在：{output_path}（使用 --overwrite 覆盖）")

    temp_handle, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
    os.close(temp_handle)
    temp_path = Path(temp_name)
    try:
        spec.generator(temp_path, size)
        actual = temp_path.stat().st_size
        if actual != size:
            raise FileForgeError(f"内部校验失败：期望 {size} 字节，实际 {actual} 字节")
        os.replace(temp_path, output_path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return output_path, spec


def verify_file(path: str | os.PathLike[str], fmt: str | None = None, deep: bool = False) -> tuple[bool, str]:
    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        return False, "文件不存在"
    try:
        spec = resolve_format(file_path, fmt)
        with file_path.open("rb") as stream:
            head = stream.read(16)
        signatures: dict[str, tuple[bytes, ...]] = {
            "png": (b"\x89PNG\r\n\x1a\n",), "jpeg": (b"\xff\xd8",), "gif": (b"GIF8",),
            "webp": (b"RIFF",), "bmp": (b"BM",), "tiff": (b"II*\0", b"MM\0*"),
            "ico": (b"\0\0\x01\0",), "pdf": (b"%PDF-",), "wav": (b"RIFF",),
            "aiff": (b"FORM",), "flac": (b"fLaC",), "mp3": (b"ID3", b"\xff\xfb"),
            "midi": (b"MThd",), "mp4": (b"\0",), "m4a": (b"\0",), "mov": (b"\0",),
            "avi": (b"RIFF",), "sqlite": (b"SQLite format 3\0",),
            "gzip": (b"\x1f\x8b",), "bzip2": (b"BZh",), "xz": (b"\xfd7zXZ\0",),
        }
        expected = signatures.get(spec.name)
        if expected and not any(head.startswith(item) for item in expected):
            return False, f"文件头不符合 {spec.name.upper()} 格式"
        if spec.name in {"zip", "jar", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub"}:
            import zipfile
            with zipfile.ZipFile(file_path) as archive:
                if deep:
                    bad = archive.testzip()
                    if bad:
                        return False, f"ZIP 条目校验失败：{bad}"
        elif spec.name == "sqlite":
            connection = sqlite3.connect(f"file:{file_path.as_posix()}?mode=ro", uri=True)
            try:
                connection.execute("PRAGMA schema_version").fetchone()
            finally:
                connection.close()
        elif spec.name == "wav":
            import wave
            with wave.open(str(file_path), "rb") as audio:
                audio.getparams()
        return True, f"有效的 {spec.name.upper()} 文件，大小 {file_path.stat().st_size} 字节"
    except Exception as exc:
        return False, f"验证失败：{exc}"


def format_table() -> str:
    rows = []
    for spec in SPECS:
        rows.append(
            f"{spec.name:<12} {spec.category:<10} {','.join('.' + ext for ext in spec.extensions):<42} "
            f"最小 {spec.minimum} B  {spec.description}"
        )
    return "\n".join(rows)


def _launch_legacy_gui() -> None:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        raise FileForgeError("当前 Python 未安装 Tk 图形组件，请使用命令行模式。") from exc

    root = tk.Tk()
    root.title(f"{APP_NAME} {VERSION} — 精确大小空白文件生成器")
    root.geometry("760x520")
    root.minsize(680, 460)

    style = ttk.Style(root)
    if "vista" in style.theme_names():
        style.theme_use("vista")

    main = ttk.Frame(root, padding=24)
    main.pack(fill="both", expand=True)
    main.columnconfigure(1, weight=1)

    title = ttk.Label(main, text="生成指定大小、可正常打开的空白文件", font=("Microsoft YaHei UI", 16, "bold"))
    title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
    ttk.Label(main, text="自动写入真实格式结构；最终文件大小精确到字节。", foreground="#555555").grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 20))

    output_var = tk.StringVar(value=str(Path.cwd() / "blank.pdf"))
    format_var = tk.StringVar(value="pdf")
    size_var = tk.StringVar(value="10")
    unit_var = tk.StringVar(value="MB")
    overwrite_var = tk.BooleanVar(value=False)
    status_var = tk.StringVar(value=f"支持 {sum(len(s.extensions) for s in SPECS)} 个扩展名、{len(SPECS)} 类格式")

    ttk.Label(main, text="输出文件").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=8)
    output_entry = ttk.Entry(main, textvariable=output_var)
    output_entry.grid(row=2, column=1, sticky="ew", pady=8)

    def browse() -> None:
        selected = filedialog.asksaveasfilename(title="选择输出文件", initialfile=Path(output_var.get()).name or "blank.pdf")
        if selected:
            output_var.set(selected)
            ext = Path(selected).suffix.lower().lstrip(".")
            spec = SPEC_BY_EXTENSION.get(ext)
            if spec:
                format_var.set(spec.name)
                update_hint()

    ttk.Button(main, text="浏览…", command=browse).grid(row=2, column=2, padx=(10, 0), pady=8)

    ttk.Label(main, text="文件格式").grid(row=3, column=0, sticky="w", padx=(0, 12), pady=8)
    format_box = ttk.Combobox(main, textvariable=format_var, state="readonly", values=[s.name for s in SPECS])
    format_box.grid(row=3, column=1, sticky="ew", pady=8)

    hint_var = tk.StringVar()
    ttk.Label(main, textvariable=hint_var, foreground="#555555").grid(row=4, column=1, columnspan=2, sticky="w")

    size_frame = ttk.Frame(main)
    size_frame.columnconfigure(0, weight=1)
    ttk.Label(main, text="文件大小").grid(row=5, column=0, sticky="w", padx=(0, 12), pady=14)
    size_frame.grid(row=5, column=1, sticky="ew", pady=14)
    ttk.Entry(size_frame, textvariable=size_var).grid(row=0, column=0, sticky="ew")
    ttk.Combobox(size_frame, textvariable=unit_var, state="readonly", width=8, values=("B", "KB", "MB", "GB", "KiB", "MiB", "GiB")).grid(row=0, column=1, padx=(8, 0))
    ttk.Checkbutton(main, text="允许覆盖已有文件", variable=overwrite_var).grid(row=6, column=1, sticky="w", pady=(4, 14))

    separator = ttk.Separator(main)
    separator.grid(row=7, column=0, columnspan=3, sticky="ew", pady=8)
    ttk.Label(main, text="提示", font=("Microsoft YaHei UI", 10, "bold")).grid(row=8, column=0, sticky="nw", pady=8)
    tips = (
        "• 大型空白二进制区会优先使用稀疏文件，逻辑大小不变，但本机磁盘占用可能更小。\n"
        "• Office、ZIP、图片、音频等写入真实结构；极小尺寸低于格式下限时会给出提示。\n"
        "• 未列出的扩展名可选择 generic，但只能保证它是指定大小的空白二进制文件。"
    )
    ttk.Label(main, text=tips, justify="left", wraplength=590).grid(row=8, column=1, columnspan=2, sticky="w", pady=8)

    progress = ttk.Progressbar(main, mode="indeterminate")
    progress.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(14, 8))
    ttk.Label(main, textvariable=status_var).grid(row=10, column=0, columnspan=2, sticky="w")

    def update_hint(*_args: object) -> None:
        spec = SPEC_BY_NAME.get(format_var.get())
        if not spec:
            return
        hint_var.set(f"{spec.category} · {spec.description} · 扩展名：{', '.join('.' + x for x in spec.extensions)} · 最小 {spec.minimum} B")
        current = Path(output_var.get())
        if current.suffix.lower().lstrip(".") not in spec.extensions:
            output_var.set(str(current.with_suffix("." + spec.extensions[0])))

    format_box.bind("<<ComboboxSelected>>", update_hint)
    update_hint()

    import queue
    result_queue: queue.Queue[tuple[str, object, object]] = queue.Queue()

    def worker() -> None:
        try:
            size = parse_size(size_var.get() + unit_var.get())
            output, spec = create_file(output_var.get(), size, format_var.get(), overwrite_var.get())
            ok, detail = verify_file(output, spec.name, deep=size <= 64 * 1024 * 1024)
            if not ok:
                raise FileForgeError(detail)
            result_queue.put(("ok", output, size))
        except Exception as exc:
            result_queue.put(("error", exc, 0))

    def poll_result() -> None:
        try:
            status, value, size_value = result_queue.get_nowait()
        except queue.Empty:
            root.after(100, poll_result)
            return
        progress.stop()
        create_button.configure(state="normal")
        if status == "ok":
            output = value
            size = int(size_value)
            status_var.set(f"已生成：{output}（{human_size(size)}，{size} 字节）")
            messagebox.showinfo("生成成功", f"文件已生成并通过格式验证：\n\n{output}\n\n精确大小：{size} 字节")
        else:
            exc = value
            status_var.set(str(exc))
            messagebox.showerror("生成失败", str(exc))

    def start_create() -> None:
        import threading
        create_button.configure(state="disabled")
        status_var.set("正在生成并验证…")
        progress.start(12)
        threading.Thread(target=worker, daemon=True).start()
        root.after(100, poll_result)

    create_button = ttk.Button(main, text="生成文件", command=start_create)
    create_button.grid(row=10, column=2, sticky="e", padx=(12, 0))
    root.bind("<Return>", lambda _event: start_create())
    root.mainloop()


def launch_gui() -> None:
    """Launch the modern application, falling back to standard Tk when needed."""
    try:
        from fileforge_app import launch_gui as launch_modern_gui

        launch_modern_gui()
    except ModuleNotFoundError as exc:
        if exc.name not in {"customtkinter", "darkdetect", "packaging"}:
            raise
        _launch_legacy_gui()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fileforge",
        description="生成指定格式、指定精确大小、可正常打开的空白文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n  fileforge blank.pdf 10MB\n  fileforge demo.docx 1MiB --overwrite\n  fileforge data.any 2GB --format generic",
    )
    parser.add_argument("output", nargs="?", help="输出文件路径")
    parser.add_argument("size", nargs="?", help="目标大小，如 512B、10MB、1.5GiB")
    parser.add_argument("-f", "--format", help="格式名或扩展名；默认从输出扩展名识别")
    parser.add_argument("-o", "--overwrite", action="store_true", help="覆盖已有文件")
    parser.add_argument("--verify", metavar="FILE", help="验证已有文件，不生成")
    parser.add_argument("--deep", action="store_true", help="验证 ZIP 时读取并校验全部条目")
    parser.add_argument("--list-formats", action="store_true", help="列出支持格式")
    parser.add_argument("--gui", action="store_true", help="启动图形界面")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.list_formats:
            print(format_table())
            return 0
        if args.verify:
            ok, message = verify_file(args.verify, args.format, args.deep)
            print(("OK: " if ok else "ERROR: ") + message)
            return 0 if ok else 2
        if args.gui or (args.output is None and args.size is None):
            launch_gui()
            return 0
        if not args.output or not args.size:
            parser.error("必须同时提供 output 和 size，或者使用 --gui")
        size = parse_size(args.size)
        output, spec = create_file(args.output, size, args.format, args.overwrite)
        ok, detail = verify_file(output, spec.name, deep=args.deep and size <= 512 * 1024 * 1024)
        if not ok:
            raise FileForgeError(detail)
        print(f"已生成 {output}")
        print(f"格式：{spec.name.upper()}  大小：{size} 字节（{human_size(size)}）")
        return 0
    except FileForgeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

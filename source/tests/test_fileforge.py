import bz2
import gzip
import lzma
import sqlite3
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fileforge


class ParseSizeTests(unittest.TestCase):
    def test_units(self):
        self.assertEqual(fileforge.parse_size("10MB"), 10 * 1024 * 1024)
        self.assertEqual(fileforge.parse_size("1.5 KiB"), 1536)
        self.assertEqual(fileforge.parse_size("10MB1000"), 10_000_000)
        self.assertEqual(fileforge.parse_size("42"), 42)

    def test_arbitrary_decimals_are_rounded_to_a_byte(self):
        self.assertEqual(fileforge.parse_size("0.1B"), 1)
        self.assertEqual(fileforge.parse_size(".1MB"), 104_858)
        self.assertEqual(fileforge.parse_size("1.234MB"), 1_293_943)
        self.assertEqual(fileforge.parse_size("2.71828KB"), 2_784)


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, name, size, fmt=None):
        path = self.directory / name
        fileforge.create_file(path, size, fmt)
        self.assertEqual(path.stat().st_size, size)
        ok, detail = fileforge.verify_file(path, fmt, deep=True)
        self.assertTrue(ok, detail)
        return path

    def test_exact_sizes_for_core_formats(self):
        cases = [
            ("blank.txt", 17), ("blank.json", 31), ("blank.xml", 128),
            ("blank.png", 4096), ("blank.jpg", 4096), ("blank.gif", 4096),
            ("blank.bmp", 4096), ("blank.tiff", 4096), ("blank.ico", 4096),
            ("blank.pdf", 8192), ("blank.wav", 8192), ("blank.flac", 8192),
            ("blank.mp3", 8192), ("blank.mid", 1024), ("blank.mp4", 8192),
            ("blank.avi", 8192), ("blank.tar", 4096),
            ("blank.gz", 4096), ("blank.bz2", 4096), ("blank.xz", 4096),
        ]
        for name, size in cases:
            with self.subTest(name=name):
                self.create(name, size)

    def test_every_declared_minimum_is_constructible(self):
        for spec in fileforge.SPECS:
            with self.subTest(format=spec.name, minimum=spec.minimum):
                extension = spec.extensions[0]
                self.create(f"minimum-{spec.name}.{extension}", spec.minimum, spec.name)

    def test_zip_family_opens_and_crc_matches(self):
        for extension in ("zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "jar"):
            with self.subTest(extension=extension):
                path = self.create(f"blank.{extension}", 32_768)
                with zipfile.ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())

    def test_compressed_streams_decompress(self):
        modules = {"gz": gzip, "bz2": bz2, "xz": lzma}
        for extension, module in modules.items():
            with self.subTest(extension=extension):
                path = self.create(f"blank.{extension}", 4096)
                self.assertEqual(module.decompress(path.read_bytes()), b"")

    def test_wav_opens(self):
        path = self.create("blank.wav", 16_384)
        with wave.open(str(path), "rb") as audio:
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getsampwidth(), 2)

    def test_sqlite_opens(self):
        path = self.create("blank.sqlite", 8192)
        connection = sqlite3.connect(path)
        try:
            connection.execute("SELECT name FROM sqlite_master").fetchall()
        finally:
            connection.close()

    def test_unknown_format_requires_generic(self):
        with self.assertRaises(fileforge.FileForgeError):
            fileforge.create_file(self.directory / "blank.unknown", 100)
        self.create("blank.unknown", 100, "generic")

    def test_no_overwrite_by_default(self):
        path = self.create("blank.bin", 10)
        with self.assertRaises(fileforge.FileForgeError):
            fileforge.create_file(path, 20)


if __name__ == "__main__":
    unittest.main()

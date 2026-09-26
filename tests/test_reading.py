"""
How much of a file extraction actually reads.

The metadata in a motion photo says where the clip begins, so there is no reason
to pull the JPEG data in between off the disk. These tests pin both halves of
that: the bytes recovered must be identical to reading the whole file, and the
amount read must stay small.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motionextract import core  # noqa: E402
from motionextract.core import (  # noqa: E402
    ExtractionError,
    find_video,
    find_video_in_file,
    output_dir_for,
    probe_video_in_file,
)
from tests import fixtures  # noqa: E402


class _CountingHandle:
    """A file handle that adds up everything read through it."""

    def __init__(self, handle, tally: CountingPath) -> None:
        self._handle = handle
        self._tally = tally

    def read(self, size: int = -1) -> bytes:
        chunk = self._handle.read(size)
        self._tally.bytes_read += len(chunk)
        return chunk

    def seek(self, *args: int) -> int:
        return self._handle.seek(*args)

    def __enter__(self) -> _CountingHandle:
        return self

    def __exit__(self, *exc: object) -> None:
        self._handle.close()


class CountingPath:
    """
    A stand-in for Path that records how many bytes came off the disk.

    The reading functions only ever call `.open('rb')` on the path they are
    given, which makes this enough to measure them honestly.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.bytes_read = 0

    def open(self, mode: str = 'rb') -> _CountingHandle:
        return _CountingHandle(self.path.open(mode), self)


class ReadingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.video = fixtures.realistic_mp4()

    def write(self, data: bytes, name: str = 'PXL_0001.MP.jpg') -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path


class TestSameResultAsReadingEverything(ReadingTestCase):
    """Reading less must not change what comes back."""

    def test_matches_whole_file_read_for_every_format(self) -> None:
        for name, build in (
            ('new', fixtures.new_format),
            ('old', fixtures.old_format),
            ('fallback', fixtures.fallback_only),
        ):
            with self.subTest(fmt=name):
                data = build(self.video)
                path = self.write(data, f'{name}.jpg')
                self.assertEqual(find_video_in_file(path), find_video(data))

    def test_recovered_clip_is_byte_identical_to_the_original(self) -> None:
        path = self.write(fixtures.new_format(self.video, body_size=500_000))
        self.assertEqual(find_video_in_file(path), self.video)

    def test_plain_jpeg_is_not_a_motion_photo(self) -> None:
        path = self.write(fixtures.plain_jpeg())
        self.assertIsNone(find_video_in_file(path))
        self.assertIsNone(probe_video_in_file(path))

    def test_markers_without_a_clip_still_raise(self) -> None:
        path = self.write(fixtures.markers_without_video())
        with self.assertRaises(ExtractionError):
            find_video_in_file(path)
        with self.assertRaises(ExtractionError):
            probe_video_in_file(path)


class TestBytesRead(ReadingTestCase):
    """The point of the exercise: less disk traffic on a slow drive."""

    def test_extraction_skips_the_jpeg_body(self) -> None:
        data = fixtures.new_format(self.video, body_size=4_000_000)
        counting = CountingPath(self.write(data))

        video = find_video_in_file(counting)

        self.assertEqual(video, self.video)
        # The header plus the clip, and none of the four megabytes between them.
        ceiling = core.HEADER_BYTES + len(self.video) + 1024
        self.assertLess(counting.bytes_read, ceiling)
        self.assertLess(counting.bytes_read, len(data) // 2)

    def test_dry_run_probe_reads_only_the_header(self) -> None:
        data = fixtures.new_format(self.video, body_size=4_000_000)
        counting = CountingPath(self.write(data))

        self.assertEqual(probe_video_in_file(counting), len(self.video))

        # The header and the eight bytes of the clip's first box. Nothing else.
        self.assertLess(counting.bytes_read, core.HEADER_BYTES + 64)

    def test_probe_is_far_cheaper_than_extraction(self) -> None:
        data = fixtures.new_format(self.video, body_size=4_000_000)
        path = self.write(data)

        probe, extract = CountingPath(path), CountingPath(path)
        probe_video_in_file(probe)
        find_video_in_file(extract)

        self.assertLess(probe.bytes_read, extract.bytes_read)
        self.assertLess(probe.bytes_read, len(data) // 10)


class TestTailSearch(ReadingTestCase):
    """
    The fallback has to scan, so it reads a window at the end of the file and
    widens to the whole file only if that misses.
    """

    def test_fallback_finds_the_clip_inside_the_tail_window(self) -> None:
        data = fixtures.fallback_only(self.video, body_size=200_000)
        counting = CountingPath(self.write(data))

        self.assertEqual(find_video_in_file(counting), self.video)

    def test_fallback_widens_to_the_whole_file_when_the_window_is_too_small(self) -> None:
        data = fixtures.fallback_only(self.video, body_size=200_000)
        path = self.write(data)

        original = core.TAIL_SEARCH_BYTES
        # Smaller than the clip, so the first window cannot possibly contain it.
        core.TAIL_SEARCH_BYTES = 64
        self.addCleanup(setattr, core, 'TAIL_SEARCH_BYTES', original)

        self.assertEqual(find_video_in_file(path), self.video)

    def test_a_clip_larger_than_the_window_is_still_recovered_whole(self) -> None:
        big = fixtures.mp4_box(b'ftyp', b'isom' * 4) + fixtures.mp4_box(b'mdat', b'D' * 50_000)
        path = self.write(fixtures.fallback_only(big))

        original = core.TAIL_SEARCH_BYTES
        core.TAIL_SEARCH_BYTES = 1024
        self.addCleanup(setattr, core, 'TAIL_SEARCH_BYTES', original)

        self.assertEqual(find_video_in_file(path), big)


class TestHeaderWindow(ReadingTestCase):
    """
    Detection reads the head of the file. That is sound because the XMP lives
    there, and this records the edge of the assumption deliberately.
    """

    def test_markers_are_found_anywhere_inside_the_header(self) -> None:
        padding = b'<!-- ' + b'x' * (core.HEADER_BYTES - 2048) + b' -->'
        data = fixtures.JPEG_HEAD + padding + (
            b'<x:xmpmeta GCamera:MicroVideo="1" GCamera:MicroVideoOffset="'
            + str(len(self.video)).encode() + b'"/>'
        ) + fixtures.JPEG_TAIL + self.video
        path = self.write(data)

        self.assertEqual(find_video_in_file(path), self.video)

    def test_a_short_file_is_read_in_full(self) -> None:
        data = fixtures.new_format(self.video)
        self.assertLess(len(data), core.HEADER_BYTES)
        counting = CountingPath(self.write(data))

        self.assertEqual(find_video_in_file(counting), self.video)


class TestOutputDirFor(unittest.TestCase):
    """Flat by default, mirrored on request."""

    def setUp(self) -> None:
        self.source = Path('/photos')
        self.out = Path('/photos/extracted_videos')

    def test_flat_puts_everything_in_one_folder(self) -> None:
        photo = self.source / '2025-Wedding' / 'PXL_0001.jpg'
        self.assertEqual(output_dir_for(photo, self.source, self.out, False), self.out)

    def test_mirror_recreates_the_subfolder(self) -> None:
        photo = self.source / '2025-Wedding' / 'PXL_0001.jpg'
        self.assertEqual(
            output_dir_for(photo, self.source, self.out, True),
            self.out / '2025-Wedding',
        )

    def test_mirror_leaves_top_level_photos_at_the_top(self) -> None:
        photo = self.source / 'PXL_0001.jpg'
        self.assertEqual(output_dir_for(photo, self.source, self.out, True), self.out)

    def test_a_photo_outside_the_source_falls_back_to_flat(self) -> None:
        photo = Path('/elsewhere/PXL_0001.jpg')
        self.assertEqual(output_dir_for(photo, self.source, self.out, True), self.out)


if __name__ == '__main__':
    unittest.main()

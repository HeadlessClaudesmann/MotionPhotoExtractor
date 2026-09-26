"""Format handling and output-path decisions."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motionextract.core import (  # noqa: E402
    ExtractionError,
    _plausible_box_at,
    claim_output_path,
    extract_to,
    find_video,
    find_video_fallback,
    is_motion_photo,
    planned_output_path,
)
from tests import fixtures  # noqa: E402


class TestFindVideo(unittest.TestCase):
    def setUp(self) -> None:
        self.video = fixtures.realistic_mp4()

    def test_new_format_is_byte_identical(self) -> None:
        self.assertEqual(find_video(fixtures.new_format(self.video)), self.video)

    def test_old_format_is_byte_identical(self) -> None:
        self.assertEqual(find_video(fixtures.old_format(self.video)), self.video)

    def test_fallback_is_byte_identical(self) -> None:
        self.assertEqual(find_video(fixtures.fallback_only(self.video)), self.video)

    def test_plain_jpeg_yields_nothing(self) -> None:
        self.assertIsNone(find_video(fixtures.plain_jpeg()))

    def test_markers_without_video_raise(self) -> None:
        with self.assertRaises(ExtractionError):
            find_video(fixtures.markers_without_video())

    def test_is_motion_photo_detects_both_generations(self) -> None:
        self.assertTrue(is_motion_photo(fixtures.new_format(self.video)))
        self.assertTrue(is_motion_photo(fixtures.old_format(self.video)))
        self.assertFalse(is_motion_photo(fixtures.plain_jpeg()))


class TestFallbackAnchoring(unittest.TestCase):
    """
    Regression cover for a fallback that anchored on the last box marker in the
    file. Since a clip is ftyp, moov, then mdat, that started the output at
    `mdat` and silently discarded the container header.
    """

    def test_fallback_starts_at_ftyp_not_mdat(self) -> None:
        video = fixtures.realistic_mp4()
        found = find_video_fallback(fixtures.fallback_only(video))
        self.assertIsNotNone(found)
        self.assertEqual(found[4:8], b'ftyp')
        self.assertEqual(len(found), len(video))

    def test_fallback_keeps_header_when_ftyp_is_absent(self) -> None:
        from tests.fixtures import JPEG_HEAD, JPEG_TAIL, mp4_box

        headerless = mp4_box(b'moov', b'M' * 200) + mp4_box(b'mdat', b'D' * 900)
        data = JPEG_HEAD + b'GCamera:MotionPhoto="1"' + b'\x00' * 512 + JPEG_TAIL + headerless
        found = find_video_fallback(data)
        self.assertEqual(found[4:8], b'moov')
        self.assertEqual(len(found), len(headerless))

    def test_plausible_box_rejects_a_nonsense_size(self) -> None:
        # 0xffffffff is far longer than the data that follows it.
        data = b'\xff\xff\xff\xffftyp' + b'\x00' * 16
        self.assertFalse(_plausible_box_at(data, data.find(b'ftyp')))

    def test_plausible_box_accepts_a_real_size(self) -> None:
        box = fixtures.mp4_box(b'ftyp', b'isom')
        self.assertTrue(_plausible_box_at(box, box.find(b'ftyp')))


class TestOutputPaths(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_fresh_destination_is_not_already_done(self) -> None:
        dest, already = claim_output_path(self.out, 'PXL_0001', {})
        self.assertEqual(dest, planned_output_path(self.out, 'PXL_0001'))
        self.assertFalse(already)

    def test_existing_file_reports_already_done(self) -> None:
        planned_output_path(self.out, 'PXL_0001').write_bytes(b'x')
        dest, already = claim_output_path(self.out, 'PXL_0001', {})
        self.assertTrue(already)
        self.assertEqual(dest.name, 'PXL_0001_video.mp4')

    def test_second_photo_of_a_stem_gets_a_counter_not_a_skip(self) -> None:
        """Two different photos sharing a filename must both be written."""
        seen: dict[str, int] = {}
        first, _ = claim_output_path(self.out, 'PXL_0001', seen)
        first.write_bytes(b'x')
        second, already = claim_output_path(self.out, 'PXL_0001', seen)
        self.assertFalse(already)
        self.assertEqual(second.name, 'PXL_0001_video_2.mp4')

    def test_the_same_photos_claim_the_same_names_every_run(self) -> None:
        """What makes a repeat run a no-op rather than a fresh set of copies."""
        def one_pass() -> list[str]:
            seen: dict[str, int] = {}
            return [claim_output_path(self.out, stem, seen)[0].name
                    for stem in ('PXL_0001', 'PXL_0001', 'PXL_0002')]

        self.assertEqual(
            one_pass(),
            ['PXL_0001_video.mp4', 'PXL_0001_video_2.mp4', 'PXL_0002_video.mp4'])
        self.assertEqual(one_pass(), one_pass())

    def test_counters_are_tracked_per_stem(self) -> None:
        seen: dict[str, int] = {}
        claim_output_path(self.out, 'PXL_0001', seen)
        dest, _ = claim_output_path(self.out, 'PXL_0002', seen)
        self.assertEqual(dest.name, 'PXL_0002_video.mp4')


class TestExtractTo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.video = fixtures.realistic_mp4()

    def test_writes_exactly_the_requested_destination(self) -> None:
        photo = self.root / 'PXL_0001.jpg'
        photo.write_bytes(fixtures.new_format(self.video))
        dest = self.root / 'out' / 'nested' / 'clip.mp4'

        written = extract_to(photo, dest)
        self.assertEqual(written, dest)
        self.assertEqual(dest.read_bytes(), self.video)

    def test_replaces_an_existing_destination(self) -> None:
        photo = self.root / 'PXL_0001.jpg'
        photo.write_bytes(fixtures.new_format(self.video))
        dest = self.root / 'clip.mp4'
        dest.write_bytes(b'stale')

        extract_to(photo, dest)
        self.assertEqual(dest.read_bytes(), self.video)

    def test_plain_jpeg_writes_nothing(self) -> None:
        photo = self.root / 'plain.jpg'
        photo.write_bytes(fixtures.plain_jpeg())
        dest = self.root / 'clip.mp4'

        self.assertIsNone(extract_to(photo, dest))
        self.assertFalse(dest.exists())


if __name__ == '__main__':
    unittest.main()

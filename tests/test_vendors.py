"""
Coverage of the layouts different manufacturers use.

There is no standard for motion photos, and the two conventions in the wild put
their metadata at opposite ends of the file: Google's in the XMP at the head,
Samsung's in an index appended to the very end. These tests pin that both are
found, that a Samsung trailer without a clip in it is not mistaken for a broken
motion photo, and that a clip from a vendor we have never heard of is still
recovered as long as it is appended in the usual way.

The Samsung format has no public specification, so the fixtures are built from
reverse-engineered accounts of it. That makes the cases where our reading of the
index turns out to be wrong the important ones: they must cost speed, never
correctness.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from motionextract.core import (
    ExtractionError, find_video, find_video_in_file, probe_video_in_file,
)
from tests import fixtures
from tests.test_reading import CountingPath


class VendorCase(unittest.TestCase):
    """Writes a fixture to disk, since the readers work on paths, not bytes."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.video = fixtures.realistic_mp4()

    def photo(self, data: bytes) -> Path:
        path = self.dir / 'photo.jpg'
        path.write_bytes(data)
        return path

    def assert_recovers_clip(self, data: bytes) -> None:
        """The clip comes back whole, and the dry run agrees about its size."""
        path = self.photo(data)
        self.assertEqual(find_video_in_file(path), self.video)
        self.assertEqual(probe_video_in_file(path), len(self.video))
        self.assertEqual(find_video(data), self.video)

    def assert_reads_as_plain_photo(self, data: bytes) -> None:
        path = self.photo(data)
        self.assertIsNone(find_video_in_file(path))
        self.assertIsNone(probe_video_in_file(path))
        self.assertIsNone(find_video(data))

    def bytes_read(self, data: bytes) -> tuple[int, int]:
        """What extracting and dry-running this file costs off the disk."""
        path = CountingPath(self.photo(data))
        find_video_in_file(path)
        extract = path.bytes_read

        path = CountingPath(self.photo(data))
        probe_video_in_file(path)
        return extract, path.bytes_read


class TestGoogleConvention(VendorCase):
    """Pixel, and the vendors that follow it -- Motorola, OnePlus, Xiaomi."""

    def test_new_format_is_recovered(self) -> None:
        self.assert_recovers_clip(fixtures.new_format(self.video))

    def test_old_format_is_recovered(self) -> None:
        self.assert_recovers_clip(fixtures.old_format(self.video))


class TestSamsung(VendorCase):

    def test_sef_index_locates_the_clip(self) -> None:
        self.assert_recovers_clip(fixtures.samsung_motion_photo(self.video))

    def test_older_bare_marker_layout_is_recovered(self) -> None:
        """No trailer at all: the name sits in front of the clip and nowhere else."""
        self.assert_recovers_clip(fixtures.samsung_bare_marker(self.video))

    def test_a_clip_far_from_both_ends_is_still_found(self) -> None:
        """
        The whole point of the two-ended read.

        With a large photo body, Samsung's marker is megabytes from the head of
        the file, so a reader that only inspects the head reports an ordinary
        JPEG and loses the clip silently.
        """
        self.assert_recovers_clip(
            fixtures.samsung_bare_marker(self.video, body_size=500_000))
        self.assert_recovers_clip(
            fixtures.samsung_motion_photo(self.video, body_size=500_000))

    def test_the_trailer_is_not_written_into_the_mp4(self) -> None:
        """
        Samsung's index sits after the clip, so taking everything to the end of
        the file would append it to the video. The box sizes say where the clip
        really stops.
        """
        path = self.photo(fixtures.samsung_motion_photo(self.video))
        recovered = find_video_in_file(path)
        self.assertEqual(recovered, self.video)
        self.assertNotIn(fixtures.SEF_TAIL, recovered)

    def test_a_misread_index_still_yields_the_whole_clip(self) -> None:
        """
        If our reading of the undocumented index is off, extraction must fall
        back to scanning rather than write a clip cut off at the wrong place --
        an MP4 that looks fine until someone tries to play it.
        """
        for error in (-64, -32, 32, 64):
            with self.subTest(offset_error=error):
                self.assert_recovers_clip(
                    fixtures.samsung_motion_photo(self.video, offset_error=error))

    def test_a_trailer_without_a_clip_is_an_ordinary_photo(self) -> None:
        """Samsung writes a trailer for other camera features too."""
        self.assert_reads_as_plain_photo(fixtures.samsung_sound_shot())


class TestUnknownVendors(VendorCase):

    def test_a_clip_appended_without_metadata_is_recovered(self) -> None:
        self.assert_recovers_clip(fixtures.appended_clip_only(self.video))

    def test_padding_after_the_photo_is_not_mistaken_for_a_clip(self) -> None:
        self.assert_reads_as_plain_photo(fixtures.plain_jpeg_with_padding())

    def test_an_ordinary_photo_is_still_an_ordinary_photo(self) -> None:
        self.assert_reads_as_plain_photo(fixtures.plain_jpeg())

    def test_google_markers_without_a_clip_still_fail_loudly(self) -> None:
        """
        A file that says it holds a clip and does not is a real failure, worth
        reporting. That is different from a file that never claimed to.
        """
        path = self.photo(fixtures.markers_without_video())
        with self.assertRaises(ExtractionError):
            find_video_in_file(path)
        with self.assertRaises(ExtractionError):
            probe_video_in_file(path)


class TestCostOfTheVendorPaths(unittest.TestCase):
    """
    What the vendor fast paths are for: not reading the file.

    The photos live on an external disk, so bytes read is the number that decides
    how long a run takes. These are the tests that catch a refactor quietly turning
    a seek into a full read -- easy to do, and invisible until a folder of a few
    thousand photos takes twenty minutes.

    The sizes here are those of a real motion photo, because the thing being
    measured is a fixed-size window: against a 5 KB fixture, reading 192 KB of
    windows would look like a catastrophe and reading the whole file like a win.
    """

    PHOTO = 4_000_000          # a 12 MP JPEG
    CLIP = 2_000_000           # about three seconds of 1080p

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / 'photo.jpg'
        self.video = fixtures.realistic_mp4(self.CLIP)

    def costs(self, data: bytes) -> tuple[int, int]:
        """Bytes read by an extraction and by a dry run of the same file."""
        self.path.write_bytes(data)

        counted = CountingPath(self.path)
        found = find_video_in_file(counted)
        extract = counted.bytes_read

        counted = CountingPath(self.path)
        size = probe_video_in_file(counted)

        # Measuring a wrong answer would be measuring nothing.
        self.assertEqual(found, self.video)
        self.assertEqual(size, len(self.video))
        return extract, counted.bytes_read

    def test_google_reads_the_clip_and_little_else(self) -> None:
        extract, dry = self.costs(fixtures.new_format(self.video, body_size=self.PHOTO))
        self.assertLess(extract - self.CLIP, 200_000)
        self.assertLess(dry, 200_000)

    def test_samsung_reads_the_clip_and_little_else(self) -> None:
        """
        The index puts us straight at the clip, so the photo body is never touched.

        Extraction has to read the clip itself; what matters is that it reads
        essentially nothing besides.
        """
        extract, dry = self.costs(
            fixtures.samsung_motion_photo(self.video, body_size=self.PHOTO))
        self.assertLess(extract - self.CLIP, 200_000)
        self.assertLess(dry, 200_000)

    def test_a_dry_run_never_reads_the_clip(self) -> None:
        """
        A dry run needs the clip's size, not its bytes, and the box headers give it.

        Following the box chain by seeking is what makes this cheap. Reading the
        whole region the index points at would cost the entire clip for a number we
        could have had in four eight-byte reads.
        """
        for name, build in (
            ('google', fixtures.new_format),
            ('samsung', fixtures.samsung_motion_photo),
        ):
            with self.subTest(vendor=name):
                _, dry = self.costs(build(self.video, body_size=self.PHOTO))
                self.assertLess(dry, self.CLIP // 4)

    def assert_dismissed_cheaply(self, data: bytes) -> None:
        """A photo with no clip must be ruled out without reading it."""
        self.path.write_bytes(data)
        counted = CountingPath(self.path)
        self.assertIsNone(find_video_in_file(counted))
        self.assertLess(counted.bytes_read, 300_000)

    def test_an_ordinary_photo_is_cheap_to_dismiss(self) -> None:
        self.assert_dismissed_cheaply(fixtures.plain_jpeg(body_size=self.PHOTO))

    def test_a_samsung_trailer_without_a_clip_is_cheap_to_dismiss(self) -> None:
        """
        Ordinary Galaxy photos carry a trailer too, and there are a lot of them.

        Samsung lists every block it appended, so an index that names no clip
        settles the question without scanning. Without that, every ordinary photo
        from a Galaxy would be read in full to prove a negative.
        """
        self.assert_dismissed_cheaply(fixtures.samsung_sound_shot(body_size=self.PHOTO))


if __name__ == '__main__':
    unittest.main()

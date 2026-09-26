"""End-to-end CLI behaviour, driven through main() with a temporary folder."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motionextract.cli import main  # noqa: E402
from tests import fixtures  # noqa: E402


def run(*argv: str) -> tuple[int, str]:
    """Invoke the CLI, returning its exit code and combined output."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(list(argv))
    return code, out.getvalue()


class CLITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.video = fixtures.realistic_mp4()

    def photo(self, name: str, data: bytes | None = None) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if data is not None else fixtures.new_format(self.video))
        return path

    def videos(self) -> list[str]:
        out = self.root / 'extracted_videos'
        return sorted(p.name for p in out.glob('*.mp4')) if out.exists() else []


class TestRerun(CLITestCase):
    """
    Regression cover for a second run duplicating every video. Re-running after
    adding a few photos used to re-extract the whole folder into _2 copies.
    """

    def test_second_run_writes_nothing_new(self) -> None:
        self.photo('PXL_0001.jpg')
        self.photo('PXL_0002.jpg')

        code, first = run(str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(self.videos(), ['PXL_0001_video.mp4', 'PXL_0002_video.mp4'])

        code, second = run(str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(self.videos(), ['PXL_0001_video.mp4', 'PXL_0002_video.mp4'])
        self.assertIn('[==]', second)
        self.assertIn('2 already extracted', second)
        self.assertNotIn('_video_2.mp4', second)

    def test_second_run_only_does_the_new_photo(self) -> None:
        self.photo('PXL_0001.jpg')
        run(str(self.root))

        self.photo('PXL_0002.jpg')
        code, out = run(str(self.root))

        self.assertEqual(code, 0)
        self.assertEqual(self.videos(), ['PXL_0001_video.mp4', 'PXL_0002_video.mp4'])
        self.assertIn('1 video(s) extracted', out)
        self.assertIn('1 already extracted', out)

    def test_overwrite_replaces_in_place(self) -> None:
        self.photo('PXL_0001.jpg')
        run(str(self.root))
        target = self.root / 'extracted_videos' / 'PXL_0001_video.mp4'
        target.write_bytes(b'corrupted')

        code, out = run(str(self.root), '--overwrite')

        self.assertEqual(code, 0)
        self.assertEqual(self.videos(), ['PXL_0001_video.mp4'])
        self.assertEqual(target.read_bytes(), self.video)
        self.assertIn('1 video(s) extracted', out)


class TestRecursiveCollisions(CLITestCase):
    def test_same_filename_in_two_subfolders_both_extract(self) -> None:
        """A shared camera filename is a collision, not work already done."""
        self.photo('2024-Holiday/PXL_0001.jpg')
        self.photo('2025-Wedding/PXL_0001.jpg', fixtures.old_format(self.video))

        code, out = run(str(self.root), '-r')

        self.assertEqual(code, 0)
        self.assertEqual(
            self.videos(), ['PXL_0001_video.mp4', 'PXL_0001_video_2.mp4'])
        self.assertNotIn('[==]', out)

    def test_recursive_rerun_is_still_idempotent(self) -> None:
        self.photo('2024-Holiday/PXL_0001.jpg')
        self.photo('2025-Wedding/PXL_0001.jpg', fixtures.old_format(self.video))
        run(str(self.root), '-r')

        code, out = run(str(self.root), '-r')

        self.assertEqual(code, 0)
        self.assertEqual(
            self.videos(), ['PXL_0001_video.mp4', 'PXL_0001_video_2.mp4'])
        self.assertIn('2 already extracted', out)


class TestReporting(CLITestCase):
    def test_plain_jpeg_is_reported_as_skipped(self) -> None:
        self.photo('plain.jpg', fixtures.plain_jpeg())
        code, out = run(str(self.root))
        self.assertEqual(code, 0)
        self.assertIn('[--]', out)
        self.assertIn('1 non-motion JPEG(s) skipped', out)

    def test_unrecoverable_video_fails_without_stopping_the_run(self) -> None:
        self.photo('broken.jpg', fixtures.markers_without_video())
        self.photo('PXL_0001.jpg')

        code, out = run(str(self.root))

        self.assertEqual(code, 2)
        self.assertIn('[!!]', out)
        self.assertIn('1 failed', out)
        self.assertEqual(self.videos(), ['PXL_0001_video.mp4'])

    def test_non_ascii_filename_survives(self) -> None:
        self.photo('PXL_café_ümläut.jpg')
        code, out = run(str(self.root))
        self.assertEqual(code, 0)
        self.assertEqual(self.videos(), ['PXL_café_ümläut_video.mp4'])

    def test_dry_run_writes_nothing(self) -> None:
        self.photo('PXL_0001.jpg')
        code, out = run(str(self.root), '--dry-run')
        self.assertEqual(code, 0)
        self.assertEqual(self.videos(), [])
        self.assertIn('would extract', out)

    def test_output_same_puts_videos_beside_the_photos(self) -> None:
        self.photo('PXL_0001.jpg')
        code, _ = run(str(self.root), '-o', 'same')
        self.assertEqual(code, 0)
        self.assertTrue((self.root / 'PXL_0001_video.mp4').exists())

    def test_missing_path_is_an_input_error(self) -> None:
        code, out = run(str(self.root / 'nope'))
        self.assertEqual(code, 1)
        self.assertIn('path not found', out)

    def test_non_jpeg_file_is_an_input_error(self) -> None:
        notes = self.root / 'notes.txt'
        notes.write_text('hello')
        code, out = run(str(notes))
        self.assertEqual(code, 1)
        self.assertIn('not a JPEG', out)


if __name__ == '__main__':
    unittest.main()

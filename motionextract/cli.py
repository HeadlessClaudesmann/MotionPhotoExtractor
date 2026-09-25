"""
Command line interface.

Run `motionextract` with no arguments in a folder of photos and it does the
obvious thing: extracts every motion photo it finds into ./extracted_videos.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .core import (
    JPEG_EXTENSIONS,
    ExtractionError,
    collect_jpegs,
    extract_video,
    find_video,
)

DEFAULT_OUTPUT_DIRNAME = 'extracted_videos'


def _make_console_safe() -> None:
    """
    Stop a non-ASCII filename from killing the run on a legacy Windows console.

    Windows terminals frequently default to cp1252, which raises
    UnicodeEncodeError the moment we print a filename containing an accent or
    emoji. Replacing unmappable characters is far better than a traceback.

    Line buffering is set at the same time so that per-file errors on stderr
    stay in step with the progress lines on stdout when output is redirected.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
            except (OSError, ValueError):
                pass


def _resolve_output_dir(raw: str | None, source_dir: Path) -> Path:
    if raw is None:
        return source_dir / DEFAULT_OUTPUT_DIRNAME
    if raw.lower() in ('same', '.'):
        return source_dir
    return Path(raw).expanduser().resolve()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='motionextract',
        description='Extract the embedded MP4 video from Google Pixel motion photos.',
        epilog=(
            'examples:\n'
            '  motionextract                     current folder -> ./extracted_videos\n'
            '  motionextract -r                  current folder and subfolders\n'
            '  motionextract photo.jpg           one file\n'
            '  motionextract ~/Photos            a specific folder\n'
            '  motionextract -o same             save next to the originals\n'
            '  motionextract --dry-run           report what would happen, write nothing\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'path', nargs='?', default='.',
        help='JPEG file or folder of photos (default: the current folder)')
    parser.add_argument(
        '--output', '-o', metavar='DIR',
        help=f'where to save videos. Use "same" for alongside the originals. '
             f'(default: an {DEFAULT_OUTPUT_DIRNAME}/ subfolder)')
    parser.add_argument(
        '--recursive', '-r', action='store_true',
        help='include subfolders')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='list what would be extracted without writing any files')
    parser.add_argument(
        '--version', action='version', version=f'motionextract {__version__}')
    return parser


def main(argv: list[str] | None = None) -> int:
    _make_console_safe()
    args = _build_parser().parse_args(argv)

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        print(f'[error] path not found: {target}', file=sys.stderr)
        return 1

    if target.is_file():
        if target.suffix.lower() not in JPEG_EXTENSIONS:
            print(f'[error] not a JPEG: {target.name}', file=sys.stderr)
            return 1
        jpegs = [target]
        source_dir = target.parent
    else:
        source_dir = target
        jpegs = collect_jpegs(target, args.recursive)
        if not jpegs:
            where = 'folder or its subfolders' if args.recursive else 'folder'
            print(f'[!] no JPEG files found in this {where}: {target}')
            return 0

    output_dir = _resolve_output_dir(args.output, source_dir)

    scope = ' (including subfolders)' if args.recursive else ''
    print(f'[>>] {len(jpegs)} JPEG(s) found in {target}{scope}')
    print(f'[>>] output: {output_dir}')
    if args.dry_run:
        print('[>>] dry run, nothing will be written')
    print()

    extracted = skipped = failed = 0

    for jpeg in jpegs:
        try:
            if args.dry_run:
                found = find_video(jpeg.read_bytes())
                if found is None:
                    skipped += 1
                    print(f'  [--] {jpeg.name}')
                else:
                    extracted += 1
                    print(f'  [OK] {jpeg.name}  ->  would write '
                          f'{len(found) / 1_048_576:.1f} MB')
                continue

            out_path = extract_video(jpeg, output_dir)
            if out_path is None:
                skipped += 1
                print(f'  [--] {jpeg.name}')
            else:
                extracted += 1
                size_mb = out_path.stat().st_size / 1_048_576
                print(f'  [OK] {jpeg.name}  ->  {out_path.name}  ({size_mb:.1f} MB)')

        except ExtractionError as exc:
            failed += 1
            print(f'  [!!] {jpeg.name}: {exc}', file=sys.stderr)
        except OSError as exc:
            failed += 1
            print(f'  [!!] {jpeg.name}: {exc.strerror or exc}', file=sys.stderr)

    verb = 'would extract' if args.dry_run else 'extracted'
    summary = f'\n[done] {extracted} video(s) {verb}, {skipped} non-motion JPEG(s) skipped'
    if failed:
        summary += f', {failed} failed'
    print(summary)

    return 2 if failed else 0


if __name__ == '__main__':
    sys.exit(main())

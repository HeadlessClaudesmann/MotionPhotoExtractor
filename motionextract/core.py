"""
Extraction core for Google Pixel motion photos.

Pure standard library. Shared by both the CLI and the GUI so that a fix to the
format handling only ever has to be made once.

Three strategies are tried in order:
  new format  GCamera:MotionPhoto + Container:Directory, Item:Length per item
  old format  MicroVideoOffset attribute (older Pixel firmware)
  fallback    backward scan for an MP4 box marker
"""

from __future__ import annotations

import re
from pathlib import Path

JPEG_EXTENSIONS = {'.jpg', '.jpeg'}
MP4_BOX_TYPES = (b'ftyp', b'moov', b'mdat')

# Below this, whatever we found is a stray marker rather than a real clip.
MIN_VIDEO_BYTES = 16


class ExtractionError(Exception):
    """The file looked like a motion photo but the video could not be recovered."""


def parse_container_items(data: bytes) -> list[dict]:
    """Parse every <Container:Item .../> block out of the embedded XMP."""
    items = []
    for match in re.finditer(rb'<Container:Item\b(.*?)/>', data, re.DOTALL):
        block = match.group(1)

        def attr(name: bytes, block: bytes = block) -> str:
            found = re.search(rb'Item:' + name + rb'="([^"]*)"', block)
            return found.group(1).decode() if found else ''

        items.append({
            'mime': attr(b'Mime'),
            'semantic': attr(b'Semantic'),
            'length': int(attr(b'Length') or 0),
            'padding': int(attr(b'Padding') or 0),
        })
    return items


def _looks_like_mp4(candidate: bytes) -> bool:
    return len(candidate) >= 8 and candidate[4:8] in MP4_BOX_TYPES


def find_video_new_format(data: bytes) -> bytes | None:
    """
    New format. Non-primary items are appended to the file in Container order,
    so walking them in reverse from EOF gives the video's start offset.
    """
    if b'GCamera:MotionPhoto' not in data:
        return None

    non_primary = [i for i in parse_container_items(data) if i['semantic'] != 'Primary']
    if not non_primary:
        return None

    offset_from_eof = 0
    for item in reversed(non_primary):
        offset_from_eof += item['padding'] + item['length']
        if 'video' in item['mime']:
            candidate = data[len(data) - offset_from_eof:]
            if _looks_like_mp4(candidate):
                return candidate
    return None


def find_video_old_format(data: bytes) -> bytes | None:
    """Old format: MicroVideoOffset is the byte count from EOF to the video start."""
    if b'MicroVideoOffset' not in data:
        return None
    match = re.search(rb'MicroVideoOffset="(\d+)"', data)
    if not match:
        return None
    candidate = data[len(data) - int(match.group(1)):]
    return candidate if _looks_like_mp4(candidate) else None


def find_video_fallback(data: bytes) -> bytes | None:
    """
    Last resort, used when the XMP is absent or unparseable. Finds the last box
    marker in the file via rfind rather than stepping back a byte at a time.
    """
    best = -1
    for box in MP4_BOX_TYPES:
        pos = data.rfind(box)
        if pos > best:
            best = pos
    # A box marker is preceded by its 4-byte length field.
    return data[best - 4:] if best > 4 else None


def is_motion_photo(data: bytes) -> bool:
    """Cheap check for the markers that indicate an embedded clip."""
    return b'MotionPhoto' in data or b'MicroVideo' in data


def find_video(data: bytes) -> bytes | None:
    """Recover the embedded MP4 from raw JPEG bytes, or None if there isn't one."""
    if not is_motion_photo(data):
        return None

    video = (
        find_video_new_format(data)
        or find_video_old_format(data)
        or find_video_fallback(data)
    )
    if video is None or len(video) < MIN_VIDEO_BYTES:
        raise ExtractionError('motion photo markers found but no MP4 data could be located')
    return video


def unique_output_path(out_dir: Path, stem: str) -> Path:
    """<out_dir>/<stem>_video.mp4, with a counter suffix if that already exists."""
    candidate = out_dir / f'{stem}_video.mp4'
    counter = 2
    while candidate.exists():
        candidate = out_dir / f'{stem}_video_{counter}.mp4'
        counter += 1
    return candidate


def extract_video(filepath: Path, output_dir: Path) -> Path | None:
    """
    Extract one JPEG's embedded clip into output_dir.

    Returns the written path, or None if the file simply isn't a motion photo.
    Raises ExtractionError if it is one but the video can't be recovered, and
    OSError if the file can't be read or the output can't be written.
    """
    data = filepath.read_bytes()
    video = find_video(data)
    if video is None:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = unique_output_path(output_dir, filepath.stem)
    out_path.write_bytes(video)
    return out_path


def collect_jpegs(target: Path, recursive: bool) -> list[Path]:
    """Every .jpg/.jpeg under target, sorted. Non-JPEGs are ignored entirely."""
    pattern = '**/*' if recursive else '*'
    return sorted(
        f for f in target.glob(pattern)
        if f.is_file() and f.suffix.lower() in JPEG_EXTENSIONS
    )

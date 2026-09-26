"""
Extraction core for Google Pixel motion photos.

Pure standard library. Shared by both the CLI and the GUI so that a fix to the
format handling only ever has to be made once.

Three strategies are tried in order:
  new format  GCamera:MotionPhoto + Container:Directory, Item:Length per item
  old format  MicroVideoOffset attribute (older Pixel firmware)
  fallback    locate the appended MP4 by its box markers
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


def _plausible_box_at(data: bytes, marker_pos: int) -> bool:
    """
    True if marker_pos is preceded by a credible 4-byte box size field.

    Compressed JPEG data can contain a box marker's four bytes by chance. The
    size field in front of a real box is a useful filter: per the MP4 spec it is
    the box length, or 0 for "runs to end of file", or 1 for "64-bit size
    follows the type".
    """
    if marker_pos < 4:
        return False
    start = marker_pos - 4
    size = int.from_bytes(data[start:start + 4], 'big')
    return size in (0, 1) or 8 <= size <= len(data) - start


def find_video_fallback(data: bytes) -> bytes | None:
    """
    Last resort, used when the XMP is absent or unparseable.

    An MP4 starts at its `ftyp` box, so that is what we anchor on. We take the
    last one: the clip is appended after all of the JPEG data, so any stray
    marker inside that data is necessarily earlier in the file.

    Only when there is no `ftyp` at all do we settle for the outermost of the
    remaining boxes. Anchoring on `mdat` -- which in a real clip sits after the
    container header -- would silently truncate that header and produce an
    unplayable file.
    """
    ftyp = data.rfind(b'ftyp')
    if ftyp >= 4 and _plausible_box_at(data, ftyp):
        return data[ftyp - 4:]

    fallbacks = [
        pos for pos in (data.rfind(b'moov'), data.rfind(b'mdat'))
        if pos >= 4 and _plausible_box_at(data, pos)
    ]
    return data[min(fallbacks) - 4:] if fallbacks else None


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


def planned_output_path(out_dir: Path, stem: str) -> Path:
    """The name this photo's clip gets when nothing is in the way."""
    return out_dir / f'{stem}_video.mp4'


def claim_output_path(
    out_dir: Path, stem: str, seen: dict[str, int],
) -> tuple[Path, bool]:
    """
    Claim the destination for one photo's clip, and say whether it already exists.

    Returns (destination, already_extracted).

    `seen` counts how many photos with each stem the caller has processed so far,
    and is updated here. The first photo with a given stem gets
    `<stem>_video.mp4`, the second `<stem>_video_2.mp4`, and so on -- so two
    different photos sharing a camera filename, as happens when a recursive run
    flattens several subfolders, both get written.

    Basing the suffix on position in the run rather than on the first free name
    is what makes a second run a no-op: photos are visited in sorted order, so
    each one claims the same destination every time and finds its own work
    already done.
    """
    occurrence = seen.get(stem, 0)
    seen[stem] = occurrence + 1
    dest = (
        planned_output_path(out_dir, stem) if occurrence == 0
        else out_dir / f'{stem}_video_{occurrence + 1}.mp4'
    )
    return dest, dest.exists()


def extract_to(filepath: Path, dest: Path) -> Path | None:
    """
    Extract one JPEG's embedded clip to exactly `dest`, replacing it if present.

    Returns `dest`, or None if the file simply isn't a motion photo. Raises
    ExtractionError if it is one but the video can't be recovered, and OSError
    if the file can't be read or the output can't be written.
    """
    video = find_video(filepath.read_bytes())
    if video is None:
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(video)
    return dest


def collect_jpegs(target: Path, recursive: bool) -> list[Path]:
    """Every .jpg/.jpeg under target, sorted. Non-JPEGs are ignored entirely."""
    pattern = '**/*' if recursive else '*'
    return sorted(
        f for f in target.glob(pattern)
        if f.is_file() and f.suffix.lower() in JPEG_EXTENSIONS
    )

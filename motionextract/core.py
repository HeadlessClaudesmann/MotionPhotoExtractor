"""
Extraction core for Google Pixel motion photos.

Pure standard library. Shared by both the CLI and the GUI so that a fix to the
format handling only ever has to be made once.

Three strategies are tried in order:
  new format  GCamera:MotionPhoto + Container:Directory, Item:Length per item
  old format  MicroVideoOffset attribute (older Pixel firmware)
  fallback    locate the appended MP4 by its box markers

The two metadata strategies only need the small XMP block at the head of the
file to work out where the clip starts, which is what lets `find_video_in_file`
skip the megabytes of JPEG data in between. The byte-oriented functions below
are kept alongside them for callers that already hold a whole file in memory.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

JPEG_EXTENSIONS = {'.jpg', '.jpeg'}
MP4_BOX_TYPES = (b'ftyp', b'moov', b'mdat')

# Below this, whatever we found is a stray marker rather than a real clip.
MIN_VIDEO_BYTES = 16

# How much of the head of the file we read to find and parse the XMP. The JPEG
# spec caps a single APP1 segment at 64 KB, so this leaves room for the standard
# block plus an extension segment and still costs almost nothing to read.
HEADER_BYTES = 131_072

# The window at the end of the file searched for an MP4 box marker when the XMP
# cannot tell us where the clip is. Clips run to a few MB, so this is generous;
# if it misses, the whole file is read instead and correctness is unaffected.
TAIL_SEARCH_BYTES = 16_777_216


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


def video_offset_new_format(header: bytes) -> int | None:
    """
    New format. How many bytes from the end of the file the clip begins.

    Non-primary items are appended to the file in Container order, so summing
    their lengths in reverse gives the video's distance from EOF. Only the XMP
    at the head of the file is needed, not the file itself.
    """
    if b'GCamera:MotionPhoto' not in header:
        return None

    non_primary = [i for i in parse_container_items(header) if i['semantic'] != 'Primary']
    if not non_primary:
        return None

    offset_from_eof = 0
    for item in reversed(non_primary):
        offset_from_eof += item['padding'] + item['length']
        if 'video' in item['mime']:
            return offset_from_eof
    return None


def video_offset_old_format(header: bytes) -> int | None:
    """Old format: MicroVideoOffset is the byte count from EOF to the video start."""
    if b'MicroVideoOffset' not in header:
        return None
    match = re.search(rb'MicroVideoOffset="(\d+)"', header)
    return int(match.group(1)) if match else None


def _slice_at_offset(data: bytes, offset_from_eof: int | None) -> bytes | None:
    """Take the last `offset_from_eof` bytes of `data`, if they look like an MP4."""
    if offset_from_eof is None or not 0 < offset_from_eof <= len(data):
        return None
    candidate = data[len(data) - offset_from_eof:]
    return candidate if _looks_like_mp4(candidate) else None


def find_video_new_format(data: bytes) -> bytes | None:
    """New format, applied to a whole file already in memory."""
    return _slice_at_offset(data, video_offset_new_format(data))


def find_video_old_format(data: bytes) -> bytes | None:
    """Old format, applied to a whole file already in memory."""
    return _slice_at_offset(data, video_offset_old_format(data))


def _plausible_box_at(data: bytes, marker_pos: int) -> bool:
    """
    True if marker_pos is preceded by a credible 4-byte box size field.

    Compressed JPEG data can contain a box marker's four bytes by chance. The
    size field in front of a real box is a useful filter: per the MP4 spec it is
    the box length, or 0 for "runs to end of file", or 1 for "64-bit size
    follows the type".

    `data` may be a window taken from the end of the file rather than the whole
    thing. That is safe: a real box cannot extend past EOF, and because the
    window also ends at EOF, `len(data) - start` is the same bound either way.
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
    """
    Cheap check for the markers that indicate an embedded clip.

    Given only the head of a file this is still reliable, because the markers
    live in the XMP block that sits within the first few KB.
    """
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


def _search_tail_for_video(handle, filesize: int) -> bytes | None:
    """
    Find the clip by scanning for its opening box, reading from the end.

    The clip is appended after all of the JPEG data, so a window at the tail
    almost always contains it. Reading the whole file is kept as a second
    attempt so that an unusually large clip, or one whose marker straddles the
    window boundary, is still found.
    """
    windows = [min(TAIL_SEARCH_BYTES, filesize)]
    if windows[0] != filesize:
        windows.append(filesize)

    for window in windows:
        handle.seek(filesize - window)
        video = find_video_fallback(handle.read())
        if video is not None and len(video) >= MIN_VIDEO_BYTES:
            return video
    return None


def _open_and_read_header(handle) -> tuple[int, bytes]:
    """Return the file's size and its leading HEADER_BYTES."""
    filesize = handle.seek(0, os.SEEK_END)
    handle.seek(0)
    return filesize, handle.read(min(HEADER_BYTES, filesize))


def _offset_from_header(header: bytes) -> int | None:
    offset = video_offset_new_format(header)
    return offset if offset is not None else video_offset_old_format(header)


def find_video_in_file(filepath: Path) -> bytes | None:
    """
    Recover a file's embedded clip while reading as little of it as possible.

    The XMP at the head of the file states how far from EOF the clip begins, so
    in the common case we read a small header, seek straight to the clip, and
    never touch the JPEG data in between. That matters most when the photos are
    on a slow disk or are being read across a USB cable.

    Returns the clip's bytes, or None if this simply is not a motion photo.
    Raises ExtractionError if it is one but the clip cannot be located, and
    OSError if the file cannot be read.
    """
    with filepath.open('rb') as handle:
        filesize, header = _open_and_read_header(handle)
        if not is_motion_photo(header):
            return None

        offset = _offset_from_header(header)
        if offset is not None and MIN_VIDEO_BYTES <= offset <= filesize:
            handle.seek(filesize - offset)
            candidate = handle.read()
            if _looks_like_mp4(candidate):
                return candidate

        video = _search_tail_for_video(handle, filesize)
        if video is None:
            raise ExtractionError(
                'motion photo markers found but no MP4 data could be located')
        return video


def probe_video_in_file(filepath: Path) -> int | None:
    """
    Report the size of a file's embedded clip without reading the clip itself.

    This is what the dry run wants: it needs to say how big the video would be
    but has no use for the bytes. When the XMP supplies the offset, only the
    header and the eight bytes of the clip's first box are read, which is why a
    dry run over a folder costs a small fraction of a real extraction.

    Returns the clip's size in bytes, or None if this is not a motion photo.
    """
    with filepath.open('rb') as handle:
        filesize, header = _open_and_read_header(handle)
        if not is_motion_photo(header):
            return None

        offset = _offset_from_header(header)
        if offset is not None and MIN_VIDEO_BYTES <= offset <= filesize:
            handle.seek(filesize - offset)
            if _looks_like_mp4(handle.read(8)):
                return offset

        video = _search_tail_for_video(handle, filesize)
        if video is None:
            raise ExtractionError(
                'motion photo markers found but no MP4 data could be located')
        return len(video)


def planned_output_path(out_dir: Path, stem: str) -> Path:
    """The name this photo's clip gets when nothing is in the way."""
    return out_dir / f'{stem}_video.mp4'


def claim_output_path(
    out_dir: Path, stem: str, seen: dict[str, int],
) -> tuple[Path, bool]:
    """
    Claim the destination for one photo's clip, and say whether it already exists.

    Returns (destination, already_extracted).

    `seen` counts how many photos have already claimed each name, and is updated
    here. The first photo to claim a name gets `<stem>_video.mp4`, the second
    `<stem>_video_2.mp4`, and so on -- so two different photos sharing a camera
    filename, as happens when a recursive run flattens several subfolders, both
    get written.

    Counting is keyed on the full destination, not the stem alone, so that when
    the output mirrors the source tree the two photos land in different folders
    and neither needs a suffix.

    Basing the suffix on position in the run rather than on the first free name
    is what makes a second run a no-op: photos are visited in sorted order, so
    each one claims the same destination every time and finds its own work
    already done. It also means deleting a few videos and running again refills
    exactly those gaps, instead of shuffling every name along.
    """
    planned = planned_output_path(out_dir, stem)
    key = str(planned)
    occurrence = seen.get(key, 0)
    seen[key] = occurrence + 1
    dest = (
        planned if occurrence == 0
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
    video = find_video_in_file(filepath)
    if video is None:
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(video)
    return dest


def output_dir_for(photo: Path, source_dir: Path, out_dir: Path, mirror: bool) -> Path:
    """
    Where this photo's clip belongs.

    Flat by default: everything goes straight into `out_dir`. With `mirror`, the
    photo's folder path relative to the source is recreated underneath it, which
    keeps clips from different subfolders apart and so avoids the `_2` suffix
    entirely.
    """
    if not mirror:
        return out_dir
    try:
        relative = photo.parent.relative_to(source_dir)
    except ValueError:
        return out_dir
    return out_dir / relative


def collect_jpegs(target: Path, recursive: bool) -> list[Path]:
    """Every .jpg/.jpeg under target, sorted. Non-JPEGs are ignored entirely."""
    pattern = '**/*' if recursive else '*'
    return sorted(
        f for f in target.glob(pattern)
        if f.is_file() and f.suffix.lower() in JPEG_EXTENSIONS
    )

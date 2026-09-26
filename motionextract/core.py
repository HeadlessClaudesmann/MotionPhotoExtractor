"""
Extraction core for motion photos -- the stills with a short clip inside them.

Pure standard library. Shared by both the CLI and the GUI so that a fix to the
format handling only ever has to be made once.

There is no single standard, so several strategies are tried in order:
  google new    GCamera:MotionPhoto + Container:Directory, Item:Length per item
  google old    MicroVideoOffset attribute (older firmware)
  samsung       the SEF index appended to the end of the file
  fallback      locate the appended MP4 by its box markers

Google's convention -- also used by Motorola, OnePlus and Xiaomi -- puts its
metadata in the XMP at the head of the file. Samsung instead appends its own
index to the very end and leaves the head of the file looking like an ordinary
JPEG. Both are cheap to reach, which is what lets `find_video_in_file` skip the
megabytes of photo data in between; but it does mean both ends have to be looked
at before a file can be called an ordinary JPEG. The byte-oriented functions
below are kept alongside them for callers that already hold a whole file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

JPEG_EXTENSIONS = {'.jpg', '.jpeg'}
MP4_BOX_TYPES = (b'ftyp', b'moov', b'mdat')

# A clip that is genuinely whole contains all three: `ftyp` names the container
# format, `moov` describes the tracks, `mdat` holds the frames. Finding fewer
# means we are looking at a fragment, however plausible its first box looks.
MP4_REQUIRED_BOXES = frozenset(MP4_BOX_TYPES)

# End of Image. A JPEG with nothing appended finishes here, so a file that does
# not is carrying something extra -- which is the one signal every vendor's
# motion photo shares, whatever it calls its metadata.
JPEG_EOI = b'\xff\xd9'

# Below this, whatever we found is a stray marker rather than a real clip.
MIN_VIDEO_BYTES = 16

# How much of the head of the file we read to find and parse the XMP. A JPEG
# stores it in an APP1 segment, which the spec caps at 64 KB, and the only thing
# normally large enough to sit in front of it is the Exif segment -- also capped
# at 64 KB. Two segments' worth is therefore a generous bound, and costs almost
# nothing to read.
HEADER_BYTES = 131_072

# How much of the end of the file we read to look for Samsung's SEF index. The
# index is a short table of 12-byte records, so this is far more than enough.
TRAILER_BYTES = 65_536

# The window at the end of the file searched for an MP4 box marker when the
# metadata cannot tell us where the clip is. Clips run to a few MB, so this is
# generous; if it misses, the whole file is read instead and correctness is
# unaffected.
TAIL_SEARCH_BYTES = 16_777_216

# Samsung's markers. The SEF ("Samsung Extended Format") index is bracketed by
# SEFH and SEFT, with SEFT the last four bytes of the file. Within it, records
# of type 0x0a30 describe the embedded clip, and the block each record points at
# opens with the name below.
SAMSUNG_VIDEO_MARKER = b'MotionPhoto_Data'
SEF_HEAD = b'SEFH'
SEF_TAIL = b'SEFT'
SEF_MOTION_PHOTO_TYPE = 0x0a30

# How much of the start of a vendor's block is read to find the clip's opening
# box. The block begins with a short header naming it, so the MP4 starts within a
# few dozen bytes of it; this is generous, and means the clip's own megabytes are
# never read just to find out where it begins.
BLOCK_HEADER_BYTES = 512

# How big an SEF block has to be before it is worth checking for a clip when its
# type is not the one we recognise. Samsung appends small blocks for timestamps
# and scene information; a video is orders of magnitude larger.
SEF_VIDEO_BLOCK_MINIMUM = 65_536


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


def video_offset_new_format(header: bytes) -> int | None:
    """
    New Google format. How many bytes from the end of the file the clip begins.

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
    """Old Google format: MicroVideoOffset is the byte count from EOF to the video."""
    if b'MicroVideoOffset' not in header:
        return None
    match = re.search(rb'MicroVideoOffset="(\d+)"', header)
    return int(match.group(1)) if match else None


def _looks_like_mp4(data: bytes) -> bool:
    """True if these bytes open with a recognised MP4 box type."""
    return len(data) >= 8 and data[4:8] in MP4_BOX_TYPES


def _slice_at_offset(data: bytes, offset_from_eof: int | None) -> bytes | None:
    """Take the last `offset_from_eof` bytes of `data`, if they look like an MP4."""
    if offset_from_eof is None or not 0 < offset_from_eof <= len(data):
        return None
    candidate = data[len(data) - offset_from_eof:]
    return candidate if _looks_like_mp4(candidate) else None


def find_video_new_format(data: bytes) -> bytes | None:
    """New Google format, applied to a whole file already in memory."""
    return _slice_at_offset(data, video_offset_new_format(data))


def find_video_old_format(data: bytes) -> bytes | None:
    """Old Google format, applied to a whole file already in memory."""
    return _slice_at_offset(data, video_offset_old_format(data))


def _plausible_box_at(data: bytes, marker_pos: int) -> bool:
    """
    True if marker_pos is preceded by a credible 4-byte box size field.

    Compressed JPEG data can contain a box marker's four bytes by chance. The
    size field in front of a real box is a useful filter: per the MP4 spec it is
    the box length, or 0 for "runs to end of file", or 1 for "64-bit size
    follows the type".

    `data` is often a window taken from the file rather than the whole thing.
    That only loosens the check, never breaks it: a real box cannot extend past
    the end of the data it lives in, so `len(data) - start` remains a valid
    upper bound whatever the window.
    """
    if marker_pos < 4:
        return False
    start = marker_pos - 4
    size = int.from_bytes(data[start:start + 4], 'big')
    return size in (0, 1) or 8 <= size <= len(data) - start


def _is_box_type(kind: bytes) -> bool:
    """MP4 box types are four printable ASCII characters."""
    return len(kind) == 4 and all(0x20 <= byte <= 0x7e for byte in kind)


def _box_chain(data: bytes, start: int) -> tuple[int, set[bytes]]:
    """
    Follow the MP4 box chain from `start` and report where it ends.

    Every box states its own length, so walking them finds the clip's true
    extent. That is how the clip is told apart from anything a vendor appended
    after it -- Samsung's index sits there, for one -- which would otherwise be
    written into the MP4. The walk stops at the first thing that is not a box
    that fits, and returns the types it consumed along the way.
    """
    pos, seen = start, set()
    while pos + 8 <= len(data):
        kind = data[pos + 4:pos + 8]
        if not _is_box_type(kind):
            break

        size = int.from_bytes(data[pos:pos + 4], 'big')
        if size == 0:                       # per spec, this box runs to the end
            seen.add(kind)
            return len(data), seen
        if size == 1:                       # 64-bit size follows the type
            if pos + 16 > len(data):
                break
            size = int.from_bytes(data[pos + 8:pos + 16], 'big')
        if size < 8 or pos + size > len(data):
            break

        seen.add(kind)
        pos += size
    return pos, seen


def _span_from(data: bytes, start: int) -> tuple[int, int]:
    """The clip beginning at `start`, ending wherever its box chain does."""
    end, _ = _box_chain(data, start)
    return start, end if end > start else len(data)


def _fallback_span(data: bytes) -> tuple[int, int] | None:
    """
    Where the appended MP4 starts and ends within `data`, judged by box markers.

    The end comes from following the box chain rather than assuming the clip runs
    to the end of the data, so that whatever a vendor appended after it is left
    out of the MP4 we write.
    """
    ftyp = data.rfind(b'ftyp')
    if ftyp >= 4 and _plausible_box_at(data, ftyp):
        return _span_from(data, ftyp - 4)

    fallbacks = [
        pos for pos in (data.rfind(b'moov'), data.rfind(b'mdat'))
        if pos >= 4 and _plausible_box_at(data, pos)
    ]
    return _span_from(data, min(fallbacks) - 4) if fallbacks else None


def find_video_fallback(data: bytes) -> bytes | None:
    """
    Last resort, used when no vendor's metadata is present or parseable.

    An MP4 starts at its `ftyp` box, so that is what we anchor on. We take the
    last one: the clip is appended after all of the JPEG data, so any stray
    marker inside that data is necessarily earlier in the file.

    Only when there is no `ftyp` at all do we settle for the outermost of the
    remaining boxes. Anchoring on `mdat` -- which in a real clip sits after the
    container header -- would silently truncate that header and produce an
    unplayable file.
    """
    span = _fallback_span(data)
    return None if span is None else data[span[0]:span[1]]


def _sef_index(trailer: bytes) -> tuple[int, list[tuple[int, int, int]]] | None:
    """
    Parse Samsung's SEF index out of the bytes at the end of the file.

    The index is a short table bracketed by SEFH and SEFT, the latter being the
    file's final four bytes. Each 12-byte record is two bytes we ignore, a type,
    the distance back from SEFH to the block it describes, and that block's
    length, all little-endian.

    We locate SEFH by searching rather than by trusting the size field that
    precedes SEFT, because published accounts disagree about whether that size
    counts itself. Searching for the marker sidesteps the question.

    Returns where the index starts within `trailer` and its records, or None if
    this does not parse as an SEF index.
    """
    if not trailer.endswith(SEF_TAIL):
        return None

    index = trailer.rfind(SEF_HEAD)
    if index < 0 or index + 12 > len(trailer):
        return None

    count = int.from_bytes(trailer[index + 8:index + 12], 'little')
    if not 0 < count <= 1024 or index + 12 + count * 12 > len(trailer):
        return None

    records = []
    for n in range(count):
        at = index + 12 + n * 12
        records.append((
            int.from_bytes(trailer[at + 2:at + 4], 'little'),
            int.from_bytes(trailer[at + 4:at + 8], 'little'),
            int.from_bytes(trailer[at + 8:at + 12], 'little'),
        ))
    return index, records


def samsung_video_candidates(
        trailer: bytes,
        filesize: int) -> tuple[list[tuple[int, int]], bool] | None:
    """
    Regions of the file Samsung's SEF index says could hold the clip.

    These are regions to read, not the clip itself: each block opens with its own
    short name header rather than with the MP4, and the index is appended after
    them all. The exact start is pinned afterwards by looking for the opening box,
    which keeps a misreading of the index costing speed rather than correctness.

    The block the index names as the motion photo comes first. Any other block
    large enough to be a video follows it, because the type codes have no public
    specification and a model we have not seen may number its clip differently;
    checking a candidate costs one small read, which is worth spending before
    falling back to scanning the whole file.

    Returns absolute (start, stop) pairs, best candidate first, and whether the
    index actually named one of them as a motion photo -- that second answer is
    what separates a promise of video from a guess at it. Returns None instead if
    there is no SEF index here at all, which is a different thing from an index
    that lists no clip.
    """
    parsed = _sef_index(trailer)
    if parsed is None:
        return None

    index, records = parsed
    index_pos = filesize - (len(trailer) - index)

    named, sized = [], []
    for kind, back, length in records:
        start = index_pos - back
        if length < MIN_VIDEO_BYTES or start < 0 or start + length > index_pos:
            continue
        if kind == SEF_MOTION_PHOTO_TYPE:
            named.append((start, start + length))
        elif length >= SEF_VIDEO_BLOCK_MINIMUM:
            sized.append((start, start + length))

    return named + sized, bool(named)


def has_google_markers(data: bytes) -> bool:
    """True if these bytes carry Google's XMP markers for an embedded clip."""
    return b'MotionPhoto' in data or b'MicroVideo' in data


def has_samsung_markers(data: bytes) -> bool:
    """
    True if these bytes carry Samsung's own marker for an embedded clip.

    The name sits immediately in front of the clip, so it is found in a window
    taken from the end of a file only when that window reaches back past the
    clip -- which is why the SEF index is tried first.
    """
    return SAMSUNG_VIDEO_MARKER in data


def is_motion_photo(data: bytes) -> bool:
    """
    Cheap check for the markers that indicate an embedded clip.

    Reliable on a whole file, and on the head of one for Google's convention,
    where the markers live in the XMP within the first few KB. Samsung's marker
    sits at the other end of the file, so a head-only window will not show it;
    `find_video_in_file` reads both ends for that reason.
    """
    return has_google_markers(data) or has_samsung_markers(data)


def find_video(data: bytes) -> bytes | None:
    """
    Recover the embedded MP4 from raw JPEG bytes, or None if there isn't one.

    The same strategies `find_video_in_file` uses, for a caller that already holds
    the whole file. Both ends of it are in hand here, so there is nothing to be
    saved by reading one before the other.
    """
    if is_motion_photo(data):
        video = (
            find_video_new_format(data)
            or find_video_old_format(data)
            or find_video_fallback(data)
        )
        if video is None or len(video) < MIN_VIDEO_BYTES:
            raise ExtractionError(
                'motion photo markers found but no MP4 data could be located')
        return video

    # Nothing named a clip, but a photo that does not end at its own end marker is
    # carrying something appended. `_locate_video` explains why that is a weak
    # signal rather than a promise, and so returns None here instead of raising.
    if data.endswith(JPEG_EOI):
        return None

    video = find_video_fallback(data)
    return video if video and len(video) >= MIN_VIDEO_BYTES else None


def _read_window(handle, start: int, length: int) -> bytes:
    """Read `length` bytes from `start`, without falling off either end."""
    start = max(0, start)
    handle.seek(start)
    return handle.read(max(0, length))


def _box_chain_in_file(handle, start: int, limit: int) -> tuple[int, set[bytes]]:
    """
    Follow the box chain by seeking, reading only each box's header.

    The same walk as `_box_chain`, for when the clip is still on disk. Because
    every box states its own length, the whole chain can be followed in a handful
    of eight-byte reads -- which is how a dry run reports a clip's size without
    reading the clip.

    `limit` is where the chain is not allowed to run past: the end of the region a
    vendor index pointed at, or of the file.
    """
    pos, seen = start, set()
    while pos + 8 <= limit:
        head = _read_window(handle, pos, 16)
        if len(head) < 8:
            break

        kind = head[4:8]
        if not _is_box_type(kind):
            break

        size = int.from_bytes(head[:4], 'big')
        if size == 0:                           # per spec, this box runs to the end
            seen.add(kind)
            return limit, seen
        if size == 1:                           # 64-bit size follows the type
            if len(head) < 16:
                break
            size = int.from_bytes(head[8:16], 'big')
        if size < 8 or pos + size > limit:
            break

        seen.add(kind)
        pos += size
    return pos, seen


def _scan_for_video(handle, filesize: int) -> tuple[int, int] | None:
    """
    Find the clip by scanning for its opening box, working back from the end.

    The clip is appended after all of the photo data, so a window at the tail
    almost always contains it. Reading the whole file is kept as a second
    attempt so that an unusually large clip, or one whose marker straddles the
    window boundary, is still found.

    Returns absolute (start, stop) positions, or None.
    """
    windows = [min(TAIL_SEARCH_BYTES, filesize)]
    if windows[0] != filesize:
        windows.append(filesize)

    for window in windows:
        base = filesize - window
        span = _fallback_span(_read_window(handle, base, window))
        if span is not None and span[1] - span[0] >= MIN_VIDEO_BYTES:
            return base + span[0], base + span[1]
    return None


def _offset_from_header(header: bytes) -> int | None:
    offset = video_offset_new_format(header)
    return offset if offset is not None else video_offset_old_format(header)


def _narrow_to_video(handle, region: tuple[int, int]) -> tuple[int, int] | None:
    """
    Pin the clip's exact extent inside a region a vendor index pointed us at.

    The region opens with a few bytes of the vendor's own block header, so the
    start is found by looking for the opening box in a short window and the end by
    following the box chain from there. Only those box headers are read: the clip
    itself is left on disk, which is what the dry run relies on.

    A whole clip has all three of `ftyp`, `moov` and `mdat`. If the region does not
    yield one, the index cannot mean what we took it to mean, and the slower scan
    is a better answer than a clip cut off part-way -- an MP4 that looks fine right
    up until someone tries to play it.

    Returns absolute (start, stop) positions, or None if this region holds no
    whole clip.
    """
    region_start, region_stop = region
    window = min(BLOCK_HEADER_BYTES, region_stop - region_start)
    head = _read_window(handle, region_start, window)

    ftyp = head.find(b'ftyp')
    if ftyp < 4:
        return None

    # The box chain does the validating from here: a box that does not fit inside
    # the region stops the walk, and a chain missing any of the three required
    # boxes is rejected below, which is a far stronger check than the size field
    # in front of this marker.
    video_start = region_start + ftyp - 4
    stop, seen = _box_chain_in_file(handle, video_start, region_stop)
    if not MP4_REQUIRED_BOXES <= seen or stop - video_start < MIN_VIDEO_BYTES:
        return None
    return video_start, stop


def _locate_video(handle, filesize: int) -> tuple[int, int] | None:
    """
    Work out where a file's clip is while reading as little as possible.

    Google's XMP and Samsung's SEF index both state the clip's position, so in
    the common case only a small window at one end of the file is read and the
    photo data in between is never touched. Each answer is checked against the
    bytes it points at, so a vendor quirk we have misread falls through to the
    scan instead of producing a wrong result.

    Returns absolute (start, stop) positions, or None if this is not a motion
    photo. Raises ExtractionError when metadata asserts a clip that cannot be
    found.
    """
    header = _read_window(handle, 0, min(HEADER_BYTES, filesize))

    if has_google_markers(header):
        offset = _offset_from_header(header)
        if offset is not None and MIN_VIDEO_BYTES <= offset <= filesize:
            start = filesize - offset
            if _looks_like_mp4(_read_window(handle, start, 8)):
                return start, filesize
        return _require(_scan_for_video(handle, filesize))

    # A short file is entirely within the header we already hold.
    trailer = (
        header if filesize <= HEADER_BYTES
        else _read_window(handle, filesize - TRAILER_BYTES, TRAILER_BYTES)
    )

    samsung = samsung_video_candidates(trailer, filesize)
    if samsung is not None:
        candidates, asserted = samsung
        for region in candidates:
            span = _narrow_to_video(handle, region)
            if span is not None:
                return span
        if asserted:
            return _require(_scan_for_video(handle, filesize))

        # The index lists every block Samsung appended and none of them holds a
        # clip. That is positive evidence of an ordinary photo -- a trailer gets
        # written for other camera features too -- and trusting it is what keeps a
        # folder of ordinary Galaxy photos cheap, since the alternative is
        # scanning every one of them in full.
        return None

    if has_samsung_markers(trailer):
        return _require(_scan_for_video(handle, filesize))

    # Nothing named a clip, but a JPEG that does not end at its own end marker is
    # carrying something appended, and an embedded clip is the usual reason. This
    # is what lets a vendor we have never heard of still work, so long as it
    # appends the MP4 like the rest. The signal is weak rather than a promise --
    # padding after the end marker is legal -- so finding no clip here means the
    # file is simply an ordinary photo, not a broken motion photo.
    if not trailer.endswith(JPEG_EOI):
        return _scan_for_video(handle, filesize)

    return None


def _require(span: tuple[int, int] | None) -> tuple[int, int]:
    """Insist that a clip promised by metadata was actually found."""
    if span is None:
        raise ExtractionError(
            'motion photo markers found but no MP4 data could be located')
    return span


def find_video_in_file(filepath: Path) -> bytes | None:
    """
    Recover a file's embedded clip while reading as little of it as possible.

    The vendor metadata states where the clip begins, so in the common case we
    read a small window at one end of the file, seek straight to the clip, and
    never touch the photo data in between. That matters most when the photos are
    on a slow disk or are being read across a USB cable.

    Returns the clip's bytes, or None if this simply is not a motion photo.
    Raises ExtractionError if it is one but the clip cannot be located, and
    OSError if the file cannot be read.
    """
    with filepath.open('rb') as handle:
        filesize = handle.seek(0, os.SEEK_END)
        span = _locate_video(handle, filesize)
        if span is None:
            return None
        start, stop = span
        return _read_window(handle, start, stop - start)


def probe_video_in_file(filepath: Path) -> int | None:
    """
    Report the size of a file's embedded clip without reading the clip itself.

    This is what the dry run wants: it needs to say how big the video would be
    but has no use for the bytes. When the metadata supplies the position, only
    a small window and the eight bytes of the clip's first box are read, which
    is why a dry run over a folder costs a fraction of a real extraction.

    Returns the clip's size in bytes, or None if this is not a motion photo.
    """
    with filepath.open('rb') as handle:
        filesize = handle.seek(0, os.SEEK_END)
        span = _locate_video(handle, filesize)
        return None if span is None else span[1] - span[0]


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

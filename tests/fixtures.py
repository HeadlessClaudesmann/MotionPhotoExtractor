"""
Synthetic motion photos for the test suite.

Built in-process rather than committed as binaries: the interesting part of a
motion photo is its byte layout, and a builder makes that layout explicit.
"""

from __future__ import annotations

import struct

JPEG_HEAD = b'\xff\xd8\xff\xe1'
JPEG_TAIL = b'\xff\xd9'


def mp4_box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack('>I', len(payload) + 8) + kind + payload


def realistic_mp4(size: int = 4640) -> bytes:
    """
    A clip with all three boxes in the order a real one uses.

    The header boxes before `mdat` are what makes this worth having: anchoring
    extraction on the last box marker in the file drops them.

    `size` pads `mdat` out to give a clip of roughly the size a phone produces.
    That matters only for the tests that measure how much of a file is read, where
    a clip smaller than the windows being measured would tell us nothing.
    """
    header = mp4_box(b'ftyp', b'isomiso2avc1mp41') + mp4_box(b'moov', b'M' * 600)
    return header + mp4_box(b'mdat', b'D' * max(1, size - len(header) - 8))


def _jpeg_body(size: int = 2048) -> bytes:
    return b'\x00' * size


def new_format(video: bytes, body_size: int = 2048) -> bytes:
    """GCamera:MotionPhoto with a Container:Directory listing the clip."""
    xmp = (
        b'<x:xmpmeta xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"\n'
        b'  GCamera:MotionPhoto="1" GCamera:MotionPhotoVersion="1">\n'
        b' <Container:Directory>\n'
        b'  <Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary"'
        b' Item:Length="0" Item:Padding="0"/>\n'
        b'  <Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto"'
        b' Item:Length="' + str(len(video)).encode() + b'" Item:Padding="0"/>\n'
        b' </Container:Directory>\n'
        b'</x:xmpmeta>'
    )
    return JPEG_HEAD + xmp + _jpeg_body(body_size) + JPEG_TAIL + video


def old_format(video: bytes, body_size: int = 2048) -> bytes:
    """Older firmware: a single MicroVideoOffset counted back from EOF."""
    xmp = (
        b'<x:xmpmeta xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"\n'
        b'  GCamera:MicroVideo="1" GCamera:MicroVideoOffset="'
        + str(len(video)).encode() + b'"/>'
    )
    return JPEG_HEAD + xmp + _jpeg_body(body_size) + JPEG_TAIL + video


def fallback_only(video: bytes, body_size: int = 2048) -> bytes:
    """Markers present, but nothing that gives an offset -- forces the scan."""
    xmp = b'<x:xmpmeta>GCamera:MotionPhoto="1" but no container directory</x:xmpmeta>'
    return JPEG_HEAD + xmp + _jpeg_body(body_size) + JPEG_TAIL + video


def plain_jpeg(body_size: int = 2048) -> bytes:
    """An ordinary photo with no clip at all."""
    return (JPEG_HEAD + b'<x:xmpmeta>nothing to see</x:xmpmeta>'
            + _jpeg_body(body_size) + JPEG_TAIL)


def markers_without_video() -> bytes:
    """Claims to be a motion photo but the clip is absent."""
    return JPEG_HEAD + b'<x:xmpmeta>GCamera:MotionPhoto="1"</x:xmpmeta>' + JPEG_TAIL


SEF_HEAD = b'SEFH'
SEF_TAIL = b'SEFT'
SEF_MOTION_PHOTO_TYPE = 0x0a30
SEF_TIMESTAMP_TYPE = 0x0a01

# The other types ExifTool's tag table names, for the layouts below. Two more
# ways a clip can be typed, and two things that are emphatically not a clip.
SEF_AUTOPLAY_VIDEO_TYPE = 0x0a33
SEF_SURROUND_SHOT_TYPE = 0x0201
SEF_MOTION_PHOTO_VERSION_TYPE = 0x0a31
SEF_AUDIO_TYPE = 0x0100


def sef_block(kind: int, name: bytes, payload: bytes) -> bytes:
    """
    One block of a Samsung trailer: a short header naming it, then its payload.

    The name is what older extractors key on, since on those phones it sits
    directly in front of the clip.
    """
    return b'\x00\x00' + struct.pack('<H', kind) + struct.pack('<I', len(name)) + name + payload


def samsung_sef(blocks: list, body_size: int = 2048, offset_error: int = 0,
                prefix: bytes = b'') -> bytes:
    """
    Newer Galaxy layout: a photo, then SEF blocks, then the index describing them.

    The index is bracketed by SEFH and SEFT and gives each block's position as a
    distance back from SEFH, so nothing at the head of the file hints that a clip
    is present. `offset_error` shifts those distances, which stands in for us
    having misread a format that has no public specification -- extraction is
    expected to notice and fall back, not to trust it.

    `prefix` is bytes sitting between the photo and the first indexed block, for
    the layouts where something is appended that the index does not describe as a
    block of its own.
    """
    data, spans = prefix, []
    for kind, name, payload in blocks:
        block = sef_block(kind, name, payload)
        spans.append((kind, len(data), len(block)))
        data += block

    index = SEF_HEAD + struct.pack('<I', 106) + struct.pack('<I', len(spans))
    for kind, start, length in spans:
        index += (
            b'\x00\x00' + struct.pack('<H', kind)
            + struct.pack('<I', len(data) - start + offset_error)
            + struct.pack('<I', length)
        )

    trailer = data + index + struct.pack('<I', len(index)) + SEF_TAIL
    return JPEG_HEAD + _jpeg_body(body_size) + JPEG_TAIL + trailer


def samsung_motion_photo(video: bytes, body_size: int = 2048, offset_error: int = 0) -> bytes:
    """A newer Galaxy motion photo: one clip block plus the timestamp Samsung writes."""
    return samsung_sef(
        [(SEF_TIMESTAMP_TYPE, b'Image_UTC_Data', b'1758844800'),
         (SEF_MOTION_PHOTO_TYPE, b'MotionPhoto_Data', video)],
        body_size=body_size, offset_error=offset_error,
    )


def samsung_sound_shot(body_size: int = 2048, audio_size: int = 262144) -> bytes:
    """
    A Samsung trailer with no clip in it: Sound & Shot, which appends audio.

    Samsung writes a trailer for other camera features too, so the trailer alone
    must not be taken as a promise of video -- this has to read as an ordinary
    photo rather than as a motion photo we failed to extract. The audio block is
    deliberately far bigger than the size at which an unrecognised block would be
    probed as possible video, because being typed as audio is what has to rule it
    out.
    """
    return samsung_sef(
        [(SEF_TIMESTAMP_TYPE, b'Image_UTC_Data', b'1758844800'),
         (SEF_AUDIO_TYPE, b'Sound_Data', b'A' * audio_size)],
        body_size=body_size)


def samsung_pointer_variant(video: bytes, body_size: int = 2048) -> bytes:
    """
    The layout where the clip's block holds a pointer to it rather than the clip.

    ExifTool documents a 0x0a30 block whose value is twelve bytes -- four to skip,
    then a big-endian offset and size. Nothing we have found says what that offset
    is measured from, so this fixture appends the clip ahead of the blocks and
    leaves the pointer deliberately unhelpful. The record still says a clip is
    here, and finding it is what must not be skipped.
    """
    pointer = b'\x00' * 4 + struct.pack('>I', 0) + struct.pack('>I', len(video))
    return samsung_sef(
        [(SEF_TIMESTAMP_TYPE, b'Image_UTC_Data', b'1758844800'),
         (SEF_MOTION_PHOTO_TYPE, b'MotionPhoto_Data', pointer)],
        body_size=body_size, prefix=video)


def samsung_bare_marker(video: bytes, body_size: int = 2048) -> bytes:
    """
    Older Galaxy layout: the photo, the 16-byte name, then the clip. No trailer.

    Neither end of the file says anything, so this is the case that forces the
    scan -- and the one that a head-only reader misses entirely.
    """
    return JPEG_HEAD + _jpeg_body(body_size) + JPEG_TAIL + b'MotionPhoto_Data' + video


def appended_clip_only(video: bytes, body_size: int = 2048) -> bytes:
    """
    A clip appended with no metadata of any kind, from a vendor we don't know.

    All that marks it is the photo not ending where a JPEG should, which is the
    signal that keeps the tool working on formats nobody has documented to us.
    """
    return JPEG_HEAD + _jpeg_body(body_size) + JPEG_TAIL + video


def plain_jpeg_with_padding(body_size: int = 2048) -> bytes:
    """
    An ordinary photo with bytes after its end marker but no clip.

    Padding after the end marker is legal, so this is the case that stops the
    "something is appended" signal from turning ordinary photos into failures.
    """
    return JPEG_HEAD + _jpeg_body(body_size) + JPEG_TAIL + b'\x00' * 4096

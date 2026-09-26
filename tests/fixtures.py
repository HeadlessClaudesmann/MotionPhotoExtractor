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


def realistic_mp4() -> bytes:
    """
    A clip with all three boxes in the order a real one uses.

    The header boxes before `mdat` are what makes this worth having: anchoring
    extraction on the last box marker in the file drops them.
    """
    return (
        mp4_box(b'ftyp', b'isomiso2avc1mp41')
        + mp4_box(b'moov', b'M' * 600)
        + mp4_box(b'mdat', b'D' * 4000)
    )


def _jpeg_body(size: int = 2048) -> bytes:
    return b'\x00' * size


def new_format(video: bytes) -> bytes:
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
    return JPEG_HEAD + xmp + _jpeg_body() + JPEG_TAIL + video


def old_format(video: bytes) -> bytes:
    """Older firmware: a single MicroVideoOffset counted back from EOF."""
    xmp = (
        b'<x:xmpmeta xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"\n'
        b'  GCamera:MicroVideo="1" GCamera:MicroVideoOffset="'
        + str(len(video)).encode() + b'"/>'
    )
    return JPEG_HEAD + xmp + _jpeg_body() + JPEG_TAIL + video


def fallback_only(video: bytes) -> bytes:
    """Markers present, but nothing that gives an offset -- forces the scan."""
    xmp = b'<x:xmpmeta>GCamera:MotionPhoto="1" but no container directory</x:xmpmeta>'
    return JPEG_HEAD + xmp + _jpeg_body() + JPEG_TAIL + video


def plain_jpeg() -> bytes:
    """An ordinary photo with no clip at all."""
    return JPEG_HEAD + b'<x:xmpmeta>nothing to see</x:xmpmeta>' + _jpeg_body() + JPEG_TAIL


def markers_without_video() -> bytes:
    """Claims to be a motion photo but the clip is absent."""
    return JPEG_HEAD + b'<x:xmpmeta>GCamera:MotionPhoto="1"</x:xmpmeta>' + JPEG_TAIL

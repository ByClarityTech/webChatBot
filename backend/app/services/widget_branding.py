"""Widget branding checks: WCAG contrast for the header, and logo files checked by their bytes.

Contrast follows WCAG 2.1: relative luminance from sRGB, ratio (L1 + 0.05) / (L2 + 0.05).
Normal text needs at least 4.5:1 (AA). The header text color is chosen automatically, white or
near-black, whichever contrasts more with the header. A header color that cannot reach 4.5:1
with either is refused.

Logos are accepted only as PNG, JPEG, or WebP. The type comes from the file's magic bytes, never
from the file name or the upload's content type. Dimensions come from the header of each
format, so no image library is needed. SVG is refused: it can carry script.
"""

import hashlib
import re
from dataclasses import dataclass

DEFAULT_HEADER_COLOR = "#1D4ED8"
WHITE = "#FFFFFF"
NEAR_BLACK = "#111827"
MIN_CONTRAST = 4.5

MAX_LOGO_BYTES = 256 * 1024
MIN_LOGO_SIDE = 16
MAX_LOGO_SIDE = 1024

HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class InvalidBrandingColor(Exception):
    """Raised when a header color cannot reach the contrast threshold."""

    def __init__(self, ratio: float) -> None:
        super().__init__(f"Contrast is {ratio:.2f}:1. The header needs at least 4.5:1.")
        self.ratio = ratio


class InvalidLogo(Exception):
    """A logo file the widget will not show. The message is safe to show to the admin."""


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    if not HEX_COLOR.match(color):
        raise ValueError("Colors must look like #1D4ED8")
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _linear(channel: int) -> float:
    value = channel / 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    red, green, blue = (_linear(c) for c in _hex_to_rgb(color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def text_color_for(background: str) -> str:
    """Whichever of white or near-black reads better on the background."""
    if contrast_ratio(WHITE, background) >= contrast_ratio(NEAR_BLACK, background):
        return WHITE
    return NEAR_BLACK


@dataclass(frozen=True)
class HeaderColors:
    header_color: str
    header_text_color: str
    ratio: float


def check_header_color(header_color: str) -> HeaderColors:
    """Refuses a header color below 4.5:1 with its best text color. Returns the colors to store."""
    text = text_color_for(header_color)
    ratio = contrast_ratio(text, header_color)
    if ratio < MIN_CONTRAST:
        raise InvalidBrandingColor(ratio)
    return HeaderColors(header_color=header_color.upper(), header_text_color=text, ratio=ratio)


@dataclass(frozen=True)
class LogoInfo:
    content_type: str
    width: int
    height: int
    sha256: str


def _u16le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise InvalidLogo("This PNG file is damaged.")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _jpeg_size(data: bytes) -> tuple[int, int]:
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            break
        marker = data[index + 1]
        if marker == 0xFF:  # fill byte
            index += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length field
            index += 2
            continue
        if marker == 0xD9 or marker == 0xDA:  # end of image, or the scan data starts
            break
        segment = int.from_bytes(data[index + 2 : index + 4], "big")
        if segment < 2 or index + 2 + segment > len(data):
            break
        is_frame_header = 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC)
        if is_frame_header and segment >= 7 and index + 9 <= len(data):
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + segment
    raise InvalidLogo("This JPEG file is damaged.")


def _webp_size(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        b0, b1, b2, b3 = data[21], data[22], data[23], data[24]
        width = 1 + (((b1 & 0x3F) << 8) | b0)
        height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
        return width, height
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return _u16le(data, 26) & 0x3FFF, _u16le(data, 28) & 0x3FFF
    raise InvalidLogo("This WebP file is damaged or uses an unsupported variant.")


def inspect_logo(data: bytes) -> LogoInfo:
    """Checks the bytes of an uploaded logo. Raises InvalidLogo with a message for the admin."""
    if not data:
        raise InvalidLogo("The file is empty.")
    if len(data) > MAX_LOGO_BYTES:
        raise InvalidLogo("The logo must be 256 KB or smaller.")

    if data[:8] == b"\x89PNG\r\n\x1a\n":
        content_type, (width, height) = "image/png", _png_size(data)
    elif data[:3] == b"\xff\xd8\xff":
        content_type, (width, height) = "image/jpeg", _jpeg_size(data)
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        content_type, (width, height) = "image/webp", _webp_size(data)
    else:
        raise InvalidLogo("Use a PNG, JPEG, or WebP image. SVG files are not accepted.")

    if not (MIN_LOGO_SIDE <= width <= MAX_LOGO_SIDE and MIN_LOGO_SIDE <= height <= MAX_LOGO_SIDE):
        raise InvalidLogo(
            f"The logo must be between {MIN_LOGO_SIDE} and {MAX_LOGO_SIDE} pixels on each side."
        )
    return LogoInfo(
        content_type=content_type,
        width=width,
        height=height,
        sha256=hashlib.sha256(data).hexdigest(),
    )

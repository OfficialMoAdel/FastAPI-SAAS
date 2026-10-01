"""A minimal, self-contained QR decoder used to prove MFA QR output is real.

Why this exists
---------------
`MFAService.generate_totp_secret` returns ``qr_code_base64``, a base64 PNG
data URI that an authenticator app is expected to scan. Asserting only that
the key *exists*, or that it starts with ``data:image/png;base64,``, proves
nothing: a blank white rectangle satisfies both.

No QR-decoding library is installed (pyzbar needs a native zbar, OpenCV is
absent, zxing-cpp is absent), so this module implements just enough of
ISO/IEC 18004 to read a symbol back out of the rendered PNG:

* PNG -> 1-bit module grid (module size measured from the finder pattern),
* format information (BCH(15,5)) -> error-correction level + mask id,
* version information (BCH(18,6)) cross-check for version >= 7,
* the zig-zag codeword placement order with data masking undone,
* block de-interleaving, Reed-Solomon error correction over GF(256),
* byte/numeric/alphanumeric segment parsing.

It is deliberately restricted to the modes and versions the ``qrcode``
library emits for ``otpauth://`` URIs (byte mode, versions 1-40). It is a
test oracle, not a general-purpose scanner.

The Reed-Solomon and version tables come from the ``qrcode`` package that
already produces the image, which keeps this decoder from re-deriving the
block geometry; everything about reading the *image* is implemented here.
"""

from __future__ import annotations

import base64
import io

import qrcode.base as qb
import qrcode.util as qu
from PIL import Image

_GF_EXP = qb.EXP_TABLE
_GF_LOG = qb.LOG_TABLE

# Format-information bits encode the EC level and mask id; the mapping from
# the two-bit EC field to the library's level constants comes from the spec.
_EC_BY_LEVEL = {
    "L": qb.constants.ERROR_CORRECT_L,
    "M": qb.constants.ERROR_CORRECT_M,
    "Q": qb.constants.ERROR_CORRECT_Q,
    "H": qb.constants.ERROR_CORRECT_H,
}
_EC_BY_BITS = {1: "L", 0: "M", 3: "Q", 2: "H"}

_ALNUM = qu.ALPHA_NUM
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class QRDecodeError(ValueError):
    """Raised when the bytes are not a QR symbol this decoder can read."""


# --------------------------------------------------------------------------
# GF(256) arithmetic (QR's primitive polynomial, generator 0x11d)
# --------------------------------------------------------------------------


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[(_GF_LOG[a] + _GF_LOG[b]) % 255]


def _gf_div(a: int, b: int) -> int:
    if b == 0:
        raise ZeroDivisionError("division by zero in GF(256)")
    if a == 0:
        return 0
    return _GF_EXP[(_GF_LOG[a] - _GF_LOG[b] + 255) % 255]


def _poly_mul(p: list[int], q: list[int]) -> list[int]:
    out = [0] * (len(p) + len(q) - 1)
    for i, c in enumerate(p):
        if c:
            for j, d in enumerate(q):
                if d:
                    out[i + j] ^= _gf_mul(c, d)
    return out


def _poly_add(p: list[int], q: list[int]) -> list[int]:
    width = max(len(p), len(q))
    out = [0] * width
    for i, c in enumerate(p):
        out[i + width - len(p)] = c
    for i, c in enumerate(q):
        out[i + width - len(q)] ^= c
    return out


def _poly_scale(p: list[int], x: int) -> list[int]:
    return [_gf_mul(c, x) for c in p]


def _poly_eval(p: list[int], x: int) -> int:
    acc = p[0]
    for c in p[1:]:
        acc = _gf_mul(acc, x) ^ c
    return acc


def _syndromes(msg: bytes | bytearray, nsym: int) -> list[int]:
    out = []
    for i in range(nsym):
        root = _GF_EXP[i % 255]
        acc = 0
        for c in msg:
            acc = _gf_mul(acc, root) ^ c
        out.append(acc)
    return out


# --------------------------------------------------------------------------
# Reed-Solomon
# --------------------------------------------------------------------------


def _rs_generator(nsym: int) -> list[int]:
    poly = [1]
    for i in range(nsym):
        poly = _poly_mul(poly, [1, _GF_EXP[i]])
    return poly


def _rs_remainder(data: bytes | bytearray, gen: list[int]) -> list[int]:
    """Remainder of ``data`` divided by the RS generator polynomial."""
    work = list(data) + [0] * (len(gen) - 1)
    lead = gen[0]
    for i in range(len(work) - len(gen) + 1):
        coef = _gf_div(work[i], lead)
        if coef:
            for j, d in enumerate(gen):
                if d:
                    work[i + j] ^= _gf_mul(coef, d)
    return work[len(work) - len(gen) + 1 :]


def rs_encode(data: bytes, nsym: int) -> bytes:
    """Reference RS parity for ``data`` (used by the decoder's own tests)."""
    return bytes(_rs_remainder(data, _rs_generator(nsym)))


def _rs_correct(block: bytes, nsym: int) -> bytes:
    """Return the data codewords of ``block``, correcting up to nsym/2 errors."""
    msg = bytearray(block)
    synd = _syndromes(msg, nsym)
    if max(synd) == 0:
        return bytes(msg[: len(msg) - nsym])

    # Berlekamp-Massey: build the error locator polynomial.
    locator, previous = [1], [1]
    for i in range(nsym):
        delta = synd[i]
        for j in range(1, len(locator)):
            delta ^= _gf_mul(locator[-(j + 1)], synd[i - j])
        previous = previous + [0]
        if delta:
            if len(previous) > len(locator):
                new_locator = _poly_scale(previous, delta)
                previous = _poly_scale(locator, _gf_div(1, delta))
                locator = new_locator
            locator = _poly_add(locator, _poly_scale(previous, delta))

    n_errors = len(locator) - 1
    if n_errors == 0 or n_errors * 2 > nsym:
        raise QRDecodeError("too many errors to correct")

    positions = [i for i in range(len(msg)) if _poly_eval(locator, _GF_EXP[(255 - i) % 255]) == 0]
    if len(positions) != n_errors:
        raise QRDecodeError("error locator degree does not match root count")

    # Forney: omega(x) = S(x) * Lambda(x) mod x^nsym
    product = [0] * (len(synd) + len(locator) - 1)
    for i, c in enumerate(synd):
        if c:
            for j, d in enumerate(locator):
                if d:
                    product[i + j] ^= _gf_mul(c, d)
    omega = product[:nsym]

    for pos in positions:
        x_inv = _gf_div(1, _GF_EXP[(255 - pos) % 255])
        numerator = 0
        for c in omega[::-1]:
            numerator = _gf_mul(numerator, x_inv) ^ c
        denominator = 1
        for other in positions:
            if other == pos:
                continue
            denominator = _gf_mul(denominator, 1 ^ _gf_mul(x_inv, _GF_EXP[(255 - other) % 255]))
        if denominator == 0:
            raise QRDecodeError("singular Forney denominator")
        msg[pos] ^= _gf_div(numerator, denominator)

    if max(_syndromes(msg, nsym)) != 0:
        raise QRDecodeError("error correction failed")
    return bytes(msg[: len(msg) - nsym])


# --------------------------------------------------------------------------
# Symbol geometry
# --------------------------------------------------------------------------


def _function_modules(version: int, n: int) -> list[list[bool]]:
    """Map of non-data modules (finder, separator, timing, alignment, info).

    True means "reserved"; data modules are False. Cross-checked against the
    ``qrcode`` library's own blank skeleton for all 40 versions.
    """
    reserved = [[False] * n for _ in range(n)]

    def mark(r0: int, c0: int, height: int, width: int) -> None:
        for r in range(r0, r0 + height):
            for c in range(c0, c0 + width):
                if 0 <= r < n and 0 <= c < n:
                    reserved[r][c] = True

    mark(0, 0, 9, 9)
    mark(0, n - 8, 9, 8)
    mark(n - 8, 0, 8, 9)

    for i in range(8, n - 8):
        reserved[6][i] = True
        reserved[i][6] = True

    positions = qu.PATTERN_POSITION_TABLE[version - 1]
    if positions:
        first, last = positions[0], positions[-1]
        for r in positions:
            for c in positions:
                if (r, c) in ((first, first), (first, last), (last, first)):
                    continue
                mark(r - 2, c - 2, 5, 5)

    if version >= 7:
        mark(n - 11, 0, 3, 6)
        mark(0, n - 11, 6, 3)
    return reserved


def _png_to_grid(data_uri: str) -> tuple[list[list[int]], int, int]:
    """Decode a PNG data URI into a module grid, deriving module size.

    The module pitch is measured from the leading edge of the top-left
    finder pattern (seven dark modules in a row), so any box_size and any
    quiet-zone width are handled.
    """
    prefix = "data:image/png;base64,"
    if not data_uri.startswith(prefix):
        raise QRDecodeError("not a base64 PNG data URI")

    png = base64.b64decode(data_uri[len(prefix) :])
    if png[:8] != _PNG_MAGIC:
        raise QRDecodeError("payload is not a PNG")

    with Image.open(io.BytesIO(png)) as img:
        image = img.convert("1")
        width, height = image.size
        if width != height or width == 0:
            raise QRDecodeError("QR image must be non-empty and square")
        pixels = image.load()

    dark = [[1 if pixels[x, y] < 128 else 0 for x in range(width)] for y in range(height)]
    xs = [x for x in range(width) for y in range(height) if dark[y][x]]
    ys = [y for y in range(height) for x in range(width) if dark[y][x]]
    if not xs:
        raise QRDecodeError("image is blank")
    x0, y0 = min(xs), min(ys)

    run = 0
    while x0 + run < width and dark[y0][x0 + run]:
        run += 1
    if run % 7:
        raise QRDecodeError(f"finder run {run} is not a multiple of 7")

    module = run // 7
    if module < 1:
        raise QRDecodeError("could not determine module size")
    border = x0 // module
    n = width // module - 2 * border
    if width != n * module + 2 * border * module:
        raise QRDecodeError("image is not an exact grid of whole modules")
    if n < 21 or (n - 17) % 4:
        raise QRDecodeError(f"implausible module count {n}")

    version = (n - 17) // 4
    if not 1 <= version <= 40:
        raise QRDecodeError(f"version {version} out of range")

    offset = border * module
    half = module // 2
    grid = [
        [dark[y * module + offset + half][x * module + offset + half] for x in range(n)]
        for y in range(n)
    ]
    return grid, n, version


def _read_format(grid: list[list[int]], n: int) -> int:
    bits = 0
    for i in range(15):
        if i < 6:
            row, col = i, 8
        elif i < 8:
            row, col = i + 1, 8
        else:
            row, col = n - 15 + i, 8
        bits |= grid[row][col] << i
    return bits


def _read_codewords(grid: list[list[int]], reserved: list[list[bool]], mask: int, n: int) -> bytes:
    """Walk the zig-zag codeword placement, undoing the data mask."""
    mask_fn = qu.mask_func(mask)
    bits: list[int] = []
    step = -1
    row = n - 1
    for col0 in range(n - 1, 0, -2):
        col = col0 - 1 if col0 <= 6 else col0
        while True:
            for c in (col, col - 1):
                if not reserved[row][c]:
                    value = grid[row][c]
                    if mask_fn(row, c):
                        value ^= 1
                    bits.append(value)
            row += step
            if row < 0 or row >= n:
                row -= step
                step = -step
                break

    # A symbol carries 0-7 remainder bits past the last full codeword
    # (they exist only to fill the module grid); they are not data.
    usable = len(bits) - (len(bits) % 8)
    out = bytearray()
    for i in range(0, usable, 8):
        byte = 0
        for j in range(8):
            byte = (byte << 1) | bits[i + j]
        out.append(byte)
    return bytes(out)


def _deinterleave(
    stream: bytes, blocks: list[qb.RSBlock]
) -> list[tuple[bytearray, bytearray]]:
    """Split the interleaved codeword stream back into per-block data/ecc."""
    ecc_counts = [b.total_count - b.data_count for b in blocks]
    data_parts: list[bytearray] = [bytearray() for _ in blocks]
    ecc_parts: list[bytearray] = [bytearray() for _ in blocks]

    index = 0
    for i in range(max(b.data_count for b in blocks)):
        for k, b in enumerate(blocks):
            if i < b.data_count:
                if index >= len(stream):
                    raise QRDecodeError("codeword stream shorter than block table requires")
                data_parts[k].append(stream[index])
                index += 1
    for i in range(max(ecc_counts)):
        for k, b in enumerate(blocks):
            if i < ecc_counts[k]:
                if index >= len(stream):
                    raise QRDecodeError("codeword stream shorter than block table requires")
                ecc_parts[k].append(stream[index])
                index += 1
    return list(zip(data_parts, ecc_parts))


def _parse_bits(bits: list[int], version: int) -> bytes:
    """Parse the data-codeword bit stream into the segment payload."""

    class _Cursor:
        def __init__(self) -> None:
            self.pos = 0

        def take(self, count: int) -> int:
            value = 0
            for _ in range(count):
                if self.pos >= len(bits):
                    raise QRDecodeError("segment ran past the end of the data codewords")
                value = (value << 1) | bits[self.pos]
                self.pos += 1
            return value

    cursor = _Cursor()
    out = bytearray()
    while cursor.pos + 4 <= len(bits):
        mode = cursor.take(4)
        if mode == 0:  # terminator
            break
        # Character-count field width depends on the symbol version
        # (ISO/IEC 18004 table 3): byte mode uses 8/16 bits at the
        # v9/v10 boundary, numeric 10/12/14 and alphanumeric 9/11/13.
        if mode == 4:  # byte
            count = cursor.take(qu.length_in_bits(qu.MODE_8BIT_BYTE, version))
            out += bytes(cursor.take(8) for _ in range(count))
        elif mode == 1:  # numeric
            count = cursor.take(qu.length_in_bits(qu.MODE_NUMBER, version))
            while count >= 3:
                out += b"%03d" % cursor.take(10)
                count -= 3
            if count == 2:
                out += b"%02d" % cursor.take(7)
            elif count == 1:
                out += b"%01d" % cursor.take(4)
        elif mode == 2:  # alphanumeric
            count = cursor.take(qu.length_in_bits(qu.MODE_ALPHA_NUM, version))
            while count >= 2:
                pair = cursor.take(11)
                out += bytes((_ALNUM[pair // 45], _ALNUM[pair % 45]))
                count -= 2
            if count == 1:
                out += bytes((_ALNUM[cursor.take(6)],))
        else:
            raise QRDecodeError(f"unsupported QR segment mode {mode}")
    return bytes(out)


def decode_qr_data_uri(data_uri: str) -> bytes:
    """Decode a base64 PNG QR data URI back into its payload bytes."""
    grid, n, version = _png_to_grid(data_uri)

    format_bits = _read_format(grid, n)
    formats = {qu.BCH_type_info(i): i for i in range(32)}
    if format_bits not in formats:
        raise QRDecodeError(f"format information {format_bits:#x} is not a valid BCH codeword")
    meta = formats[format_bits]
    level = _EC_BY_LEVEL[_EC_BY_BITS[meta >> 3]]
    mask = meta & 0x7

    reserved = _function_modules(version, n)
    if version >= 7:
        version_bits = 0
        for i in range(18):
            version_bits |= grid[n - 11 + i % 3][i // 3] << i
        if version_bits >> 12 != version:
            raise QRDecodeError(
                f"version blocks disagree: {version_bits >> 12} vs geometry {version}"
            )
        for i in range(18):
            reserved[n - 11 + i % 3][i // 3] = True

    stream = _read_codewords(grid, reserved, mask, n)
    blocks = qb.rs_blocks(version, level)

    payload = bytearray()
    for (data_part, ecc_part), block in zip(_deinterleave(stream, blocks), blocks):
        # RS parity length is per-block (blocks in a symbol may differ in size).
        ecc_count = block.total_count - block.data_count
        corrected = _rs_correct(bytes(data_part) + bytes(ecc_part), ecc_count)
        if len(corrected) != block.data_count:
            raise QRDecodeError("decoded block length disagrees with the block table")
        payload += corrected

    bits = [(payload[i // 8] >> (7 - i % 8)) & 1 for i in range(len(payload) * 8)]
    return _parse_bits(bits, version)

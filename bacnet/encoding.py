"""Minimal BACnet application/context tag encoder + decoder.

Implements just enough of ASHRAE 135 clause 20 to build Who-Is / I-Am,
ReadProperty and WriteProperty requests and to decode the replies.  No
external dependencies.

Two rules matter and are easy to get wrong:

* Context tags carrying an unsigned integer use the *minimum* number of
  octets (1..4).  A property identifier of 300 is ``0x19 0x01 0x2C``, not
  ``0x19 0x2C``.  Anything that truncates to one octet silently addresses
  the wrong property.
* ``read_tag`` returns the offset of the *value*, not of the next tag.  A
  caller that does ``data[offset]`` and then ``offset += length`` is only
  correct when ``length == 1``.
"""
from __future__ import annotations

import struct

# ---------------------------------------------------------------------------
# Application tag numbers (ASHRAE 135 Table 20-2)
# ---------------------------------------------------------------------------
NULL = 0
BOOLEAN = 1
UNSIGNED = 2
SIGNED = 3
REAL = 4
DOUBLE = 5
OCTETSTRING = 6
CHARACTERSTRING = 7
BITSTRING = 8
ENUMERATED = 9
DATE = 10
TIME = 11
OBJECTIDENTIFIER = 12

# Context tag numbers used by the services we speak
CTX_OBJECT_ID = 0
CTX_PROPERTY_ID = 1
CTX_ARRAY_INDEX = 2
CTX_VALUE = 3
CTX_PRIORITY = 4


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------
def tag_header(tag: int, context: bool, length: int) -> bytes:
    """Encode a tag header for *length* octets of content."""
    base = (tag << 4) | (0x08 if context else 0)
    if length <= 4:
        return bytes([base | length])
    if length <= 253:
        return bytes([base | 5, length])
    if length <= 0xFFFF:
        return bytes([base | 5, 254]) + struct.pack(">H", length)
    return bytes([base | 5, 255]) + struct.pack(">I", length)


def _min_unsigned(value: int) -> bytes:
    value = int(value)
    if value < 0:
        raise ValueError("unsigned value cannot be negative")
    if value < 0x100:
        return bytes([value])
    if value < 0x10000:
        return struct.pack(">H", value)
    if value < 0x1000000:
        return value.to_bytes(3, "big")
    return struct.pack(">I", value & 0xFFFFFFFF)


def enc_null() -> bytes:
    return tag_header(NULL, False, 0)


def enc_bool(value: bool) -> bytes:
    return tag_header(BOOLEAN, False, 1) + bytes([1 if value else 0])


def enc_unsigned(value: int) -> bytes:
    body = _min_unsigned(value)
    return tag_header(UNSIGNED, False, len(body)) + body


def enc_signed(value: int) -> bytes:
    body = b"\x00"
    for size in (1, 2, 3, 4):
        try:
            body = int(value).to_bytes(size, "big", signed=True)
        except OverflowError:
            continue
        if int.from_bytes(body, "big", signed=True) == int(value):
            break
    return tag_header(SIGNED, False, len(body)) + body


def enc_real(value: float) -> bytes:
    return tag_header(REAL, False, 4) + struct.pack(">f", float(value))


def enc_double(value: float) -> bytes:
    return tag_header(DOUBLE, False, 8) + struct.pack(">d", float(value))


def enc_string(value: str) -> bytes:
    body = b"\x00" + str(value).encode("utf-8")
    return tag_header(CHARACTERSTRING, False, len(body)) + body


def enc_octets(value: bytes) -> bytes:
    return tag_header(OCTETSTRING, False, len(value)) + bytes(value)


def enc_enum(value: int) -> bytes:
    body = _min_unsigned(value)
    return tag_header(ENUMERATED, False, len(body)) + body


def enc_objid(obj_type: int, instance: int) -> bytes:
    raw = struct.pack(">I", ((obj_type & 0x3FF) << 22) | (instance & 0x3FFFFF))
    return tag_header(OBJECTIDENTIFIER, False, 4) + raw


def enc_bitstring(bits) -> bytes:
    bits = list(bits)
    unused = (8 - len(bits) % 8) % 8
    value = 0
    for bit in bits:
        value = (value << 1) | (1 if bit else 0)
    value <<= unused
    nbytes = (len(bits) + 7) // 8
    body = bytes([unused]) + value.to_bytes(nbytes, "big")
    return tag_header(BITSTRING, False, len(body)) + body


# ---------------------------------------------------------------------------
# Context-tagged helpers
# ---------------------------------------------------------------------------
def ctx_objid(tag: int, obj_type: int, instance: int) -> bytes:
    raw = struct.pack(">I", ((obj_type & 0x3FF) << 22) | (instance & 0x3FFFFF))
    return tag_header(tag, True, 4) + raw


def ctx_uint(tag: int, value: int) -> bytes:
    """Context-tagged UNSIGNED using the minimum number of octets."""
    body = _min_unsigned(value)
    return tag_header(tag, True, len(body)) + body


def ctx_enum(tag: int, value: int) -> bytes:
    return ctx_uint(tag, value)


def ctx_real(tag: int, value: float) -> bytes:
    return tag_header(tag, True, 4) + struct.pack(">f", float(value))


def ctx_string(tag: int, value: str) -> bytes:
    body = b"\x00" + str(value).encode("utf-8")
    return tag_header(tag, True, len(body)) + body


def ctx_null(tag: int) -> bytes:
    return tag_header(tag, True, 0)


def ctx_opening(tag: int) -> bytes:
    """Opening tag: the low three bits are 6, never an extended length."""
    return bytes([(tag << 4) | 0x08 | 6])


def ctx_closing(tag: int) -> bytes:
    """Closing tag: the low three bits are 7, never an extended length."""
    return bytes([(tag << 4) | 0x08 | 7])


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------
def read_tag(data: bytes, offset: int):
    """Parse one tag header.

    Returns ``(tag_number, is_context, length, value_offset)`` where
    ``value_offset`` is the index of the first content octet.
    """
    if offset >= len(data):
        raise ValueError("tag header past end of buffer")
    initial = data[offset]
    offset += 1
    tag = initial >> 4
    context = bool(initial & 0x08)
    length = initial & 0x07
    if length == 5:
        ext = data[offset]
        offset += 1
        if ext == 254:
            length = struct.unpack(">H", data[offset:offset + 2])[0]
            offset += 2
        elif ext == 255:
            length = struct.unpack(">I", data[offset:offset + 4])[0]
            offset += 4
        else:
            length = ext
    return tag, context, length, offset


def decode_app(tag: int, data: bytes, offset: int, length: int):
    """Decode an application-tagged value, return (python_value, new_offset)."""
    if tag == NULL:
        return None, offset
    if tag == BOOLEAN:
        return bool(data[offset]), offset + length
    if tag == UNSIGNED:
        return int.from_bytes(data[offset:offset + length], "big"), offset + length
    if tag == SIGNED:
        return int.from_bytes(data[offset:offset + length], "big", signed=True), offset + length
    if tag == REAL:
        return struct.unpack(">f", data[offset:offset + 4])[0], offset + 4
    if tag == DOUBLE:
        return struct.unpack(">d", data[offset:offset + 8])[0], offset + 8
    if tag == OCTETSTRING:
        return data[offset:offset + length], offset + length
    if tag == CHARACTERSTRING:
        raw = data[offset:offset + length]
        if not raw:
            return "", offset + length
        try:
            return raw[1:].decode("utf-8"), offset + length
        except UnicodeDecodeError:
            return raw[1:].decode("latin-1", "replace"), offset + length
    if tag == BITSTRING:
        unused = data[offset]
        nbytes = max(0, length - 1)
        value = int.from_bytes(data[offset + 1:offset + 1 + nbytes], "big")
        nbits = max(0, nbytes * 8 - unused)
        bits = [(value >> (nbytes * 8 - 1 - i)) & 1 for i in range(nbits)]
        return bits, offset + length
    if tag == ENUMERATED:
        return int.from_bytes(data[offset:offset + length], "big"), offset + length
    if tag == DATE:
        year, month, day, weekday = data[offset:offset + 4]
        return {"year": 1900 + year, "month": month, "day": day,
                "weekday": weekday}, offset + 4
    if tag == TIME:
        hour, minute, second, hundredths = data[offset:offset + 4]
        return {"hour": hour, "minute": minute, "second": second,
                "hundredths": hundredths}, offset + 4
    if tag == OBJECTIDENTIFIER:
        value = struct.unpack(">I", data[offset:offset + 4])[0]
        return {"type": value >> 22, "instance": value & 0x3FFFFF}, offset + 4
    return data[offset:offset + length], offset + length


# Hints map a BACnet property datatype to how a context-tagged primitive
# should be interpreted when a device answers with context tags.
_HINT_DECODERS = {
    "real": lambda raw: struct.unpack(">f", raw[:4].ljust(4, b"\x00"))[0],
    "double": lambda raw: struct.unpack(">d", raw[:8].ljust(8, b"\x00"))[0],
    "unsigned": lambda raw: int.from_bytes(raw, "big"),
    "enum": lambda raw: int.from_bytes(raw, "big"),
    "signed": lambda raw: int.from_bytes(raw, "big", signed=True),
    "bool": lambda raw: bool(raw[0]) if raw else False,
    "string": lambda raw: raw[1:].decode("utf-8", "replace") if raw else "",
    "objid": lambda raw: {"type": int.from_bytes(raw[:4], "big") >> 22,
                          "instance": int.from_bytes(raw[:4], "big") & 0x3FFFFF},
}


def decode_context(tag: int, data: bytes, offset: int, length: int, hint=None):
    """Decode the content of a context-tagged primitive."""
    raw = data[offset:offset + length]
    if length == 0:
        return None
    decoder = _HINT_DECODERS.get(hint)
    if decoder is not None:
        try:
            return decoder(raw)
        except Exception:
            return raw
    if length == 4:
        # Ambiguous; a 4-octet context primitive is almost always a REAL.
        try:
            return struct.unpack(">f", raw)[0]
        except Exception:
            return raw
    return raw


def decode_property_value(data: bytes, offset: int, hint=None):
    """Decode a property value: a single primitive or a constructed list."""
    tag, context, length, voff = read_tag(data, offset)

    if context and length == 6:                       # opening tag
        items = []
        pos = voff
        while pos < len(data):
            t, c, l, p = read_tag(data, pos)
            if c and l == 7:                          # closing tag
                break
            if c and l == 6:
                sub, pos = decode_property_value(data, pos, hint)
                items.append(sub)
                continue
            if c:
                items.append(decode_context(t, data, p, l, hint))
                pos = p + l
            else:
                value, pos = decode_app(t, data, p, l)
                items.append(value)
        # A single value is wrapped in an opening/closing pair by most stacks
        # (``[3] &Type``); unwrap it so callers see a scalar, not a 1-list.
        if len(items) == 1:
            return items[0]
        return items

    if context and length == 7:                       # stray closing tag
        return []

    if context:
        return decode_context(tag, data, voff, length, hint)

    value, _ = decode_app(tag, data, voff, length)
    return value


def decode_write_value(data: bytes, offset: int, obj_type: int):
    """Decode the ``propertyValue`` of a WriteProperty.

    Two encodings are seen in the field and both are accepted:

    * constructed: ``3E`` (opening tag 3) + application tag + ``3F``
    * primitive:   context tag 3 carrying the raw content

    Returns ``(value, next_offset)``.  Binary objects are ENUMERATED 0/1,
    analog objects REAL, multi-state UNSIGNED, and a zero-length value is a
    NULL (relinquish).
    """
    if obj_type in (0, 1, 2):
        hint = "real"
    elif obj_type in (3, 4, 5):
        hint = "bool"
    elif obj_type in (13, 14, 19):
        hint = "unsigned"
    else:
        hint = None

    def _coerce(val):
        if val is None:
            return None
        if obj_type in (3, 4, 5):
            return bool(val)
        if obj_type in (13, 14, 19):
            return int(val)
        if obj_type in (0, 1, 2):
            return float(val)
        return val

    tag, context, length, voff = read_tag(data, offset)

    if context and length == 6:                       # opening tag 3
        pos = voff
        t, c, l, p = read_tag(data, pos)
        if c and l == 7:                              # empty -> NULL
            return None, p
        if c:
            value = decode_context(t, data, p, l, hint)
            nxt = p + l
        else:
            value, nxt = decode_app(t, data, p, l)
        if nxt < len(data):
            t2, c2, l2, p2 = read_tag(data, nxt)
            if c2 and l2 == 7:
                nxt = p2
        return _coerce(value), nxt

    if length == 0:
        return None, voff

    if context:
        return _coerce(decode_context(tag, data, voff, length, hint)), voff + length

    value, nxt = decode_app(tag, data, voff, length)
    return _coerce(value), nxt


def encode_write_value(obj_type: int, value, state_text=None) -> bytes:
    """Encode a WriteProperty ``propertyValue``.

    ASHRAE 135 defines the value as ``[3] ABSTRACT-SYNTAX.&Type``.  The form
    every major stack emits -- and what bacpypes3 produces -- is the
    constructed encoding: opening tag 3, the application-tagged value, then
    closing tag 3.  Analog objects carry a REAL, binary objects an ENUMERATED
    0/1, multi-state an UNSIGNED, and ``None`` a NULL (relinquish).
    """
    if value is None:
        body = enc_null()
    elif obj_type in (0, 1, 2):                      # analog in/out/value
        body = enc_real(float(value))
    elif obj_type in (3, 4, 5):                      # binary in/out/value
        if isinstance(value, str):
            lowered = value.strip().lower()
            value = lowered in ("1", "true", "on", "yes", "active")
        body = enc_enum(1 if value else 0)
    elif obj_type in (13, 14, 19):                   # multi-state
        if isinstance(value, str) and state_text:
            for index, label in enumerate(state_text, start=1):
                if label.strip().lower() == value.strip().lower():
                    return ctx_opening(CTX_VALUE) + enc_unsigned(index) + ctx_closing(CTX_VALUE)
        body = enc_unsigned(int(value))
    elif isinstance(value, bool):
        body = enc_enum(1 if value else 0)
    elif isinstance(value, int):
        body = enc_unsigned(value)
    elif isinstance(value, float):
        body = enc_real(value)
    else:
        body = enc_string(str(value))
    return ctx_opening(CTX_VALUE) + body + ctx_closing(CTX_VALUE)

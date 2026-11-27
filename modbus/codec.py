"""Modbus codec: RTU framing, TCP MBAP framing, function codes and
register data formats.

Deliberately dependency-free so the desktop build stays small.  The CRC and
frame layouts follow the Modbus Application Protocol Specification v1.1b3 and
the Modbus over Serial Line Specification v1.02.
"""
from __future__ import annotations

import struct

# ---------------------------------------------------------------------------
# Function codes
# ---------------------------------------------------------------------------
FC_READ_COILS = 0x01
FC_READ_DISCRETE_INPUTS = 0x02
FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_INPUT_REGISTERS = 0x04
FC_WRITE_SINGLE_COIL = 0x05
FC_WRITE_SINGLE_REGISTER = 0x06
FC_WRITE_MULTIPLE_COILS = 0x0F
FC_WRITE_MULTIPLE_REGISTERS = 0x10

FUNCTION_NAMES = {
    FC_READ_COILS: "Read Coils",
    FC_READ_DISCRETE_INPUTS: "Read Discrete Inputs",
    FC_READ_HOLDING_REGISTERS: "Read Holding Registers",
    FC_READ_INPUT_REGISTERS: "Read Input Registers",
    FC_WRITE_SINGLE_COIL: "Write Single Coil",
    FC_WRITE_SINGLE_REGISTER: "Write Single Register",
    FC_WRITE_MULTIPLE_COILS: "Write Multiple Coils",
    FC_WRITE_MULTIPLE_REGISTERS: "Write Multiple Registers",
}

EXCEPTION_CODES = {
    0x01: "illegal function",
    0x02: "illegal data address",
    0x03: "illegal data value",
    0x04: "slave device failure",
    0x05: "acknowledge",
    0x06: "slave device busy",
    0x08: "memory parity error",
    0x0A: "gateway path unavailable",
    0x0B: "gateway target failed to respond",
}

MAX_READ_REGISTERS = 125
MAX_READ_BITS = 2000
MAX_WRITE_REGISTERS = 123
MAX_WRITE_BITS = 1968


class ModbusError(Exception):
    """Transport or protocol failure."""

    def __init__(self, message: str, exception_code: int | None = None):
        super().__init__(message)
        self.exception_code = exception_code


class ModbusException(ModbusError):
    """The slave answered with a Modbus exception response."""

    def __init__(self, function: int, code: int):
        label = EXCEPTION_CODES.get(code, f"exception {code}")
        super().__init__(
            f"{FUNCTION_NAMES.get(function, f'function {function:#04x}')}: {label} "
            f"(code {code:#04x})", exception_code=code)
        self.function = function
        self.code = code


# ---------------------------------------------------------------------------
# CRC-16 / Modbus  (poly 0xA001 reflected, init 0xFFFF, no final XOR)
# ---------------------------------------------------------------------------
def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(pdu: bytes) -> bytes:
    crc = crc16(pdu)
    return pdu + bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def check_crc(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    body, crc_bytes = frame[:-2], frame[-2:]
    expected = crc16(body)
    return crc_bytes[0] == (expected & 0xFF) and crc_bytes[1] == (expected >> 8)


# ---------------------------------------------------------------------------
# RTU framing
# ---------------------------------------------------------------------------
def build_rtu(unit: int, pdu: bytes) -> bytes:
    return append_crc(bytes([unit & 0xFF]) + pdu)


def parse_rtu(frame: bytes):
    """Return ``(unit, pdu)`` or raise ModbusError on a bad CRC."""
    if len(frame) < 5:
        raise ModbusError("RTU frame too short")
    if not check_crc(frame):
        raise ModbusError("RTU CRC mismatch")
    return frame[0], frame[1:-2]


# ---------------------------------------------------------------------------
# TCP MBAP framing
# ---------------------------------------------------------------------------
def build_mbap(transaction: int, unit: int, pdu: bytes) -> bytes:
    header = struct.pack(">HHHB", transaction & 0xFFFF, 0, len(pdu) + 1, unit & 0xFF)
    return header + pdu


def parse_mbap(frame: bytes):
    """Return ``(transaction, unit, pdu)`` or raise ModbusError."""
    if len(frame) < 8:
        raise ModbusError("MBAP frame too short")
    transaction, protocol, length, unit = struct.unpack(">HHHB", frame[:7])
    if protocol != 0:
        raise ModbusError(f"unexpected MBAP protocol id {protocol}")
    pdu = frame[7:7 + length - 1]
    if len(pdu) != length - 1:
        raise ModbusError("MBAP length does not match payload")
    return transaction, unit, pdu


# ---------------------------------------------------------------------------
# PDU builders
# ---------------------------------------------------------------------------
def build_read_request(function: int, address: int, count: int) -> bytes:
    if function in (FC_READ_COILS, FC_READ_DISCRETE_INPUTS):
        limit = MAX_READ_BITS
    elif function in (FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS):
        limit = MAX_READ_REGISTERS
    else:
        raise ModbusError(f"not a read function: {function:#04x}")
    if not 1 <= count <= limit:
        raise ModbusError(f"quantity {count} outside 1..{limit}")
    return struct.pack(">BHH", function, address & 0xFFFF, count)


def build_write_single_coil(address: int, value: bool) -> bytes:
    return struct.pack(">BHH", FC_WRITE_SINGLE_COIL, address & 0xFFFF,
                       0xFF00 if value else 0x0000)


def build_write_single_register(address: int, value: int) -> bytes:
    return struct.pack(">BHH", FC_WRITE_SINGLE_REGISTER, address & 0xFFFF,
                       int(value) & 0xFFFF)


def build_write_multiple_coils(address: int, values) -> bytes:
    values = [bool(v) for v in values]
    if not 1 <= len(values) <= MAX_WRITE_BITS:
        raise ModbusError("coil quantity out of range")
    nbytes = (len(values) + 7) // 8
    packed = bytearray(nbytes)
    for i, bit in enumerate(values):
        if bit:
            packed[i // 8] |= 1 << (i % 8)
    return (struct.pack(">BHHB", FC_WRITE_MULTIPLE_COILS, address & 0xFFFF,
                        len(values), nbytes) + bytes(packed))


def build_write_multiple_registers(address: int, values) -> bytes:
    values = [int(v) & 0xFFFF for v in values]
    if not 1 <= len(values) <= MAX_WRITE_REGISTERS:
        raise ModbusError("register quantity out of range")
    body = b"".join(struct.pack(">H", v) for v in values)
    return (struct.pack(">BHHB", FC_WRITE_MULTIPLE_REGISTERS, address & 0xFFFF,
                        len(values), len(body)) + body)


# ---------------------------------------------------------------------------
# PDU parsers
# ---------------------------------------------------------------------------
def parse_response(pdu: bytes):
    """Parse a response PDU into a Python result.

    Raises :class:`ModbusException` for an exception response.
    """
    if not pdu:
        raise ModbusError("empty PDU")
    function = pdu[0]
    if function & 0x80:
        code = pdu[1] if len(pdu) > 1 else 0
        raise ModbusException(function & 0x7F, code)

    if function in (FC_READ_COILS, FC_READ_DISCRETE_INPUTS):
        if len(pdu) < 2:
            raise ModbusError("truncated bit response")
        nbytes = pdu[1]
        data = pdu[2:2 + nbytes]
        if len(data) != nbytes:
            raise ModbusError("bit response length mismatch")
        bits = []
        for i in range(nbytes * 8):
            bits.append(bool(data[i // 8] & (1 << (i % 8))))
        return bits

    if function in (FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS):
        if len(pdu) < 2:
            raise ModbusError("truncated register response")
        nbytes = pdu[1]
        data = pdu[2:2 + nbytes]
        if len(data) != nbytes or nbytes % 2:
            raise ModbusError("register response length mismatch")
        return list(struct.unpack(f">{nbytes // 2}H", data))

    if function in (FC_WRITE_SINGLE_COIL, FC_WRITE_SINGLE_REGISTER,
                    FC_WRITE_MULTIPLE_COILS, FC_WRITE_MULTIPLE_REGISTERS):
        if len(pdu) < 5:
            raise ModbusError("truncated write response")
        address, value = struct.unpack(">HH", pdu[1:5])
        return {"address": address, "value": value}

    raise ModbusError(f"unexpected function code {function:#04x}")


# ---------------------------------------------------------------------------
# Register data formats
# ---------------------------------------------------------------------------
FORMATS = (
    "u16", "i16", "u32", "i32", "f32", "f32s",
    "u32s", "i32s", "hex", "bin",
)

FORMAT_LABELS = {
    "u16": "Unsigned 16",
    "i16": "Signed 16",
    "u32": "Unsigned 32",
    "i32": "Signed 32",
    "f32": "Float 32 (ABCD)",
    "f32s": "Float 32 (CDAB)",
    "u32s": "Unsigned 32 (CDAB)",
    "i32s": "Signed 32 (CDAB)",
    "hex": "Hex",
    "bin": "Binary",
}


def format_registers(registers, fmt: str = "u16"):
    """Render a list of 16-bit registers in *fmt*.

    Returns a list of values; 32-bit formats consume two registers at a time.
    """
    registers = [int(r) & 0xFFFF for r in registers]
    if fmt in ("u16", "i16", "hex", "bin"):
        out = []
        for r in registers:
            if fmt == "u16":
                out.append(r)
            elif fmt == "i16":
                out.append(struct.unpack(">h", struct.pack(">H", r))[0])
            elif fmt == "hex":
                out.append(f"0x{r:04X}")
            else:
                out.append(f"{r:016b}")
        return out

    out = []
    for i in range(0, len(registers) - 1, 2):
        hi, lo = registers[i], registers[i + 1]
        if fmt in ("f32", "u32", "i32"):
            raw = struct.pack(">HH", hi, lo)
        else:  # word-swapped
            raw = struct.pack(">HH", lo, hi)
        if fmt in ("f32", "f32s"):
            out.append(struct.unpack(">f", raw)[0])
        elif fmt in ("u32", "u32s"):
            out.append(struct.unpack(">I", raw)[0])
        else:
            out.append(struct.unpack(">i", raw)[0])
    return out


def encode_registers(value, fmt: str = "u16") -> list[int]:
    """Turn one Python value into the 16-bit registers needed to hold it."""
    if fmt in ("u16", "hex", "bin"):
        return [int(value) & 0xFFFF]
    if fmt == "i16":
        return [struct.unpack(">H", struct.pack(">h", int(value)))[0]]
    if fmt in ("f32", "f32s"):
        raw = struct.pack(">f", float(value))
    elif fmt in ("u32", "u32s"):
        raw = struct.pack(">I", int(value) & 0xFFFFFFFF)
    elif fmt in ("i32", "i32s"):
        raw = struct.pack(">i", int(value))
    else:
        raise ModbusError(f"unknown format {fmt}")
    hi, lo = struct.unpack(">HH", raw)
    if fmt.endswith("s"):
        return [lo, hi]
    return [hi, lo]


def registers_to_int(registers) -> int:
    value = 0
    for r in registers:
        value = (value << 16) | (int(r) & 0xFFFF)
    return value

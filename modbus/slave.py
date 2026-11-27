"""A Modbus slave simulator.

Two front-ends share one data model:

* :class:`ModbusTcpSlave` listens on TCP and serves any number of clients.
* :class:`ModbusRtuSlave` drives a serial-like object, which lets the test
  suite run a real RTU master/slave exchange over an in-memory pipe.

The point is to make the master section demonstrable with no hardware, and to
give the test suite a slave that is independent of the master's codec.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

from . import codec as mc


class UnitData:
    """Register and coil image for one unit id."""

    def __init__(self, unit: int, coils: int = 256, discrete: int = 256,
                 holding: int = 1000, input_regs: int = 1000):
        self.unit = unit
        self.coils = [0] * coils
        self.discrete = [0] * discrete
        self.holding = [0] * holding
        self.input = [0] * input_regs

    def seed(self):
        """Populate plausible values so the viewer has something to show."""
        for i in range(min(20, len(self.holding))):
            self.holding[i] = 100 + i * 7
        for i in range(min(10, len(self.input))):
            self.input[i] = 500 + i * 3
        for i in range(min(8, len(self.coils))):
            self.coils[i] = 1 if i % 2 == 0 else 0
        for i in range(min(8, len(self.discrete))):
            self.discrete[i] = 1 if i % 3 else 0
        # A float at 100/101 and a 32-bit counter at 110/111
        if len(self.holding) > 112:
            raw = struct.pack(">f", 21.5)
            hi, lo = struct.unpack(">HH", raw)
            self.holding[100], self.holding[101] = hi, lo
            raw = struct.pack(">I", 123456)
            hi, lo = struct.unpack(">HH", raw)
            self.holding[110], self.holding[111] = hi, lo


class SlaveCore:
    """Function-code handling shared by every front-end."""

    def __init__(self, units=None, strict_address: bool = True):
        self.units: dict[int, UnitData] = {}
        for unit in (units or [1]):
            data = UnitData(int(unit))
            data.seed()
            self.units[int(unit)] = data
        self.strict_address = strict_address
        self.requests = 0
        self.writes: list[dict] = []
        self._lock = threading.Lock()

    def add_unit(self, unit: int, seed: bool = True) -> UnitData:
        data = UnitData(int(unit))
        if seed:
            data.seed()
        self.units[int(unit)] = data
        return data

    def _unit(self, unit: int):
        """Return the unit image, or ``None`` if this slave does not own it.

        A real slave stays silent for another device's address, so an unknown
        unit id must produce no response at all -- that is what makes a unit
        scan meaningful.
        """
        return self.units.get(unit)

    def _check(self, start: int, count: int, size: int, function: int):
        if count < 1 or start < 0 or start + count > size:
            if self.strict_address:
                raise mc.ModbusException(function, 0x02)

    def handle_pdu(self, unit: int, pdu: bytes) -> bytes:
        if not pdu:
            return b""
        function = pdu[0]
        with self._lock:
            self.requests += 1
            try:
                return self._dispatch(unit, function, pdu)
            except mc.ModbusException as exc:
                return bytes([(function | 0x80) & 0xFF, exc.code & 0xFF])

    def _dispatch(self, unit: int, function: int, pdu: bytes) -> bytes:
        data = self._unit(unit)
        if data is None:
            return b""

        if function in (mc.FC_READ_COILS, mc.FC_READ_DISCRETE_INPUTS):
            start, count = struct.unpack(">HH", pdu[1:5])
            source = data.coils if function == mc.FC_READ_COILS else data.discrete
            self._check(start, count, len(source), function)
            nbytes = (count + 7) // 8
            packed = bytearray(nbytes)
            for i in range(count):
                if source[start + i]:
                    packed[i // 8] |= 1 << (i % 8)
            return bytes([function, nbytes]) + bytes(packed)

        if function in (mc.FC_READ_HOLDING_REGISTERS, mc.FC_READ_INPUT_REGISTERS):
            start, count = struct.unpack(">HH", pdu[1:5])
            source = data.holding if function == mc.FC_READ_HOLDING_REGISTERS else data.input
            self._check(start, count, len(source), function)
            body = b"".join(struct.pack(">H", source[start + i] & 0xFFFF)
                            for i in range(count))
            return bytes([function, len(body)]) + body

        if function == mc.FC_WRITE_SINGLE_COIL:
            address, value = struct.unpack(">HH", pdu[1:5])
            self._check(address, 1, len(data.coils), function)
            if value not in (0x0000, 0xFF00):
                raise mc.ModbusException(function, 0x03)
            data.coils[address] = 1 if value == 0xFF00 else 0
            self.writes.append({"unit": unit, "type": "coil", "address": address,
                                "value": data.coils[address], "time": time.time()})
            return pdu[:5]

        if function == mc.FC_WRITE_SINGLE_REGISTER:
            address, value = struct.unpack(">HH", pdu[1:5])
            self._check(address, 1, len(data.holding), function)
            data.holding[address] = value
            self.writes.append({"unit": unit, "type": "register", "address": address,
                                "value": value, "time": time.time()})
            return pdu[:5]

        if function == mc.FC_WRITE_MULTIPLE_COILS:
            start, count, nbytes = struct.unpack(">HHB", pdu[1:6])
            self._check(start, count, len(data.coils), function)
            body = pdu[6:6 + nbytes]
            if len(body) != nbytes:
                raise mc.ModbusException(function, 0x03)
            for i in range(count):
                data.coils[start + i] = 1 if body[i // 8] & (1 << (i % 8)) else 0
            self.writes.append({"unit": unit, "type": "coils", "address": start,
                                "count": count, "time": time.time()})
            return struct.pack(">BHH", function, start, count)

        if function == mc.FC_WRITE_MULTIPLE_REGISTERS:
            start, count, nbytes = struct.unpack(">HHB", pdu[1:6])
            self._check(start, count, len(data.holding), function)
            body = pdu[6:6 + nbytes]
            if len(body) != nbytes or nbytes != count * 2:
                raise mc.ModbusException(function, 0x03)
            for i in range(count):
                data.holding[start + i] = struct.unpack(">H", body[i * 2:i * 2 + 2])[0]
            self.writes.append({"unit": unit, "type": "registers", "address": start,
                                "count": count, "time": time.time()})
            return struct.pack(">BHH", function, start, count)

        raise mc.ModbusException(function, 0x01)


class ModbusTcpSlave(SlaveCore):
    """Modbus TCP server."""

    def __init__(self, host: str = "0.0.0.0", port: int = 5020, units=None):
        super().__init__(units=units)
        self.host = host
        self.port = int(port)
        self._server: socket.socket | None = None
        self._running = False
        self._thread: threading.Thread | None = None
        self._clients: list[socket.socket] = []

    def start(self):
        if self._running:
            return
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(8)
        server.settimeout(0.3)
        self._server = server
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def _accept_loop(self):
        while self._running and self._server:
            try:
                conn, _addr = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._clients.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket):
        conn.settimeout(1.0)
        try:
            while self._running:
                header = self._recv_exact(conn, 7, allow_idle=True)
                if header is None:
                    continue          # idle keep-alive, nothing pending
                if len(header) < 7:
                    break
                transaction, _proto, length, unit = struct.unpack(">HHHB", header)
                body = self._recv_exact(conn, max(0, length - 1))
                if body is None or len(body) < length - 1:
                    break
                response = self.handle_pdu(unit, body)
                if not response:
                    continue
                out = struct.pack(">HHHB", transaction, 0, len(response) + 1, unit) + response
                conn.sendall(out)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass
            if conn in self._clients:
                self._clients.remove(conn)

    @staticmethod
    def _recv_exact(conn: socket.socket, count: int, allow_idle: bool = False):
        """Read exactly *count* bytes.

        With ``allow_idle`` a timeout before the first byte returns ``None``
        so the caller can keep the connection open; a timeout mid-frame
        returns whatever arrived, which the caller treats as a short frame.
        """
        chunks = bytearray()
        while len(chunks) < count:
            try:
                chunk = conn.recv(count - len(chunks))
            except socket.timeout:
                if allow_idle and not chunks:
                    return None
                return bytes(chunks)
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def stop(self):
        self._running = False
        for conn in list(self._clients):
            try:
                conn.close()
            except OSError:
                pass
        self._clients.clear()
        if self._server:
            try:
                self._server.close()
            except OSError:
                pass
        self._server = None

    @property
    def running(self) -> bool:
        return self._running

    def describe(self) -> dict:
        return {"kind": "tcp", "host": self.host, "port": self.port,
                "running": self._running, "units": sorted(self.units),
                "requests": self.requests}


class FakeSerial:
    """Minimal serial-like object backed by two in-memory buffers."""

    def __init__(self, rx: bytearray, tx: bytearray):
        self.rx = rx
        self.tx = tx
        self.timeout = 0.5
        self.is_open = True

    @property
    def in_waiting(self) -> int:
        return len(self.rx)

    def read(self, size: int = 1) -> bytes:
        deadline = time.time() + self.timeout
        while not self.rx and time.time() < deadline:
            time.sleep(0.002)
        chunk = bytes(self.rx[:size])
        del self.rx[:size]
        return chunk

    def write(self, data: bytes) -> int:
        self.tx.extend(data)
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        self.rx.clear()

    def close(self):
        self.is_open = False


def make_rtu_pair():
    """Return ``(master_serial, slave_serial)`` wired back to back."""
    a_to_b = bytearray()
    b_to_a = bytearray()
    master = FakeSerial(b_to_a, a_to_b)   # writes land in a_to_b, reads from b_to_a
    slave = FakeSerial(a_to_b, b_to_a)    # writes land in b_to_a, reads from a_to_b
    return master, slave


class ModbusRtuSlave(SlaveCore):
    """RTU slave driving a serial-like object."""

    def __init__(self, serial_port, units=None):
        super().__init__(units=units)
        self.serial = serial_port
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            head = self.serial.read(7)
            if len(head) < 7:
                continue
            function = head[1]
            if function in (mc.FC_WRITE_MULTIPLE_COILS, mc.FC_WRITE_MULTIPLE_REGISTERS):
                nbytes = head[6]
                rest = self.serial.read(nbytes + 2)
                frame = head + rest
            else:
                rest = self.serial.read(1)
                frame = head + rest
            try:
                unit, pdu = mc.parse_rtu(frame)
            except mc.ModbusError:
                continue
            response = self.handle_pdu(unit, pdu)
            if not response:
                continue
            self.serial.write(mc.append_crc(bytes([unit]) + response))

    def stop(self):
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def describe(self) -> dict:
        return {"kind": "rtu", "running": self._running,
                "units": sorted(self.units), "requests": self.requests}

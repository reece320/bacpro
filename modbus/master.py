"""Modbus master: TCP and RTU transports plus read/write/scan helpers.

The master speaks the wire protocol directly through :mod:`modbus.codec`, so
it works against any compliant slave (PLCs, VFDs, meters, gateways).
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import codec as mc


class ModbusTransport:
    """Base class: exchange one ADU with the slave."""

    kind = "base"

    def exchange(self, adu: bytes) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self):
        pass

    def describe(self) -> dict:
        return {"kind": self.kind}


class TcpTransport(ModbusTransport):
    kind = "tcp"

    def __init__(self, host: str, port: int = 502, timeout: float = 2.0):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def _connect(self):
        if self._sock is not None:
            return
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock

    def exchange(self, adu: bytes) -> bytes:
        with self._lock:
            self._connect()
            self._sock.sendall(adu)
            header = self._recv_exact(7)
            if len(header) < 7:
                raise mc.ModbusError("connection closed before MBAP header")
            _txn, _proto, length, _unit = struct.unpack(">HHHB", header)
            body = self._recv_exact(max(0, length - 1))
            return header + body

    def _recv_exact(self, count: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < count:
            try:
                chunk = self._sock.recv(count - len(chunks))
            except socket.timeout:
                raise mc.ModbusError("timed out waiting for the slave")
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def describe(self) -> dict:
        return {"kind": "tcp", "host": self.host, "port": self.port}


class RtuTransport(ModbusTransport):
    kind = "rtu"

    def __init__(self, port: str, baudrate: int = 9600, parity: str = "N",
                 stopbits: int = 1, bytesize: int = 8, timeout: float = 1.0):
        try:
            import serial  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise mc.ModbusError(
                "pyserial is required for Modbus RTU; install pyserial") from exc
        import serial

        self.port = port
        self.baudrate = int(baudrate)
        self.timeout = timeout
        self._serial = serial.Serial(
            port=port, baudrate=int(baudrate), bytesize=int(bytesize),
            parity=str(parity).upper()[:1] or "N", stopbits=int(stopbits),
            timeout=timeout, write_timeout=timeout,
        )
        self._lock = threading.Lock()
        time.sleep(0.05)
        try:
            self._serial.reset_input_buffer()
        except Exception:
            pass

    def exchange(self, adu: bytes) -> bytes:
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write(adu)
            self._serial.flush()
            head = self._read_exact(3)
            if len(head) < 3:
                raise mc.ModbusError("timed out waiting for the slave")
            function = head[1]
            if function & 0x80:
                tail = self._read_exact(2)
            elif function in (mc.FC_READ_COILS, mc.FC_READ_DISCRETE_INPUTS,
                              mc.FC_READ_HOLDING_REGISTERS, mc.FC_READ_INPUT_REGISTERS):
                tail = self._read_exact(head[2] + 2)
            else:
                tail = self._read_exact(5)
            return head + tail

    def _read_exact(self, count: int) -> bytes:
        chunks = bytearray()
        deadline = time.time() + max(0.05, self.timeout)
        while len(chunks) < count and time.time() < deadline:
            chunk = self._serial.read(count - len(chunks))
            if chunk:
                chunks.extend(chunk)
        return bytes(chunks)

    def close(self):
        try:
            self._serial.close()
        except Exception:
            pass

    def describe(self) -> dict:
        return {"kind": "rtu", "port": self.port, "baudrate": self.baudrate}


class ModbusMaster:
    """Read/write helper bound to one transport and unit id."""

    def __init__(self, transport: ModbusTransport, unit: int = 1,
                 timeout: float = 2.0, retries: int = 1):
        self.transport = transport
        self.unit = int(unit) & 0xFF
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self._transaction = 0
        self._lock = threading.Lock()
        self._exchange_lock = threading.Lock()   # one transaction at a time

    # ------------------------------------------------------------------
    # Low level
    # ------------------------------------------------------------------
    def _next_transaction(self) -> int:
        with self._lock:
            self._transaction = (self._transaction + 1) & 0xFFFF
            return self._transaction

    def _exchange_pdu(self, pdu: bytes, unit: int | None = None) -> bytes:
        unit = self.unit if unit is None else (int(unit) & 0xFF)
        if self.transport.kind == "tcp":
            adu = mc.build_mbap(self._next_transaction(), unit, pdu)
        else:
            adu = mc.build_rtu(unit, pdu)

        last_error: Exception | None = None
        for _attempt in range(self.retries + 1):
            try:
                with self._exchange_lock:
                    raw = self.transport.exchange(adu)
                if self.transport.kind == "tcp":
                    _txn, _unit, response = mc.parse_mbap(raw)
                else:
                    _unit, response = mc.parse_rtu(raw)
                return response
            except mc.ModbusException:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(0.05)
        raise mc.ModbusError(str(last_error) if last_error else "no response")

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def read(self, function: int, address: int, count: int,
             unit: int | None = None):
        pdu = mc.build_read_request(function, address, count)
        response = self._exchange_pdu(pdu, unit)
        values = mc.parse_response(response)
        return values[:count]

    def read_coils(self, address: int, count: int = 1, unit: int | None = None):
        return self.read(mc.FC_READ_COILS, address, count, unit)

    def read_discrete_inputs(self, address: int, count: int = 1, unit: int | None = None):
        return self.read(mc.FC_READ_DISCRETE_INPUTS, address, count, unit)

    def read_holding_registers(self, address: int, count: int = 1, unit: int | None = None):
        return self.read(mc.FC_READ_HOLDING_REGISTERS, address, count, unit)

    def read_input_registers(self, address: int, count: int = 1, unit: int | None = None):
        return self.read(mc.FC_READ_INPUT_REGISTERS, address, count, unit)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def write_coil(self, address: int, value: bool, unit: int | None = None):
        pdu = mc.build_write_single_coil(address, value)
        return mc.parse_response(self._exchange_pdu(pdu, unit))

    def write_register(self, address: int, value: int, unit: int | None = None):
        pdu = mc.build_write_single_register(address, value)
        return mc.parse_response(self._exchange_pdu(pdu, unit))

    def write_coils(self, address: int, values, unit: int | None = None):
        pdu = mc.build_write_multiple_coils(address, values)
        return mc.parse_response(self._exchange_pdu(pdu, unit))

    def write_registers(self, address: int, values, unit: int | None = None):
        pdu = mc.build_write_multiple_registers(address, values)
        return mc.parse_response(self._exchange_pdu(pdu, unit))

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def probe(self, unit: int, function: int = mc.FC_READ_HOLDING_REGISTERS,
              address: int = 0, count: int = 1, timeout: float = 0.4) -> dict:
        """Ask one unit id whether it exists.

        A slave that answers -- even with an exception -- is online; a slave
        that stays silent is not.
        """
        old_timeout = self.timeout
        self.timeout = timeout
        started = time.time()
        try:
            values = self.read(function, address, count, unit=unit)
            return {"unit": unit, "online": True, "response": "data",
                    "values": values, "ms": round((time.time() - started) * 1000)}
        except mc.ModbusException as exc:
            return {"unit": unit, "online": True, "response": "exception",
                    "error": str(exc), "code": exc.code,
                    "ms": round((time.time() - started) * 1000)}
        except Exception as exc:  # noqa: BLE001
            return {"unit": unit, "online": False, "response": "timeout",
                    "error": str(exc), "ms": round((time.time() - started) * 1000)}
        finally:
            self.timeout = old_timeout

    def _spawn(self):
        """A throwaway master on its own TCP connection, for parallel scans."""
        info = self.transport.describe()
        if info.get("kind") != "tcp":
            return None
        transport = TcpTransport(info["host"], info["port"], timeout=self.timeout)
        return ModbusMaster(transport, unit=self.unit, timeout=self.timeout, retries=0)

    def _probe_isolated(self, unit, function, address, count, timeout):
        master = self._spawn()
        if master is None:
            return self.probe(unit, function, address, count, timeout)
        try:
            return master.probe(unit, function, address, count, timeout)
        finally:
            master.close()

    def scan(self, units=None, function: int = mc.FC_READ_HOLDING_REGISTERS,
             address: int = 0, count: int = 1, timeout: float = 0.4,
             workers: int = 16) -> list[dict]:
        """Scan a range of unit ids and return the ones that answered."""
        units = list(range(1, 248)) if units is None else list(units)
        results: list[dict] = []
        if self.transport.kind == "rtu":
            # A serial bus is shared: probing in parallel would collide.
            for unit in units:
                results.append(self.probe(unit, function, address, count, timeout))
            return results

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(self._probe_isolated, unit, function, address, count, timeout): unit
                for unit in units
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001
                    results.append({"unit": futures[future], "online": False,
                                    "response": "error", "error": str(exc)})
        results.sort(key=lambda item: item["unit"])
        return results

    def close(self):
        self.transport.close()

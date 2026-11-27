"""In-memory MS/TP bus + slave device, used to test the master stack.

``FakeSerial`` gives the master a byte-stream endpoint; ``MstpSlaveSim`` is a
BACnet device on the other end that answers Who-Is / ReadProperty /
WriteProperty and passes the token back.  This exercises the real frame codec,
CRC, token state machine and APDU exchange without RS-485 hardware.
"""
from __future__ import annotations

import queue
import threading
import time

from .mstp import (
    FRAME_DATA_EXPECTING_REPLY, FRAME_DATA_NOT_EXPECTING_REPLY,
    FRAME_POLL_FOR_MASTER, FRAME_REPLY_TO_POLL, FRAME_TOKEN, MstpTransport,
    apdu_to_npdu, build_frame, npdu_to_apdu, parse_frame,
)
from .simulator import BacnetSimulator, _build_devices


class FakeSerial:
    """One end of a two-way byte pipe."""

    def __init__(self, rx, tx):
        self.rx = rx
        self.tx = tx
        self.timeout = 0.05
        self._buffer = bytearray()
        self.closed = False

    @property
    def in_waiting(self) -> int:
        while True:
            try:
                self._buffer += self.rx.get_nowait()
            except queue.Empty:
                break
        return len(self._buffer)

    def read(self, size: int = 1) -> bytes:
        if self.in_waiting < size:
            size = len(self._buffer)
        chunk = bytes(self._buffer[:size])
        del self._buffer[:size]
        return chunk

    def write(self, data: bytes) -> int:
        self.tx.put(bytes(data))
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


def make_bus():
    """Return (master_serial, slave_serial) connected back to back."""
    a_to_b = queue.Queue()
    b_to_a = queue.Queue()
    return FakeSerial(b_to_a, a_to_b), FakeSerial(a_to_b, b_to_a)


class MstpSlaveSim(BacnetSimulator):
    """A BACnet MS/TP device that shares the simulator's object model."""

    def __init__(self, serial_port, mac: int = 2, master_mac: int = 1):
        # Deliberately not calling BacnetSimulator.__init__ (no UDP socket).
        self.port = 0
        self.devices = _build_devices()
        self.sock = None
        self._running = False
        self._thread = None
        self.requests = 0
        self.write_log = []
        self.serial = serial_port
        self.mac = mac
        self.master_mac = master_mac
        self._buffer = b""

    # -- outbound ----------------------------------------------------------
    def _send(self, apdu: bytes, addr=None):
        frame = build_frame(FRAME_DATA_NOT_EXPECTING_REPLY, self.master_mac,
                            self.mac, apdu_to_npdu(apdu))
        try:
            self.serial.write(frame)
            self.serial.flush()
        except Exception:
            pass

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                waiting = self.serial.in_waiting
                if not waiting:
                    time.sleep(0.003)
                    continue
                self._buffer += self.serial.read(waiting)
            except Exception:
                break

            while True:
                parsed = parse_frame(self._buffer)
                if not parsed:
                    break
                frame_type, dest, src, data, consumed = parsed
                self._buffer = self._buffer[consumed:]
                if frame_type in ("bad-crc", "bad-length"):
                    continue
                self._handle_frame(frame_type, dest, src, data)

    def _handle_frame(self, frame_type, dest, src, data):
        if frame_type == FRAME_TOKEN and dest == self.mac:
            self._pass_token(src)
            return
        if frame_type == FRAME_POLL_FOR_MASTER and dest == self.mac:
            self.serial.write(build_frame(FRAME_REPLY_TO_POLL, src, self.mac))
            return
        if frame_type in (FRAME_DATA_EXPECTING_REPLY, FRAME_DATA_NOT_EXPECTING_REPLY):
            apdu = npdu_to_apdu(data)
            if not apdu:
                return
            self._dispatch(apdu)

    def _pass_token(self, to_mac: int):
        try:
            self.serial.write(build_frame(FRAME_TOKEN, to_mac, self.mac))
            self.serial.flush()
        except Exception:
            pass

    def _dispatch(self, apdu: bytes):
        pdu_type = apdu[0] & 0xF0
        if pdu_type == 0x10 and len(apdu) >= 2 and apdu[1] == 0x08:
            self._handle_who_is(apdu, None)
        elif pdu_type == 0x00 and len(apdu) >= 4 and apdu[3] == 0x0C:
            self.requests += 1
            self._handle_read_property(apdu, None)
        elif pdu_type == 0x00 and len(apdu) >= 4 and apdu[3] == 0x0F:
            self.requests += 1
            self._handle_write_property(apdu, None)


def build_loopback(mac: int = 1, slave_mac: int = 2, max_master: int = 127):
    """Create a started master transport talking to a simulated device."""
    master_serial, slave_serial = make_bus()
    slave = MstpSlaveSim(slave_serial, mac=slave_mac, master_mac=mac)
    slave.start()
    transport = MstpTransport(master_serial, mac=mac, max_master=max_master)
    transport.start()
    return transport, slave

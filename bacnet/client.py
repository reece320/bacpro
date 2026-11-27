"""BACnet/IP client: Who-Is discovery and ReadProperty.

The socket is serviced by a single background thread.  Confirmed requests are
matched to their replies by invoke-ID, so callers can issue reads from a thread
pool without racing on recvfrom().
"""
from __future__ import annotations

import queue
import random
import socket
import struct
import threading
import time
from typing import Iterable, Optional

from . import encoding as enc

BVLC_TYPE = 0x81
BVLC_RESULT = 0x00
BVLC_WRITE_BDT = 0x01
BVLC_READ_BDT = 0x02
BVLC_READ_BDT_ACK = 0x03
BVLC_FORWARDED_NPDU = 0x04
BVLC_REGISTER_FOREIGN_DEVICE = 0x05
BVLC_READ_FDT = 0x06
BVLC_READ_FDT_ACK = 0x07
BVLC_DELETE_FDT = 0x08
BVLC_DISTRIBUTE_BROADCAST = 0x09
BVLC_UNICAST = 0x0A
BVLC_BROADCAST = 0x0B

# BVLC-Result codes (ASHRAE 135 Table 5-1)
BVLC_RESULT_CODES = {
    0x0000: "successful completion",
    0x0010: "write-BDT: incorrect length",
    0x0011: "write-BDT: register foreign device full",
    0x0020: "read-BDT: incorrect length",
    0x0030: "register-foreign-device: incorrect length",
    0x0031: "register-foreign-device: unsupported",
    0x0040: "read-FDT: incorrect length",
    0x0050: "delete-FDT-entry: incorrect length",
    0x0060: "distribute-broadcast: incorrect length",
}

ERROR_CODES = {
    0: "other",
    9: "write access denied",
    32: "unknown property",
    37: "value out of range",
    40: "invalid data type",
    42: "invalid configuration data",
    45: "invalid priority",
    50: "property is not an array",
    51: "invalid array index",
}

DEFAULT_PORT = 47808
DEMO_PORT = 47809


def parse_complex_ack(apdu: bytes) -> dict:
    """Decode a ReadProperty ComplexACK into {object_type, object_instance,
    property, value}."""
    offset = 3
    tag, context, length, offset = enc.read_tag(apdu, offset)
    obj_raw = struct.unpack(">I", apdu[offset:offset + 4])[0]
    offset += 4
    tag, context, length, offset = enc.read_tag(apdu, offset)
    prop_id = apdu[offset]
    offset += length
    if offset < len(apdu):
        tag2, ctx2, len2, off2 = enc.read_tag(apdu, offset)
        if ctx2 and tag2 == 2:
            offset = off2 + len2
    value = enc.decode_property_value(apdu, offset)
    return {
        "object_type": obj_raw >> 22,
        "object_instance": obj_raw & 0x3FFFFF,
        "property": prop_id,
        "value": value,
    }


class BacnetError(Exception):
    def __init__(self, message: str, error_class: Optional[int] = None,
                 error_code: Optional[int] = None):
        super().__init__(message)
        self.error_class = error_class
        self.error_code = error_code


class BacnetTimeout(BacnetError):
    pass


class BacnetClient:
    def __init__(self, port: int = DEFAULT_PORT, timeout: float = 4.0,
                 demo_target: bool = True):
        self.timeout = timeout
        self.demo_target = demo_target
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.local_port = port
        try:
            self.sock.bind(("0.0.0.0", port))
        except OSError:
            # Port already taken (another BACnet tool?) fall back to ephemeral.
            self.sock.bind(("0.0.0.0", 0))
            self.local_port = self.sock.getsockname()[1]
        self.sock.settimeout(0.25)

        self._pending: dict[int, dict] = {}
        self._pending_lock = threading.Lock()
        self._iams: list[dict] = []
        self._iam_lock = threading.Lock()
        self._invoke = random.randint(1, 200)
        self._bvlc_inbox: "queue.Queue" = queue.Queue(maxsize=64)
        self._foreign = None
        self._foreign_thread = None
        self._foreign_stop = None
        self._running = True
        self._thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def close(self):
        self._running = False
        try:
            self.sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Frame helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _bvlc(function: int, payload: bytes) -> bytes:
        return bytes([BVLC_TYPE, function]) + struct.pack(">H", len(payload) + 4) + payload

    @staticmethod
    def _npdu(apdu: bytes, expecting_reply: bool = False) -> bytes:
        return bytes([0x01, 0x04 if expecting_reply else 0x00]) + apdu

    @staticmethod
    def _parse_npdu(npdu: bytes):
        """Return APDU bytes or None if the NPDU carries no APDU."""
        if len(npdu) < 2 or npdu[0] != 0x01:      # NPDU version
            return None
        offset = 1
        control = npdu[offset]
        offset += 1
        if control & 0x20:                        # destination specifier
            if offset + 3 > len(npdu):
                return None
            dlen = npdu[offset + 2]
            offset += 3 + dlen + 1
        if control & 0x08:                        # source specifier
            if offset + 3 > len(npdu):
                return None
            slen = npdu[offset + 2]
            offset += 3 + slen
        if control & 0x80:                        # network-layer message
            return None
        if offset >= len(npdu):
            return None
        return npdu[offset:]

    # ------------------------------------------------------------------
    # Receive loop
    # ------------------------------------------------------------------
    def _recv_loop(self):
        while self._running:
            try:
                data, addr = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle_frame(data, addr)
            except Exception:
                # A malformed frame must never kill discovery.
                continue

    def _handle_frame(self, data: bytes, addr):
        if len(data) < 4 or data[0] != BVLC_TYPE:
            return
        function = data[1]
        length = struct.unpack(">H", data[2:4])[0]
        payload = data[4:4 + max(0, length - 4)]

        if function == BVLC_FORWARDED_NPDU:
            # A BBMD relays a broadcast on behalf of a remote device: the
            # first six octets are the original source IP and port.
            if len(payload) < 6:
                return
            origin = (socket.inet_ntoa(payload[0:4]),
                      struct.unpack(">H", payload[4:6])[0])
            self._process_npdu(payload[6:], origin)
            return

        if function in (BVLC_RESULT, BVLC_READ_BDT_ACK, BVLC_READ_FDT_ACK):
            try:
                self._bvlc_inbox.put_nowait((function, payload, addr))
            except queue.Full:
                pass
            return

        if function in (BVLC_UNICAST, BVLC_BROADCAST, BVLC_DISTRIBUTE_BROADCAST):
            self._process_npdu(payload, addr)

    def _process_npdu(self, npdu: bytes, addr):
        apdu = self._parse_npdu(npdu)
        if not apdu:
            return
        pdu_type = apdu[0] & 0xF0

        if pdu_type == 0x10 and len(apdu) >= 2 and apdu[1] == 0x00:
            self._handle_iam(apdu, addr)
            return

        if pdu_type in (0x20, 0x30, 0x50, 0x60, 0x70) and len(apdu) >= 2:
            invoke = apdu[1]
            with self._pending_lock:
                slot = self._pending.get(invoke)
            if slot is None:
                return
            if pdu_type == 0x20:  # SimpleACK (WriteProperty success)
                slot["value"] = {"ok": True}
            elif pdu_type == 0x30:
                try:
                    slot["value"] = parse_complex_ack(apdu)
                except Exception as exc:  # noqa: BLE001
                    slot["error"] = BacnetError(f"Malformed ComplexACK: {exc}")
            elif pdu_type == 0x50:
                slot["error"] = self._parse_error(apdu)
            elif pdu_type == 0x60:
                slot["error"] = BacnetError("Request rejected by device")
            else:
                slot["error"] = BacnetError("Request aborted by device")
            slot["event"].set()

    def iams(self) -> list[dict]:
        """Snapshot of the I-Am records seen since the last Who-Is."""
        with self._iam_lock:
            return list(self._iams)

    def _handle_iam(self, apdu: bytes, addr):
        if len(apdu) < 10:
            return
        raw = struct.unpack(">I", apdu[2:6])[0]
        obj_type = raw >> 22
        instance = raw & 0x3FFFFF
        if obj_type != 8:
            return
        record = {
            "device_instance": instance,
            "max_apdu": apdu[6],
            "segmentation": apdu[7],
            "vendor_id": struct.unpack(">H", apdu[8:10])[0],
            "address": addr[0],
            "port": addr[1],
        }
        with self._iam_lock:
            self._iams.append(record)

    # ------------------------------------------------------------------
    # BBMD / foreign device
    # ------------------------------------------------------------------
    def _bvlc_request(self, bbmd, function: int, payload: bytes,
                      expect: int, timeout: float = 3.0) -> bytes:
        """Send a BVLC request and wait for the matching BVLC reply."""
        self.sock.sendto(self._bvlc(function, payload), bbmd)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                fn, data, _addr = self._bvlc_inbox.get(
                    timeout=max(0.02, deadline - time.time()))
            except queue.Empty:
                break
            if fn == expect:
                return data
        raise BacnetTimeout(f"No BVLC reply (function {expect:#04x}) from {bbmd[0]}:{bbmd[1]}")

    def register_foreign_device(self, bbmd, ttl: int = 300, timeout: float = 3.0) -> dict:
        """Register as a foreign device with a BBMD (BVLC 0x05)."""
        payload = self._bvlc_request(bbmd, BVLC_REGISTER_FOREIGN_DEVICE,
                                     struct.pack(">H", int(ttl)),
                                     BVLC_RESULT, timeout)
        code = struct.unpack(">H", payload[:2])[0] if len(payload) >= 2 else 0xFFFF
        return {
            "ok": code == 0,
            "code": code,
            "message": BVLC_RESULT_CODES.get(code, f"result {code}"),
            "bbmd": f"{bbmd[0]}:{bbmd[1]}",
            "ttl": ttl,
        }

    def read_bdt(self, bbmd, timeout: float = 3.0) -> list:
        """Read a BBMD's broadcast distribution table (BVLC 0x02)."""
        payload = self._bvlc_request(bbmd, BVLC_READ_BDT, b"", BVLC_READ_BDT_ACK, timeout)
        entries = []
        for i in range(0, len(payload) - 5, 6):
            entries.append({
                "address": socket.inet_ntoa(payload[i:i + 4]),
                "port": struct.unpack(">H", payload[i + 4:i + 6])[0],
            })
        return entries

    def read_fdt(self, bbmd, timeout: float = 3.0) -> list:
        """Read a BBMD's foreign device table (BVLC 0x06)."""
        payload = self._bvlc_request(bbmd, BVLC_READ_FDT, b"", BVLC_READ_FDT_ACK, timeout)
        entries = []
        for i in range(0, len(payload) - 9, 10):
            entries.append({
                "address": socket.inet_ntoa(payload[i:i + 4]),
                "port": struct.unpack(">H", payload[i + 4:i + 6])[0],
                "ttl": struct.unpack(">H", payload[i + 6:i + 8])[0],
                "remaining": struct.unpack(">H", payload[i + 8:i + 10])[0],
            })
        return entries

    def delete_fdt_entry(self, bbmd, address, port: int, timeout: float = 3.0) -> dict:
        """Delete one foreign-device entry from a BBMD (BVLC 0x08)."""
        payload = socket.inet_aton(address) + struct.pack(">H", int(port))
        data = self._bvlc_request(bbmd, BVLC_DELETE_FDT, payload, BVLC_RESULT, timeout)
        code = struct.unpack(">H", data[:2])[0] if len(data) >= 2 else 0xFFFF
        return {"ok": code == 0, "code": code,
                "message": BVLC_RESULT_CODES.get(code, f"result {code}")}

    def distribute_broadcast(self, bbmd, npdu: bytes):
        """Ask a BBMD to distribute an NPDU to its broadcast domain."""
        self.sock.sendto(self._bvlc(BVLC_DISTRIBUTE_BROADCAST, npdu), bbmd)

    def start_foreign_registration(self, bbmd, ttl: int = 300) -> dict:
        """Register and keep the registration alive in the background."""
        self.stop_foreign_registration()
        result = self.register_foreign_device(bbmd, ttl)
        if not result["ok"]:
            return result
        self._foreign = {"bbmd": bbmd, "ttl": int(ttl), "registered_at": time.time()}
        self._foreign_stop = threading.Event()

        def renew():
            interval = max(10.0, int(ttl) / 2.0)
            while not self._foreign_stop.wait(interval):
                try:
                    self.register_foreign_device(bbmd, ttl)
                except Exception:
                    pass

        self._foreign_thread = threading.Thread(target=renew, daemon=True)
        self._foreign_thread.start()
        return result

    def stop_foreign_registration(self):
        stop = getattr(self, "_foreign_stop", None)
        if stop is not None:
            stop.set()
        self._foreign = None
        self._foreign_thread = None

    def foreign_status(self) -> Optional[dict]:
        foreign = getattr(self, "_foreign", None)
        if not foreign:
            return None
        return {
            "bbmd": f"{foreign['bbmd'][0]}:{foreign['bbmd'][1]}",
            "ttl": foreign["ttl"],
            "age": round(time.time() - foreign["registered_at"], 1),
        }

    @staticmethod
    def _parse_error(apdu: bytes) -> BacnetError:
        try:
            offset = 3
            # Skip object-identifier (ctx 0), property (ctx 1) and optional
            # array-index (ctx 2) before the error-class / error-code values.
            while offset < len(apdu):
                tag, context, length, off = enc.read_tag(apdu, offset)
                if not context:
                    break
                offset = off + length
            tag, context, length, offset = enc.read_tag(apdu, offset)
            error_class, offset = enc.decode_app(tag, apdu, offset, length)
            tag, context, length, offset = enc.read_tag(apdu, offset)
            error_code, offset = enc.decode_app(tag, apdu, offset, length)
            label = ERROR_CODES.get(error_code, f"code {error_code}")
            return BacnetError(
                f"Device rejected request: {label} (class {error_class}, code {error_code})",
                error_class=error_class, error_code=error_code,
            )
        except Exception:
            return BacnetError("Unparseable BACnet error response")

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def who_is(self, targets: Optional[Iterable[tuple]] = None,
               low: Optional[int] = None, high: Optional[int] = None,
               timeout: Optional[float] = None) -> list[dict]:
        timeout = self.timeout if timeout is None else timeout
        apdu = bytes([0x10, 0x08])  # Unconfirmed-Request / Who-Is
        if low is not None and high is not None:
            apdu += enc.ctx_objid(0, 8, low) + enc.ctx_objid(1, 8, high)
        npdu = self._npdu(apdu)

        destinations: list[tuple] = []
        if targets:
            destinations.extend(targets)
        if self.demo_target:
            destinations.append(("127.0.0.1", DEMO_PORT))
        destinations.append(("255.255.255.255", DEFAULT_PORT))
        # Common directed broadcast on a /24 lab network
        destinations.append(("192.168.1.255", DEFAULT_PORT))

        with self._iam_lock:
            self._iams.clear()

        foreign = getattr(self, "_foreign", None)
        if foreign:
            # A foreign device cannot broadcast on the BBMD's subnet; it asks
            # the BBMD to distribute the Who-Is on its behalf.
            try:
                self.distribute_broadcast(foreign["bbmd"], npdu)
            except OSError:
                pass

        for dest in destinations:
            function = BVLC_BROADCAST if dest[0].endswith(".255") else BVLC_UNICAST
            try:
                self.sock.sendto(self._bvlc(function, npdu), dest)
            except OSError:
                continue

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.05)

        with self._iam_lock:
            records = list(self._iams)

        # De-duplicate by instance + address, keep the first reply.
        unique: dict[tuple, dict] = {}
        for record in records:
            key = (record["device_instance"], record["address"], record["port"])
            unique.setdefault(key, record)
        return list(unique.values())

    # ------------------------------------------------------------------
    # ReadProperty
    # ------------------------------------------------------------------
    def _next_invoke(self) -> int:
        with self._pending_lock:
            for _ in range(255):
                self._invoke = (self._invoke % 255) + 1
                if self._invoke not in self._pending:
                    return self._invoke
        return random.randint(1, 255)

    def read_property(self, address: tuple, obj_type: int, instance: int,
                      prop_id: int, array_index: Optional[int] = None,
                      timeout: Optional[float] = None, retries: int = 1):
        timeout = self.timeout if timeout is None else timeout
        last_error: Optional[Exception] = None

        for _attempt in range(retries + 1):
            invoke = self._next_invoke()
            slot = {"event": threading.Event(), "value": None, "error": None}
            with self._pending_lock:
                self._pending[invoke] = slot

            apdu = bytes([0x00, 0x05, invoke, 0x0C])  # Confirmed-Request, ReadProperty
            apdu += enc.ctx_objid(0, obj_type, instance)
            apdu += enc.ctx_uint(1, prop_id)
            if array_index is not None:
                apdu += enc.ctx_uint(2, array_index)
            frame = self._bvlc(BVLC_UNICAST, self._npdu(apdu, expecting_reply=True))

            try:
                self.sock.sendto(frame, address)
            except OSError as exc:
                with self._pending_lock:
                    self._pending.pop(invoke, None)
                last_error = BacnetError(f"Send failed: {exc}")
                continue

            if slot["event"].wait(timeout):
                with self._pending_lock:
                    self._pending.pop(invoke, None)
                if slot["error"]:
                    last_error = slot["error"]
                    continue
                result = slot["value"]
                return result["value"] if isinstance(result, dict) else result

            with self._pending_lock:
                self._pending.pop(invoke, None)
            last_error = BacnetTimeout(
                f"No reply from {address[0]}:{address[1]} for object "
                f"{obj_type}:{instance} property {prop_id}"
            )

        raise last_error or BacnetTimeout("Read timed out")

    def read_array(self, address: tuple, obj_type: int, instance: int,
                   prop_id: int, max_items: int = 512) -> list:
        """Read an array property by first reading index 0 (the count)."""
        count = self.read_property(address, obj_type, instance, prop_id, array_index=0)
        if not isinstance(count, int):
            return []
        count = min(count, max_items)
        items = []
        for index in range(1, count + 1):
            items.append(self.read_property(address, obj_type, instance, prop_id,
                                            array_index=index))
        return items

    # ------------------------------------------------------------------
    # WriteProperty
    # ------------------------------------------------------------------
    def write_property(self, address: tuple, obj_type: int, instance: int,
                       prop_id: int, value, priority=None,
                       array_index=None, timeout=None, retries: int = 0):
        """Write a property.  ``value=None`` relinquishes the priority slot."""
        timeout = self.timeout if timeout is None else timeout
        last_error = None

        for _attempt in range(retries + 1):
            invoke = self._next_invoke()
            slot = {"event": threading.Event(), "value": None, "error": None}
            with self._pending_lock:
                self._pending[invoke] = slot

            apdu = bytes([0x00, 0x05, invoke, 0x0F])
            apdu += enc.ctx_objid(0, obj_type, instance)
            apdu += enc.ctx_uint(1, prop_id)
            if array_index is not None:
                apdu += enc.ctx_uint(2, array_index)
            apdu += enc.encode_write_value(obj_type, value)
            if priority is not None:
                apdu += enc.ctx_uint(4, priority)
            frame = self._bvlc(BVLC_UNICAST, self._npdu(apdu, expecting_reply=True))

            try:
                self.sock.sendto(frame, address)
            except OSError as exc:
                with self._pending_lock:
                    self._pending.pop(invoke, None)
                last_error = BacnetError(f"Send failed: {exc}")
                continue

            if slot["event"].wait(timeout):
                with self._pending_lock:
                    self._pending.pop(invoke, None)
                if slot["error"]:
                    last_error = slot["error"]
                    continue
                return True

            with self._pending_lock:
                self._pending.pop(invoke, None)
            last_error = BacnetTimeout(
                f"No reply from {address[0]}:{address[1]} for write to object "
                f"{obj_type}:{instance} property {prop_id}"
            )

        raise last_error or BacnetTimeout("Write timed out")

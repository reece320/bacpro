"""BACnet MS/TP data link layer (ASHRAE 135 clause 9) over RS-485.

Implements a master node: frame codec with CRC-16, token passing, Poll-For-
Master discovery of the successor, and confirmed-request/response exchange.

The transport is deliberately serial-library agnostic: anything with
``read``/``write``/``in_waiting``/``flush`` works, which lets the test suite
drive it over an in-memory bus instead of real hardware.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

# Frame types
FRAME_TOKEN = 0x00
FRAME_POLL_FOR_MASTER = 0x01
FRAME_REPLY_TO_POLL = 0x02
FRAME_TEST_REQUEST = 0x03
FRAME_TEST_RESPONSE = 0x04
FRAME_DATA_EXPECTING_REPLY = 0x05
FRAME_DATA_NOT_EXPECTING_REPLY = 0x06
FRAME_REPLY_POSTPONED = 0x07

PREAMBLE = b"\x55\xff"
BROADCAST_MAC = 0xFF

T_NO_TOKEN = 0.6
T_POLL_TIMEOUT = 0.12


class MstpError(Exception):
    pass


class MstpTimeout(MstpError):
    pass


# ---------------------------------------------------------------------------
# CRC-16 (BACnet uses the X.25 / CCITT reflected polynomial)
# ---------------------------------------------------------------------------
def _crc_header_step(data: int, crc: int) -> int:
    """One octet of the MS/TP header CRC (BACnet clause 9.4.2)."""
    crc = (crc ^ data) & 0xFF
    crc = (crc ^ (crc << 1) ^ (crc << 2) ^ (crc << 3) ^ (crc << 4)
           ^ (crc << 5) ^ (crc << 6) ^ (crc << 7)) & 0xFFFF
    return ((crc & 0xFE) ^ ((crc >> 8) & 1)) & 0xFF


def _crc_data_step(data: int, crc: int) -> int:
    """One octet of the MS/TP data CRC (BACnet clause 9.4.2)."""
    low = ((crc & 0xFF) ^ data) & 0xFF
    return ((crc >> 8) ^ (low << 8) ^ (low << 3) ^ (low << 12)
            ^ (low >> 4) ^ (low & 0x0F) ^ ((low & 0x0F) << 7)) & 0xFFFF


def crc_header(data: bytes) -> int:
    """8-bit header CRC, returned already complemented (as transmitted)."""
    crc = 0xFF
    for byte in data:
        crc = _crc_header_step(byte, crc)
    return crc ^ 0xFF


def crc_data(data: bytes) -> int:
    """16-bit data CRC, returned already complemented (as transmitted)."""
    crc = 0xFFFF
    for byte in data:
        crc = _crc_data_step(byte, crc)
    return crc ^ 0xFFFF


def crc16(data: bytes) -> int:
    """Alias kept for callers that expect the MS/TP data CRC."""
    return crc_data(data)


def build_frame(frame_type: int, dest: int, src: int, data: bytes = b"") -> bytes:
    length = len(data) + 2
    header = bytes([frame_type, dest & 0xFF, src & 0xFF,
                    (length >> 8) & 0xFF, length & 0xFF])
    header_crc = crc_header(header)
    body = header + bytes([header_crc])
    data_crc = crc_data(data)
    return PREAMBLE + body + data + bytes([data_crc & 0xFF, (data_crc >> 8) & 0xFF])


def parse_frame(buffer: bytes):
    """Parse one frame from the start of *buffer*.

    Returns (frame_type, dest, src, data, consumed) or None if incomplete.
    """
    start = buffer.find(PREAMBLE)
    if start < 0:
        return None
    if len(buffer) < start + 8:
        return None
    offset = start + 2
    frame_type = buffer[offset]
    dest = buffer[offset + 1]
    src = buffer[offset + 2]
    length = (buffer[offset + 3] << 8) | buffer[offset + 4]
    header_crc = buffer[offset + 5]
    if crc_header(buffer[offset:offset + 5]) != header_crc:
        return ("bad-crc", dest, src, b"", start + 2)
    if length < 2 or length > 1500:
        return ("bad-length", dest, src, b"", start + 2)
    end = offset + 6 + length
    if len(buffer) < end:
        return None
    data = buffer[offset + 6:end - 2]
    data_crc = buffer[end - 2] | (buffer[end - 1] << 8)
    if data and crc_data(data) != data_crc:
        return ("bad-crc", dest, src, b"", end)
    return (frame_type, dest, src, data, end)


def npdu_to_apdu(npdu: bytes) -> Optional[bytes]:
    if len(npdu) < 3 or npdu[0] != 0x01:
        return None
    offset = 1
    control = npdu[offset]
    offset += 1
    if control & 0x20:
        if offset + 3 > len(npdu):
            return None
        dlen = npdu[offset + 2]
        offset += 3 + dlen + 1
    if control & 0x08:
        if offset + 3 > len(npdu):
            return None
        slen = npdu[offset + 2]
        offset += 3 + slen
    if control & 0x80:
        return None
    return npdu[offset:] if offset < len(npdu) else None


def apdu_to_npdu(apdu: bytes) -> bytes:
    return bytes([0x01, 0x00]) + apdu


# ---------------------------------------------------------------------------
# Master node
# ---------------------------------------------------------------------------
class MstpTransport:
    """A BACnet MS/TP master node on one serial port."""

    def __init__(self, serial_port, mac: int = 1, max_master: int = 127,
                 device_instance: int = 999999, name: str = "bacpro"):
        self.serial = serial_port
        self.mac = mac
        self.max_master = max(1, min(max_master, 127))
        self.device_instance = device_instance
        self.name = name

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._buffer = b""
        self._lock = threading.Lock()

        self._have_token = False
        self._last_token = 0.0
        self._token_time = 0.0
        self._ns = (mac % self.max_master) + 1
        self._info_frames = 0
        self._max_info_frames = 1

        self._requests: list[dict] = []
        self._request_lock = threading.Lock()
        self._iams: list[dict] = []
        self._iam_lock = threading.Lock()

        self.state = "idle"
        self.stats = {
            "tokens": 0, "frames_sent": 0, "frames_received": 0,
            "crc_errors": 0, "timeouts": 0, "polls": 0,
        }

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._running:
            return
        self._running = True
        self._last_token = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        try:
            self.serial.close()
        except Exception:
            pass

    # -- low level I/O -----------------------------------------------------
    def _read_bytes(self, timeout: float) -> bytes:
        deadline = time.time() + timeout
        chunk = b""
        while time.time() < deadline:
            try:
                waiting = getattr(self.serial, "in_waiting", 0)
            except Exception:
                waiting = 0
            if waiting:
                try:
                    chunk += self.serial.read(waiting)
                except Exception:
                    break
                continue
            if chunk:
                break
            time.sleep(0.003)
        return chunk

    def _read_frame(self, timeout: float):
        deadline = time.time() + timeout
        while time.time() < deadline:
            parsed = parse_frame(self._buffer)
            if parsed:
                frame_type, dest, src, data, consumed = parsed
                self._buffer = self._buffer[consumed:]
                if frame_type in ("bad-crc", "bad-length"):
                    self.stats["crc_errors"] += 1
                    continue
                return frame_type, dest, src, data
            self._buffer += self._read_bytes(0.02)
            if len(self._buffer) > 4096:
                self._buffer = self._buffer[-1024:]
        return None

    def _send(self, frame_type: int, dest: int, data: bytes = b""):
        frame = build_frame(frame_type, dest, self.mac, data)
        try:
            self.serial.write(frame)
            self.serial.flush()
        except Exception as exc:  # noqa: BLE001
            raise MstpError(f"serial write failed: {exc}") from exc
        self.stats["frames_sent"] += 1

    # -- token handling ----------------------------------------------------
    def _run(self):
        while self._running:
            try:
                frame = self._read_frame(0.02)
                if frame:
                    self._handle_frame(frame)

                now = time.time()
                if not self._have_token and now - self._last_token > T_NO_TOKEN:
                    # No other master on the bus: take the token ourselves.
                    self._have_token = True
                    self._token_time = now
                    self._info_frames = 0
                    self.state = "use-token"

                if self._have_token:
                    if now - self._token_time > 5.0:
                        self._pass_token()
                    else:
                        self._service_requests()
            except Exception:
                self.state = "error"
                time.sleep(0.05)

    def _handle_frame(self, frame):
        frame_type, dest, src, data = frame
        self.stats["frames_received"] += 1

        if frame_type == FRAME_TOKEN and dest == self.mac:
            self._have_token = True
            self._last_token = time.time()
            self._token_time = time.time()
            self._info_frames = 0
            self.stats["tokens"] += 1
            self.state = "use-token"
            return

        if frame_type == FRAME_POLL_FOR_MASTER and dest == self.mac:
            self._send(FRAME_REPLY_TO_POLL, src)
            return

        if frame_type == FRAME_TEST_REQUEST and dest == self.mac:
            self._send(FRAME_TEST_RESPONSE, src, data)
            return

        if frame_type in (FRAME_DATA_EXPECTING_REPLY, FRAME_DATA_NOT_EXPECTING_REPLY):
            apdu = npdu_to_apdu(data)
            if not apdu:
                return
            self._handle_apdu(apdu, src)

    def _handle_apdu(self, apdu: bytes, src: int):
        pdu_type = apdu[0] & 0xF0

        if pdu_type == 0x10 and len(apdu) >= 2 and apdu[1] == 0x00:
            self._record_iam(apdu, src)
            return

        if pdu_type in (0x20, 0x30, 0x50, 0x60, 0x70) and len(apdu) >= 2:
            invoke = apdu[1]
            with self._request_lock:
                for request in self._requests:
                    if request.get("invoke") == invoke and request.get("awaiting"):
                        request["response"] = apdu
                        request["awaiting"] = False
                        request["event"].set()
                        return

    def _record_iam(self, apdu: bytes, src: int):
        if len(apdu) < 10:
            return
        raw = int.from_bytes(apdu[2:6], "big")
        if (raw >> 22) != 8:
            return
        with self._iam_lock:
            self._iams.append({
                "device_instance": raw & 0x3FFFFF,
                "max_apdu": apdu[6],
                "segmentation": apdu[7],
                "vendor_id": int.from_bytes(apdu[8:10], "big"),
                "mac": src,
                "address": f"MS/TP {src}",
                "port": 0,
                "transport": "mstp",
            })

    def _pass_token(self):
        self.state = "pass-token"
        ns = self._ns
        found = False
        for _ in range(min(self.max_master, 32)):
            if ns == self.mac:
                break
            self.stats["polls"] += 1
            try:
                self._send(FRAME_POLL_FOR_MASTER, ns)
            except MstpError:
                break
            deadline = time.time() + 0.15
            while time.time() < deadline:
                frame = self._read_frame(0.02)
                if not frame:
                    continue
                ftype, fdest, fsrc, _ = frame
                if ftype == FRAME_TOKEN and fdest == self.mac:
                    # The token came back while we were polling: keep it.
                    self._handle_frame(frame)
                    self.state = "use-token"
                    return
                if ftype == FRAME_REPLY_TO_POLL and fsrc == ns:
                    found = True
                    break
                self._handle_frame(frame)
            if found:
                break
            ns = (ns + 1) % (self.max_master + 1)

        if not found:
            ns = self.mac
        self._ns = ns if found else (ns + 1) % (self.max_master + 1)
        if ns == self.mac:
            self._have_token = True
            self._token_time = time.time()
            self._info_frames = 0
            self.state = "use-token"
            return
        try:
            self._send(FRAME_TOKEN, ns)
        except MstpError:
            pass
        self._have_token = False
        self._last_token = time.time()
        self.state = "idle"

    def _service_requests(self):
        with self._request_lock:
            request = next((r for r in self._requests if not r.get("sent")), None)
            if request is not None:
                request["sent"] = True
                request["awaiting"] = True
                request["invoke"] = (request["apdu"][2]
                                     if len(request["apdu"]) > 2 else None)
        if request is None:
            self._pass_token()
            return

        frame_type = (FRAME_DATA_EXPECTING_REPLY if request["expecting_reply"]
                      else FRAME_DATA_NOT_EXPECTING_REPLY)
        try:
            self._send(frame_type, request["dest"], apdu_to_npdu(request["apdu"]))
        except MstpError as exc:
            request["error"] = exc
            request["event"].set()
            self._finish(request)
            return

        self._info_frames += 1
        if request["expecting_reply"]:
            self.state = "wait-reply"
            deadline = time.time() + request["timeout"]
            while time.time() < deadline and self._running:
                if request["event"].is_set():
                    break
                frame = self._read_frame(0.02)
                if not frame:
                    continue
                self._handle_frame(frame)
            if not request["event"].is_set():
                request["error"] = MstpTimeout(
                    f"no MS/TP reply from MAC {request['dest']}")
                self.stats["timeouts"] += 1
                request["event"].set()

        self._finish(request)
        if not request["event"].is_set():
            request["event"].set()
        if self._info_frames >= self._max_info_frames:
            self._pass_token()

    def _finish(self, request):
        with self._request_lock:
            if request in self._requests:
                self._requests.remove(request)

    # -- public API --------------------------------------------------------
    def request(self, apdu: bytes, dest: int, expecting_reply: bool = True,
                timeout: float = 3.0) -> bytes:
        if not self._running:
            raise MstpError("MS/TP transport is not running")
        request = {
            "apdu": apdu, "dest": dest, "expecting_reply": expecting_reply,
            "timeout": timeout, "event": threading.Event(),
            "response": None, "error": None, "sent": False, "awaiting": False,
            "invoke": None,
        }
        with self._request_lock:
            self._requests.append(request)
        if not request["event"].wait(timeout + 3.0):
            self._finish(request)
            raise MstpTimeout("MS/TP request timed out waiting for the token")
        if request["error"]:
            raise request["error"]
        return request["response"] or b""

    def who_is(self, low: Optional[int] = None, high: Optional[int] = None,
               timeout: float = 3.0) -> list[dict]:
        from . import encoding as enc

        apdu = bytes([0x10, 0x08])
        if low is not None and high is not None:
            apdu += enc.ctx_objid(0, 8, low) + enc.ctx_objid(1, 8, high)
        with self._iam_lock:
            self._iams.clear()
        self.request(apdu, BROADCAST_MAC, expecting_reply=False, timeout=timeout)
        time.sleep(timeout)
        with self._iam_lock:
            records = list(self._iams)
        unique = {}
        for record in records:
            unique.setdefault(record["device_instance"], record)
        return list(unique.values())

    def status(self) -> dict:
        return {
            "running": self._running,
            "mac": self.mac,
            "max_master": self.max_master,
            "state": self.state,
            "have_token": self._have_token,
            "next_station": self._ns,
            "stats": dict(self.stats),
        }


# ---------------------------------------------------------------------------
# MS/TP client (mirrors BacnetClient's read/write API)
# ---------------------------------------------------------------------------
class MstpClient:
    def __init__(self, transport: MstpTransport):
        self.transport = transport
        self._invoke = 0

    def _next_invoke(self) -> int:
        self._invoke = (self._invoke % 255) + 1
        return self._invoke

    def read_property(self, mac: int, obj_type: int, instance: int,
                      prop_id: int, array_index: Optional[int] = None,
                      timeout: float = 3.0, retries: int = 1):
        from . import encoding as enc
        from .client import BacnetError, BacnetTimeout, parse_complex_ack

        last_error = None
        for _ in range(retries + 1):
            invoke = self._next_invoke()
            apdu = bytes([0x00, 0x05, invoke, 0x0C])
            apdu += enc.ctx_objid(0, obj_type, instance)
            apdu += enc.ctx_uint(1, prop_id)
            if array_index is not None:
                apdu += enc.ctx_uint(2, array_index)
            try:
                response = self.transport.request(apdu, mac, True, timeout)
            except MstpError as exc:
                last_error = BacnetTimeout(str(exc))
                continue
            if not response:
                last_error = BacnetTimeout("empty MS/TP response")
                continue
            pdu_type = response[0] & 0xF0
            if pdu_type == 0x30:
                return parse_complex_ack(response)["value"]
            if pdu_type == 0x50:
                raise BacnetError("BACnet error response over MS/TP")
            last_error = BacnetError(f"unexpected APDU type 0x{pdu_type:02X}")
        raise last_error or BacnetTimeout("MS/TP read timed out")

    def read_array(self, mac: int, obj_type: int, instance: int,
                   prop_id: int, max_items: int = 512) -> list:
        count = self.read_property(mac, obj_type, instance, prop_id, array_index=0)
        if not isinstance(count, int):
            return []
        return [self.read_property(mac, obj_type, instance, prop_id, array_index=i)
                for i in range(1, min(count, max_items) + 1)]

    def write_property(self, mac: int, obj_type: int, instance: int,
                       prop_id: int, value, priority: Optional[int] = None,
                       array_index: Optional[int] = None,
                       timeout: float = 3.0) -> bool:
        from . import encoding as enc
        from .client import BacnetError

        invoke = self._next_invoke()
        apdu = bytes([0x00, 0x05, invoke, 0x0F])
        apdu += enc.ctx_objid(0, obj_type, instance)
        apdu += enc.ctx_uint(1, prop_id)
        if array_index is not None:
            apdu += enc.ctx_uint(2, array_index)
        apdu += enc.encode_write_value(obj_type, value)
        if priority is not None:
            apdu += enc.ctx_uint(4, priority)
        response = self.transport.request(apdu, mac, True, timeout)
        if not response:
            raise BacnetError("no MS/TP write response")
        pdu_type = response[0] & 0xF0
        if pdu_type == 0x20:
            return True
        raise BacnetError(f"write rejected (APDU 0x{pdu_type:02X})")

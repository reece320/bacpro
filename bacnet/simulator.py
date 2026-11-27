"""A small BACnet/IP device simulator.

Listens on UDP 47809 and answers Who-Is and ReadProperty for three virtual
devices.  This makes the discovery tool demonstrable on a laptop with no
building network attached, and is handy for testing clients.
"""
from __future__ import annotations

import math
import random
import socket
import struct
import threading
import time
from typing import Optional

from . import encoding as enc
from .client import (
    BVLC_TYPE, BVLC_UNICAST, BVLC_BROADCAST, BVLC_RESULT, BVLC_READ_BDT,
    BVLC_READ_BDT_ACK, BVLC_FORWARDED_NPDU, BVLC_REGISTER_FOREIGN_DEVICE,
    BVLC_READ_FDT, BVLC_READ_FDT_ACK, BVLC_DELETE_FDT,
    BVLC_DISTRIBUTE_BROADCAST, DEMO_PORT,
)

# Property IDs
P_OBJECT_IDENTIFIER = 75
P_OBJECT_NAME = 77
P_OBJECT_TYPE = 79
P_PRESENT_VALUE = 85
P_DESCRIPTION = 28
P_UNITS = 117
P_STATUS_FLAGS = 111
P_EVENT_STATE = 36
P_RELIABILITY = 103
P_OUT_OF_SERVICE = 81
P_COV_INCREMENT = 22
P_VENDOR_NAME = 121
P_VENDOR_IDENTIFIER = 120
P_MODEL_NAME = 70
P_FIRMWARE_REVISION = 44
P_APPLICATION_SOFTWARE_VERSION = 12
P_PROTOCOL_VERSION = 98
P_PROTOCOL_REVISION = 139
P_SYSTEM_STATUS = 112
P_LOCATION = 58
P_OBJECT_LIST = 76
P_MAX_APDU = 62
P_SEGMENTATION = 107
P_NUMBER_OF_STATES = 74
P_STATE_TEXT = 110
P_POLARITY = 84
P_DATABASE_REVISION = 155
P_PRIORITY_ARRAY = 87
P_RELINQUISH_DEFAULT = 104


COMMANDABLE = (1, 2, 4, 5, 14, 19)   # AO, AV, BO, BV, MO, MSV


def _point(obj_type: int, instance: int, name: str, description: str,
           pv, units: Optional[int] = None, jitter: float = 0.0,
           states: Optional[list] = None, flags: Optional[list] = None) -> dict:
    return {
        "type": obj_type,
        "instance": instance,
        "name": name,
        "description": description,
        "pv": pv,
        "units": units,
        "jitter": jitter,
        "states": states,
        "flags": flags,
        "prio": [None] * 16,
        "phase": random.uniform(0, math.tau),
    }


def _build_devices(large_points: int = 0) -> dict[int, dict]:
    ahu_points = [
        _point(0, 1, "Space Temperature", "Zone temperature sensor", 22.4, 62, 0.35),
        _point(0, 2, "Supply Air Temperature", "Discharge air temperature", 13.8, 62, 0.25),
        _point(0, 3, "Return Air Temperature", "Return air temperature", 24.1, 62, 0.2),
        _point(0, 4, "CO2 Level", "Zone carbon dioxide", 612.0, 96, 18.0),
        _point(0, 5, "Supply Air Humidity", "Discharge humidity", 47.5, 98, 1.2),
        _point(0, 6, "Mixed Air Temperature", "Mixed air temperature", 18.6, 62, 0.3),
        _point(1, 1, "Cooling Valve Position", "Chilled water valve", 62.0, 95, 2.0),
        _point(1, 2, "Heating Valve Position", "Hot water valve", 0.0, 95, 0.5),
        _point(1, 3, "Supply Fan Speed", "VFD speed command", 78.0, 95, 1.5),
        _point(1, 4, "Damper Position", "Outside air damper", 35.0, 95, 1.0),
        _point(2, 1, "Static Pressure Setpoint", "Duct static pressure setpoint", 1.8, 109),
        _point(3, 1, "Supply Fan Status", "Fan proof switch", 1),
        _point(3, 2, "Filter Alarm", "Dirty filter switch", 1, flags=[1, 0, 0, 0]),
        _point(3, 3, "Freeze Stat", "Low limit freeze protection", 0),
        _point(4, 1, "Supply Fan Command", "Fan start/stop command", 1),
        _point(4, 2, "Alarm Horn", "Alarm output", 0),
        _point(19, 1, "Occupancy Mode", "Scheduler occupancy state", 1,
               states=["Unoccupied", "Occupied", "Bypass", "Standby"]),
    ]

    vav_points = [
        _point(0, 1, "Zone Temperature", "VAV zone temperature", 21.8, 62, 0.3),
        _point(0, 2, "Airflow", "Measured primary airflow", 320.0, 89, 12.0),
        _point(0, 3, "Discharge Air Temperature", "VAV discharge temperature", 14.2, 62, 0.2),
        _point(1, 1, "Damper Position", "VAV damper command", 45.0, 95, 2.5),
        _point(1, 2, "Reheat Valve Position", "Hot water reheat valve", 12.0, 95, 1.0),
        _point(2, 1, "Flow Setpoint", "Airflow setpoint", 350.0, 89),
        _point(3, 1, "Occupancy Sensor", "Zone occupancy", 1),
        _point(4, 1, "Box Enable", "VAV enable command", 1),
        _point(19, 1, "Occupancy Mode", "Zone occupancy mode", 1,
               states=["Unoccupied", "Occupied", "Bypass", "Standby"]),
    ]

    chiller_points = [
        _point(0, 1, "Chilled Water Supply Temp", "CHWS temperature", 6.8, 62, 0.15),
        _point(0, 2, "Chilled Water Return Temp", "CHWR temperature", 12.3, 62, 0.2),
        _point(0, 3, "Condenser Water Temp", "Condenser water temperature", 29.5, 62, 0.3),
        _point(0, 4, "Evaporator Pressure", "Evaporator refrigerant pressure", 410.0, 28, 4.0),
        _point(1, 1, "Load Limit", "Chiller load limit", 85.0, 95, 1.0),
        _point(1, 2, "CHW Valve Position", "Chilled water isolation valve", 100.0, 95),
        _point(2, 1, "CHWS Setpoint", "Chilled water supply setpoint", 6.7, 62),
        _point(3, 1, "Chiller Status", "Chiller running proof", 1),
        _point(3, 2, "Chiller Alarm", "Chiller alarm contact", 0),
        _point(4, 1, "Chiller Enable", "Chiller enable command", 1),
        _point(19, 1, "Chiller Mode", "Operating mode", 2,
               states=["Off", "Startup", "Running", "Fault"]),
    ]

    # All three virtual devices share one UDP socket, so give their points
    # globally unique instance numbers (real devices each have their own
    # address and namespace).
    for base, points in ((100, ahu_points), (200, vav_points), (300, chiller_points)):
        for point in points:
            point["instance"] += base

    devices = {
        1001: {
            "instance": 1001,
            "name": "AHU-1",
            "model": "CPO-AHU-1001",
            "firmware": "1.4.2",
            "software": "ComfortPoint 4.2",
            "location": "Building A / Roof",
            "vendor_id": 17,
            "vendor_name": "Honeywell",
            "points": ahu_points,
        },
        2002: {
            "instance": 2002,
            "name": "VAV-2-14",
            "model": "CPO-VAV-2002",
            "firmware": "1.4.2",
            "software": "ComfortPoint 4.2",
            "location": "Building A / Level 2",
            "vendor_id": 17,
            "vendor_name": "Honeywell",
            "points": vav_points,
        },
        3003: {
            "instance": 3003,
            "name": "CH-1",
            "model": "CPO-CH-3003",
            "firmware": "2.0.1",
            "software": "ComfortPoint 4.2",
            "location": "Building A / Mechanical",
            "vendor_id": 17,
            "vendor_name": "Honeywell",
            "points": chiller_points,
        },
    }

    if large_points:
        # A synthetic "large" controller: proves the object-list loader no
        # longer stops at 250 objects.
        bulk = [
            _point(0, 1000 + i, f"Bulk Sensor {i}", "Synthetic point", 20.0 + i * 0.01, 62)
            for i in range(large_points)
        ]
        devices[4004] = {
            "instance": 4004,
            "name": "LARGE-4",
            "model": "CPO-LARGE-4004",
            "firmware": "1.0.0",
            "software": "ComfortPoint 4.2",
            "location": "Building A / Lab",
            "vendor_id": 17,
            "vendor_name": "Honeywell",
            "points": bulk,
        }
    return devices


class BacnetSimulator:
    def __init__(self, port: int = DEMO_PORT, large_points: int = 0):
        self.port = port
        self.devices = _build_devices(large_points)
        self.sock: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.requests = 0
        self.write_log: list[dict] = []
        # BBMD role: a broadcast distribution table and a foreign device table
        self.bbmd_address = ("127.0.0.1", port)
        self.bdt: list[dict] = [{"address": "127.0.0.1", "port": port}]
        self.fdt: dict[tuple, dict] = {}
        self._forward_to = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def start(self):
        if self._running:
            return
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("0.0.0.0", self.port))
        self.sock.settimeout(0.25)
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    @property
    def running(self) -> bool:
        return self._running

    def summary(self) -> list[dict]:
        return [
            {"instance": d["instance"], "name": d["name"], "model": d["model"],
             "location": d["location"], "points": len(d["points"])}
            for d in self.devices.values()
        ]

    # ------------------------------------------------------------------
    def _loop(self):
        while self._running and self.sock:
            try:
                data, addr = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._handle(data, addr)
            except Exception:
                continue

    # ------------------------------------------------------------------
    # BBMD / foreign device support
    # ------------------------------------------------------------------
    def _send_bvlc(self, addr, function: int, payload: bytes):
        frame = bytes([BVLC_TYPE, function]) + struct.pack(">H", len(payload) + 4) + payload
        try:
            self.sock.sendto(frame, addr)
        except OSError:
            pass

    def _send_bvlc_result(self, addr, code: int):
        self._send_bvlc(addr, BVLC_RESULT, struct.pack(">H", code))

    def _expire_fdt(self):
        now = time.time()
        with self._lock:
            for key in [k for k, v in self.fdt.items() if v["expires"] <= now]:
                del self.fdt[key]

    def bdt_entries(self) -> list:
        return [dict(entry) for entry in self.bdt]

    def fdt_entries(self) -> list:
        self._expire_fdt()
        now = time.time()
        with self._lock:
            return [
                {"address": key[0], "port": key[1], "ttl": val["ttl"],
                 "remaining": int(max(0, val["expires"] - now))}
                for key, val in self.fdt.items()
            ]

    def _handle(self, data: bytes, addr):
        if len(data) < 4 or data[0] != BVLC_TYPE:
            return
        function = data[1]
        length = struct.unpack(">H", data[2:4])[0]
        payload = data[4:4 + max(0, length - 4)]
        self._forward_to = None

        if function == BVLC_REGISTER_FOREIGN_DEVICE:
            if len(payload) < 2:
                self._send_bvlc_result(addr, 0x0030)
                return
            ttl = struct.unpack(">H", payload[:2])[0]
            with self._lock:
                self.fdt[addr] = {"ttl": ttl, "expires": time.time() + max(1, ttl)}
            self._send_bvlc_result(addr, 0x0000)
            return

        if function == BVLC_READ_BDT:
            body = b"".join(
                socket.inet_aton(e["address"]) + struct.pack(">H", e["port"])
                for e in self.bdt
            )
            self._send_bvlc(addr, BVLC_READ_BDT_ACK, body)
            return

        if function == BVLC_READ_FDT:
            self._expire_fdt()
            body = b""
            now = time.time()
            with self._lock:
                for (ip, port), val in self.fdt.items():
                    body += (socket.inet_aton(ip) + struct.pack(">H", port)
                             + struct.pack(">H", val["ttl"])
                             + struct.pack(">H", int(max(0, val["expires"] - now))))
            self._send_bvlc(addr, BVLC_READ_FDT_ACK, body)
            return

        if function == BVLC_DELETE_FDT:
            if len(payload) >= 6:
                key = (socket.inet_ntoa(payload[:4]), struct.unpack(">H", payload[4:6])[0])
                with self._lock:
                    self.fdt.pop(key, None)
            self._send_bvlc_result(addr, 0x0000)
            return

        if function == BVLC_FORWARDED_NPDU:
            if len(payload) < 6:
                return
            origin = (socket.inet_ntoa(payload[:4]),
                      struct.unpack(">H", payload[4:6])[0])
            # Reply through the same foreign device that relayed the request.
            self._forward_to = addr
            self._dispatch_npdu(payload[6:], origin)
            return

        if function == BVLC_DISTRIBUTE_BROADCAST:
            self._dispatch_npdu(payload, addr)
            # Relay the broadcast to every other registered foreign device.
            self._expire_fdt()
            with self._lock:
                members = [key for key in self.fdt if key != addr]
            for member in members:
                relay = (socket.inet_aton(addr[0]) + struct.pack(">H", addr[1]) + payload)
                self._send_bvlc(member, BVLC_FORWARDED_NPDU, relay)
            return

        if function in (BVLC_UNICAST, BVLC_BROADCAST):
            self._dispatch_npdu(payload, addr)

    def _dispatch_npdu(self, npdu: bytes, addr):
        if len(npdu) < 2 or npdu[0] != 0x01:
            return
        offset = 1
        control = npdu[offset]
        offset += 1
        if control & 0x20:
            if offset + 3 > len(npdu):
                return
            offset += 3 + npdu[offset + 2] + 1
        if control & 0x08:
            if offset + 3 > len(npdu):
                return
            offset += 3 + npdu[offset + 2]
        if control & 0x80 or offset >= len(npdu):
            return
        apdu = npdu[offset:]
        pdu_type = apdu[0] & 0xF0

        if pdu_type == 0x10 and len(apdu) >= 2 and apdu[1] == 0x08:
            self._handle_who_is(apdu, addr)
        elif pdu_type == 0x00 and len(apdu) >= 4 and apdu[3] == 0x0C:
            self.requests += 1
            self._handle_read_property(apdu, addr)
        elif pdu_type == 0x00 and len(apdu) >= 4 and apdu[3] == 0x0F:
            self.requests += 1
            self._handle_write_property(apdu, addr)

    def _handle_who_is(self, apdu: bytes, addr):
        low = high = None
        offset = 2
        try:
            if offset < len(apdu):
                tag, ctx, length, offset = enc.read_tag(apdu, offset)
                low = struct.unpack(">I", apdu[offset:offset + 4])[0] & 0x3FFFFF
                offset += 4
                tag, ctx, length, offset = enc.read_tag(apdu, offset)
                high = struct.unpack(">I", apdu[offset:offset + 4])[0] & 0x3FFFFF
        except Exception:
            low = high = None

        for device in self.devices.values():
            inst = device["instance"]
            if low is not None and high is not None and not (low <= inst <= high):
                continue
            apdu_out = (
                bytes([0x10, 0x00])
                + struct.pack(">I", (8 << 22) | inst)
                + bytes([0x05, 0x03])
                + struct.pack(">H", device["vendor_id"])
            )
            self._send(apdu_out, addr)

    def _handle_read_property(self, apdu: bytes, addr):
        invoke = apdu[2]
        offset = 4
        try:
            tag, ctx, length, offset = enc.read_tag(apdu, offset)
            raw = struct.unpack(">I", apdu[offset:offset + 4])[0]
            offset += 4
            obj_type = raw >> 22
            obj_instance = raw & 0x3FFFFF

            tag, ctx, length, offset = enc.read_tag(apdu, offset)
            prop_id = int.from_bytes(apdu[offset:offset + length], "big")
            offset += length

            index = None
            if offset < len(apdu):
                tag, ctx, length, offset = enc.read_tag(apdu, offset)
                if ctx and tag == enc.CTX_ARRAY_INDEX:
                    index = int.from_bytes(apdu[offset:offset + length], "big")
        except Exception:
            return

        if obj_type != 8:
            device = None
            point = None
            for candidate_device in self.devices.values():
                for candidate in candidate_device["points"]:
                    if candidate["type"] == obj_type and candidate["instance"] == obj_instance:
                        device = candidate_device
                        point = candidate
                        break
                if point:
                    break
            if device is None or point is None:
                self._send_error(invoke, obj_type, obj_instance, prop_id, addr)
                return
            value, kind = self._point_property(point, prop_id, index)
        else:
            device = self.devices.get(obj_instance)
            if device is None:
                self._send_error(invoke, obj_type, obj_instance, prop_id, addr)
                return
            value, kind = self._device_property(device, prop_id, index)

        if value is None and kind is None:
            self._send_error(invoke, obj_type, obj_instance, prop_id, addr)
            return

        body = self._encode_value(value, kind)
        out = (
            bytes([0x30, invoke, 0x0C])
            + enc.ctx_objid(0, obj_type, obj_instance)
            + enc.ctx_uint(1, prop_id)
        )
        if index is not None:
            out += enc.ctx_uint(2, index)
        out += body
        self._send(out, addr)

    # ------------------------------------------------------------------
    def _device_property(self, device: dict, prop: int, index):
        inst = device["instance"]
        if prop == P_OBJECT_IDENTIFIER:
            return (8, inst), "objid"
        if prop == P_OBJECT_NAME:
            return device["name"], "string"
        if prop == P_OBJECT_TYPE:
            return 8, "enum"
        if prop == P_VENDOR_NAME:
            return device["vendor_name"], "string"
        if prop == P_VENDOR_IDENTIFIER:
            return device["vendor_id"], "unsigned"
        if prop == P_MODEL_NAME:
            return device["model"], "string"
        if prop == P_FIRMWARE_REVISION:
            return device["firmware"], "string"
        if prop == P_APPLICATION_SOFTWARE_VERSION:
            return device["software"], "string"
        if prop == P_PROTOCOL_VERSION:
            return 1, "unsigned"
        if prop == P_PROTOCOL_REVISION:
            return 24, "unsigned"
        if prop == P_SYSTEM_STATUS:
            return 0, "enum"
        if prop == P_LOCATION:
            return device["location"], "string"
        if prop == P_MAX_APDU:
            return 1024, "unsigned"
        if prop == P_SEGMENTATION:
            return 3, "enum"
        if prop == P_DATABASE_REVISION:
            return 1, "unsigned"
        if prop == P_OBJECT_LIST:
            items = [(8, inst)] + [(p["type"], p["instance"]) for p in device["points"]]
            if index is None:
                return items, "objid_list"
            if index == 0:
                return len(items), "unsigned"
            if 1 <= index <= len(items):
                return items[index - 1], "objid"
            return None, None
        return None, None

    def _point_property(self, point: dict, prop: int, index):
        obj_type = point["type"]
        if prop == P_OBJECT_IDENTIFIER:
            return (obj_type, point["instance"]), "objid"
        if prop == P_OBJECT_NAME:
            return point["name"], "string"
        if prop == P_OBJECT_TYPE:
            return obj_type, "enum"
        if prop == P_DESCRIPTION:
            return point["description"], "string"
        if prop == P_UNITS:
            if point["units"] is None:
                return None, None
            return point["units"], "enum"
        if prop == P_STATUS_FLAGS:
            return list(point.get("flags") or [0, 0, 0, 0]), "bits"
        if prop == P_EVENT_STATE:
            return 0, "enum"
        if prop == P_RELIABILITY:
            return 0, "enum"
        if prop == P_OUT_OF_SERVICE:
            return False, "bool"
        if prop == P_COV_INCREMENT:
            return 0.1, "real"
        if prop == P_POLARITY:
            return 0, "enum"
        if prop == P_PRESENT_VALUE:
            prio = point.get("prio") or [None] * 16
            active = next((v for v in prio if v is not None), None)
            if active is not None:
                if obj_type in (0, 1, 2):
                    return round(float(active), 2), "real"
                return int(active), "enum"
            if obj_type in (0, 1, 2):
                base = float(point["pv"])
                jitter = float(point["jitter"])
                value = base + math.sin(time.time() / 7.0 + point["phase"]) * jitter
                value += random.uniform(-jitter * 0.15, jitter * 0.15)
                if point.get("units") == 95:  # percent: keep within 0-100
                    value = max(0.0, min(100.0, value))
                return round(value, 2), "real"
            if obj_type == 19:
                return int(point["pv"]), "unsigned"
            return int(point["pv"]), "enum"
        if prop == P_PRIORITY_ARRAY and obj_type in COMMANDABLE:
            prio = point.get("prio") or [None] * 16
            if index is None:
                return list(prio), "prio_list"
            if index == 0:
                return len(prio), "unsigned"
            if 1 <= index <= len(prio):
                return prio[index - 1], "prio_value"
            return None, None
        if prop == P_NUMBER_OF_STATES and point["states"]:
            return len(point["states"]), "unsigned"
        if prop == P_STATE_TEXT and point["states"]:
            if index is None:
                return point["states"], "string_list"
            if index == 0:
                return len(point["states"]), "unsigned"
            if 1 <= index <= len(point["states"]):
                return point["states"][index - 1], "string"
            return None, None
        return None, None

    # ------------------------------------------------------------------
    @staticmethod
    def _encode_value(value, kind: str) -> bytes:
        if kind == "string":
            return enc.enc_string(value)
        if kind == "real":
            return enc.enc_real(float(value))
        if kind == "unsigned":
            return enc.enc_unsigned(int(value))
        if kind == "enum":
            return enc.enc_enum(int(value))
        if kind == "bool":
            return enc.enc_bool(bool(value))
        if kind == "objid":
            return enc.enc_objid(value[0], value[1])
        if kind == "bits":
            return enc.enc_bitstring(value)
        if kind == "objid_list":
            out = b"\x3e"  # opening tag 3
            for item in value:
                out += enc.enc_objid(item[0], item[1])
            return out + b"\x3f"
        if kind == "prio_list":
            out = b"\x3e"
            for item in value:
                if item is None:
                    out += enc.enc_null()
                else:
                    out += enc.enc_real(float(item)) if isinstance(item, float) else enc.enc_unsigned(int(item))
            return out + b"\x3f"
        if kind == "prio_value":
            if value is None:
                return enc.enc_null()
            return enc.enc_real(float(value)) if isinstance(value, float) else enc.enc_unsigned(int(value))
        if kind == "string_list":
            out = b"\x3e"
            for item in value:
                out += enc.enc_string(item)
            return out + b"\x3f"
        return enc.enc_null()

    def _send(self, apdu: bytes, addr):
        npdu = bytes([0x01, 0x00]) + apdu
        if self._forward_to is not None:
            # The request arrived through a foreign device, so the reply must
            # go back the same way, wrapped in a Forwarded-NPDU whose origin
            # is this device's own address.
            origin = self.bbmd_address
            payload = (socket.inet_aton(origin[0]) + struct.pack(">H", origin[1]) + npdu)
            self._send_bvlc(self._forward_to, BVLC_FORWARDED_NPDU, payload)
            return
        frame = bytes([BVLC_TYPE, BVLC_UNICAST]) + struct.pack(">H", len(npdu) + 4) + npdu
        try:
            self.sock.sendto(frame, addr)
        except OSError:
            pass

    def _send_error(self, invoke: int, obj_type: int, obj_instance: int,
                    prop: int, addr, error_class: int = 2, code: int = 32,
                    service: int = 0x0C):
        apdu = (
            bytes([0x50, invoke, service])
            + enc.ctx_objid(0, obj_type, obj_instance)
            + enc.ctx_uint(1, prop)
            + enc.enc_enum(error_class)
            + enc.enc_enum(code)
        )
        self._send(apdu, addr)

    # ------------------------------------------------------------------
    # WriteProperty
    # ------------------------------------------------------------------
    def _handle_write_property(self, apdu: bytes, addr):
        invoke = apdu[2]
        offset = 4
        try:
            tag, ctx, length, offset = enc.read_tag(apdu, offset)
            raw = struct.unpack(">I", apdu[offset:offset + 4])[0]
            offset += 4
            obj_type = raw >> 22
            obj_instance = raw & 0x3FFFFF

            tag, ctx, length, offset = enc.read_tag(apdu, offset)
            prop_id = int.from_bytes(apdu[offset:offset + length], "big")
            offset += length

            tag, ctx, length, off2 = enc.read_tag(apdu, offset)
            if ctx and tag == enc.CTX_ARRAY_INDEX:      # array index
                offset = off2 + length

            value, offset = enc.decode_write_value(apdu, offset, obj_type)

            priority = None
            if offset < len(apdu):
                ptag, pctx, plen, poff = enc.read_tag(apdu, offset)
                if pctx and ptag == 4:
                    priority = int.from_bytes(apdu[poff:poff + plen], "big")
        except Exception:
            return

        point = None
        device = None
        for candidate_device in self.devices.values():
            for candidate in candidate_device["points"]:
                if candidate["type"] == obj_type and candidate["instance"] == obj_instance:
                    point, device = candidate, candidate_device
                    break
            if point:
                break

        if point is None or device is None:
            self._send_error(invoke, obj_type, obj_instance, prop_id, addr,
                             service=0x0F)
            return

        if prop_id != P_PRESENT_VALUE or obj_type not in COMMANDABLE:
            # write-access-denied
            self._send_error(invoke, obj_type, obj_instance, prop_id, addr,
                             error_class=2, code=9, service=0x0F)
            self._log_write(device, point, value, priority, "denied")
            return

        if priority is None:
            priority = 16
        if not 1 <= priority <= 16:
            self._send_error(invoke, obj_type, obj_instance, prop_id, addr,
                             error_class=2, code=37, service=0x0F)  # value out of range
            return

        if value is None:
            point["prio"][priority - 1] = None
            outcome = "relinquished"
        else:
            if obj_type in (0, 1, 2):
                value = float(value)
                if point.get("units") == 95:
                    value = max(0.0, min(100.0, value))
            else:
                value = int(value)
            point["prio"][priority - 1] = value
            outcome = "written"

        flags = point.get("flags") or [0, 0, 0, 0]
        flags = list(flags)
        flags[2] = 1 if any(v is not None for v in point["prio"]) else 0
        point["flags"] = flags

        self._log_write(device, point, value, priority, outcome)
        self._send(bytes([0x20, invoke, 0x0F]), addr)

    def _log_write(self, device, point, value, priority, outcome):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "device": device["instance"],
            "device_name": device["name"],
            "object": f"{point['type']}:{point['instance']}",
            "point": point["name"],
            "value": value,
            "priority": priority,
            "outcome": outcome,
        }
        self.write_log.append(entry)
        del self.write_log[:-200]

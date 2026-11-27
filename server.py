"""bacpro — local API + console for BACnet/IP and MS/TP commissioning."""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from flask import Flask, jsonify, request, send_from_directory

from bacnet import BacnetClient, BacnetSimulator, MstpClient, MstpTransport
from bacnet.client import DEMO_PORT
from bacnet.constants import (
    OBJECT_SHORT, OBJECT_TYPES, PROPERTIES, SEGMENTATION, SYSTEM_STATUS, UNITS,
    VENDORS,
)

APP_NAME = "bacpro"
APP_VERSION = "1.0.0"


def _static_dir() -> str:
    """Locate the bundled static assets both from source and from a PyInstaller exe."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = os.path.join(bundle, "static")
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


STATIC_DIR = _static_dir()

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


@app.after_request
def add_no_cache_headers(response):
    if request.path == "/" or request.path.endswith((".js", ".css", ".svg", ".html")):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


# ---------------------------------------------------------------------------
# Desktop launcher heartbeat
#
# run.py polls this to know whether a browser page is still showing the app.
# It is only a fallback: when bacpro launched its own Chromium app window it
# watches that process instead.
# ---------------------------------------------------------------------------
LAST_SEEN = {"time": 0.0}


def last_seen() -> float:
    """Timestamp of the last heartbeat from a browser page (0.0 = never)."""
    return LAST_SEEN["time"]


@app.route("/api/ping", methods=["GET", "POST"])
def api_ping():
    LAST_SEEN["time"] = time.time()
    return jsonify({"ok": True, "app": APP_NAME, "version": APP_VERSION})


simulator = BacnetSimulator(DEMO_PORT)
simulator.start()

ip_client = BacnetClient(demo_target=True, timeout=3.0)
CACHE: dict[str, dict] = {}
EXECUTOR = ThreadPoolExecutor(max_workers=12)
AUDIT: list[dict] = []

MSTP = {
    "transport": None,
    "client": None,
    "port": None,
    "baud": None,
    "mac": None,
    "max_master": None,
    "connected": False,
    "error": None,
}


# ---------------------------------------------------------------------------
# Transport abstraction: an address is either (host, port) or an MS/TP MAC int
# ---------------------------------------------------------------------------
def mstp_connected() -> bool:
    return bool(MSTP["connected"] and MSTP["transport"])


def resolve_address(value) -> tuple:
    """Return (kind, address) where kind is 'ip' or 'mstp'."""
    if isinstance(value, int):
        return "mstp", value
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return "ip", (str(value[0]), int(value[1]))
    text = str(value or "").strip()
    if text.lower().startswith("mstp:"):
        return "mstp", int(text.split(":", 1)[1])
    if text.isdigit() and mstp_connected():
        return "mstp", int(text)
    return "ip", parse_address(text)


def parse_address(value: str) -> tuple[str, int]:
    value = (value or "").strip()
    if not value:
        raise ValueError("address is required")
    if ":" in value:
        host, port = value.rsplit(":", 1)
        return host, int(port)
    return value, 47808


def parse_targets(raw: str) -> list[tuple[str, int]]:
    targets = []
    for chunk in (raw or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            targets.append(parse_address(chunk))
        except ValueError:
            continue
    return targets


def link_read(address, obj_type, instance, prop, index=None, timeout=2.0):
    kind, addr = resolve_address(address)
    if kind == "mstp":
        if not mstp_connected():
            raise RuntimeError("MS/TP is not connected")
        return MSTP["client"].read_property(addr, obj_type, instance, prop,
                                            array_index=index, timeout=timeout)
    return ip_client.read_property(addr, obj_type, instance, prop,
                                   array_index=index, timeout=timeout)


def link_write(address, obj_type, instance, prop, value, priority=None,
               index=None, timeout=4.0):
    kind, addr = resolve_address(address)
    if kind == "mstp":
        if not mstp_connected():
            raise RuntimeError("MS/TP is not connected")
        return MSTP["client"].write_property(addr, obj_type, instance, prop,
                                             value, priority=priority,
                                             array_index=index, timeout=timeout)
    return ip_client.write_property(addr, obj_type, instance, prop, value,
                                    priority=priority, array_index=index,
                                    timeout=timeout)


def json_safe(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    return value


def safe_read(address, obj_type, instance, prop, index=None):
    try:
        return link_read(address, obj_type, instance, prop, index=index, timeout=2.0)
    except Exception:
        return None


def vendor_label(vendor_id: Optional[int]) -> str:
    if vendor_id is None:
        return "Unknown"
    return VENDORS.get(vendor_id, f"Vendor {vendor_id}")


def status_from_flags(flags) -> str:
    if not isinstance(flags, list) or len(flags) < 4:
        return "Unknown"
    in_alarm, fault, overridden, out_of_service = flags[:4]
    if fault:
        return "Fault"
    if in_alarm:
        return "Alarm"
    if out_of_service:
        return "Out of Service"
    if overridden:
        return "Overridden"
    return "Normal"


def format_value(value, obj_type: int, state_text=None):
    if value is None:
        return "\u2014"
    if obj_type in (3, 4, 5) and isinstance(value, int):
        return "On" if value else "Off"
    if obj_type in (13, 14, 19) and isinstance(value, int):
        if state_text and 1 <= value <= len(state_text):
            return state_text[value - 1]
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return f"{value.get('type')}:{value.get('instance')}"
    return str(value)


def read_device_summary(address, iam: dict) -> dict:
    inst = iam["device_instance"]
    props = {
        "name": PROPERTIES["object-name"],
        "model": PROPERTIES["model-name"],
        "firmware": PROPERTIES["firmware-revision"],
        "software": PROPERTIES["application-software-version"],
        "location": PROPERTIES["location"],
        "vendor_name": PROPERTIES["vendor-name"],
        "vendor_id": PROPERTIES["vendor-identifier"],
        "protocol_version": PROPERTIES["protocol-version"],
        "protocol_revision": PROPERTIES["protocol-revision"],
        "system_status": PROPERTIES["system-status"],
        "max_apdu": PROPERTIES["max-apdu-length-accepted"],
        "segmentation": PROPERTIES["segmentation-supported"],
        "database_revision": PROPERTIES["database-revision"],
    }

    values = {}
    futures = {
        EXECUTOR.submit(safe_read, address, 8, inst, prop): key
        for key, prop in props.items()
    }
    for future in as_completed(futures):
        values[futures[future]] = future.result()

    vendor_id = values.get("vendor_id")
    if vendor_id is None:
        vendor_id = iam.get("vendor_id")
    vendor_name = values.get("vendor_name") or vendor_label(vendor_id)
    system_status = values.get("system_status")
    segmentation = values.get("segmentation")

    kind, addr = resolve_address(address)
    return {
        "instance": inst,
        "name": values.get("name") or f"Device-{inst}",
        "address": addr[0] if kind == "ip" else f"MS/TP {addr}",
        "port": addr[1] if kind == "ip" else 0,
        "mac": addr if kind == "mstp" else None,
        "transport": kind,
        "vendor": vendor_name,
        "vendor_id": vendor_id,
        "model": values.get("model") or "\u2014",
        "firmware": values.get("firmware") or "\u2014",
        "software": values.get("software") or "\u2014",
        "location": values.get("location") or "\u2014",
        "protocol_version": values.get("protocol_version"),
        "protocol_revision": values.get("protocol_revision"),
        "system_status": SYSTEM_STATUS.get(system_status, "Unknown")
        if isinstance(system_status, int) else "Unknown",
        "max_apdu": values.get("max_apdu"),
        "segmentation": SEGMENTATION.get(segmentation, "Unknown")
        if isinstance(segmentation, int) else "Unknown",
        "database_revision": values.get("database_revision"),
        "max_apdu_from_iam": iam.get("max_apdu"),
    }


def read_object_metadata(address, inst: int, obj_type: int, obj_instance: int) -> dict:
    name = safe_read(address, obj_type, obj_instance, PROPERTIES["object-name"])
    description = safe_read(address, obj_type, obj_instance, PROPERTIES["description"])
    state_text = None
    if obj_type == 19:
        count = safe_read(address, obj_type, obj_instance, PROPERTIES["number-of-states"])
        if isinstance(count, int) and 0 < count <= 64:
            state_text = [
                safe_read(address, obj_type, obj_instance, PROPERTIES["state-text"], i)
                for i in range(1, count + 1)
            ]
    return {
        "type": obj_type,
        "type_name": OBJECT_TYPES.get(obj_type, f"Object {obj_type}"),
        "short": OBJECT_SHORT.get(obj_type, str(obj_type)),
        "instance": obj_instance,
        "name": name or f"{OBJECT_SHORT.get(obj_type, obj_type)}-{obj_instance}",
        "description": description or "",
        "state_text": state_text,
        "commandable": obj_type in (1, 2, 4, 5, 14, 19),
    }


def read_live(address, obj: dict) -> dict:
    obj_type = obj["type"]
    obj_instance = obj["instance"]
    present = safe_read(address, obj_type, obj_instance, PROPERTIES["present-value"])
    flags = safe_read(address, obj_type, obj_instance, PROPERTIES["status-flags"])
    units_id = None
    if obj_type in (0, 1, 2):
        units_id = safe_read(address, obj_type, obj_instance, PROPERTIES["units"])
    obj = dict(obj)
    obj["present_value"] = json_safe(present)
    obj["present_value_display"] = format_value(present, obj_type, obj.get("state_text"))
    obj["units_id"] = units_id
    obj["units"] = UNITS.get(units_id, f"unit {units_id}") if units_id is not None else ""
    obj["status_flags"] = json_safe(flags)
    obj["status"] = status_from_flags(flags)
    return obj


MAX_OBJECTS = 20000          # sanity guard, not a product limit


def _batches(items, size: int):
    """Yield successive lists of at most *size* items."""
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def load_device(address, inst: int, refresh: bool = False) -> dict:
    kind, addr = resolve_address(address)
    key = f"{kind}:{addr}/{inst}"
    if not refresh and key in CACHE:
        meta = CACHE[key]
    else:
        count = safe_read(address, 8, inst, PROPERTIES["object-list"], 0)
        if not isinstance(count, int):
            raise RuntimeError(f"Device {inst} did not return an object list")

        # Devices routinely expose several thousand objects.  Read the whole
        # list; the only ceiling is a sanity guard against a corrupt count.
        reported = count
        count = max(0, min(count, MAX_OBJECTS))
        truncated = reported > count

        object_ids = []
        for batch in _batches(range(1, count + 1), 64):
            futures = {
                EXECUTOR.submit(safe_read, address, 8, inst,
                                PROPERTIES["object-list"], i): i
                for i in batch
            }
            for future in as_completed(futures):
                value = future.result()
                if isinstance(value, dict) and "type" in value:
                    object_ids.append((value["type"], value["instance"]))
        object_ids.sort(key=lambda item: (item[0], item[1]))

        objects = []
        for batch in _batches(object_ids, 64):
            meta_futures = {
                EXECUTOR.submit(read_object_metadata, address, inst, otype, oinst): (otype, oinst)
                for otype, oinst in batch
            }
            for future in as_completed(meta_futures):
                try:
                    objects.append(future.result())
                except Exception:
                    continue
        objects.sort(key=lambda item: (item["type"], item["instance"]))

        meta = {
            "objects": objects,
            "loaded_at": time.time(),
            "reported_count": reported,
            "truncated": truncated,
        }
        CACHE[key] = meta

    live_objects = []
    for batch in _batches(meta["objects"], 64):
        futures = {EXECUTOR.submit(read_live, address, obj): obj for obj in batch}
        for future in as_completed(futures):
            try:
                live_objects.append(future.result())
            except Exception:
                live_objects.append(dict(futures[future]))
    live_objects.sort(key=lambda item: (item["type"], item["instance"]))

    points = [o for o in live_objects if o["type"] != 8]
    alarms = sum(1 for o in points if o["status"] == "Alarm")
    faults = sum(1 for o in points if o["status"] == "Fault")
    overrides = sum(1 for o in points if o["status"] == "Overridden")

    return {
        "objects": live_objects,
        "stats": {
            "objects": len(live_objects),
            "points": len(points),
            "alarms": alarms,
            "faults": faults,
            "overrides": overrides,
            "reported_count": meta.get("reported_count", len(live_objects)),
            "truncated": meta.get("truncated", False),
        },
    }


def audit(entry: dict):
    AUDIT.append(entry)
    del AUDIT[:-500]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/status")
def api_status():
    mstp_status = MSTP["transport"].status() if mstp_connected() else None
    return jsonify({
        "app": APP_NAME,
        "version": APP_VERSION,
        "simulator": {
            "running": simulator.running,
            "port": simulator.port,
            "devices": simulator.summary() if simulator.running else [],
            "requests": simulator.requests,
        },
        "client_port": ip_client.local_port,
        "transport": "mstp" if mstp_connected() else "ip",
        "mstp": {
            "connected": MSTP["connected"],
            "port": MSTP["port"],
            "baud": MSTP["baud"],
            "mac": MSTP["mac"],
            "max_master": MSTP["max_master"],
            "error": MSTP["error"],
            "status": mstp_status,
        },
        "bbmd": {
            "foreign": ip_client.foreign_status(),
            "simulator_bdt": simulator.bdt_entries() if simulator.running else [],
            "simulator_fdt": simulator.fdt_entries() if simulator.running else [],
        },
        "server_time": time.strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/simulator/start", methods=["POST"])
def api_simulator_start():
    try:
        simulator.start()
        return jsonify({"running": simulator.running, "devices": simulator.summary()})
    except OSError as exc:
        return jsonify({"error": f"Could not start simulator: {exc}"}), 500


@app.route("/api/simulator/stop", methods=["POST"])
def api_simulator_stop():
    simulator.stop()
    return jsonify({"running": simulator.running})


@app.route("/api/discover")
def api_discover():
    timeout = min(max(float(request.args.get("timeout", 3)), 1.0), 15.0)
    transport = request.args.get("transport", "auto")
    started = time.time()

    if transport == "mstp" or (transport == "auto" and mstp_connected()):
        if not mstp_connected():
            return jsonify({"error": "MS/TP is not connected"}), 400
        try:
            found = MSTP["transport"].who_is(timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502
        addresses = [iam["mac"] for iam in found]
    else:
        targets = parse_targets(request.args.get("targets", ""))
        try:
            found = ip_client.who_is(targets=targets, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502
        addresses = [
            (
                iam["address"][0]
                if isinstance(iam.get("address"), (tuple, list))
                else iam.get("address"),
                iam.get("port", 47808),
            )
            for iam in found
        ]

    CACHE.clear()
    devices = []
    futures = {
        EXECUTOR.submit(read_device_summary, addr, iam): iam
        for addr, iam in zip(addresses, found)
    }
    for future in as_completed(futures):
        iam = futures[future]
        try:
            devices.append(future.result())
        except Exception:
            devices.append({
                "instance": iam["device_instance"],
                "name": f"Device-{iam['device_instance']}",
                "address": (iam["address"][0] if isinstance(iam.get("address"), (tuple, list))
                            else iam.get("address", "?")),
                "port": iam.get("port", 0),
                "mac": iam.get("mac"),
                "transport": iam.get("transport", "ip"),
                "vendor": vendor_label(iam.get("vendor_id")),
                "vendor_id": iam.get("vendor_id"),
                "model": "\u2014", "firmware": "\u2014", "software": "\u2014",
                "location": "\u2014", "protocol_version": None,
                "protocol_revision": None, "system_status": "Unknown",
                "max_apdu": None, "segmentation": "Unknown",
                "database_revision": None,
            })

    devices.sort(key=lambda d: d["instance"])
    return jsonify({
        "devices": devices,
        "elapsed_ms": round((time.time() - started) * 1000),
        "transport": "mstp" if (transport == "mstp" or
                                (transport == "auto" and mstp_connected())) else "ip",
        "demo_target": ip_client.demo_target and not mstp_connected(),
    })


@app.route("/api/device")
def api_device():
    raw_address = request.args.get("address", "")
    try:
        kind, address = resolve_address(raw_address)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        instance = int(request.args.get("instance", ""))
    except ValueError:
        return jsonify({"error": "instance is required"}), 400

    refresh = request.args.get("refresh", "0") in ("1", "true", "yes")
    try:
        payload = load_device(address, instance, refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502

    if request.args.get("live") != "1":
        iam = {"device_instance": instance, "address": address[0] if kind == "ip" else "",
               "port": address[1] if kind == "ip" else 0, "vendor_id": None,
               "mac": address if kind == "mstp" else None}
        payload["device"] = read_device_summary(address, iam)
    payload["loaded_at"] = time.strftime("%H:%M:%S")
    return jsonify(payload)


@app.route("/api/read", methods=["POST"])
def api_read():
    body = request.get_json(silent=True) or {}
    try:
        address = body.get("address", "")
        obj_type = int(body["object_type"])
        instance = int(body["instance"])
        prop = int(body.get("property", PROPERTIES["present-value"]))
    except (ValueError, KeyError, TypeError) as exc:
        return jsonify({"error": f"bad request: {exc}"}), 400
    try:
        value = link_read(address, obj_type, instance, prop,
                          index=body.get("array_index"))
        return jsonify({"value": json_safe(value)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.route("/api/priority")
def api_priority():
    try:
        address = request.args.get("address", "")
        obj_type = int(request.args["object_type"])
        instance = int(request.args["instance"])
    except (ValueError, KeyError, TypeError) as exc:
        return jsonify({"error": f"bad request: {exc}"}), 400
    try:
        values = link_read(address, obj_type, instance,
                           PROPERTIES["priority-array"], timeout=4.0)
        if not isinstance(values, list):
            values = []
        values = (values + [None] * 16)[:16]
        return jsonify({"priority_array": json_safe(values)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.route("/api/write", methods=["POST"])
def api_write():
    body = request.get_json(silent=True) or {}
    try:
        address = body.get("address", "")
        obj_type = int(body["object_type"])
        instance = int(body["instance"])
        prop = int(body.get("property", PROPERTIES["present-value"]))
        priority = body.get("priority")
        priority = int(priority) if priority not in (None, "", "auto") else None
        relinquish = bool(body.get("relinquish"))
        value = None if relinquish else body.get("value")
        point_name = body.get("point_name", "")
        device_name = body.get("device_name", "")
    except (ValueError, KeyError, TypeError) as exc:
        return jsonify({"error": f"bad request: {exc}"}), 400

    if priority is not None and not 1 <= priority <= 16:
        return jsonify({"error": "priority must be 1-16"}), 400
    if not relinquish and value in (None, ""):
        return jsonify({"error": "value is required"}), 400

    started = time.time()
    entry = {
        "time": time.strftime("%H:%M:%S"),
        "timestamp": time.time(),
        "device": device_name,
        "address": str(address),
        "object": f"{OBJECT_SHORT.get(obj_type, obj_type)} {instance}",
        "point": point_name,
        "value": value,
        "priority": priority,
        "action": "relinquish" if relinquish else "write",
        "outcome": "pending",
        "elapsed_ms": None,
        "error": None,
    }
    try:
        link_write(address, obj_type, instance, prop, value, priority=priority)
        entry["outcome"] = "ok"
    except Exception as exc:  # noqa: BLE001
        entry["outcome"] = "failed"
        entry["error"] = str(exc)
        entry["elapsed_ms"] = round((time.time() - started) * 1000)
        audit(entry)
        return jsonify({"error": str(exc), "audit": entry}), 502

    entry["elapsed_ms"] = round((time.time() - started) * 1000)
    audit(entry)

    # Return the new value so the UI can update without waiting for a poll.
    try:
        new_value = link_read(address, obj_type, instance,
                              PROPERTIES["present-value"], timeout=3.0)
        flags = link_read(address, obj_type, instance,
                          PROPERTIES["status-flags"], timeout=3.0)
    except Exception:
        new_value, flags = None, None

    return jsonify({
        "ok": True,
        "audit": entry,
        "present_value": json_safe(new_value),
        "status_flags": json_safe(flags),
        "status": status_from_flags(flags),
    })


@app.route("/api/audit")
def api_audit():
    return jsonify({"entries": list(reversed(AUDIT[-200:]))})


# ---------------------------------------------------------------------------
# MS/TP
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# BBMD / foreign device
# ---------------------------------------------------------------------------
def _bbmd_target(raw: str):
    host, port = parse_address(raw)
    return (host, port)


@app.route("/api/bbmd/status")
def api_bbmd_status():
    return jsonify({
        "foreign": ip_client.foreign_status(),
        "simulator": {
            "running": simulator.running,
            "port": simulator.port,
            "bdt": simulator.bdt_entries() if simulator.running else [],
            "fdt": simulator.fdt_entries() if simulator.running else [],
        },
    })


@app.route("/api/bbmd/register", methods=["POST"])
def api_bbmd_register():
    body = request.get_json(force=True, silent=True) or {}
    raw = str(body.get("bbmd", "")).strip()
    if not raw:
        return jsonify({"error": "A BBMD address is required"}), 400
    ttl = int(body.get("ttl") or 300)
    try:
        target = _bbmd_target(raw)
        result = ip_client.start_foreign_registration(target, ttl)
        audit({"time": time.time(), "action": "bbmd-register",
               "target": raw, "ttl": ttl, "ok": result.get("ok")})
        return jsonify(result)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.route("/api/bbmd/unregister", methods=["POST"])
def api_bbmd_unregister():
    ip_client.stop_foreign_registration()
    return jsonify({"ok": True, "foreign": None})


@app.route("/api/bbmd/bdt")
def api_bbmd_bdt():
    raw = str(request.args.get("bbmd", "")).strip()
    if not raw:
        return jsonify({"error": "A BBMD address is required"}), 400
    try:
        return jsonify({"entries": ip_client.read_bdt(_bbmd_target(raw))})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.route("/api/bbmd/fdt")
def api_bbmd_fdt():
    raw = str(request.args.get("bbmd", "")).strip()
    if not raw:
        return jsonify({"error": "A BBMD address is required"}), 400
    try:
        return jsonify({"entries": ip_client.read_fdt(_bbmd_target(raw))})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.route("/api/bbmd/delete", methods=["POST"])
def api_bbmd_delete():
    body = request.get_json(force=True, silent=True) or {}
    raw = str(body.get("bbmd", "")).strip()
    address = str(body.get("address", "")).strip()
    port = int(body.get("port") or 47808)
    if not raw or not address:
        return jsonify({"error": "bbmd and address are required"}), 400
    try:
        result = ip_client.delete_fdt_entry(_bbmd_target(raw), address, port)
        return jsonify(result)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 502


@app.route("/api/mstp/ports")
def api_mstp_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        return jsonify({"ports": [], "error": "pyserial not installed"})
    ports = []
    for info in list_ports.comports():
        ports.append({
            "device": info.device,
            "description": info.description or "",
            "hwid": info.hwid or "",
        })
    return jsonify({"ports": ports})


@app.route("/api/mstp/connect", methods=["POST"])
def api_mstp_connect():
    body = request.get_json(silent=True) or {}
    port = str(body.get("port", "")).strip()
    baud = int(body.get("baud", 38400))
    mac = int(body.get("mac", 1))
    max_master = int(body.get("max_master", 127))

    if not port:
        return jsonify({"error": "serial port is required"}), 400
    if not 0 <= mac <= 127:
        return jsonify({"error": "MAC address must be 0-127"}), 400

    disconnect_mstp()
    try:
        import serial
        serial_port = serial.Serial(port=port, baudrate=baud, bytesize=8,
                                    parity="N", stopbits=1, timeout=0.05,
                                    write_timeout=1.0)
    except Exception as exc:  # noqa: BLE001
        MSTP["error"] = f"Could not open {port}: {exc}"
        return jsonify({"error": MSTP["error"]}), 502

    transport = MstpTransport(serial_port, mac=mac, max_master=max_master)
    transport.start()
    MSTP.update({
        "transport": transport,
        "client": MstpClient(transport),
        "port": port,
        "baud": baud,
        "mac": mac,
        "max_master": max_master,
        "connected": True,
        "error": None,
    })
    time.sleep(0.8)  # let the token state machine settle
    return jsonify({"connected": True, "status": transport.status()})


@app.route("/api/mstp/disconnect", methods=["POST"])
def api_mstp_disconnect():
    disconnect_mstp()
    return jsonify({"connected": False})


def disconnect_mstp():
    transport = MSTP.get("transport")
    if transport:
        try:
            transport.stop()
        except Exception:
            pass
    MSTP.update({"transport": None, "client": None, "connected": False})


@app.route("/api/mstp/status")
def api_mstp_status():
    if not mstp_connected():
        return jsonify({"connected": False, "error": MSTP.get("error")})
    return jsonify({"connected": True, "port": MSTP["port"], "baud": MSTP["baud"],
                    "status": MSTP["transport"].status()})


# ---------------------------------------------------------------------------
# Modbus master section (routes live in modbus/api.py)
# ---------------------------------------------------------------------------
try:
    from modbus.api import register as register_modbus_api
    register_modbus_api(app, audit)
except Exception as exc:  # noqa: BLE001
    print(f"[bacpro] Modbus section unavailable: {exc}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, threaded=True, use_reloader=False)

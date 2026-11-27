"""Modbus section of the bacpro API.

Kept in its own module so the BACnet server file stays readable.  The routes are
registered with :func:`register`, which the server calls once at import time.
"""
from __future__ import annotations

import time
import traceback

from flask import jsonify, request

from modbus import codec as mc
from modbus.master import ModbusMaster, RtuTransport, TcpTransport
from modbus.slave import ModbusTcpSlave

STATE = {
    "master": None,
    "transport": None,
    "kind": None,
    "target": None,
    "unit": 1,
    "connected": False,
    "error": None,
    "simulator": None,
    "sim_config": None,
}

SIM_DEFAULT_PORT = 5020
MAX_SCAN_UNITS = 247


_AUDIT_FN = None


def _audit(entry: dict):
    """Hand the entry to the shared audit log, if one was supplied."""
    if _AUDIT_FN is None:
        return
    try:
        _AUDIT_FN(entry)
    except Exception:  # noqa: BLE001
        pass


def _status_payload() -> dict:
    master = STATE["master"]
    payload = {
        "connected": bool(STATE["connected"] and master),
        "kind": STATE["kind"],
        "target": STATE["target"],
        "unit": STATE["unit"],
        "error": STATE["error"],
    }
    if master:
        payload["transport"] = master.transport.describe()
    sim = STATE["simulator"]
    payload["simulator"] = sim.describe() if sim else None
    return payload


def _disconnect():
    master = STATE["master"]
    if master:
        try:
            master.close()
        except Exception:  # noqa: BLE001
            pass
    STATE.update({"master": None, "transport": None, "connected": False})


def _require_master():
    master = STATE["master"]
    if not master:
        raise RuntimeError("not connected to a Modbus device")
    return master


def _int(body, key, default):
    try:
        return int(body.get(key, default))
    except (TypeError, ValueError):
        return default


def register(app, audit=None):
    global _AUDIT_FN
    if audit is not None:
        _AUDIT_FN = audit
    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    @app.route("/api/modbus/ports")
    def api_modbus_ports():
        try:
            from serial.tools import list_ports
        except ImportError:
            return jsonify({"ports": [], "error": "pyserial not installed"})
        ports = [{"device": p.device, "description": p.description or "",
                  "hwid": p.hwid or ""} for p in list_ports.comports()]
        return jsonify({"ports": ports})

    @app.route("/api/modbus/status")
    def api_modbus_status():
        return jsonify(_status_payload())

    @app.route("/api/modbus/connect", methods=["POST"])
    def api_modbus_connect():
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode", "tcp")).lower()
        unit = _int(body, "unit", 1)
        timeout = float(body.get("timeout", 2.0) or 2.0)
        if not 1 <= unit <= 247:
            return jsonify({"error": "unit id must be 1-247"}), 400

        _disconnect()
        try:
            if mode == "rtu":
                port = str(body.get("serial_port", "")).strip()
                if not port:
                    return jsonify({"error": "serial port is required"}), 400
                transport = RtuTransport(
                    port,
                    baudrate=_int(body, "baudrate", 9600),
                    parity=str(body.get("parity", "N")),
                    stopbits=_int(body, "stopbits", 1),
                    bytesize=_int(body, "bytesize", 8),
                    timeout=timeout,
                )
                target = f"{port} @ {transport.baudrate} {transport.parity}{transport.bytesize}{transport.stopbits}"
                kind = "rtu"
            else:
                host = str(body.get("host", "127.0.0.1")).strip() or "127.0.0.1"
                port = _int(body, "port", 502)
                transport = TcpTransport(host, port, timeout=timeout)
                transport._connect()      # fail fast on a bad host/port
                target = f"{host}:{port}"
                kind = "tcp"
        except Exception as exc:  # noqa: BLE001
            STATE["error"] = str(exc)
            return jsonify({"error": f"could not open connection: {exc}"}), 502

        master = ModbusMaster(transport, unit=unit, timeout=timeout)
        STATE.update({"master": master, "transport": transport, "kind": kind,
                      "target": target, "unit": unit, "connected": True,
                      "error": None})
        _audit({"type": "modbus-connect", "target": target, "unit": unit,
                "time": time.time()})
        return jsonify(_status_payload())

    @app.route("/api/modbus/disconnect", methods=["POST"])
    def api_modbus_disconnect():
        _disconnect()
        return jsonify({"connected": False})

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @app.route("/api/modbus/scan", methods=["POST"])
    def api_modbus_scan():
        body = request.get_json(silent=True) or {}
        try:
            master = _require_master()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409

        start = max(1, min(247, _int(body, "start", 1)))
        end = max(start, min(247, _int(body, "end", 247)))
        function = _int(body, "function", mc.FC_READ_HOLDING_REGISTERS)
        address = _int(body, "address", 0)
        count = max(1, min(125, _int(body, "count", 1)))
        timeout = float(body.get("timeout", 0.4) or 0.4)
        if end - start + 1 > MAX_SCAN_UNITS:
            return jsonify({"error": "range too large"}), 400

        started = time.time()
        results = master.scan(units=range(start, end + 1), function=function,
                              address=address, count=count, timeout=timeout)
        online = [r for r in results if r["online"]]
        return jsonify({
            "start": start,
            "end": end,
            "scanned": len(results),
            "online": online,
            "offline_count": len(results) - len(online),
            "elapsed": round(time.time() - started, 2),
        })

    # ------------------------------------------------------------------
    # Register access
    # ------------------------------------------------------------------
    @app.route("/api/modbus/read", methods=["POST"])
    def api_modbus_read():
        body = request.get_json(silent=True) or {}
        try:
            master = _require_master()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409

        function = _int(body, "function", mc.FC_READ_HOLDING_REGISTERS)
        address = _int(body, "address", 0)
        count = _int(body, "count", 10)
        unit = body.get("unit")
        unit = int(unit) if unit not in (None, "") else None
        fmt = str(body.get("format", "u16"))
        if function not in (mc.FC_READ_COILS, mc.FC_READ_DISCRETE_INPUTS,
                            mc.FC_READ_HOLDING_REGISTERS, mc.FC_READ_INPUT_REGISTERS):
            return jsonify({"error": "unsupported function code"}), 400
        if not 0 <= address <= 65535:
            return jsonify({"error": "address must be 0-65535"}), 400
        limit = mc.MAX_READ_BITS if function in (mc.FC_READ_COILS, mc.FC_READ_DISCRETE_INPUTS) else mc.MAX_READ_REGISTERS
        if not 1 <= count <= limit:
            return jsonify({"error": f"count must be 1-{limit}"}), 400

        started = time.time()
        try:
            values = master.read(function, address, count, unit=unit)
        except mc.ModbusException as exc:
            return jsonify({"error": str(exc), "exception": True,
                            "code": exc.code, "function": function}), 502
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502

        bits = function in (mc.FC_READ_COILS, mc.FC_READ_DISCRETE_INPUTS)
        rows = []
        for i, value in enumerate(values):
            row = {"address": address + i, "raw": value}
            if bits:
                row["value"] = bool(value)
            else:
                row["value"] = value
                row["hex"] = f"0x{value:04X}"
            rows.append(row)
        payload = {
            "function": function,
            "function_name": mc.FUNCTION_NAMES.get(function, str(function)),
            "address": address,
            "count": len(values),
            "values": values,
            "rows": rows,
            "unit": unit if unit is not None else master.unit,
            "ms": round((time.time() - started) * 1000),
            "format": fmt,
        }
        if not bits:
            payload["formatted"] = mc.format_registers(values, fmt)
            payload["format_label"] = mc.FORMAT_LABELS.get(fmt, fmt)
        return jsonify(payload)

    @app.route("/api/modbus/write", methods=["POST"])
    def api_modbus_write():
        body = request.get_json(silent=True) or {}
        try:
            master = _require_master()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409

        kind = str(body.get("kind", "register")).lower()
        unit = body.get("unit")
        unit = int(unit) if unit not in (None, "") else None
        address = _int(body, "address", 0)
        fmt = str(body.get("format", "u16"))

        try:
            if kind == "coil":
                value = bool(body.get("value"))
                master.write_coil(address, value, unit=unit)
                written = [1 if value else 0]
            elif kind == "coils":
                values = [bool(v) for v in (body.get("values") or [])]
                if not values:
                    return jsonify({"error": "no coil values supplied"}), 400
                master.write_coils(address, values, unit=unit)
                written = [1 if v else 0 for v in values]
            elif kind == "register":
                value = body.get("value")
                if fmt in ("u16", "i16", "hex", "bin"):
                    registers = mc.encode_registers(value, fmt)
                else:
                    return jsonify({"error": "single-register writes need a 16-bit format"}), 400
                master.write_register(address, registers[0], unit=unit)
                written = registers
            elif kind == "registers":
                raw = body.get("values")
                if raw in (None, "", []):
                    return jsonify({"error": "no register values supplied"}), 400
                if isinstance(raw, str):
                    registers = [int(tok, 0) for tok in raw.replace(",", " ").split()]
                elif isinstance(raw, list) and len(raw) == 1 and isinstance(raw[0], str):
                    registers = [int(tok, 0) for tok in raw[0].replace(",", " ").split()]
                else:
                    registers = [int(v) for v in raw]
                if fmt not in ("u16", "hex", "bin"):
                    registers = mc.encode_registers(raw[0] if isinstance(raw, list) else raw, fmt)
                master.write_registers(address, registers, unit=unit)
                written = registers
            else:
                return jsonify({"error": f"unknown write kind {kind}"}), 400
        except mc.ModbusException as exc:
            return jsonify({"error": str(exc), "exception": True, "code": exc.code}), 502
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": str(exc)}), 502

        _audit({"type": "modbus-write", "target": STATE["target"], "kind": kind,
                "address": address, "values": written,
                "unit": unit if unit is not None else master.unit, "time": time.time()})
        return jsonify({"ok": True, "address": address, "values": written,
                        "unit": unit if unit is not None else master.unit})

    # ------------------------------------------------------------------
    # Built-in slave simulator
    # ------------------------------------------------------------------
    @app.route("/api/modbus/sim/start", methods=["POST"])
    def api_modbus_sim_start():
        body = request.get_json(silent=True) or {}
        host = str(body.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        port = _int(body, "port", SIM_DEFAULT_PORT)
        units = body.get("units") or [1, 2, 3]
        if isinstance(units, str):
            units = [int(tok) for tok in units.replace(",", " ").split() if tok.strip()]
        units = [int(u) for u in units][:32] or [1]

        sim = STATE["simulator"]
        if sim and sim.running:
            return jsonify({"error": "simulator already running",
                            "simulator": sim.describe()}), 409
        try:
            sim = ModbusTcpSlave(host=host, port=port, units=units)
            sim.start()
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": f"could not start simulator: {exc}"}), 502
        STATE["simulator"] = sim
        STATE["sim_config"] = {"host": host, "port": port, "units": units}
        return jsonify({"simulator": sim.describe()})

    @app.route("/api/modbus/sim/stop", methods=["POST"])
    def api_modbus_sim_stop():
        sim = STATE["simulator"]
        if sim:
            try:
                sim.stop()
            except Exception:  # noqa: BLE001
                pass
        STATE["simulator"] = None
        return jsonify({"simulator": None})

    @app.route("/api/modbus/sim/status")
    def api_modbus_sim_status():
        sim = STATE["simulator"]
        return jsonify({"simulator": sim.describe() if sim else None,
                        "config": STATE["sim_config"]})

    @app.route("/api/modbus/sim/registers")
    def api_modbus_sim_registers():
        """Peek straight into the simulator image (no protocol round trip)."""
        sim = STATE["simulator"]
        if not sim:
            return jsonify({"error": "simulator not running"}), 409
        unit = _int(request.args, "unit", 1)
        start = max(0, _int(request.args, "start", 0))
        count = max(1, min(125, _int(request.args, "count", 20)))
        data = sim.units.get(unit)
        if not data:
            return jsonify({"error": f"unit {unit} not simulated"}), 404
        return jsonify({"unit": unit, "start": start,
                        "holding": data.holding[start:start + count],
                        "coils": data.coils[start:start + count],
                        "writes": sim.writes[-20:]})

    return app

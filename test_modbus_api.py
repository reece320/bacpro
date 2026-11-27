"""End-to-end test of the Modbus HTTP API and its UI-facing payloads."""
from __future__ import annotations

import sys

import server

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


def main():
    client = server.app.test_client()

    print("\nSimulator control")
    r = client.post("/api/modbus/sim/start", json={"port": 5030, "units": [1, 2]})
    check("sim start", r.status_code == 200, r.data[:200])
    check("sim describes units", r.get_json()["simulator"]["units"] == [1, 2])
    r = client.post("/api/modbus/sim/start", json={"port": 5030})
    check("double start rejected", r.status_code == 409)

    print("\nConnection")
    r = client.post("/api/modbus/connect", json={"mode": "tcp", "host": "127.0.0.1",
                                                 "port": 5030, "unit": 1})
    check("connect tcp", r.status_code == 200, r.data[:200])
    check("status connected", client.get("/api/modbus/status").get_json()["connected"])

    r = client.post("/api/modbus/connect", json={"mode": "tcp", "host": "127.0.0.1",
                                                 "port": 5999, "unit": 1})
    check("bad port reports error", r.status_code == 502, r.status_code)

    r = client.post("/api/modbus/connect", json={"mode": "tcp", "host": "127.0.0.1",
                                                 "port": 5030, "unit": 1})
    check("reconnect after failure", r.status_code == 200)

    print("\nReads")
    r = client.post("/api/modbus/read", json={"function": 3, "address": 0, "count": 5})
    payload = r.get_json()
    check("read holding", r.status_code == 200 and payload["values"] == [100, 107, 114, 121, 128],
          str(payload)[:200])
    check("read rows carry addresses", [row["address"] for row in payload["rows"]] == [0, 1, 2, 3, 4])
    check("read rows carry hex", payload["rows"][0]["hex"] == "0x0064")

    r = client.post("/api/modbus/read", json={"function": 1, "address": 0, "count": 4})
    check("read coils", r.get_json()["values"] == [1, 0, 1, 0], str(r.get_json())[:200])

    r = client.post("/api/modbus/read", json={"function": 3, "address": 100, "count": 2,
                                              "format": "f32"})
    check("read float format", r.get_json()["formatted"] == [21.5], str(r.get_json())[:200])

    r = client.post("/api/modbus/read", json={"function": 3, "address": 9000, "count": 2})
    check("illegal address -> exception payload",
          r.status_code == 502 and r.get_json().get("code") == 2, r.data[:200])

    r = client.post("/api/modbus/read", json={"function": 3, "address": 0, "count": 999})
    check("count limit enforced", r.status_code == 400)

    r = client.post("/api/modbus/read", json={"function": 3, "address": 0, "count": 1, "unit": 2})
    check("read other unit", r.status_code == 200 and r.get_json()["values"] == [100])

    print("\nWrites")
    r = client.post("/api/modbus/write", json={"kind": "register", "address": 40, "value": 4321})
    check("write register", r.status_code == 200, r.data[:200])
    r = client.post("/api/modbus/read", json={"function": 3, "address": 40, "count": 1})
    check("write read back", r.get_json()["values"] == [4321])

    r = client.post("/api/modbus/write", json={"kind": "coil", "address": 12, "value": True})
    check("write coil", r.status_code == 200)
    r = client.post("/api/modbus/read", json={"function": 1, "address": 12, "count": 1})
    check("coil read back", r.get_json()["values"] == [1])

    r = client.post("/api/modbus/write", json={"kind": "registers", "address": 50,
                                               "values": [11, 22, 33]})
    check("write registers", r.status_code == 200)
    r = client.post("/api/modbus/read", json={"function": 3, "address": 50, "count": 3})
    check("registers read back", r.get_json()["values"] == [11, 22, 33])

    r = client.post("/api/modbus/write", json={"kind": "coils", "address": 20,
                                               "values": [True, False, True]})
    check("write coils", r.status_code == 200)
    r = client.post("/api/modbus/read", json={"function": 1, "address": 20, "count": 3})
    check("coils read back", r.get_json()["values"] == [1, 0, 1])

    r = client.post("/api/modbus/write", json={"kind": "registers", "address": 70,
                                               "values": [21.5], "format": "f32"})
    check("write float", r.status_code == 200, r.data[:200])
    r = client.post("/api/modbus/read", json={"function": 3, "address": 70, "count": 2,
                                              "format": "f32"})
    check("float read back", r.get_json()["formatted"] == [21.5], str(r.get_json())[:200])

    print("\nScan")
    r = client.post("/api/modbus/scan", json={"start": 1, "end": 6, "timeout": 0.3})
    payload = r.get_json()
    online = [item["unit"] for item in payload["online"]]
    check("scan finds simulated units", online == [1, 2], str(online))
    check("scan reports offline count", payload["offline_count"] == 4, str(payload))
    check("scan reports elapsed", "elapsed" in payload)

    print("\nSimulator image")
    r = client.get("/api/modbus/sim/registers?unit=1&start=0&count=3")
    check("sim image readable", r.get_json()["holding"] == [100, 107, 114], r.data[:200])
    check("sim write log populated", len(r.get_json()["writes"]) > 0)

    print("\nDisconnect")
    r = client.post("/api/modbus/disconnect")
    check("disconnect", r.status_code == 200)
    r = client.post("/api/modbus/read", json={"function": 3, "address": 0, "count": 1})
    check("read after disconnect is 409", r.status_code == 409, r.status_code)

    client.post("/api/modbus/sim/stop")
    check("sim stopped", client.get("/api/modbus/sim/status").get_json()["simulator"] is None)

    print(f"\n{PASS} passed, {FAIL} failed")
    if FAILURES:
        print("Failures: " + ", ".join(FAILURES))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

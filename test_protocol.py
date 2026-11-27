"""BACnet/IP protocol tests: discover, read, write, priority array, relinquish.

Run:  PYTHONPATH=. python3 test_protocol.py
"""
import time

from bacnet import BacnetClient, BacnetError, BacnetSimulator
from bacnet.constants import PROPERTIES

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def main():
    sim = BacnetSimulator()
    sim.start()
    time.sleep(0.25)
    client = BacnetClient(demo_target=True, timeout=2.0)
    try:
        print("== discovery ==")
        devices = client.who_is(timeout=1.5)
        instances = sorted(d["device_instance"] for d in devices)
        check("three devices answer Who-Is", instances == [1001, 2002, 3003], instances)
        addr = (devices[0]["address"], devices[0]["port"])
        inst = devices[0]["device_instance"]

        print("== device reads ==")
        name = client.read_property(addr, 8, inst, PROPERTIES["object-name"])
        check("object-name readable", isinstance(name, str) and bool(name), name)
        count = client.read_property(addr, 8, inst, PROPERTIES["object-list"], 0)
        check("object-list count > 0", isinstance(count, int) and count > 0, count)

        print("== write / priority array ==")
        pv0 = client.read_property(addr, 1, 101, PROPERTIES["present-value"])
        client.write_property(addr, 1, 101, PROPERTIES["present-value"], 45.0, priority=8)
        pv1 = client.read_property(addr, 1, 101, PROPERTIES["present-value"])
        check("present-value follows write", abs(pv1 - 45.0) < 0.01, pv1)

        prio = client.read_array(addr, 1, 101, PROPERTIES["priority-array"])
        check("priority array has 16 slots", len(prio) == 16, len(prio))
        check("slot 8 holds 45.0", prio[7] is not None and abs(prio[7] - 45.0) < 0.01, prio[7])
        check("slot 1 is NULL", prio[0] is None, prio[0])

        flags = client.read_property(addr, 1, 101, PROPERTIES["status-flags"])
        check("overridden flag set", bool(flags[2]), flags)

        client.write_property(addr, 1, 101, PROPERTIES["present-value"], 10.0, priority=3)
        pv2 = client.read_property(addr, 1, 101, PROPERTIES["present-value"])
        check("priority 3 beats priority 8", abs(pv2 - 10.0) < 0.01, pv2)

        client.write_property(addr, 1, 101, PROPERTIES["present-value"], None, priority=3)
        pv3 = client.read_property(addr, 1, 101, PROPERTIES["present-value"])
        check("relinquish falls back to P8", abs(pv3 - 45.0) < 0.01, pv3)

        client.write_property(addr, 1, 101, PROPERTIES["present-value"], None, priority=8)
        pv4 = client.read_property(addr, 1, 101, PROPERTIES["present-value"])
        check("full relinquish restores automatic value", abs(pv4 - pv0) < 6.0, (pv0, pv4))
        flags2 = client.read_property(addr, 1, 101, PROPERTIES["status-flags"])
        check("override flag cleared", not flags2[2], flags2)

        print("== binary write ==")
        client.write_property(addr, 4, 102, PROPERTIES["present-value"], True, priority=8)
        bv = client.read_property(addr, 4, 102, PROPERTIES["present-value"])
        check("BO write sets active", bv is True or bv == 1, bv)
        client.write_property(addr, 4, 102, PROPERTIES["present-value"], None, priority=8)

        print("== error handling ==")
        try:
            client.write_property(addr, 0, 101, PROPERTIES["present-value"], 1.0, priority=8)
            check("analog-input write rejected", False, "no error raised")
        except BacnetError as exc:
            text = str(exc).lower()
            check("analog-input write rejected", "denied" in text or "write" in text, exc)

        print("== write audit log ==")
        check("simulator recorded writes", len(sim.write_log) >= 5, len(sim.write_log))
    finally:
        client.close()
        sim.stop()

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

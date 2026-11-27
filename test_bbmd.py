"""BBMD / foreign-device and large-device tests.

Exercises the real BVLC layer against the built-in simulator acting as a
BBMD: registration, TTL, BDT/FDT reads, forwarded NPDUs and distributed
broadcasts.  Also proves the object-list loader no longer stops at 250.

Run:  PYTHONPATH=. python3 test_bbmd.py
"""
import time

from bacnet.client import BacnetClient
from bacnet.simulator import BacnetSimulator

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


def test_bbmd():
    print("== BBMD / foreign device ==")
    sim = BacnetSimulator(port=47830)
    sim.start()
    client = BacnetClient(port=47820, demo_target=False)
    time.sleep(0.3)
    bbmd = ("127.0.0.1", 47830)
    try:
        result = client.register_foreign_device(bbmd, ttl=60)
        check("register-foreign-device accepted", result["ok"], result)
        check("result message is success", result["message"] == "successful completion",
              result["message"])

        bdt = client.read_bdt(bbmd)
        check("read-BDT returns the BBMD itself",
              any(e["address"] == "127.0.0.1" and e["port"] == 47830 for e in bdt), bdt)

        fdt = client.read_fdt(bbmd)
        check("read-FDT lists this client",
              any(e["port"] == 47820 for e in fdt), fdt)
        check("FDT entry carries the requested TTL",
              any(e["ttl"] == 60 for e in fdt), fdt)

        # A foreign device cannot broadcast locally, so it asks the BBMD to
        # distribute the Who-Is.  Replies must come back as Forwarded-NPDUs.
        with client._iam_lock:
            client._iams.clear()
        apdu = bytes([0x10, 0x08])
        client.distribute_broadcast(bbmd, client._npdu(apdu))
        time.sleep(0.8)
        iams = client.iams()
        check("distributed Who-Is produced I-Ams", len(iams) >= 3, len(iams))
        check("I-Am origin is the simulator address",
              all(r["address"] == "127.0.0.1" and r["port"] == 47830 for r in iams),
              iams[:1])
        check("all three virtual devices answered",
              {r["device_instance"] for r in iams} >= {1001, 2002, 3003},
              sorted({r["device_instance"] for r in iams}))

        deleted = client.delete_fdt_entry(bbmd, "127.0.0.1", 47820)
        check("delete-FDT-entry accepted", deleted["ok"], deleted)
        fdt_after = client.read_fdt(bbmd)
        check("FDT no longer lists the client",
              not any(e["port"] == 47820 for e in fdt_after), fdt_after)
    finally:
        client.close()
        sim.stop()


def test_large_device():
    print("== large device (no 250-object cap) ==")
    sim = BacnetSimulator(port=47831, large_points=300)
    sim.start()
    time.sleep(0.3)
    try:
        import server
        server.CACHE.clear()
        data = server.load_device("127.0.0.1:47831", 4004, refresh=True)
        count = data["stats"]["objects"]
        check("all 300 bulk objects loaded", count >= 300, count)
        check("reported count matches", data["stats"]["reported_count"] >= 300,
              data["stats"]["reported_count"])
        check("not flagged as truncated", data["stats"]["truncated"] is False,
              data["stats"]["truncated"])
    finally:
        sim.stop()


if __name__ == "__main__":
    test_bbmd()
    test_large_device()
    print(f"\n{PASS} passed, {FAIL} failed")
    raise SystemExit(1 if FAIL else 0)

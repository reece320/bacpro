"""MS/TP over RS-485 tests using an in-memory loopback bus.

Exercises the real data-link layer: CRC-16 framing, token passing,
Poll-For-Master, Who-Is, ReadProperty and WriteProperty to a slave.

Run:  PYTHONPATH=. python3 test_mstp.py
"""
import time

from bacnet.constants import PROPERTIES
from bacnet.mstp import (
    crc16,
    crc_header,
    crc_data,
    build_frame,
    parse_frame,
    FRAME_DATA_EXPECTING_REPLY,
)
from bacnet.mstp_sim import build_loopback

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


def test_framing():
    print("== frame codec ==")
    payload = bytes([0x01, 0x02, 0x03, 0x04])
    frame = build_frame(FRAME_DATA_EXPECTING_REPLY, 5, 1, payload)
    check("preamble 55 FF", frame[:2] == b"\x55\xff", frame[:2])
    check("frame type byte", frame[2] == FRAME_DATA_EXPECTING_REPLY, frame[2])
    check("length = data + 2", frame[5:7] == (len(payload) + 2).to_bytes(2, "big"), frame[5:7])
    parsed = parse_frame(frame)
    check("round-trip type", parsed[0] == FRAME_DATA_EXPECTING_REPLY, parsed[0])
    check("round-trip dest/src", (parsed[1], parsed[2]) == (5, 1), parsed[1:3])
    check("round-trip payload", parsed[3] == payload, parsed[3])
    check("consumed == frame length", parsed[4] == len(frame), parsed[4])

    corrupt = bytearray(frame)
    corrupt[-1] ^= 0xFF
    check("corrupt frame flagged", parse_frame(bytes(corrupt))[0] in ("bad-crc", "bad-length"))

    # Reference values from bacnet-stack src/bacnet/datalink/crc.c
    check("header CRC (BACnet std)", crc_header(bytes([0x00, 0x10, 0x05, 0x00, 0x02])) == 0x73,
          hex(crc_header(bytes([0x00, 0x10, 0x05, 0x00, 0x02]))))
    check("data CRC (BACnet std)", crc_data(bytes([0x01, 0x22, 0x30])) == 0xBD10,
          hex(crc_data(bytes([0x01, 0x22, 0x30]))))
    check("data CRC alias", crc16(bytes([0x01, 0x22, 0x30])) == 0xBD10)


def test_loopback():
    print("== master/slave loopback ==")
    transport, slave = build_loopback(mac=1, slave_mac=2, max_master=127)
    transport.start()
    slave.start()
    time.sleep(0.3)
    try:
        found = transport.who_is(timeout=2.0)
        macs = sorted({iam["mac"] for iam in found})
        check("slave answers Who-Is over MS/TP", macs == [2], macs)
        check("slave reports its three devices", len(found) == 3, len(found))
        if not found:
            return
        iam = found[0]
        check("I-Am carries device instance", iam["device_instance"] > 0, iam)

        from bacnet.mstp import MstpClient
        client = MstpClient(transport)

        name = client.read_property(2, 8, iam["device_instance"], PROPERTIES["object-name"])
        check("ReadProperty object-name over MS/TP", isinstance(name, str) and bool(name), name)

        pv = client.read_property(2, 1, 101, PROPERTIES["present-value"])
        check("ReadProperty present-value over MS/TP", isinstance(pv, (int, float)), pv)

        ok = client.write_property(2, 1, 101, PROPERTIES["present-value"], 42.5, priority=8)
        check("WriteProperty over MS/TP acknowledged", ok is True, ok)
        pv2 = client.read_property(2, 1, 101, PROPERTIES["present-value"])
        check("value applied over MS/TP", abs(pv2 - 42.5) < 0.01, pv2)

        prio = client.read_property(2, 1, 101, PROPERTIES["priority-array"])
        check("priority array readable over MS/TP",
              isinstance(prio, list) and len(prio) == 16, prio)

        client.write_property(2, 1, 101, PROPERTIES["present-value"], None, priority=8)
        pv3 = client.read_property(2, 1, 101, PROPERTIES["present-value"])
        check("relinquish over MS/TP", abs(pv3 - 42.5) > 0.01, pv3)

        stats = transport.status()["stats"]
        print("   stats:", stats)
        check("token traffic observed", stats["tokens"] > 0, stats)
        check("no CRC errors", stats["crc_errors"] == 0, stats["crc_errors"])
        check("frames received", stats["frames_received"] > 0, stats["frames_received"])
    finally:
        slave.stop()
        transport.stop()


def main():
    test_framing()
    test_loopback()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

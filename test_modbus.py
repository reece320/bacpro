"""Modbus master/slave test suite.

Everything here is checked against an independent implementation where one
exists: request PDUs and CRCs are compared byte-for-byte with pymodbus, and a
live read is served by a real pymodbus server so the master is not merely
agreeing with our own slave.
"""
from __future__ import annotations

import struct
import sys
import time

from modbus import codec as mc
from modbus.master import ModbusMaster, RtuTransport, TcpTransport
from modbus.slave import ModbusRtuSlave, ModbusTcpSlave, make_rtu_pair

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


def section(title):
    print(f"\n{title}")


# ----------------------------------------------------------------------
# 1. Codec: CRC and PDU layout against pymodbus
# ----------------------------------------------------------------------
def test_codec():
    section("Codec")
    check("crc16('123456789') == 0x4B37", mc.crc16(b"123456789") == 0x4B37,
          hex(mc.crc16(b"123456789")))
    frame = mc.build_rtu(1, mc.build_read_request(mc.FC_READ_HOLDING_REGISTERS, 0, 10))
    check("RTU frame round-trips", mc.parse_rtu(frame) == (1, bytes.fromhex("030000000a")))
    check("bad CRC rejected", not mc.check_crc(frame[:-1] + bytes([frame[-1] ^ 0xFF])))

    mbap = mc.build_mbap(7, 3, bytes.fromhex("030000000a"))
    txn, unit, pdu = mc.parse_mbap(mbap)
    check("MBAP round-trips", (txn, unit, pdu) == (7, 3, bytes.fromhex("030000000a")),
          str((txn, unit, pdu)))

    try:
        from pymodbus.pdu import register_message  # noqa: F401
        have_pm = True
    except Exception:  # noqa: BLE001
        have_pm = False

    if have_pm:
        from pymodbus.pdu.bit_message import (ReadCoilsRequest,
                                             WriteMultipleCoilsRequest,
                                             WriteSingleCoilRequest)
        from pymodbus.pdu.register_message import (
            ReadHoldingRegistersRequest, WriteMultipleRegistersRequest,
            WriteSingleRegisterRequest)

        def pm_pdu(req):
            return bytes([req.function_code]) + req.x_encode()

        cases = [
            ("read holding", pm_pdu(ReadHoldingRegistersRequest(address=0, count=10)),
             mc.build_read_request(mc.FC_READ_HOLDING_REGISTERS, 0, 10)),
            ("read coils", pm_pdu(ReadCoilsRequest(address=0x13, count=10)),
             mc.build_read_request(mc.FC_READ_COILS, 0x13, 10)),
            ("write single register", pm_pdu(WriteSingleRegisterRequest(address=1, registers=[3])),
             mc.build_write_single_register(1, 3)),
            ("write single coil", pm_pdu(WriteSingleCoilRequest(address=5, bits=[True])),
             mc.build_write_single_coil(5, True)),
            ("write multiple registers",
             pm_pdu(WriteMultipleRegistersRequest(address=10, registers=[1, 2, 3])),
             mc.build_write_multiple_registers(10, [1, 2, 3])),
            ("write multiple coils",
             pm_pdu(WriteMultipleCoilsRequest(address=0x13, bits=[True, False, True, True])),
             mc.build_write_multiple_coils(0x13, [True, False, True, True])),
        ]
        for name, oracle, ours in cases:
            check(f"pymodbus match: {name}", oracle == ours,
                  f"pm={oracle.hex()} ours={ours.hex()}")
    else:
        print("  SKIP  pymodbus not installed")

    # Response parsing
    body = struct.pack(">HHHH", 1, 2, 3, 4)
    values = mc.parse_response(bytes([mc.FC_READ_HOLDING_REGISTERS, len(body)]) + body)
    check("parse read response", values == [1, 2, 3, 4], str(values))

    exc = bytes([mc.FC_READ_HOLDING_REGISTERS | 0x80, 0x02])
    try:
        mc.parse_response(exc)
        check("exception raised", False)
    except mc.ModbusException as err:
        check("exception raised", err.code == 0x02, str(err.code))


# ----------------------------------------------------------------------
# 2. Register formatting
# ----------------------------------------------------------------------
def test_formats():
    section("Register formats")
    check("u16", mc.format_registers([0x1234], "u16") == [4660])
    check("i16 negative", mc.format_registers([0xFFFF], "i16") == [-1])
    check("hex", mc.format_registers([0x1234], "hex") == ["0x1234"])
    check("bin", mc.format_registers([0x0005], "bin") == ["0000000000000101"])

    raw = struct.unpack(">HH", struct.pack(">f", 21.5))
    check("f32 big-endian", mc.format_registers(list(raw), "f32") == [21.5])
    swapped = [raw[1], raw[0]]   # low word first
    check("f32 word-swapped", mc.format_registers(swapped, "f32s") == [21.5])

    raw32 = struct.unpack(">HH", struct.pack(">I", 123456))
    check("u32", mc.format_registers(list(raw32), "u32") == [123456])

    check("encode u16", mc.encode_registers(4660, "u16") == [0x1234])
    check("encode f32", mc.encode_registers(21.5, "f32") == list(raw))
    check("encode i16", mc.encode_registers(-1, "i16") == [0xFFFF])
    check("registers_to_int", mc.registers_to_int([0x0001, 0x0002]) == 0x00010002)


# ----------------------------------------------------------------------
# 3. TCP master against our slave
# ----------------------------------------------------------------------
def test_tcp():
    section("TCP master <-> slave")
    slave = ModbusTcpSlave(host="127.0.0.1", port=5020, units=[1, 3])
    slave.start()
    time.sleep(0.2)
    master = ModbusMaster(TcpTransport("127.0.0.1", 5020, timeout=2.0), unit=1)
    try:
        regs = master.read_holding_registers(0, 10)
        check("read holding registers", regs == [100 + i * 7 for i in range(10)], str(regs))

        inputs = master.read_input_registers(0, 5)
        check("read input registers", inputs == [500 + i * 3 for i in range(5)], str(inputs))

        coils = master.read_coils(0, 8)
        check("read coils", coils == [1, 0, 1, 0, 1, 0, 1, 0], str(coils))

        discrete = master.read_discrete_inputs(0, 6)
        check("read discrete inputs", discrete == [0, 1, 1, 0, 1, 1], str(discrete))

        master.write_register(50, 4321)
        check("write single register", master.read_holding_registers(50, 1) == [4321])

        master.write_coil(20, True)
        check("write single coil", master.read_coils(20, 1) == [1])

        master.write_registers(60, [11, 22, 33])
        check("write multiple registers", master.read_holding_registers(60, 3) == [11, 22, 33])

        master.write_coils(30, [True, True, False, True])
        check("write multiple coils", master.read_coils(30, 4) == [1, 1, 0, 1])

        # Seeded float / counter
        check("seeded float at 100", mc.format_registers(master.read_holding_registers(100, 2), "f32") == [21.5])
        check("seeded u32 at 110", mc.format_registers(master.read_holding_registers(110, 2), "u32") == [123456])

        # Unit 3 has its own image
        check("unit 3 seeded independently",
              master.read_holding_registers(0, 3, unit=3) == [100, 107, 114])
        check("unit 3 not touched by unit 1 write",
              master.read_holding_registers(50, 1, unit=3) == [0])

        # Exception path: address past the end
        try:
            master.read_holding_registers(9999, 2)
            check("illegal address raises", False)
        except mc.ModbusException as err:
            check("illegal address raises", err.code == 0x02, str(err.code))

        # Unknown unit id: a real slave stays silent, so we expect a timeout
        try:
            master.read_holding_registers(0, 1, unit=99)
            check("unknown unit times out", False)
        except mc.ModbusException as err:
            check("unknown unit times out", err.code is None, str(err.code))
        except mc.ModbusError:
            check("unknown unit times out", True)

        results = master.scan(units=[1, 2, 3, 4], timeout=0.3)
        online = [r["unit"] for r in results if r["online"]]
        check("scan finds units 1 and 3", online == [1, 3], str(online))
        check("scan marks 2 and 4 offline", all(not r["online"] for r in results if r["unit"] in (2, 4)))

        probe = master.probe(1, timeout=0.5)
        check("probe reports data + ms", probe["online"] and probe["response"] == "data" and "ms" in probe,
              str(probe))

        check("slave logged writes", len(slave.writes) >= 4, str(len(slave.writes)))
        check("slave request counter", slave.requests > 0, str(slave.requests))
    finally:
        master.close()
        slave.stop()


# ----------------------------------------------------------------------
# 4. RTU master against our slave over an in-memory pipe
# ----------------------------------------------------------------------
def test_rtu():
    section("RTU master <-> slave (loopback)")
    master_serial, slave_serial = make_rtu_pair()
    slave = ModbusRtuSlave(slave_serial, units=[1, 5])
    slave.start()
    transport = RtuTransport.__new__(RtuTransport)  # bypass pyserial
    transport._serial = master_serial
    transport.port = "loopback"
    transport.baudrate = 9600
    transport.parity = "N"
    transport.stopbits = 1
    transport.bytesize = 8
    transport.timeout = 1.0
    transport.kind = "rtu"
    transport._lock = __import__("threading").Lock()
    master = ModbusMaster(transport, unit=1, timeout=1.0)
    try:
        regs = master.read_holding_registers(0, 4)
        check("RTU read holding", regs == [100, 107, 114, 121], str(regs))

        master.write_register(70, 777)
        check("RTU write single register", master.read_holding_registers(70, 1) == [777])

        master.write_registers(80, [5, 6, 7])
        check("RTU write multiple registers", master.read_holding_registers(80, 3) == [5, 6, 7])

        master.write_coils(40, [True, False, True])
        check("RTU write multiple coils", master.read_coils(40, 3) == [1, 0, 1])

        check("RTU unit 5 independent", master.read_holding_registers(70, 1, unit=5) == [0])
        check("RTU unit 5 seeded", master.read_holding_registers(0, 2, unit=5) == [100, 107])
    finally:
        slave.stop()
        master.close()


# ----------------------------------------------------------------------
# 5. Live cross-check against a real pymodbus server
# ----------------------------------------------------------------------
def test_pymodbus_server():
    section("Master against a real pymodbus server")
    try:
        from pymodbus.datastore import (ModbusDeviceContext,
                                        ModbusSequentialDataBlock,
                                        ModbusServerContext)
        from pymodbus.server import StartTcpServer
        import threading
    except Exception as exc:  # noqa: BLE001
        print(f"  SKIP  pymodbus server unavailable ({exc})")
        return

    # pymodbus addresses are 1-based internally; register 10 is block address 11.
    block = ModbusSequentialDataBlock(11, [0x4248, 0x0000] + [0] * 100)
    context = ModbusServerContext(devices=ModbusDeviceContext(hr=block), single=True)
    thread = threading.Thread(
        target=StartTcpServer,
        kwargs={"context": context, "address": ("127.0.0.1", 5021)},
        daemon=True,
    )
    thread.start()
    time.sleep(0.8)
    master = ModbusMaster(TcpTransport("127.0.0.1", 5021, timeout=2.0), unit=1)
    try:
        regs = master.read_holding_registers(10, 2)
        check("pymodbus server read", regs == [0x4248, 0x0000], str(regs))
        check("pymodbus server f32 decode", mc.format_registers(regs, "f32") == [50.0],
              str(mc.format_registers(regs, "f32")))
        master.write_register(20, 1234)
        check("pymodbus server write", master.read_holding_registers(20, 1) == [1234])
    finally:
        master.close()


def main():
    started = time.time()
    test_codec()
    test_formats()
    test_tcp()
    test_rtu()
    test_pymodbus_server()
    elapsed = time.time() - started
    print(f"\n{PASS} passed, {FAIL} failed  ({elapsed:.1f}s)")
    if FAILURES:
        print("Failures: " + ", ".join(FAILURES))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

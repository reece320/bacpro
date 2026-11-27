import io

p = "README.md"
s = io.open(p, encoding="utf-8").read()


def sub(old, new):
    global s
    assert old in s, old[:80]
    s = s.replace(old, new, 1)


# ------------------------------------------------------------ architecture tree
sub("""bacnet/
  constants.py          Object types, property IDs, service codes
  encoding.py           ASHRAE 135 tag encode/decode (app + context tags, NULL)
  client.py             BACnet/IP client: Who-Is, ReadProperty, WriteProperty
  simulator.py          Virtual BACnet/IP device with priority arrays
  mstp.py               MS/TP: CRC-16/X.25, frame codec, master state machine, transport
  mstp_sim.py           Loopback MS/TP slave for hardware-free testing

static/
  index.html            Console shell
  styles.css            ComfortPoint-inspired theme
  app.js                Vanilla-JS front end (no framework)

tools/make_icon.py      Generates assets/icon.png + icon.ico
test_protocol.py        15 assertions: IP discover/read/write/relinquish
test_mstp.py            21 assertions: MS/TP frame codec + loopback discover/read/write
```""",
    """bacnet/
  constants.py          Object types, property IDs, service codes
  encoding.py           ASHRAE 135 tag encode/decode (app + context tags, NULL)
  client.py             BACnet/IP client: Who-Is, ReadProperty, WriteProperty, BBMD
  simulator.py          Virtual BACnet/IP device with priority arrays + BBMD behaviour
  mstp.py               MS/TP: CRC-16/X.25, frame codec, master state machine, transport
  mstp_sim.py           Loopback MS/TP slave for hardware-free testing

modbus/
  codec.py              RTU CRC-16, MBAP framing, PDU builders/parsers, register formats
  master.py             TCP + RTU transports, read/write, unit-ID scan
  slave.py              Slave core + TCP/RTU servers used by the simulator and tests
  api.py                Flask routes for the Modbus section

static/
  index.html            Console shell
  styles.css            ComfortPoint-inspired theme
  app.js                Vanilla-JS front end (no framework)

tools/make_icon.py      Generates assets/icon.png + icon.ico
installer/bacpro.iss    Inno Setup script (optional Windows installer)
test_protocol.py        15 assertions: IP discover/read/write/relinquish
test_mstp.py            23 assertions: MS/TP frame codec + loopback discover/read/write
test_bbmd.py            13 assertions: foreign-device registration, BDT/FDT, large device
test_modbus.py          51 assertions: codec, CRC, TCP/RTU master-slave, live pymodbus server
test_modbus_api.py      33 assertions: Modbus HTTP API end to end
```""")

# ------------------------------------------------------------ tests
sub("""```bash
python test_protocol.py     # 15 passed, 0 failed
python test_mstp.py         # 21 passed, 0 failed
```

The MS/TP test runs against an in-process loopback slave, so it needs no serial hardware.
It exercises real frame encoding, CRC validation, token passing, Who-Is, ReadProperty,
WriteProperty, priority-array readback, and relinquish.""",
    """```bash
python test_protocol.py     # 15 passed, 0 failed
python test_mstp.py         # 23 passed, 0 failed
python test_bbmd.py         # 13 passed, 0 failed
python test_modbus.py       # 51 passed, 0 failed
python test_modbus_api.py   # 33 passed, 0 failed
```

135 checks in total. The MS/TP test runs against an in-process loopback slave, so it needs
no serial hardware: real frame encoding, CRC validation, token passing, Who-Is,
ReadProperty, WriteProperty, priority-array readback, and relinquish.

The protocol encoders are checked against independent implementations rather than against
themselves \u2014 `bacpypes3` for BVLC headers, ASHRAE 135 tag encoding and WriteProperty
payloads, the reference `bacnet-stack` CRC routines for MS/TP, and `pymodbus` for every
Modbus PDU, the RTU CRC, and a live Modbus TCP server that the master reads and writes
through. If a test passes, an independent stack agrees with our bytes.""")

# ------------------------------------------------------------ API table
sub("""| POST | `/api/simulator/start` \\| `/stop` | Demo network control |""",
    """| POST | `/api/simulator/start` \\| `/stop` | Demo network control |
| GET | `/api/bbmd/status` | Foreign-device state + simulator BDT/FDT |
| POST | `/api/bbmd/register` \\| `/unregister` | Register as a foreign device |
| GET | `/api/bbmd/bdt` \\| `/fdt` | Read a BBMD's tables |
| POST | `/api/bbmd/delete` | Delete one foreign-device entry |
| GET | `/api/modbus/ports` | Enumerate serial ports |
| POST | `/api/modbus/connect` \\| `/disconnect` | Open / close a Modbus link |
| GET | `/api/modbus/status` | Connection + simulator state |
| POST | `/api/modbus/scan` | Scan a range of unit IDs |
| POST | `/api/modbus/read` | Read registers or coils |
| POST | `/api/modbus/write` | Write registers or coils |
| POST | `/api/modbus/sim/start` \\| `/stop` \\| `/status` \\| `/registers` | Built-in slave simulator |""")

# ------------------------------------------------------------ interface bullets
sub("""* **Left icon rail** \u2014 Devices, Alarms, Command (audit log), Network.""",
    """* **Left icon rail** \u2014 Devices, Alarms, Command (audit log), Modbus, Network.""")

sub("""* **Status bar** \u2014 transport, port, device count, poll state, last update time.""",
    """* **Status bar** \u2014 transport, port, device count, poll state, last update time.
* **Modbus view** \u2014 connection bar, unit scan, register viewer/writer, slave simulator.
* **Network view** \u2014 transport and MS/TP statistics, plus the BBMD / foreign-device card.""")

io.open(p, "w", encoding="utf-8").write(s)
print("ok")

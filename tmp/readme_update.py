import io

p = "README.md"
s = io.open(p, encoding="utf-8").read()


def sub(old, new, count=1):
    global s
    assert old in s, old[:70]
    s = s.replace(old, new, count)


# ---------------------------------------------------------------- intro
sub("**A lightweight BACnet commissioning console for your laptop \u2014 discover devices, browse points, and write values over BACnet/IP or RS-485 MS/TP.**",
    "**A lightweight BACnet commissioning console for your laptop \u2014 discover devices, browse points, and write values over BACnet/IP or RS-485 MS/TP \u2014 with a full Modbus TCP/RTU master and slave simulator built in.**")

sub("""in pure Python with **no external BACnet library**, and the front end is dependency-free
vanilla JavaScript. No runtime, no database, no installer bloat \u2014 one executable.""",
    """in pure Python with **no external BACnet library**, the Modbus master and simulator are
equally self-contained, and the front end is dependency-free vanilla JavaScript. No runtime,
no database, no installer bloat \u2014 one executable.""")

# ---------------------------------------------------------------- contents
sub("""1. [Install and run](#1-install-and-run)
2. [Building the executable](#2-building-the-executable)
3. [Connecting to a building](#3-connecting-to-a-building)
4. [Writing values and manual override](#4-writing-values-and-manual-override)
5. [MS/TP over RS-485](#5-mstp-over-rs-485)
6. [The interface](#6-the-interface)
7. [Safety](#7-safety)
8. [Architecture](#8-architecture)
9. [Troubleshooting](#9-troubleshooting)""",
    """1. [Install and run](#1-install-and-run)
2. [Building the executable](#2-building-the-executable)
3. [Connecting to a building](#3-connecting-to-a-building)
4. [Writing values and manual override](#4-writing-values-and-manual-override)
5. [MS/TP over RS-485](#5-mstp-over-rs-485)
6. [Modbus master](#6-modbus-master)
7. [BBMD and foreign device](#7-bbmd-and-foreign-device)
8. [The interface](#8-the-interface)
9. [Safety](#9-safety)
10. [Architecture](#10-architecture)
11. [Troubleshooting](#11-troubleshooting)""")

# ---------------------------------------------------------------- renumber
sub("## 6. The interface", "## 8. The interface")
sub("## 7. Safety", "## 9. Safety")
sub("## 8. Architecture", "## 10. Architecture")
sub("## 9. Troubleshooting", "## 11. Troubleshooting")

# ---------------------------------------------------------------- new sections
new_sections = """## 6. Modbus master

The **Modbus** rail button opens a Radzio-style master that talks to PLCs, VFDs, meters,
valve actuators and anything else speaking Modbus TCP or Modbus RTU over RS-485.

### Connect

Pick **Modbus TCP** (host + port, default 502) or **RTU \u00b7 RS-485** (COM port, baud,
parity). The unit ID and timeout live in the same bar. *Connect* opens the link and keeps
it open; every subsequent read and write reuses it, exactly like a bench master. RTU uses
the same USB\u2192RS-485 converter as the MS/TP section \u2014 A\u2192A, B\u2192B, GND\u2192GND, 120 \u03a9 at
both ends.

### What is online

**Unit scan** walks a range of unit IDs (1\u2013247) and asks each one for a single register
or coil. A slave that answers \u2014 even with an exception such as *illegal data address* \u2014
is online; silence means nothing is at that address. TCP scans run in parallel with one
short-lived connection per worker, so a scan of 247 units finishes in seconds without
upsetting a gateway. Results list the unit ID, the round-trip time, and whether the answer
was data or an exception.

### Registers

The viewer reads 1\u2013125 registers or coils at a time from any of the four tables:

| Table | Function | Address range |
|---|---|---|
| Holding registers | 03 | 0\u201365535 (40001\u2013 in Modicon notation) |
| Input registers | 04 | 0\u201365535 (30001\u2013) |
| Coils | 01 | 0\u201365535 (00001\u2013) |
| Discrete inputs | 02 | 0\u201365535 (10001\u2013) |

Each row shows the address, the raw register word(s), hex, and a decoded value. **Format**
changes the decoding without another read: unsigned/signed 16-bit, unsigned/signed 32-bit,
32-bit float, hex, binary, ASCII, and both word orders for 32-bit values. 32-bit formats
consume two registers and the second row is marked as a continuation, so a float spread
across 40001/40002 reads as one number.

### Writing

Write a single register (06), a block of registers (16), a single coil (05), or a block of
coils (15). Values are entered in the same format as the viewer, so typing `21.5` with
*float* selected writes the correct two words. Every write is echoed in the **Command** log
with the target, address, value and result, and Modbus exceptions are surfaced in plain
English (*illegal data value*, *server device failure*, and so on) rather than as a timeout.

### Built-in slave simulator

The **Built-in slave simulator** card starts a real Modbus TCP server on your machine
(default port 5020) serving unit IDs 1, 2, 3. Holding registers 0\u201319 hold `100 + 7n`,
input registers 0\u20139 hold `500 + 3n`, coils alternate on/off, a float `21.5` sits at address
100, and a 32-bit counter `123456` at 110. Point the master at `127.0.0.1:5020` and every
screen can be exercised \u2014 reads, formats, writes, scans \u2014 with no hardware and no risk.
Unknown unit IDs deliberately stay silent, which is what makes the scan meaningful.

---

## 7. BBMD and foreign device

BACnet/IP broadcasts (Who-Is, I-Am, COV notifications) do not cross IP routers. A **BBMD**
(BACnet Broadcast Management Device) on each subnet fixes that: it forwards broadcasts
between subnets. bacpro can register itself with a BBMD as a **foreign device**, which is
how you commission a building from a laptop plugged into a different subnet \u2014 or over a
VPN \u2014 without touching the routers.

Open **Network** and use the **BBMD / foreign device** card:

* **BBMD address** \u2014 `10.0.0.5` or `10.0.0.5:47808`.
* **TTL** \u2014 how long the registration lasts (default 300 s). bacpro renews it
  automatically at roughly two-thirds of the TTL, so a long commissioning session keeps
  working; if the link drops, the registration expires on its own and the BBMD forgets you.
* **Register / Unregister** \u2014 sends *Register-Foreign-Device* (BVLC 0x05) and reports the
  BBMD's result code, or *Delete-Foreign-Device-Table-Entry* when you unregister.
* **Read BDT** \u2014 the BBMD's Broadcast Distribution Table: the peer BBMDs it forwards to.
* **Read FDT** \u2014 the Foreign Device Table: every foreign device currently registered with
  it, with TTL and time remaining. Your own entry appears here, which is a useful proof that
  the registration is live.

Once registered, Who-Is is sent as *Distribute-Broadcast-To-Network* (BVLC 0x09) and the
replies come back wrapped in *Forwarded-NPDU* (BVLC 0x04), so devices on the far side of
the router show up in the station tree like local ones. Everything else \u2014 ReadProperty,
WriteProperty, priority arrays \u2014 is unicast and works unchanged.

The **demo network** acts as a BBMD too: register against `127.0.0.1:47809` and you can
watch the BDT/FDT fill in and a Who-Is travel the whole foreign-device path on one machine.

---

"""
sub("## 8. The interface", new_sections + "## 8. The interface")
io.open(p, "w", encoding="utf-8").write(s)
print("readme sections ok")

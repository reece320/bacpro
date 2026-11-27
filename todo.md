# bacpro — Desktop BACnet Commissioning Tool

## 1. Rebrand to bacpro
- [x] Rename app/package, window title, UI branding, footer
- [x] Keep protocol package name `bacnet` (internal), app shell `bacpro`

## 2. Write support (WriteProperty + priority array)
- [x] Encoding: context-tagged values, null relinquish
- [x] Client: WriteProperty (0x0F), SimpleACK handling, error mapping
- [x] Simulator: 16-slot priority array, commandable objects, PV resolution
- [x] Simulator: read priority-array (87), write log
- [x] Server: POST /api/write, audit log, /api/audit
- [x] UI: Command dialog (value, priority 1-16, relinquish), priority array table
- [x] UI: Manual/override indicator + release-all

## 3. MS/TP over RS-485
- [x] CRC-16 + frame codec (preamble, types, lengths)
- [x] Master node state machine (token pass, poll for master, max info frames)
- [x] MstpTransport: send/recv APDU, background thread
- [x] MstpClient: who_is / read_property / write_property over MS/TP
- [x] Serial port enumeration, baud/MAC/MaxMaster config
- [x] Loopback MS/TP slave simulator for testing
- [x] Server: /api/mstp/ports, /connect, /disconnect, /status
- [x] UI: transport switcher, RS-485 connection panel

## 4. Closer to ComfortPoint Online
- [x] Left icon rail + station tree
- [x] Alarm banner + acknowledge
- [x] Point detail drawer with priority array + command
- [x] Status bar (transport, device count, poll state)
- [x] Commissioning / audit tab

## 5. Desktop packaging
- [x] run.py launcher (local server + app window, single instance)
- [x] PyInstaller spec + build scripts (Windows .bat, Linux .sh)
- [x] requirements.txt, icon
- [x] Build Linux binary and verify it launches

## 6. Test & deliver
- [x] Protocol test (IP): discover, read, write, relinquish
- [x] MS/TP loopback test: discover, read, write
- [x] Screenshot verification
- [x] README rewrite for bacpro

## 7. Site-readiness fixes (BACnet)
- [x] Remove 250-object cap (large devices), report truncation honestly
- [x] BBMD: Register-Foreign-Device, TTL renew, Distribute-Broadcast-To-Network, Forwarded-NPDU
- [x] Simulator: BBMD behaviour so foreign-device path is testable
- [x] Server routes + UI for BBMD registration
- [x] Tests for BBMD + large device

## 8. Modbus master section
- [x] Modbus codec (RTU CRC, TCP MBAP), FC 01-06, 15, 16, exceptions
- [x] Transports: serial RTU, TCP
- [x] Slave/unit ID scan ("what's online")
- [x] Register viewer with formats + write
- [x] Built-in Modbus slave simulator + tests
- [x] Server routes + UI view

## 9. Desktop app polish + review
- [x] App exits when window closes (no orphan background process)
- [x] Log file in windowed mode
- [x] Full review pass: all tests, browser walkthrough of every view
- [x] Rebuild binary, verify; Windows .exe via CI workflow + Inno Setup installer
- [x] Docs updated

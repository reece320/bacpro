import io

p = "BUILD.md"
s = io.open(p, encoding="utf-8").read()


def sub(old, new):
    global s
    assert old in s, old[:80]
    s = s.replace(old, new, 1)


sub("""The workflow (`.github/workflows/build-windows.yml`) also runs both protocol test suites
before building, so a broken build never reaches you.""",
    """The workflow (`.github/workflows/build-windows.yml`) runs all five test suites (135
checks: BACnet/IP, MS/TP, BBMD, Modbus, Modbus API) and a smoke test of the built `.exe`
before uploading, so a broken build never reaches you.""")

sub("""3. Result: `dist\\bacpro.exe`.

The `.exe` is fully self-contained \u2014 it does **not** need Python on the machine where it
runs. Only the build machine needs Python.""",
    """3. Result: `dist\\bacpro.exe`.

The `.exe` is fully self-contained \u2014 it does **not** need Python on the machine where it
runs. Only the build machine needs Python.

### Optional: a real installer

`bacpro.exe` is portable \u2014 copy it to the desktop and double-click. If you would rather
have a normal Windows install with a Start Menu entry, a desktop shortcut and an
uninstaller, use the included Inno Setup script:

```bat
build_windows.bat
iscc installer\\bacpro.iss
:: result: installer\\Output\\bacpro-setup.exe
```

It installs per-user into `%LOCALAPPDATA%\\Programs\\bacpro`, so it needs no administrator
rights \u2014 which is usually the only way to install anything on a work laptop. The GitHub
workflow builds both files and attaches them to the release.""")

sub("""| `run.py` | Entry point: starts the local service, opens the app window |
| `server.py` + `bacnet/` | Flask API and the BACnet/IP + MS/TP protocol stack |""",
    """| `run.py` | Entry point: starts the local service, opens the app window, owns shutdown |
| `server.py` + `bacnet/` | Flask API and the BACnet/IP + MS/TP + BBMD protocol stack |
| `modbus/` | Modbus TCP/RTU master, slave core, and the simulator |""")

sub("""A JSON body containing `\"app\":\"bacpro\"` means the build is good. The CI workflow runs
exactly this check before uploading.""",
    """A JSON body containing `\"app\":\"bacpro\"` means the build is good. The CI workflow runs
exactly this check before uploading.

### Behaviour to check by hand

The automated smoke test proves the service starts. These are the things only a human can
confirm on a real Windows box, and they are worth two minutes before you rely on the tool:

1. **Double-click `bacpro.exe`.** A window opens with no console and no taskbar clutter.
2. **Close the window.** Open Task Manager \u2014 there must be no `bacpro.exe` left running,
   and `netstat -ano | findstr 5001` must return nothing. If a copy is still there, the
   window process was not tracked; start with `--keep-alive` and report it.
3. **Launch it again.** It must come straight back up on the same port.
4. **Check the log** at `%LOCALAPPDATA%\\bacpro\\logs\\bacpro.log` \u2014 it should end with
   `shutting down (window closed)` from the previous run.
5. **Start the demo network, scan, open a point, write a value at priority 8, relinquish.**
6. **Start the Modbus simulator, connect to `127.0.0.1:5020`, read holding registers 0\u201319,
   switch the format to float, write a register.**
7. **Register against the demo BBMD `127.0.0.1:47809`, read the BDT and FDT.**""")

io.open(p, "w", encoding="utf-8").write(s)
print("ok")

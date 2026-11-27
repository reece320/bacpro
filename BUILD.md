# Building bacpro

Three ways to get a runnable bacpro. Pick the one that matches what you have.

---

## Option A — No Python anywhere (recommended for a locked-down work laptop)

GitHub's free Windows runners build the `.exe` for you. Your laptop never sees Python.

1. Create a GitHub repository and push this folder to it:

   ```bash
   git init
   git add .
   git commit -m "bacpro"
   git branch -M main
   git remote add origin https://github.com/<you>/bacpro.git
   git push -u origin main
   ```

2. On GitHub, open the **Actions** tab → **Build bacpro for Windows** → **Run workflow**.

3. Wait about three minutes. Download the **bacpro-windows** artifact, unzip it, and you
   have `bacpro.exe`. Copy it anywhere and double-click.

The workflow (`.github/workflows/build-windows.yml`) runs all five test suites (135
checks: BACnet/IP, MS/TP, BBMD, Modbus, Modbus API) and a smoke test of the built `.exe`
before uploading, so a broken build never reaches you. If you tag a commit
(`git tag v1.0.0 && git push --tags`) the `.exe` is attached to a GitHub Release instead,
which gives you a permanent download link.

**Caveats:** GitHub Actions is free for public repos; private repos get 2,000 free
Windows minutes per month, which is roughly 600 builds. Some corporate networks block
GitHub — if yours does, use Option B on a personal machine and carry the `.exe` across.

---

## Option B — One double-click on a Windows machine with Python

If you have Python 3.10+ on any Windows PC (work, home, a colleague's), this is the
fastest route:

1. Double-click **`build_windows.bat`**.
2. It creates `.venv`, installs dependencies, generates the icon, and runs PyInstaller.
3. Result: `dist\bacpro.exe`.

The `.exe` is fully self-contained — it does **not** need Python on the machine where it
runs. Only the build machine needs Python.

### Optional: a real installer

`bacpro.exe` is portable — copy it to the desktop and double-click. If you would rather
have a normal Windows install with a Start Menu entry, a desktop shortcut and an
uninstaller, use the included Inno Setup script:

```bat
build_windows.bat
iscc installer\bacpro.iss
:: result: installer\Output\bacpro-setup.exe
```

It installs per-user into `%LOCALAPPDATA%\Programs\bacpro`, so it needs no administrator
rights — which is usually the only way to install anything on a work laptop. The GitHub
workflow builds both files and attaches them to the release.

---

## Option C — Run from source, no build at all

For development, or when you just want to use it:

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt
python run.py
```

---

## Why can't the .exe be built on Linux or macOS?

PyInstaller does not cross-compile. A Windows executable must be assembled by a Windows
Python, because the bootloader, the C runtime, and the bundled DLLs are all
platform-specific. There is no flag or tool that changes this — the options are a Windows
machine (Option B) or a Windows runner in the cloud (Option A).

The Linux binary in `dist/bacpro` was built here on Linux and is verified working. It will
not run on Windows, and the Windows `.exe` will not run on Linux.

---

## What ends up in the binary

`bacpro.spec` bundles:

| Item | Purpose |
|---|---|
| `run.py` | Entry point: starts the local service, opens the app window, owns shutdown |
| `server.py` + `bacnet/` | Flask API and the BACnet/IP + MS/TP + BBMD protocol stack |
| `modbus/` | Modbus TCP/RTU master, slave core, and the simulator |
| `static/` | HTML, CSS, JavaScript console (unpacked to a temp dir at runtime) |
| `assets/` | Icon |
| `serial`, `serial.tools.list_ports` | Hidden imports pyserial needs for COM port enumeration |

Excluded to keep it small: `tkinter`, `numpy`, `pandas`, `matplotlib`, `scipy`, `pytest`.
Result is about 17 MB.

---

## Signing and antivirus

The `.exe` is unsigned, so Windows SmartScreen will show *"Windows protected your PC"* on
first run — click **More info → Run anyway**. Some heuristic scanners also flag PyInstaller
one-file binaries as suspicious. Two fixes:

* **Sign it.** If your organisation has a code-signing certificate:

  ```powershell
  signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
      /f mycert.pfx /p <password> dist\bacpro.exe
  ```

* **Use one-folder mode.** In `bacpro.spec`, move `a.binaries`, `a.zipfiles`, and `a.datas`
  out of the `EXE(...)` call and into a `COLLECT(...)` call. This produces a folder instead
  of a single file; it starts faster and trips fewer heuristics, at the cost of being a
  directory rather than one file.

---

## Verifying a build

```bash
# Linux
./dist/bacpro --no-browser --port 5099 &
curl -s http://127.0.0.1:5099/api/status
```

```powershell
# Windows
Start-Process .\dist\bacpro.exe -ArgumentList "--no-browser","--port","5099"
Invoke-WebRequest http://127.0.0.1:5099/api/status -UseBasicParsing
```

A JSON body containing `"app":"bacpro"` means the build is good. The CI workflow runs
exactly this check before uploading.

### Behaviour to check by hand

The automated smoke test proves the service starts. These are the things only a human can
confirm on a real Windows box, and they are worth two minutes before you rely on the tool:

1. **Double-click `bacpro.exe`.** A window opens with no console and no taskbar clutter.
2. **Close the window.** Open Task Manager — there must be no `bacpro.exe` left running,
   and `netstat -ano | findstr 5001` must return nothing. If a copy is still there, the
   window process was not tracked; start with `--keep-alive` and report it.
3. **Launch it again.** It must come straight back up on the same port.
4. **Check the log** at `%LOCALAPPDATA%\bacpro\logs\bacpro.log` — it should end with
   `shutting down (window closed)` from the previous run.
5. **Start the demo network, scan, open a point, write a value at priority 8, relinquish.**
6. **Start the Modbus simulator, connect to `127.0.0.1:5020`, read holding registers 0–19,
   switch the format to float, write a register.**
7. **Register against the demo BBMD `127.0.0.1:47809`, read the BDT and FDT.**

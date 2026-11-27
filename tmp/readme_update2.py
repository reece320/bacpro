import io

p = "README.md"
s = io.open(p, encoding="utf-8").read()


def sub(old, new):
    global s
    assert old in s, old[:70]
    s = s.replace(old, new, 1)


sub("""The first launch unpacks the bundled web UI into a temp folder and starts a private
web service on `127.0.0.1:5001`. Your firewall does not need to allow anything inbound \u2014
the console is only reachable from your own machine.""",
    """The first launch unpacks the bundled web UI into a temp folder and starts a private
web service on `127.0.0.1:5001`. Your firewall does not need to allow anything inbound \u2014
the console is only reachable from your own machine. The window is drawn by the Edge or
Chrome browser that every Windows 10/11 machine already has; nothing is installed and no
Python is required.

**It behaves like a normal desktop app.** Close the window and bacpro exits \u2014 no orphaned
background process, no port left open. Launch it again and it comes straight back. If
something goes wrong there is no console to read, so everything is written to a log:

| Platform | Log file |
|---|---|
| Windows | `%LOCALAPPDATA%\\bacpro\\logs\\bacpro.log` |
| Linux | `~/.local/share/bacpro/logs/bacpro.log` |
| macOS | `~/Library/Application Support/bacpro/logs/bacpro.log` |

The log rotates at 2 MB (three older copies kept), and a fatal startup error is also shown
in a message box pointing at that file.""")

sub("""| `--port 5002` | Use a different local port (handy when 5001 is taken) |
| `--no-browser` | Serve only, don't open a window |
| `--headless` | Same as `--no-browser`, for running on a server |""",
    """| `--port 5002` | Use a different local port (handy when 5001 is taken) |
| `--no-browser` | Serve only, don't open a window |
| `--headless` | Same as `--no-browser`, for running on a server |
| `--keep-alive` | Keep serving after the window is closed (Ctrl+C to stop) |
| `--idle-timeout 180` | Seconds without a live page before exiting, when the window process cannot be tracked |
| `--verbose` | Debug-level logging |""")

sub("""Double-click **`build_windows.bat`**. It creates `.venv`, installs dependencies, generates
the icon, and runs PyInstaller. The result is `dist\\bacpro.exe` \u2014 a single ~17 MB file you
can copy to any Windows machine (no Python needed).""",
    """Double-click **`build_windows.bat`**. It creates `.venv`, installs dependencies, generates
the icon, and runs PyInstaller. The result is `dist\\bacpro.exe` \u2014 a single ~17 MB file you
can copy to any Windows machine (no Python needed).

There is also an **Inno Setup script** at `installer\\bacpro.iss` for a proper installer
with Start Menu and desktop shortcuts and an uninstaller. Build it on Windows with Inno
Setup 6 installed (`iscc installer\\bacpro.iss`), or let the GitHub workflow do both: it
publishes `bacpro.exe` (portable) and `bacpro-setup.exe` (installer).""")

io.open(p, "w", encoding="utf-8").write(s)
print("ok")

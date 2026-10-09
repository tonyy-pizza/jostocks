r"""platform_support.py - the only module that knows which OS this is.

Section 9b of the brief is a hard rule: nothing Windows-specific may leak into
the app code. So every place the two platforms differ lives here, behind four
functions the rest of the project calls without caring:

    install_autostart()    put jostocks in the login path
    uninstall_autostart()  take it out again
    lower_priority()       subprocess kwargs for a background scan
    network_ready()        is there a route to the internet yet

Two further helpers the app needs for the same reason: no_window_kwargs() for
spawning a console program without flashing a window, and python_executable(),
which is always sys.executable. The brief is explicit that app code must never
call the `py` launcher or `pythonw` - those appear only inside the Windows
shortcut the installer writes.

AUTOSTART MECHANISM
-------------------
Windows uses a Startup-folder shortcut, matching how Dionysus already starts
(its README names `pythonw.exe dashboard_widget.py` as the startup shortcut
target; there is no scheduled task and no Run key). The shortcut is created by
install_autostart.ps1 rather than from Python, because:

  - creating a .lnk needs the WScript.Shell COM object, which from Python
    means pywin32, a dependency the project does not otherwise have, and
  - setup_shortcuts.ps1 in the stocks project already does exactly this, with
    the cmd.exe quoting rule worked out and commented. One implementation of
    that, in the language that already holds it, beats two.

There is deliberately no wake trigger. jostocks detects wake inside the app by
watching the wall clock jump between timer ticks - the same approach Dionysus
uses (WAKE_GAP_SECONDS = 90) and the one section 9b specifies for macOS. The
consequence is stated plainly in the README: with the window closed, a wake
does not refresh; the next logon does.

macOS (Phase 4) is a LaunchAgent, and the installer scripts for it are not
part of this phase. The functions below report that rather than pretending.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

#: Hosts to try for a connectivity check, in order. Yahoo first, because that
#: is the one the pipeline actually needs; a machine that can reach the DNS
#: root but not Yahoo cannot run a scan.
PROBE_HOSTS = (("query1.finance.yahoo.com", 443),
               ("finance.yahoo.com", 443),
               ("1.1.1.1", 53))

PROBE_TIMEOUT = 4.0


def platform_name() -> str:
    """"windows", "macos" or the bare sys.platform."""
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    return sys.platform


def python_executable() -> str:
    """The interpreter to run pipeline scripts with.

    Always sys.executable. Under pythonw.exe that is pythonw itself, which is
    what we want for a child that should not own a console either - and
    no_window_kwargs() covers the case where it is python.exe.
    """
    return sys.executable


def no_window_kwargs() -> dict:
    """Keep a spawned console program from flashing a window.

    Windows gives a console program its own console even when the parent has
    none and the output is piped. CREATE_NO_WINDOW suppresses that. Nothing to
    suppress anywhere else.
    """
    if not IS_WINDOWS:
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def lower_priority() -> dict:
    """subprocess kwargs that put the child below normal priority.

    A scan runs for minutes and competes with whatever the person is actually
    doing, so it asks for less than its share. Windows has a priority class
    for exactly this; POSIX has nice(), applied in the child because niceness
    is inherited and we must not renice the app itself.
    """
    if IS_WINDOWS:
        flags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": flags}

    def _nice():
        try:
            os.nice(10)
        except OSError:
            pass

    return {"preexec_fn": _nice}


def network_ready(hosts=PROBE_HOSTS, timeout=PROBE_TIMEOUT) -> bool:
    """Whether a TCP connection to any probe host succeeds right now.

    A wake fires before Wi-Fi is back, so the autorun needs a real answer
    rather than an assumption. A TCP connect is used instead of an HTTP
    request so this costs nothing and cannot be confused by a captive portal
    returning a cheerful 200 for every URL.
    """
    for host, port in hosts:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


# ─── AUTOSTART ───────────────────────────────────────────────────────────

def startup_dir():
    """The per-user Startup folder, or None off Windows."""
    if not IS_WINDOWS:
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup")


def shortcut_path():
    """Where the Startup shortcut lives, or None off Windows."""
    directory = startup_dir()
    return None if directory is None else directory / "jostocks.lnk"


def launch_agent_path():
    """Where the macOS LaunchAgent plist lives, or None elsewhere."""
    if not IS_MACOS:
        return None
    return Path.home() / "Library" / "LaunchAgents" / "com.jostocks.autorun.plist"


def autostart_installed() -> bool:
    """Whether the login hook is in place."""
    if IS_WINDOWS:
        path = shortcut_path()
        return bool(path and path.is_file())
    if IS_MACOS:
        path = launch_agent_path()
        return bool(path and path.is_file())
    return False


def _run_powershell(script: Path, *arguments) -> tuple:
    """Run one of the project's .ps1 installers. Returns (ok, output).

    -ExecutionPolicy Bypass applies to this invocation only and changes no
    machine setting; without it Windows refuses unsigned .ps1 files quietly
    enough to look like a broken script.
    """
    if not script.is_file():
        return False, f"{script.name} not found next to jostocks.py"
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(script), *[str(a) for a in arguments]]
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=120, **no_window_kwargs())
    except Exception as exc:                        # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    output = (done.stdout or "") + (done.stderr or "")
    return done.returncode == 0, output.strip()


def install_autostart() -> tuple:
    """Add jostocks to the login path. Returns (ok, message).

    Idempotent on both platforms: the Windows script overwrites its own
    shortcut, and launchctl is asked to unload before it loads.
    """
    if IS_WINDOWS:
        return _run_powershell(PROJECT_DIR / "install_autostart.ps1")

    if IS_MACOS:
        script = PROJECT_DIR / "install_autostart_mac.sh"
        if not script.is_file():
            return False, ("macOS autostart is Phase 4; install_autostart_mac.sh "
                           "is not in this build yet.")
        try:
            done = subprocess.run(["/bin/sh", str(script)], capture_output=True,
                                  text=True, timeout=120)
        except Exception as exc:                    # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
        return done.returncode == 0, ((done.stdout or "") + (done.stderr or "")).strip()

    return False, f"no autostart mechanism for {platform_name()}"


def uninstall_autostart() -> tuple:
    """Take jostocks out of the login path. Returns (ok, message)."""
    if IS_WINDOWS:
        return _run_powershell(PROJECT_DIR / "uninstall_autostart.ps1")

    if IS_MACOS:
        script = PROJECT_DIR / "uninstall_autostart_mac.sh"
        if not script.is_file():
            return False, ("macOS autostart is Phase 4; uninstall_autostart_mac.sh "
                           "is not in this build yet.")
        try:
            done = subprocess.run(["/bin/sh", str(script)], capture_output=True,
                                  text=True, timeout=120)
        except Exception as exc:                    # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"
        return done.returncode == 0, ((done.stdout or "") + (done.stderr or "")).strip()

    return False, f"no autostart mechanism for {platform_name()}"


def describe_autostart() -> str:
    """One line for the README and the status tooltip."""
    if IS_WINDOWS:
        path = shortcut_path()
        where = str(path) if path else "the Startup folder"
        state = "installed" if autostart_installed() else "not installed"
        return f"Startup-folder shortcut ({state}): {where}"
    if IS_MACOS:
        state = "installed" if autostart_installed() else "not installed"
        return f"LaunchAgent ({state}): {launch_agent_path()}"
    return f"no autostart support on {platform_name()}"


def _self_test() -> int:
    print(f"  platform:        {platform_name()}")
    print(f"  python:          {python_executable()}")
    print(f"  lower_priority:  {sorted(lower_priority())}")
    print(f"  no_window:       {sorted(no_window_kwargs())}")
    print(f"  autostart:       {describe_autostart()}")
    print(f"  network_ready(): {network_ready()}")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())

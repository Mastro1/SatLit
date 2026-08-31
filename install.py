import sys
import os
import subprocess
import platform
from pathlib import Path

MIN_PYTHON = (3, 8)
PROJECT_ROOT = Path(__file__).parent.resolve()
VENV_DIR = PROJECT_ROOT / ".venv"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
APP_NAME = "SatLit"
ICON_ICO = PROJECT_ROOT / "assets" / "favicon.ico"
ICON_PNG = PROJECT_ROOT / "assets" / "favicon.png"

def print_banner():
    banner_path = PROJECT_ROOT / "assets" / "ASCII_art.txt"
    if not banner_path.exists():
        return

    # Enable ANSI escape sequences on Windows if needed
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # STD_OUTPUT_HANDLE = -11
            # 7 = ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            try:
                os.system('')
            except Exception:
                pass

    try:
        lines = banner_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return

    c1 = (255, 50, 50)      # Red
    c2 = (139, 0, 0)        # Dark Red
    c3 = (160, 160, 160)    # Grey

    n = len(lines)
    if n == 0:
        return

    for i, line in enumerate(lines):
        if n > 1:
            if i < n / 2:
                t = i / (n / 2)
                r = int(c1[0] + (c2[0] - c1[0]) * t)
                g = int(c1[1] + (c2[1] - c1[1]) * t)
                b = int(c1[2] + (c2[2] - c1[2]) * t)
            else:
                t = (i - n / 2) / (n - 1 - n / 2) if (n - 1 - n / 2) > 0 else 0
                r = int(c2[0] + (c3[0] - c2[0]) * t)
                g = int(c2[1] + (c3[1] - c2[1]) * t)
                b = int(c2[2] + (c3[2] - c2[2]) * t)
        else:
            r, g, b = c1

        print(f"\033[38;2;{r};{g};{b}m{line}\033[0m")


def check_python_version():
    if sys.version_info < MIN_PYTHON:
        print(f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required. Found {sys.version}")
        sys.exit(1)


def ensure_venv():
    if not VENV_DIR.exists():
        print("Creating virtual environment...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        print("  Done.")
    else:
        print("Virtual environment already exists.")


def get_venv_python():
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def install_dependencies():
    if not REQUIREMENTS.exists():
        print("WARNING: requirements.txt not found, skipping dependency install.")
        return
    print("Syncing dependencies... (might take a while)")
    venv_python = get_venv_python()
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        check=True,
    )
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-r", str(REQUIREMENTS), "--quiet"],
        check=True,
    )
    print("  Done.")


def get_desktop() -> Path:
    if platform.system() == "Windows":
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, check=True,
        )
        return Path(result.stdout.strip())
    return Path.home() / "Desktop"


def ask_create_shortcut() -> bool:
    """Ask user via terminal prompt. Returns True if user says yes, default is No."""
    try:
        answer = input("Create a desktop shortcut? [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except (Exception, KeyboardInterrupt):
        return False


def _do_create_shortcut(shortcut_path: Path, force: bool = False) -> bool:
    """Return True if shortcut should be created."""
    if force:
        return True
    if shortcut_path.exists():
        print("  Shortcut already exists, skipping.")
        return False
    if not sys.stdin.isatty():
        print("  Non-interactive mode, skipping shortcut creation.")
        return False
    return ask_create_shortcut()


def create_shortcut_windows(force: bool = False):
    desktop = get_desktop()
    shortcut_path = desktop / f"{APP_NAME}.lnk"

    if not _do_create_shortcut(shortcut_path, force=force):
        return

    target = PROJECT_ROOT / "run.bat"
    icon = str(ICON_ICO) if ICON_ICO.exists() else ""

    ps_script = f"""
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{shortcut_path}')
$Shortcut.TargetPath = 'cmd.exe'
$Shortcut.Arguments = '/k "{target}"'
$Shortcut.WorkingDirectory = '{PROJECT_ROOT}'
$Shortcut.WindowStyle = 7
"""
    if icon:
        ps_script += f"$Shortcut.IconLocation = '{icon}'\n"
    ps_script += "$Shortcut.Save()"

    subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True)
    print(f"  Desktop shortcut created: {shortcut_path}")


def create_shortcut_mac(force: bool = False):
    desktop = get_desktop()
    shortcut_path = desktop / f"{APP_NAME}.command"

    if not _do_create_shortcut(shortcut_path, force=force):
        return

    venv_python = get_venv_python()
    script_content = f"""#!/bin/bash
cd "{PROJECT_ROOT}"
"{venv_python}" run.py
"""
    shortcut_path.write_text(script_content)
    shortcut_path.chmod(0o755)
    print(f"  Desktop launcher created: {shortcut_path}")
    print("  Note: double-click the .command file to launch SatLit.")


def create_shortcut_linux(force: bool = False):
    desktop = get_desktop()
    shortcut_path = desktop / f"{APP_NAME}.desktop"

    if not _do_create_shortcut(shortcut_path, force=force):
        return

    venv_python = get_venv_python()
    icon_path = str(ICON_PNG) if ICON_PNG.exists() else ""
    desktop_entry = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={APP_NAME}
Exec=bash -c 'cd "{PROJECT_ROOT}" && "{venv_python}" run.py'
Icon={icon_path}
Terminal=false
StartupNotify=false
"""
    shortcut_path.write_text(desktop_entry)
    shortcut_path.chmod(0o755)
    print(f"  Desktop shortcut created: {shortcut_path}")


def create_shortcut(force: bool = False):
    print("Creating desktop shortcut...")
    system = platform.system()
    if system == "Windows":
        create_shortcut_windows(force=force)
    elif system == "Darwin":
        create_shortcut_mac(force=force)
    elif system == "Linux":
        create_shortcut_linux(force=force)
    else:
        print(f"  Unsupported OS '{system}', skipping shortcut creation.")


def main():
    if "--create-shortcut-only" in sys.argv:
        create_shortcut(force=True)
        return

    from_runbat = "--from-runbat" in sys.argv

    print_banner()
    print(f"=== SatLit Installer ===")
    print(f"Project root: {PROJECT_ROOT}\n")

    check_python_version()
    ensure_venv()
    install_dependencies()

    print(f"\nInstallation complete.")
    if not from_runbat:
        print(f"Run the app: run  run.bat  (Windows) / python run.py  (any OS with venv active).")


if __name__ == "__main__":
    main()

"""Operating-system specifics kept in one place so the rest stays portable."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

APP_DIR = Path(__file__).resolve().parent.parent
SETTINGS_FILE = APP_DIR / "settings.json"
EXPORT_DIR = APP_DIR / "exports"
LOG_DIR = APP_DIR / "logs"


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def can_elevate() -> bool:
    return IS_WINDOWS and not is_admin()


def relaunch_elevated() -> bool:
    """Start a new elevated instance. Returns True if a launch was requested."""
    if not IS_WINDOWS:
        return False
    args = " ".join(f'"{a}"' for a in ["-m", "mimir", *sys.argv[1:]])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, str(APP_DIR), 1)
    return rc > 32


ASSET_DIR = APP_DIR / "assets"
ICON_FILE = ASSET_DIR / "mimir.ico"


def style_title_bar(window_title: str, caption_rgb: tuple[int, int, int], text_rgb: tuple[int, int, int],
                    border_rgb: tuple[int, int, int] | None = None) -> bool:
    """Colour the native title bar to match the theme (Windows 11; Windows 10 gets dark mode only)."""
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        hwnd = user32.FindWindowW(None, window_title)
        if not hwnd:
            return False

        def set_attr(attr: int, value: int) -> None:
            v = ctypes.c_int(value)
            dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        def colorref(rgb) -> int:
            r, g, b = rgb
            return r | (g << 8) | (b << 16)

        set_attr(20, 1)                                   # DWMWA_USE_IMMERSIVE_DARK_MODE
        set_attr(35, colorref(caption_rgb))               # DWMWA_CAPTION_COLOR (Win11)
        set_attr(36, colorref(text_rgb))                  # DWMWA_TEXT_COLOR (Win11)
        set_attr(34, colorref(border_rgb or caption_rgb))  # DWMWA_BORDER_COLOR (Win11)
        set_attr(33, 2)                                   # DWMWA_WINDOW_CORNER_PREFERENCE = round
        return True
    except Exception:
        return False


def viewport_handle(window_title: str) -> int:
    """Native handle of our top-level window (0 when unknown / not Windows)."""
    if not IS_WINDOWS:
        return 0
    try:
        return int(ctypes.windll.user32.FindWindowW(None, window_title) or 0)
    except Exception:
        return 0


def window_alive(hwnd: int) -> bool:
    if not IS_WINDOWS or not hwnd:
        return True
    try:
        return bool(ctypes.windll.user32.IsWindow(hwnd))
    except Exception:
        return True


def font_candidates() -> list[tuple[str, str]]:
    """(regular, semibold) font file pairs to try, best first."""
    if IS_WINDOWS:
        d = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        return [(str(d / "segoeui.ttf"), str(d / "seguisb.ttf"))]
    roots = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts"]
    pairs = [("NotoSans-Regular.ttf", "NotoSans-SemiBold.ttf"),
             ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
             ("Ubuntu-R.ttf", "Ubuntu-M.ttf")]
    out = []
    for root in roots:
        if not root.exists():
            continue
        for reg, bold in pairs:
            r = next(root.rglob(reg), None)
            if r:
                b = next(root.rglob(bold), None)
                out.append((str(r), str(b or r)))
    return out


def elevation_hint() -> str:
    if IS_WINDOWS:
        return "Restart as administrator to attribute network traffic to processes."
    return "Run with sudo (or grant CAP_NET_RAW) to attribute network traffic to processes."

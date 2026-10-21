"""Small Windows helpers using ctypes, replacing the keyboard, pyautogui and
pyperclip packages the original scripts used.

- type_text: types Unicode text into whichever window has focus with
  SendInput. Unlike the old Ctrl+V approach it leaves the clipboard alone, and
  it never moves focus.
- GlobalHotkeys: RegisterHotKey on the GUI thread; WM_HOTKEY is picked up by a
  Qt native event filter.
- set_start_with_windows: per-user Run key (no admin rights).

Every function is a safe no-op on other platforms so tests run anywhere.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
WM_HOTKEY = 0x0312
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "LiveCaption"

MODIFIERS = {"ALT": 0x1, "CTRL": 0x2, "CONTROL": 0x2, "SHIFT": 0x4, "WIN": 0x8}
MOD_NOREPEAT = 0x4000


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """'Ctrl+Alt+F9' -> (modifiers, virtual key). None if not understood."""
    parts = [p.strip().upper() for p in text.replace("-", "+").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in MODIFIERS:
            return None
        mods |= MODIFIERS[p]
    key = parts[-1]
    if key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and (key.isalpha() or key.isdigit()):
        vk = ord(key)
    elif key in ("PAUSE", "SCROLLLOCK", "INSERT", "HOME", "END"):
        vk = {"PAUSE": 0x13, "SCROLLLOCK": 0x91, "INSERT": 0x2D, "HOME": 0x24, "END": 0x23}[key]
    else:
        return None
    return mods, vk


# ---------------------------------------------------------------------------
# SendInput
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT


def _utf16_units(text: str) -> list[int]:
    data = text.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def type_text(text: str) -> bool:
    """Type text into the focused window. Returns False if nothing was sent."""
    if not IS_WINDOWS or not text:
        return False
    KEYEVENTF_UNICODE, KEYEVENTF_KEYUP, INPUT_KEYBOARD = 0x4, 0x2, 1
    units = _utf16_units(text)
    events = (INPUT * (len(units) * 2))()
    for i, unit in enumerate(units):
        for j, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
            ev = events[i * 2 + j]
            ev.type = INPUT_KEYBOARD
            ev.u.ki = KEYBDINPUT(0, unit, flags, 0, 0)
    sent = _user32.SendInput(len(events), events, ctypes.sizeof(INPUT))
    if sent != len(events):
        log.warning("SendInput sent %d of %d events (error %d)", sent, len(events),
                    ctypes.get_last_error())
    return sent > 0


# ---------------------------------------------------------------------------
# Global hotkeys
# ---------------------------------------------------------------------------

class GlobalHotkeys:
    """Registers hotkeys for the calling (GUI) thread. Call handle_message()
    from a native event filter with each MSG pointer."""

    def __init__(self):
        self._callbacks: dict[int, object] = {}
        self._next_id = 1

    def register(self, text: str, callback) -> str | None:
        """Returns an error message, or None on success."""
        parsed = parse_hotkey(text)
        if parsed is None:
            return f"'{text}' is not a supported shortcut."
        if not IS_WINDOWS:
            return None
        mods, vk = parsed
        hk_id = self._next_id
        if not ctypes.windll.user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, vk):
            return f"'{text}' is already used by another program."
        self._next_id += 1
        self._callbacks[hk_id] = callback
        return None

    def unregister_all(self) -> None:
        if IS_WINDOWS:
            for hk_id in self._callbacks:
                ctypes.windll.user32.UnregisterHotKey(None, hk_id)
        self._callbacks.clear()

    def handle_message(self, msg_ptr) -> bool:
        if not IS_WINDOWS or not self._callbacks:
            return False
        msg = wintypes.MSG.from_address(int(msg_ptr))
        if msg.message != WM_HOTKEY:
            return False
        cb = self._callbacks.get(int(msg.wParam))
        if cb is None:
            return False
        cb()
        return True


# ---------------------------------------------------------------------------
# Start with Windows
# ---------------------------------------------------------------------------

def launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pythonw}" -m livecaption --minimized'


def set_start_with_windows(enabled: bool) -> None:
    if not IS_WINDOWS:
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass

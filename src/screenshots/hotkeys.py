"""Qt-free helpers for the screenshot capture hotkey (BU110).

``parse_hotkey`` turns a config string such as "Ctrl+Alt+S" into the Win32
``RegisterHotKey`` modifier mask and virtual-key code. ``ClipboardDeduper``
drops the repeated clipboard notifications one snip produces.
"""
import time
from typing import Dict, Optional, Tuple

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIERS = {
    'ctrl': MOD_CONTROL, 'control': MOD_CONTROL,
    'alt': MOD_ALT,
    'shift': MOD_SHIFT,
    'win': MOD_WIN, 'meta': MOD_WIN, 'super': MOD_WIN,
}

_NAMED_KEYS = {
    'space': 0x20,
    'printscreen': 0x2C, 'print': 0x2C, 'prtsc': 0x2C,
    'insert': 0x2D, 'ins': 0x2D,
    'home': 0x24, 'end': 0x23,
    'pageup': 0x21, 'pagedown': 0x22,
}


def _virtual_key(name: str) -> int:
    key = name.strip().lower()
    if len(key) == 1 and ('a' <= key <= 'z' or '0' <= key <= '9'):
        return ord(key.upper())
    if key.startswith('f') and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x70 + int(key[1:]) - 1
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    raise ValueError(f"Unsupported key '{name}'")


def parse_hotkey(text: str) -> Tuple[int, int]:
    """``(modifiers, virtual_key)`` for a hotkey like "Ctrl+Alt+S".

    Exactly one non-modifier key and at least one modifier are required, so
    a bare letter can never be grabbed system-wide. Raises ValueError.
    """
    parts = [p.strip() for p in (text or '').split('+') if p.strip()]
    if not parts:
        raise ValueError('Empty hotkey')
    modifiers, keys = 0, []
    for part in parts:
        mask = _MODIFIERS.get(part.lower())
        if mask:
            modifiers |= mask
        else:
            keys.append(part)
    if len(keys) != 1:
        raise ValueError(f"Hotkey '{text}' needs exactly one non-modifier key")
    if not modifiers:
        raise ValueError(f"Hotkey '{text}' needs at least one modifier (Ctrl, Alt, Shift, Win)")
    return modifiers, _virtual_key(keys[0])


def is_reserved_by_windows(modifiers: int, vk: int) -> bool:
    """Win+Shift+S belongs to the Windows Snipping Tool and can't be taken."""
    return modifiers & ~MOD_NOREPEAT == (MOD_WIN | MOD_SHIFT) and vk == ord('S')


class ClipboardDeduper:
    """Remembers recently imported clipboard images by a content key."""

    def __init__(self, window_seconds: float = 5.0):
        self.window_seconds = window_seconds
        self._seen: Dict[str, float] = {}

    def is_duplicate(self, key: str, now: Optional[float] = None) -> bool:
        """True if ``key`` was seen within the window; records it otherwise."""
        now = time.monotonic() if now is None else now
        self._seen = {k: t for k, t in self._seen.items() if now - t < self.window_seconds}
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

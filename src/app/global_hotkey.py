"""System-wide hotkey for screenshot capture (BU110).

Win32 ``RegisterHotKey`` delivers ``WM_HOTKEY`` to the main window; a native
event filter picks it up and emits ``activated``. No extra dependency. On
other platforms ``register`` simply returns False.
"""
import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

from ..screenshots.hotkeys import MOD_NOREPEAT, is_reserved_by_windows, parse_hotkey

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312


class _HotkeyEventFilter(QAbstractNativeEventFilter):
    def __init__(self, on_hotkey):
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        name = event_type.data() if hasattr(event_type, 'data') else bytes(event_type)
        if name == b'windows_generic_MSG':
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                self._on_hotkey(int(msg.wParam))
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    """One registrable system-wide hotkey bound to a window handle."""

    activated = Signal()

    def __init__(self, hwnd_provider, hotkey_id: int = 0xB110, parent=None):
        super().__init__(parent)
        self._hwnd_provider = hwnd_provider
        self._hotkey_id = hotkey_id
        self._hwnd = None
        self._spec = None
        self._filter = None

    @property
    def registered_spec(self):
        """The hotkey string currently held, or None."""
        return self._spec

    def register(self, spec: str) -> bool:
        """Grab ``spec`` system-wide. Returns False (and logs why) on failure:
        bad spec, reserved by Windows, taken by another app, or not Windows."""
        if self._spec == spec:
            return True
        self.unregister()
        if sys.platform != 'win32':
            return False
        try:
            modifiers, vk = parse_hotkey(spec)
        except ValueError as e:
            logger.warning(f"Invalid screenshot hotkey '{spec}': {e}")
            return False
        if is_reserved_by_windows(modifiers, vk):
            logger.warning(f"'{spec}' is reserved by Windows (Snipping Tool)")
            return False

        import ctypes
        hwnd = int(self._hwnd_provider())
        if not ctypes.windll.user32.RegisterHotKey(hwnd, self._hotkey_id,
                                                   modifiers | MOD_NOREPEAT, vk):
            logger.warning(f"Could not register screenshot hotkey '{spec}' "
                           f"(error {ctypes.GetLastError()}); another app may own it")
            return False

        if self._filter is None:
            self._filter = _HotkeyEventFilter(self._on_native_hotkey)
            QCoreApplication.instance().installNativeEventFilter(self._filter)
        self._hwnd = hwnd
        self._spec = spec
        logger.info(f"Screenshot hotkey registered: {spec}")
        return True

    def unregister(self) -> None:
        if self._spec is None:
            return
        try:
            import ctypes
            ctypes.windll.user32.UnregisterHotKey(self._hwnd, self._hotkey_id)
        except Exception as e:
            logger.warning(f"Could not unregister screenshot hotkey: {e}")
        self._spec = None
        self._hwnd = None

    def _on_native_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == self._hotkey_id and self._spec is not None:
            self.activated.emit()

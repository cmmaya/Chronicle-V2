# Qt-backed classes are loaded lazily so the Qt-free modules in this package
# (metadata, search) can be imported where PySide6 isn't available.


def __getattr__(name):
    if name == 'ScreenshotCapture':
        from .capture import ScreenshotCapture
        return ScreenshotCapture
    if name == 'SnippingOverlay':
        from .snipping import SnippingOverlay
        return SnippingOverlay
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

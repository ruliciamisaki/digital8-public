"""Request native image pixels before Tk creates any Windows windows."""

import ctypes
import os


def enable_native_pixels() -> bool:
    """Disable Windows bitmap stretching for this process, not OS settings.

    https://learn.microsoft.com/en-us/windows/win32/hidpi/setting-the-default-dpi-awareness-for-a-process
    """
    if os.name != "nt":
        return False
    user32 = ctypes.windll.user32
    try:
        request = user32.SetProcessDpiAwarenessContext
        request.argtypes = [ctypes.c_void_p]
        request.restype = ctypes.c_int
        if request(ctypes.c_void_p(-4)):  # Per-monitor V2.
            return True
        context = user32.GetThreadDpiAwarenessContext
        context.restype = ctypes.c_void_p
        awareness = user32.GetAwarenessFromDpiAwarenessContext
        awareness.argtypes = [ctypes.c_void_p]
        if awareness(context()) >= 1:
            return True  # A launcher/manifest already chose an aware context.
    except (AttributeError, OSError):
        pass
    try:
        return bool(user32.SetProcessDPIAware())
    except (AttributeError, OSError):
        return False

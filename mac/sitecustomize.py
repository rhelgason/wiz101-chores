"""Wizard101.app's Wine 8 does not implement kernel32.CopyFile2.

Python 3.13 shutil.copy2 calls it, and Wine aborts the process instead of
returning an error. The crash dialog names Wizard101 because the game is in
the same wineserver. The crashed process is this Python.

Loaded by site.py before the bot or pip runs. Removing CopyFile2 makes
shutil.copy2 use its normal read/write copy.
"""

try:
    import _winapi
except ImportError:
    _winapi = None

if _winapi is not None and hasattr(_winapi, "CopyFile2"):
    try:
        del _winapi.CopyFile2
    except (AttributeError, TypeError):

        def _CopyFile2(src, dst, flags=0, progress_routine=None):
            err = OSError(13, "CopyFile2 is not implemented in this Wine", src)
            err.winerror = getattr(_winapi, "ERROR_ACCESS_DENIED", 5)
            raise err

        _winapi.CopyFile2 = _CopyFile2

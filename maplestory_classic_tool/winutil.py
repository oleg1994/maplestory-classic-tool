"""Small Win32 helpers: process working set and the single-instance mutex."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger("maplestory_classic_tool")

MUTEX_NAME = "Local\\MapleStoryClassicTool"
ERROR_ALREADY_EXISTS = 183
ERROR_ACCESS_DENIED = 5  # the mutex exists but belongs to an elevated copy

# Private WinDLL instances: argtypes set here never leak into other users of ctypes.windll.
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCurrentProcess.argtypes = []
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


_k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                                         wintypes.DWORD]
_k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL


def memory_counters() -> PROCESS_MEMORY_COUNTERS | None:
    pmc = PROCESS_MEMORY_COUNTERS()
    pmc.cb = ctypes.sizeof(pmc)
    if not _k32.K32GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
        return None
    return pmc


def working_set_bytes() -> int:
    pmc = memory_counters()
    return int(pmc.WorkingSetSize) if pmc is not None else 0


def log_memory(when: str) -> None:
    pmc = memory_counters()
    if pmc is None:
        log.info("memory (%s): unavailable", when)
        return
    mb = 1024 * 1024
    log.info("memory (%s): working set %.0f MB, peak %.0f MB, private %.0f MB", when,
             pmc.WorkingSetSize / mb, pmc.PeakWorkingSetSize / mb, pmc.PagefileUsage / mb)


class SingleInstance:
    """Named mutex held for the process lifetime; acquire() is False when another copy holds it."""

    def __init__(self, name: str = MUTEX_NAME):
        self.name = name
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        h = _k32.CreateMutexW(None, False, self.name)
        err = ctypes.get_last_error()
        if not h:
            if err != ERROR_ACCESS_DENIED:
                log.warning("CreateMutexW failed (error %d); not enforcing a single instance", err)
                return True  # never block startup on an unexpected failure
            return False
        if err == ERROR_ALREADY_EXISTS:
            _k32.CloseHandle(h)
            return False
        self._handle = h
        return True

    def release(self) -> None:
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

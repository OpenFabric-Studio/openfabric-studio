"""Checked Win32 Job Object boundary; no breakaway and kill on last close.

The supervisor joins before creating children. The parent retains a query
handle so a launcher exit cannot be mistaken for an empty process tree.
"""
from __future__ import annotations

import ctypes
import re
import sys
import threading
import uuid
from collections.abc import Callable
from typing import Protocol

NAME_PATTERN = r'Local\\OpenFabricNative_[0-9a-f]{32}'
KILL_ON_JOB_CLOSE = 0x2000


class JobApi(Protocol):
    def create(self, name: str) -> int: ...
    def open(self, name: str) -> int: ...
    def set_kill_on_close(self, handle: int) -> None: ...
    def assign_current(self, handle: int) -> None: ...
    def active_processes(self, handle: int) -> int: ...
    def terminate(self, handle: int, code: int) -> None: ...
    def close(self, handle: int) -> None: ...


# Fixed Windows widths are deliberate: c_ulong is 64 bits on some Unix hosts.
class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                ('flags', ctypes.c_uint32), ('minimum_working_set', ctypes.c_size_t),
                ('maximum_working_set', ctypes.c_size_t), ('active_limit', ctypes.c_uint32),
                ('affinity', ctypes.c_size_t), ('priority', ctypes.c_uint32),
                ('scheduling', ctypes.c_uint32)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ('read_operations', 'write_operations', 'other_operations',
                 'read_bytes', 'write_bytes', 'other_bytes')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', _IoCounters),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in ('user_time', 'kernel_time', 'period_user_time', 'period_kernel_time')] + [
                (name, ctypes.c_uint32) for name in ('page_faults', 'total_processes', 'active', 'terminated')]


def checked_handle(value: object) -> int:
    if type(value) is not int or not 0 < value < 1 << (ctypes.sizeof(ctypes.c_void_p) * 8):
        raise OSError('Invalid Windows process handle')
    return value


def checked_success(value: object) -> None:
    if type(value) is not int or value == 0:
        raise OSError('Windows process ownership operation failed')


class CtypesJobApi:
    def __init__(self) -> None:
        if sys.platform != 'win32':
            raise OSError('Windows Job Objects require Windows')
        # WINFUNCTYPE supplies stdcall ABI and explicit C argument/return types.
        # Every native result is narrowed from object before app use.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self._create: Callable[[None, str], object] = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p)(('CreateJobObjectW', kernel))
        self._open: Callable[[int, int, str], object] = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32, ctypes.c_wchar_p, use_last_error=True)(('OpenJobObjectW', kernel))
        self._set: Callable[[int, int, object, int], object] = ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32)(('SetInformationJobObject', kernel))
        self._assign: Callable[[int, int], object] = ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p)(('AssignProcessToJobObject', kernel))
        self._current: Callable[[], object] = ctypes.WINFUNCTYPE(ctypes.c_void_p)(('GetCurrentProcess', kernel))
        self._query: Callable[[int, int, object, int, None], object] = ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)(('QueryInformationJobObject', kernel))
        self._terminate: Callable[[int, int], object] = ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_uint32)(('TerminateJobObject', kernel))
        self._close: Callable[[int], object] = ctypes.WINFUNCTYPE(ctypes.c_int32, ctypes.c_void_p)(('CloseHandle', kernel))

    def create(self, name: str) -> int:
        return checked_handle(self._create(None, name))

    def open(self, name: str) -> int:
        if sys.platform != 'win32':
            raise OSError('Windows Job Objects require Windows')
        # Assign, query and terminate; no inheritable kernel handle is passed.
        value = self._open(0x1 | 0x4 | 0x8, 0, name)
        if value is None or (type(value) is int and value == 0):
            # Absence is different from access denial during crash recovery.
            raise ctypes.WinError(ctypes.get_last_error())
        return checked_handle(value)

    def set_kill_on_close(self, handle: int) -> None:
        information = _ExtendedLimits()
        information.basic.flags = KILL_ON_JOB_CLOSE
        checked_success(self._set(handle, 9, ctypes.byref(information), ctypes.sizeof(information)))

    def assign_current(self, handle: int) -> None:
        current = checked_handle(self._current())
        checked_success(self._assign(handle, current))

    def active_processes(self, handle: int) -> int:
        information = _Accounting()
        checked_success(self._query(handle, 1, ctypes.byref(information), ctypes.sizeof(information), None))
        value: object = information.active
        if type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
            raise OSError('Invalid Windows job accounting')
        return value

    def terminate(self, handle: int, code: int) -> None:
        checked_success(self._terminate(handle, code))

    def close(self, handle: int) -> None:
        checked_success(self._close(handle))


class WindowsJob:
    def __init__(self, name: str, api: JobApi, handle: int) -> None:
        if re.fullmatch(NAME_PATTERN, name) is None:
            raise ValueError('Invalid native job name')
        self.name = name
        self._api = api
        self._handle: int | None = checked_handle(handle)
        self._lock = threading.Lock()

    @classmethod
    def create(cls, api: JobApi | None = None) -> WindowsJob:
        native = api if api is not None else CtypesJobApi()
        name = 'Local\\OpenFabricNative_' + uuid.uuid4().hex
        handle = checked_handle(native.create(name))
        try:
            native.set_kill_on_close(handle)
        except BaseException:
            native.close(handle)
            raise
        return cls(name, native, handle)

    @classmethod
    def open(cls, name: str, api: JobApi | None = None) -> WindowsJob:
        if re.fullmatch(NAME_PATTERN, name) is None:
            raise ValueError('Invalid native job name')
        native = api if api is not None else CtypesJobApi()
        return cls(name, native, checked_handle(native.open(name)))

    def assign_current(self) -> None:
        with self._lock:
            if self._handle is None:
                raise OSError('Native job is closed')
            self._api.assign_current(self._handle)

    def active_processes(self) -> int:
        with self._lock:
            return 0 if self._handle is None else self._api.active_processes(self._handle)

    def terminate(self, code: int = 1) -> None:
        if type(code) is not int or not 0 <= code <= 0xFFFFFFFF:
            raise ValueError('Invalid native exit code')
        with self._lock:
            if self._handle is not None:
                self._api.terminate(self._handle, code)

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._api.close(self._handle)
                self._handle = None

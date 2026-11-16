# -*- coding: utf-8 -*-
"""单实例保护：保证同一时刻只有一个备忘录程序在运行。

原理（纯 ctypes 直调 Win32，零第三方依赖）：

* 首个实例用 kernel32.CreateMutexW 创建一个**命名互斥体**
  ``Global\\PyMemoTool_SingleInstance_Mutex``（不要求 ABANDONED 处理，
  进程退出时操作系统会自动释放）；
* 后续实例 CreateMutexW 会拿到同一个句柄，调用 GetLastError 得到
  ``ERROR_ALREADY_EXISTS (183)``，即判定已有实例在跑；
* 后续实例再用 FindWindowW 按窗口类名 / 标题找到首个实例的 tk 主窗口，
  若窗口处于隐藏（托盘）状态则发 WM_SHOWWINDOW 唤醒，并调用
  SetForegroundWindow 把它带到前台，然后本进程直接退出。

非 Windows 平台 / API 缺失时所有函数安全降级为「允许运行」。
"""
from __future__ import annotations

import ctypes
import sys

IS_WINDOWS = (sys.platform == "win32")

MUTEX_NAME = "Global\\PyMemoTool_SingleInstance_Mutex"
MAIN_WINDOW_CLASS = "TkTopLevel"
MAIN_WINDOW_TITLE = "备忘录工具"

WM_SHOWWINDOW = 0x0018
SW_SHOW = 5
SW_RESTORE = 9
ERROR_ALREADY_EXISTS = 183

HWND_BROADCAST = 0xFFFF

_user32 = None
_kernel32 = None
if IS_WINDOWS:
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _kernel32.CreateMutexW.restype = ctypes.c_void_p
        _kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
        _kernel32.ReleaseMutex.restype = ctypes.c_int
        _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        _kernel32.CloseHandle.restype = ctypes.c_int
        _kernel32.GetLastError.restype = ctypes.c_uint32

        _user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        _user32.FindWindowW.restype = ctypes.c_void_p
        _user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        _user32.PostMessageW.restype = ctypes.c_int
        _user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        _user32.SetForegroundWindow.restype = ctypes.c_int
        _user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        _user32.ShowWindow.restype = ctypes.c_int
        _user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        _user32.IsWindowVisible.restype = ctypes.c_int
    except Exception:  # pragma: no cover - 极端缺 DLL 的环境
        _user32 = None
        _kernel32 = None


class SingleInstanceGuard(object):
    """命名互斥体单实例守卫。

    用法::

        guard = SingleInstanceGuard()
        if not guard.acquire():
            guard.activate_existing_window()   # 唤起已开窗口
            sys.exit(0)                        # 本进程退出
    """

    def __init__(self, mutex_name: str = MUTEX_NAME):
        self._mutex_name = mutex_name
        self._handle = None  # type: None
        self._owned = False

    def acquire(self) -> bool:
        """尝试持有互斥体。

        返回 True 表示本实例是「首个实例」，可继续启动；
        返回 False 表示已有实例在运行，本进程应退出。
        非 Windows / API 不可用时恒返回 True（降级允许运行）。
        """
        if not IS_WINDOWS or _kernel32 is None:
            return True
        try:
            handle = _kernel32.CreateMutexW(None, 0, self._mutex_name)
        except Exception:
            return True
        if not handle:
            # 创建失败（极少见）：宁可允许运行，也不要让程序起不来
            return True
        self._handle = handle
        try:
            already = _kernel32.GetLastError() == ERROR_ALREADY_EXISTS
        except Exception:
            already = False
        self._owned = not already
        return self._owned

    def release(self) -> None:
        """释放并关闭互斥体句柄（正常退出时调用；进程结束也会自动释放）。"""
        if self._handle and _kernel32 is not None:
            try:
                if self._owned:
                    _kernel32.ReleaseMutex(self._handle)
                _kernel32.CloseHandle(self._handle)
            except Exception:
                pass
        self._handle = None
        self._owned = False

    def activate_existing_window(self) -> bool:
        """找到首个实例的主窗口并唤起到前台。

        窗口最小化到托盘（隐藏）时先发 WM_SHOWWINDOW 让它显示，
        再 SetForegroundWindow 抢焦点。返回是否找到了窗口。
        """
        if not IS_WINDOWS or _user32 is None:
            return False
        try:
            hwnd = _user32.FindWindowW(MAIN_WINDOW_CLASS, MAIN_WINDOW_TITLE)
        except Exception:
            return False
        if not hwnd:
            return False
        try:
            if not _user32.IsWindowVisible(hwnd):
                _user32.PostMessageW(hwnd, WM_SHOWWINDOW, SW_SHOW, 0)
                _user32.ShowWindow(hwnd, SW_RESTORE)
            _user32.SetForegroundWindow(hwnd)
            return True
        except Exception:
            return False

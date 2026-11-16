# -*- coding: utf-8 -*-
"""Windows 会话解锁监听（ctypes 直调 WTS API，零第三方依赖）。

通过 WTSRegisterSessionNotification 把隐藏消息窗口注册到当前会话，
窗口过程收到 WM_WTSSESSION_CHANGE 且 wParam == WTS_SESSION_UNLOCK(0x8)
时即判定为「解锁屏幕」。

非 Windows 平台下本模块仍可安全 import：所有 Win32 调用退化为返回 False，
常量与纯逻辑函数（is_unlock_event）保持可用，方便单元测试。
"""
from __future__ import annotations

import ctypes
import sys
from typing import Callable, Optional

IS_WINDOWS = (sys.platform == "win32")

# Win32 常量
WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
WTS_SESSION_LOGON = 0x5
WTS_SESSION_LOGOFF = 0x6
NOTIFY_FOR_THIS_SESSION = 0x00000000
NOTIFY_FOR_ALL_SESSIONS = 0x00000001

_wtsapi32 = None
if IS_WINDOWS:
    try:
        _wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
        _wtsapi32.WTSRegisterSessionNotification.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        _wtsapi32.WTSRegisterSessionNotification.restype = ctypes.c_int  # BOOL
        _wtsapi32.WTSUnRegisterSessionNotification.argtypes = [ctypes.c_void_p]
        _wtsapi32.WTSUnRegisterSessionNotification.restype = ctypes.c_int  # BOOL
    except Exception:  # pragma: no cover - 仅在缺 DLL 的异常系统出现
        _wtsapi32 = None


def register_session_notification(hwnd: int) -> bool:
    """把窗口注册为接收本会话的会话变更通知。"""
    if not IS_WINDOWS or _wtsapi32 is None or not hwnd:
        return False
    try:
        result = _wtsapi32.WTSRegisterSessionNotification(
            ctypes.c_void_p(int(hwnd)), NOTIFY_FOR_THIS_SESSION
        )
        return bool(result)
    except Exception:
        return False


def unregister_session_notification(hwnd: int) -> bool:
    """注销会话变更通知。"""
    if not IS_WINDOWS or _wtsapi32 is None or not hwnd:
        return False
    try:
        result = _wtsapi32.WTSUnRegisterSessionNotification(ctypes.c_void_p(int(hwnd)))
        return bool(result)
    except Exception:
        return False


def is_unlock_event(msg: int, wparam: int) -> bool:
    """是否为「解锁屏幕」事件（纯逻辑，可跨平台测试）。"""
    return int(msg) == WM_WTSSESSION_CHANGE and int(wparam) == WTS_SESSION_UNLOCK


def is_lock_event(msg: int, wparam: int) -> bool:
    """是否为「锁屏」事件。"""
    return int(msg) == WM_WTSSESSION_CHANGE and int(wparam) == WTS_SESSION_LOCK


class SessionWatcher(object):
    """把 WTS 注册/注销与消息判定封装成一个可复用对象。"""

    def __init__(
        self,
        on_unlock: Optional[Callable[[], None]] = None,
        on_lock: Optional[Callable[[], None]] = None,
    ):
        self.on_unlock = on_unlock
        self.on_lock = on_lock
        self.hwnd = 0
        self.registered = False

    def register(self, hwnd: int) -> bool:
        """注册指定窗口，返回是否成功。"""
        self.hwnd = int(hwnd or 0)
        self.registered = register_session_notification(self.hwnd)
        return self.registered

    def unregister(self) -> bool:
        """注销通知。"""
        if self.registered and self.hwnd:
            unregister_session_notification(self.hwnd)
        self.registered = False
        return True

    def handle_message(self, msg: int, wparam: int) -> bool:
        """处理窗口消息；命中锁屏/解锁事件时触发回调并返回 True。"""
        if is_unlock_event(msg, wparam):
            if self.on_unlock:
                self.on_unlock()
            return True
        if is_lock_event(msg, wparam):
            if self.on_lock:
                self.on_lock()
            return True
        return False

# -*- coding: utf-8 -*-
"""Windows 系统托盘（ctypes 直调 Win32 API，零第三方依赖）。

实现要点：
  * RegisterClassExW + CreateWindowExW 创建一个**不显示**的隐藏消息窗口；
  * Shell_NotifyIconW(shell32.dll) 添加托盘图标，NIF_INFO 弹出气泡提醒；
  * CreatePopupMenu / TrackPopupMenu 提供右键菜单（显示 / 新建 / 退出）；
  * 同一隐藏窗口通过 session_watcher 注册 WTS 会话通知，接收解锁事件；
  * 消息循环跑在独立守护线程；所有回调经 dispatcher 投递回 UI 主线程，
    避免在托盘线程直接操作 tkinter；
  * 主线程通过 PostMessageW 发送自定义消息来请求气泡 / 退出，
    保证 Shell_NotifyIcon 始终在创建图标的那个线程里被调用。

非 Windows 平台下本模块可安全 import：start() 返回 False，程序降级为普通窗口模式。
"""
from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import uuid
from typing import Any, Callable, Optional, Tuple

import session_watcher
import hotkey as hotkey_mod

IS_WINDOWS = (sys.platform == "win32")

# ---------------------------------------------------------------------------
# Win32 常量
# ---------------------------------------------------------------------------
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1          # uCallbackMessage
WM_APP_NOTIFY = WM_APP + 2        # 主线程请求弹气泡
WM_APP_QUIT = WM_APP + 3          # 主线程请求退出托盘
WM_APP_HOTKEY = WM_APP + 4        # 主线程请求（重）注册全局热键
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_NONE = 0x00000000
NIIF_INFO = 0x00000001

IDI_APPLICATION = 32512
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
WS_OVERLAPPED = 0x00000000

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_LEFTALIGN = 0x00000000
TPM_RIGHTBUTTON = 0x00000002
TPM_RETURNCMD = 0x00000100
TPM_NONOTIFY = 0x00000080

ID_SHOW = 1001
ID_NEW = 1002
ID_EXIT = 1003

# ---------------------------------------------------------------------------
# 平台无关的 ctypes 基础类型（不使用 ctypes.wintypes，保证非 Windows 可 import）
# ---------------------------------------------------------------------------
HWND = ctypes.c_void_p
HICON = ctypes.c_void_p
HMENU = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
HBRUSH = ctypes.c_void_p
HCURSOR = ctypes.c_void_p
DWORD = ctypes.c_uint32
UINT = ctypes.c_uint32
UINT_PTR = ctypes.c_size_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
LPCWSTR = ctypes.c_wchar_p
WORD = ctypes.c_uint16
LONG = ctypes.c_int32
BOOL = ctypes.c_int


class WNDCLASSEXW(ctypes.Structure):
    """Win32 WNDCLASSEXW。lpfnWndProc 用 c_void_p 保存回调指针。"""

    _fields_ = [
        ("cbSize", UINT),
        ("style", UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", LPCWSTR),
        ("lpszClassName", LPCWSTR),
        ("hIconSm", HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    """NOTIFYICONDATAW（经典 V3 布局，兼容 Win2000 之后所有 Windows）。"""

    _fields_ = [
        ("cbSize", DWORD),
        ("hWnd", HWND),
        ("uID", UINT),
        ("uFlags", UINT),
        ("uCallbackMessage", UINT),
        ("hIcon", HICON),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", DWORD),
        ("dwStateMask", DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", DWORD),
    ]


class POINT(ctypes.Structure):
    """Win32 POINT。"""

    _fields_ = [("x", LONG), ("y", LONG)]


class MSG(ctypes.Structure):
    """Win32 MSG。"""

    _fields_ = [
        ("hwnd", HWND),
        ("message", UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", DWORD),
        ("pt", POINT),
        ("lPrivate", DWORD),
    ]


# WINFUNCTYPE 只在 Windows 上存在，必须条件化
WNDPROC = (
    ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM) if IS_WINDOWS else None
)

# ---------------------------------------------------------------------------
# DLL 与函数签名绑定（64 位 Python 下必须显式声明 argtypes / restype）
# ---------------------------------------------------------------------------
_user32 = None
_kernel32 = None
_shell32 = None
if IS_WINDOWS:
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _shell32 = ctypes.WinDLL("shell32", use_last_error=True)

        _kernel32.GetModuleHandleW.argtypes = [LPCWSTR]
        _kernel32.GetModuleHandleW.restype = HINSTANCE

        _user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        _user32.RegisterClassExW.restype = WORD  # ATOM
        _user32.UnregisterClassW.argtypes = [LPCWSTR, HINSTANCE]
        _user32.UnregisterClassW.restype = BOOL
        _user32.CreateWindowExW.argtypes = [
            DWORD, LPCWSTR, LPCWSTR, DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            HWND, HMENU, HINSTANCE, ctypes.c_void_p,
        ]
        _user32.CreateWindowExW.restype = HWND
        _user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        _user32.DefWindowProcW.restype = LRESULT
        _user32.DestroyWindow.argtypes = [HWND]
        _user32.DestroyWindow.restype = BOOL
        _user32.LoadIconW.argtypes = [HINSTANCE, ctypes.c_void_p]
        _user32.LoadIconW.restype = HICON

        _user32.CreatePopupMenu.argtypes = []
        _user32.CreatePopupMenu.restype = HMENU
        _user32.AppendMenuW.argtypes = [HMENU, UINT, UINT_PTR, LPCWSTR]
        _user32.AppendMenuW.restype = BOOL
        _user32.DestroyMenu.argtypes = [HMENU]
        _user32.DestroyMenu.restype = BOOL
        _user32.TrackPopupMenu.argtypes = [
            HMENU, UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, HWND, ctypes.c_void_p
        ]
        _user32.TrackPopupMenu.restype = ctypes.c_int
        _user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
        _user32.GetCursorPos.restype = BOOL
        _user32.SetForegroundWindow.argtypes = [HWND]
        _user32.SetForegroundWindow.restype = BOOL

        _user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        _user32.PostMessageW.restype = BOOL
        _user32.PostQuitMessage.argtypes = [ctypes.c_int]
        _user32.PostQuitMessage.restype = None
        _user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT]
        _user32.GetMessageW.restype = ctypes.c_int
        _user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        _user32.TranslateMessage.restype = BOOL
        _user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        _user32.DispatchMessageW.restype = LRESULT

        _shell32.Shell_NotifyIconW.argtypes = [DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        _shell32.Shell_NotifyIconW.restype = BOOL
    except Exception:  # pragma: no cover - 极端缺 DLL 的环境
        _user32 = None
        _kernel32 = None
        _shell32 = None


# ---------------------------------------------------------------------------
# 托盘实现
# ---------------------------------------------------------------------------
class TrayIcon(object):
    """系统托盘图标 + 气泡提醒 + 右键菜单 + 会话解锁转发。

    回调(on_show / on_new / on_exit / on_unlock)都是无参函数。
    dispatcher 形如 ``dispatcher(fn, args_tuple)``，用于把回调投递到 UI 线程；
    若不传则直接调用（仅供测试使用）。
    """

    def __init__(
        self,
        on_show: Optional[Callable[[], None]] = None,
        on_new: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        on_unlock: Optional[Callable[[], None]] = None,
        on_hotkey: Optional[Callable[[], None]] = None,
        tooltip: str = "备忘录工具",
        dispatcher: Optional[Callable[[Callable[..., Any], Tuple[Any, ...]], None]] = None,
        icon_id: int = 1,
    ):
        self._on_show = on_show
        self._on_new = on_new
        self._on_exit = on_exit
        self._on_unlock = on_unlock
        self._on_hotkey = on_hotkey
        self._dispatcher = dispatcher
        self._tooltip = tooltip or "备忘录工具"
        self._icon_id = int(icon_id)
        self._hotkey = hotkey_mod.GlobalHotkey()
        self._hotkey_request = (0, 0)  # 待注册的 (modifiers, vk)

        self._pending = queue.Queue()  # type: queue.Queue
        self._ready = threading.Event()
        self._thread = None  # type: Optional[threading.Thread]
        self._running = False
        self._icon_added = False
        self._hwnd = 0
        self._hinstance = 0
        self._hicon = 0
        self._class_name = ""
        self._registered_class = False
        self._wnd_proc = None  # 必须长期持有引用，防止回调被 GC

        self._watcher = session_watcher.SessionWatcher(
            on_unlock=lambda: self._fire(self._on_unlock),
        )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def is_available(self) -> bool:
        """当前平台/环境是否具备托盘能力。"""
        return bool(IS_WINDOWS and _user32 is not None and _shell32 is not None)

    def is_running(self) -> bool:
        """消息循环是否正在运行。"""
        return self._running

    def start(self) -> bool:
        """启动托盘守护线程；非 Windows 或 API 不可用时返回 False。"""
        if not self.is_available():
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="MemoTrayThread", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5.0)
        return self._running

    def notify(self, title: str, message: str) -> None:
        """请求弹出托盘气泡（线程安全，可从 UI 主线程调用）。"""
        self._pending.put((title or "", message or ""))
        if self._hwnd and IS_WINDOWS and _user32 is not None:
            try:
                _user32.PostMessageW(ctypes.c_void_p(self._hwnd), WM_APP_NOTIFY, 0, 0)
            except Exception:
                pass

    def stop(self) -> None:
        """请求托盘线程清理图标并退出消息循环（线程安全）。"""
        if self._hwnd and IS_WINDOWS and _user32 is not None:
            try:
                _user32.PostMessageW(ctypes.c_void_p(self._hwnd), WM_APP_QUIT, 0, 0)
            except Exception:
                pass

    def set_hotkey(self, hotkey_text: str) -> bool:
        """请求（重）注册全局热键（线程安全；注册发生在托盘线程）。

        hotkey_text 形如 "F2" / "Ctrl+Alt+M"；非法文本返回 False 且不改动
        现有注册。返回 True 表示请求已提交（是否真正注册成功看 is_hotkey_active）。
        """
        parsed = hotkey_mod.parse_hotkey(hotkey_text)
        if parsed is None:
            return False
        self._hotkey_request = parsed
        if self._hwnd and IS_WINDOWS and _user32 is not None:
            try:
                _user32.PostMessageW(
                    ctypes.c_void_p(self._hwnd), WM_APP_HOTKEY, 0, 0)
                return True
            except Exception:
                return False
        return False

    def is_hotkey_active(self) -> bool:
        """全局热键当前是否已成功注册。"""
        return self._hotkey.is_registered

    def unregister_hotkey(self) -> None:
        """注销全局热键（退出程序时调用）。"""
        try:
            self._hotkey.unregister()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 内部：回调派发
    # ------------------------------------------------------------------
    def _fire(self, callback: Optional[Callable[..., Any]], *args: Any) -> None:
        """触发回调；有 dispatcher 时投递到 UI 线程，否则直接调用。"""
        if callback is None:
            return
        if self._dispatcher is not None:
            try:
                self._dispatcher(callback, tuple(args))
                return
            except Exception:
                pass
        try:
            callback(*args)
        except Exception:
            pass

    def _handle_command(self, command_id: int) -> None:
        """处理右键菜单命令。"""
        if command_id == ID_SHOW:
            self._fire(self._on_show)
        elif command_id == ID_NEW:
            self._fire(self._on_new)
        elif command_id == ID_EXIT:
            self._fire(self._on_exit)

    # ------------------------------------------------------------------
    # 内部：图标与气泡（必须运行在托盘线程）
    # ------------------------------------------------------------------
    def _base_nid(self) -> NOTIFYICONDATAW:
        """构造填充了公共字段的 NOTIFYICONDATAW。"""
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = ctypes.c_void_p(self._hwnd)
        nid.uID = self._icon_id
        return nid

    def _add_icon(self) -> bool:
        """NIM_ADD 添加托盘图标。"""
        if _shell32 is None:
            return False
        nid = self._base_nid()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAYICON
        nid.hIcon = ctypes.c_void_p(self._hicon or 0)
        nid.szTip = self._tooltip[:127]
        return bool(_shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _remove_icon(self) -> None:
        """NIM_DELETE 移除托盘图标。"""
        if _shell32 is None or not self._hwnd:
            return
        nid = self._base_nid()
        _shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def _show_balloon(self, title: str, message: str) -> None:
        """NIF_INFO 气泡提醒。"""
        if _shell32 is None or not self._hwnd:
            return
        nid = self._base_nid()
        nid.uFlags = NIF_INFO
        nid.dwInfoFlags = NIIF_INFO
        nid.szInfoTitle = (title or "备忘录工具")[:63]
        nid.szInfo = (message or "")[:255]
        _shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _drain_notifies(self) -> None:
        """把主线程投递的气泡请求全部弹出。"""
        while True:
            try:
                title, message = self._pending.get_nowait()
            except queue.Empty:
                break
            self._show_balloon(str(title), str(message))

    def _show_context_menu(self, hwnd: int) -> None:
        """弹出右键菜单（在托盘线程内同步执行）。"""
        if _user32 is None:
            return
        menu = _user32.CreatePopupMenu()
        if not menu:
            return
        try:
            _user32.AppendMenuW(menu, MF_STRING, ID_SHOW, "显示主窗口")
            _user32.AppendMenuW(menu, MF_STRING, ID_NEW, "新建事务")
            _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            _user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "退出")
            point = POINT()
            _user32.GetCursorPos(ctypes.byref(point))
            _user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
            flags = TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_NONOTIFY | TPM_LEFTALIGN
            command = _user32.TrackPopupMenu(
                menu, flags, point.x, point.y, 0, ctypes.c_void_p(hwnd), None
            )
            # MSDN 建议：TrackPopupMenu 返回后投递一条空消息，避免菜单卡住
            _user32.PostMessageW(ctypes.c_void_p(hwnd), WM_NULL, 0, 0)
            if command:
                self._handle_command(int(command))
        finally:
            _user32.DestroyMenu(menu)

    # ------------------------------------------------------------------
    # 内部：窗口过程与消息循环
    # ------------------------------------------------------------------
    def _make_wnd_proc(self):
        """创建 WNDPROC 回调（闭包里捕获 self）。"""

        def _proc(hwnd, msg, wparam, lparam):
            try:
                return self._handle_message(
                    int(hwnd or 0), int(msg), int(wparam), int(lparam)
                )
            except Exception:
                return LRESULT(0).value

        return WNDPROC(_proc)

    def _handle_message(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        """隐藏窗口消息分发。"""
        if msg == WM_TRAYICON:
            event = int(lparam) & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._fire(self._on_show)
            elif event == WM_RBUTTONUP:
                self._show_context_menu(hwnd)
            return 0

        if msg == WM_COMMAND:
            self._handle_command(int(wparam) & 0xFFFF)
            return 0

        if msg == WM_APP_NOTIFY:
            self._drain_notifies()
            return 0

        if msg == WM_APP_HOTKEY:
            # 在托盘线程里执行注册（RegisterHotKey 绑定线程的消息循环）
            modifiers, vk = self._hotkey_request
            if vk:
                if not self._hotkey.register(hwnd, modifiers, vk):
                    # 注册失败（常见：1409 快捷键已被其他程序占用），气泡提醒
                    name = hotkey_mod.vk_display_name(int(vk))
                    self._pending.put((
                        "快捷键注册失败",
                        "快捷键 %s 可能已被其他程序占用，请在「设置 → 快捷键」里换一个按键。"
                        % name,
                    ))
                    if _user32 is not None:
                        try:
                            _user32.PostMessageW(
                                ctypes.c_void_p(hwnd), WM_APP_NOTIFY, 0, 0)
                        except Exception:
                            pass
            return 0

        if msg == hotkey_mod.WM_HOTKEY:
            # 用户按下全局快捷键 → 切换主窗口显示 / 最小化
            if int(wparam) == self._hotkey._id:
                self._fire(self._on_hotkey)
            return 0

        if msg == WM_APP_QUIT:
            self._remove_icon()
            self._hotkey.unregister()
            self._watcher.unregister()
            if _user32 is not None:
                _user32.DestroyWindow(ctypes.c_void_p(hwnd))
            return 0

        if msg == WM_DESTROY:
            if _user32 is not None:
                _user32.PostQuitMessage(0)
            return 0

        if self._watcher.handle_message(msg, wparam):
            return 0

        if _user32 is not None:
            return int(
                _user32.DefWindowProcW(
                    ctypes.c_void_p(hwnd), UINT(msg), WPARAM(wparam), LPARAM(lparam)
                )
            )
        return 0

    def _unregister_class(self) -> None:
        """注销窗口类。"""
        if self._registered_class and self._class_name and _user32 is not None:
            try:
                _user32.UnregisterClassW(self._class_name, ctypes.c_void_p(self._hinstance or 0))
            except Exception:
                pass
        self._registered_class = False

    def _load_app_icon(self) -> int:
        """加载应用图标 HICON：优先 exe 内嵌图标（打包时 --icon 注入），
        其次源码目录的 assets/memotool.ico；都失败返回 0（回退系统默认图标）。
        """
        if _user32 is None:
            return 0
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x10
        candidates = []
        # PyInstaller --onefile：sys.executable 就是 exe 本身，图标已内嵌
        exe_path = getattr(sys, "executable", "") or ""
        if exe_path.lower().endswith(".exe"):
            candidates.append(exe_path)
        # 源码运行：直接读 ico 文件
        try:
            ico_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "assets", "memotool.ico")
            candidates.append(ico_path)
        except Exception:
            pass
        for path in candidates:
            if not path or not os.path.isfile(path):
                continue
            try:
                hicon = _user32.LoadImageW(
                    None, ctypes.c_wchar_p(path), IMAGE_ICON, 0, 0,
                    LR_LOADFROMFILE)
                if hicon:
                    return int(hicon)
            except Exception:
                continue
        return 0

    def _run(self) -> None:
        """托盘线程主体：建窗 → 加图标 → 注册会话通知 → GetMessage 循环。"""
        if _user32 is None or _kernel32 is None or _shell32 is None or WNDPROC is None:
            self._ready.set()
            return

        self._class_name = "PyMemoTrayWnd_" + uuid.uuid4().hex[:10]
        try:
            self._hinstance = _kernel32.GetModuleHandleW(None) or 0
            self._hicon = self._load_app_icon() or _user32.LoadIconW(
                None, ctypes.c_void_p(IDI_APPLICATION)) or 0
            self._wnd_proc = self._make_wnd_proc()

            wnd_class = WNDCLASSEXW()
            wnd_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wnd_class.style = CS_HREDRAW | CS_VREDRAW
            wnd_class.lpfnWndProc = ctypes.cast(self._wnd_proc, ctypes.c_void_p)
            wnd_class.cbClsExtra = 0
            wnd_class.cbWndExtra = 0
            wnd_class.hInstance = ctypes.c_void_p(self._hinstance or 0)
            wnd_class.hIcon = None
            wnd_class.hCursor = None
            wnd_class.hbrBackground = None
            wnd_class.lpszMenuName = None
            wnd_class.lpszClassName = self._class_name
            wnd_class.hIconSm = None

            if not _user32.RegisterClassExW(ctypes.byref(wnd_class)):
                self._ready.set()
                return
            self._registered_class = True

            hwnd = _user32.CreateWindowExW(
                0,
                self._class_name,
                "PyMemoTrayHiddenWindow",
                WS_OVERLAPPED,
                0, 0, 0, 0,
                None, None,
                ctypes.c_void_p(self._hinstance or 0),
                None,
            )
            if not hwnd:
                self._remove_icon()
                self._unregister_class()
                self._ready.set()
                return
            self._hwnd = int(hwnd)

            self._icon_added = self._add_icon()
            self._watcher.register(self._hwnd)
            self._running = True
            self._ready.set()
            self._drain_notifies()  # 处理启动前排队的气泡请求
            # 处理启动前就提交的全局热键注册请求（失败会弹气泡）
            modifiers, vk = self._hotkey_request
            if vk:
                if not self._hotkey.register(hwnd, modifiers, vk):
                    name = hotkey_mod.vk_display_name(int(vk))
                    self._pending.put((
                        "快捷键注册失败",
                        "快捷键 %s 可能已被其他程序占用，请在「设置 → 快捷键」里换一个按键。"
                        % name,
                    ))
                    self._drain_notifies()

            message = MSG()
            while True:
                result = _user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:  # 0 = WM_QUIT, -1 = 出错
                    break
                _user32.TranslateMessage(ctypes.byref(message))
                _user32.DispatchMessageW(ctypes.byref(message))
        except Exception:
            pass
        finally:
            self._watcher.unregister()
            self._hotkey.unregister()
            self._remove_icon()
            self._unregister_class()
            self._running = False
            self._icon_added = False
            self._hwnd = 0
            self._ready.set()

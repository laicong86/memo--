# -*- coding: utf-8 -*-
"""全局快捷键：解析 / 校验用户输入，并在 Windows 上注册系统级热键。

功能：按下配置的快捷键（默认 F2）时，弹出已运行的备忘录窗口；
若窗口已在前台则最小化回托盘。快捷键文本可在「设置 → 快捷键」里修改，
例如：F2、Ctrl+Alt+M、Ctrl+Shift+F9。

热键注册跑在托盘线程的消息窗口上（RegisterHotKey + WM_HOTKEY），
因此托盘不可用（非 Windows）时快捷键自动失效，但不影响其他功能。

解析规则（parse_hotkey）：
  * 修饰键：Ctrl/Control、Alt、Shift、Win/Windows，顺序不限，不区分大小写；
  * 主键：单个字符（字母/数字）、F1-F24、或常见键名（Home/End/...）；
  * 至少要有一个主键；返回 (modifiers, vk_code) 或 None。
"""
from __future__ import annotations

import ctypes
import sys
from typing import Optional, Tuple

IS_WINDOWS = (sys.platform == "win32")

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

DEFAULT_HOTKEY = "F2"
DEFAULT_NEW_HOTKEY = "Ctrl+Alt+N"  # 全局快捷键：快速新建事务

# 修饰键名 → MOD 位
_MODIFIER_MAP = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
}

# 主键名 → VK 虚拟键码（不含 F1-F24，那些单独算）
_SPECIAL_VK = {
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "insert": 0x2D, "delete": 0x2E, "ins": 0x2D, "del": 0x2E,
    "backspace": 0x08, "space": 0x20, "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B, "enter": 0x0D, "return": 0x0D,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "pause": 0x13, "scrolllock": 0x91, "numlock": 0x90,
}

# 设置界面里允许直接下拉选择的常用预设
HOTKEY_PRESETS = ("F2", "F4", "F9", "Ctrl+Alt+M", "Ctrl+Shift+Space")
# 「新建事务」快捷键的常用预设（避免与呼出键/截屏工具冲突）
NEW_HOTKEY_PRESETS = ("Ctrl+Alt+N", "Ctrl+Alt+T", "Ctrl+Shift+N", "F9")

# 两个全局热键的注册 ID（必须互不相同，RegisterHotKey 以 ID 区分回调）
HOTKEY_ID_SHOW = 0xB00B      # 呼出 / 最小化主窗口
HOTKEY_ID_NEW = 0xB00C       # 快速新建事务


def normalize_hotkey_text(text: str) -> str:
    """把用户输入规整成统一的「Ctrl+Alt+F2」样式显示文本。

    规整顺序固定为 Ctrl → Alt → Shift → Win → 主键；主键统一大写。
    无法解析时返回空串（供设置界面校验提示）。
    """
    parsed = parse_hotkey(text)
    if parsed is None:
        return ""
    modifiers, vk = parsed
    parts = []
    if modifiers & MOD_CONTROL:
        parts.append("Ctrl")
    if modifiers & MOD_ALT:
        parts.append("Alt")
    if modifiers & MOD_SHIFT:
        parts.append("Shift")
    if modifiers & MOD_WIN:
        parts.append("Win")
    parts.append(vk_display_name(vk))
    return "+".join(parts)


def parse_hotkey(text: str) -> Optional[Tuple[int, int]]:
    """解析快捷键文本 → (modifiers, vk_code)；非法输入返回 None。

    支持任意顺序、任意大小写的修饰键组合，如 "alt+ctrl+m"、"WIN + F2"。
    """
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    modifiers = 0
    vk = 0
    seen_key = False
    for token in raw.replace("＋", "+").split("+"):
        part = token.strip().lower()
        if not part:
            return None
        if part in _MODIFIER_MAP:
            if seen_key:
                return None  # 主键出现在修饰键之后：不允许
            modifiers |= _MODIFIER_MAP[part]
            continue
        code = _key_vk(part)
        if code is None:
            return None
        if seen_key:
            return None  # 出现第二个主键
        vk = code
        seen_key = True
    if not seen_key or vk == 0:
        return None
    return (modifiers, vk)


def _key_vk(part: str) -> Optional[int]:
    """主键名 → VK 码；单字符按键取大写 ASCII，功能键 F1-F24 特判。"""
    if len(part) == 1:
        code = ord(part.upper())
        if 0x30 <= code <= 0x5A:  # 0-9 与 A-Z
            return code
        return None
    if part.startswith("f") and part[1:].isdigit():
        number = int(part[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1  # VK_F1 = 0x70
        return None
    return _SPECIAL_VK.get(part)


def vk_display_name(vk: int) -> str:
    """VK 码 → 展示用键名（与 parse_hotkey 互逆）。"""
    if 0x70 <= vk <= 0x87:
        return "F%d" % (vk - 0x70 + 1)
    for name, code in _SPECIAL_VK.items():
        if code == vk:
            return name.capitalize()
    if 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if vk == 0x20:
        return "Space"
    return str(vk)


def is_valid_hotkey_text(text: str) -> bool:
    """设置界面校验：能否解析为合法快捷键。"""
    return parse_hotkey(text) is not None


class GlobalHotkey(object):
    """Windows 全局热键句柄封装。

    热键绑定到指定 hwnd（通常是托盘线程的隐藏消息窗口），
    WM_HOTKEY 到达后由托盘消息循环转交回调。
    """

    def __init__(self, key_id: int = HOTKEY_ID_SHOW):
        self._registered = False
        self._id = key_id  # 注册 ID，用于区分同一消息窗口上的多个热键
        self._modifiers = 0
        self._vk = 0
        self._hwnd = 0
        if IS_WINDOWS:
            try:
                self._user32 = ctypes.WinDLL("user32", use_last_error=True)
                self._user32.RegisterHotKey.argtypes = [
                    ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
                self._user32.RegisterHotKey.restype = ctypes.c_int
                self._user32.UnregisterHotKey.argtypes = [
                    ctypes.c_void_p, ctypes.c_int]
                self._user32.UnregisterHotKey.restype = ctypes.c_int
            except Exception:  # pragma: no cover - 极端缺 DLL 的环境
                self._user32 = None
        else:
            self._user32 = None

    @property
    def is_registered(self) -> bool:
        """当前是否已成功注册热键。"""
        return self._registered

    @property
    def key_id(self) -> int:
        """注册 ID：WM_HOTKEY 的 wParam，用于区分多个全局热键。"""
        return self._id

    @property
    def vk(self) -> int:
        """当前注册的虚拟键码（未注册时为 0）。"""
        return self._vk if self._registered else 0

    def register(self, hwnd: int, modifiers: int, vk: int) -> bool:
        """注册全局热键；先注销旧键再注册新键。失败返回 False。"""
        self.unregister()
        if not IS_WINDOWS or self._user32 is None or not hwnd:
            return False
        if not vk or vk <= 0:
            return False
        flags = (int(modifiers) & 0x000F) | MOD_NOREPEAT
        try:
            ok = bool(self._user32.RegisterHotKey(
                ctypes.c_void_p(int(hwnd)), self._id, flags, int(vk)))
        except Exception:
            return False
        if ok:
            self._registered = True
            self._modifiers = int(modifiers) & 0x000F
            self._vk = int(vk)
            self._hwnd = int(hwnd)
        return ok

    def unregister(self) -> None:
        """注销热键（安全可重复调用）。"""
        if self._registered and self._user32 is not None:
            try:
                self._user32.UnregisterHotKey(
                    ctypes.c_void_p(self._hwnd), self._id)
            except Exception:
                pass
        self._registered = False
        self._vk = 0
        self._hwnd = 0

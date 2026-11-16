# -*- coding: utf-8 -*-
"""Windows 开机自启动管理（当前用户级，写入 HKCU Run）。

设计要点：
    * 只写 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run，不需要管理员权限，
      也不会影响其他用户。
    * 打包后的 exe 直接写 exe 路径；源码运行时写 pythonw.exe + main.py 路径，
      避免开机时弹出黑色控制台窗口。
    * 所有路径都加引号，兼容「D:\\ai_coding\\windows辅助工具」这类带空格/中文的路径。
    * 非 Windows 或注册表不可用时安全返回失败，绝不影响主程序启动。

本模块不依赖第三方库，可在测试中用假的 winreg 替换。
"""
from __future__ import annotations

import os
import sys
from typing import Optional, Tuple

APP_NAME = "PyMemoTool"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

try:
    import winreg  # type: ignore
except ImportError:  # pragma: no cover - 非 Windows 环境
    winreg = None  # type: ignore


def is_windows() -> bool:
    """当前平台是否支持本模块（必须是 Windows 且有 winreg）。"""
    return sys.platform == "win32" and winreg is not None


def _source_script() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "main.py"))


def startup_command(
    executable: Optional[str] = None,
    script: Optional[str] = None,
    frozen: Optional[bool] = None,
) -> str:
    """生成写进 Run 键的启动命令。

    ``frozen`` 为 True 时按单文件 exe 处理；为 False 时按源码运行处理，
    把 python.exe 换成同目录的 pythonw.exe（若存在）以免弹控制台。
    """
    is_frozen = getattr(sys, "frozen", False) if frozen is None else bool(frozen)
    target = os.path.abspath(executable or sys.executable)
    if is_frozen:
        return '"%s"' % target

    pythonw = os.path.join(os.path.dirname(target), "pythonw.exe")
    if os.path.basename(target).lower() == "python.exe" and os.path.isfile(pythonw):
        target = pythonw
    return '"%s" "%s"' % (target, os.path.abspath(script or _source_script()))


def get_autostart_command(name: str = APP_NAME) -> str:
    """读取当前用户的自启动命令；未设置或读取失败时返回空串。"""
    if not is_windows():
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, name)
        return str(value or "").strip()
    except OSError:
        return ""


def is_autostart_enabled(name: str = APP_NAME) -> bool:
    """Registry 中是否存在有效启动命令。"""
    return bool(get_autostart_command(name))


def set_autostart(
    enabled: bool,
    name: str = APP_NAME,
    executable: Optional[str] = None,
    script: Optional[str] = None,
    frozen: Optional[bool] = None,
) -> Tuple[bool, str]:
    """开启 / 关闭开机自启动，返回 (是否成功, 提示文案)。"""
    if not is_windows():
        return False, "当前系统不支持开机自启动"
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                command = startup_command(executable, script, frozen)
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
                return True, "已开启开机自启动"
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass  # 本来就没开启，视为成功
            return True, "已关闭开机自启动"
    except OSError as exc:
        return False, "注册表写入失败：%s" % exc


def ensure_autostart(
    name: str = APP_NAME,
    executable: Optional[str] = None,
    script: Optional[str] = None,
    frozen: Optional[bool] = None,
) -> Tuple[bool, str]:
    """启动时自愈：配置里开着自启，但 Run 项丢失/路径过期则重写。

    只在命令确实不一致时写盘；用户手动在系统设置里关闭后本函数会恢复它，
    这正是「配置里开着自启」应有的语义。禁用状态由 ``set_autostart(False)``
    删除 Run 项，本函数不会擅自添加。
    """
    expected = startup_command(executable, script, frozen)
    if get_autostart_command(name) == expected:
        return True, ""
    return set_autostart(True, name, executable, script, frozen)

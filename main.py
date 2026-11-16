# -*- coding: utf-8 -*-
"""备忘录工具入口：装配数据层、主窗口、系统托盘与解锁提醒。

运行（务必使用带 tkinter 的系统 Python 3.9）：
    py -3.9 main.py
或：
    C:\\Users\\laicong\\AppData\\Local\\Programs\\Python\\Python39\\python.exe main.py

打包：
    build.bat   或   py -3.9 build.py

启动后默认最小化到右下角系统托盘；关闭窗口按钮只隐藏不退出。
每次解锁屏幕时弹出托盘气泡「还有 N 条未完成事务」。
全局快捷键（默认 F2，可在「设置 → 快捷键」修改）：弹出 / 最小化主窗口。
重复启动（如再次双击 exe）不会开第二个实例，而是把已运行的窗口唤起到前台。
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from typing import Any, Callable, Tuple

from config import AppConfig
from single_instance import SingleInstanceGuard
from storage import MemoStore
from tray import IS_WINDOWS as TRAY_PLATFORM_OK
from tray import TrayIcon
from ui_main import MainWindow

APP_NAME = "PyMemoTool"


def app_base_dir() -> str:
    """返回程序基础目录。

    源码运行时为脚本所在目录；PyInstaller 打包后为 exe 所在目录。
    这样运行时数据始终写在程序旁边，不会散落到工作区之外。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _writable(dir_path: str) -> bool:
    """实测目录是否可写（Windows 上 os.access 不可靠，直接落盘探测）。"""
    try:
        os.makedirs(dir_path, exist_ok=True)
        probe = os.path.join(dir_path, ".write_test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def data_dir() -> str:
    """返回（并创建）数据目录；支持 MEMO_DATA_DIR 环境变量覆盖。

    打包成单文件 exe 后，优先把数据写在 exe 同级 data/ 目录；若该位置
    不可写（例如 exe 被放进 Program Files 或只读 U 盘），自动回退到
    %APPDATA%/PyMemoTool/，保证 exe 拷到任意位置都能独立运行。
    """
    override = os.environ.get("MEMO_DATA_DIR", "").strip()
    if override:
        try:
            os.makedirs(override, exist_ok=True)
        except OSError:
            pass
        return override

    beside_exe = os.path.join(app_base_dir(), "data")
    if _writable(beside_exe):
        return beside_exe

    appdata = os.environ.get("APPDATA", "").strip() or os.path.expanduser("~")
    fallback = os.path.join(appdata, APP_NAME)
    if _writable(fallback):
        return fallback
    return app_base_dir()


class MemoApp(object):
    """应用协调器：把托盘线程事件安全地投递到 tkinter 主线程。"""

    def __init__(self):
        base = data_dir()
        self.memos_path = os.path.join(base, "memos.json")
        self.config_path = os.path.join(base, "config.json")

        self.config = AppConfig.load(self.config_path)
        if not os.path.isfile(self.config_path):
            self.config.save(self.config_path)
        self.store = MemoStore(self.memos_path).load()

        self.root = tk.Tk()
        self.window = MainWindow(
            self.root,
            self.store,
            self.config,
            self.config_path,
            on_request_exit=self.request_exit,
        )
        # 设置窗口保存快捷键后，立即应用到托盘热键（呼出 / 新建 两个）
        self.window._hotkey_applier = self.apply_hotkey
        self.window._new_hotkey_applier = self.apply_new_hotkey
        self._exiting = False
        self.tray = TrayIcon(
            on_show=self.show_window,
            on_new=self.new_from_tray,
            on_exit=self.request_exit,
            on_unlock=self.on_unlock,
            on_hotkey=self.toggle_window,
            on_hotkey_new=self.new_from_tray,
            tooltip="备忘录工具（左键显示 / 右键菜单）",
            dispatcher=self._dispatch,
        )
        self.apply_hotkey(self.config.hotkey)
        self.apply_new_hotkey(self.config.new_hotkey)

    def apply_hotkey(self, hotkey_text: str) -> bool:
        """应用配置的全局快捷键（弹出 / 最小化主窗口）。"""
        return bool(self.tray.set_hotkey(hotkey_text))

    def apply_new_hotkey(self, hotkey_text: str) -> bool:
        """应用配置的全局快捷键（快速新建事务）。"""
        return bool(self.tray.set_new_hotkey(hotkey_text))

    # ------------------------------------------------------------------
    # 线程调度
    # ------------------------------------------------------------------
    def _dispatch(self, fn: Callable[..., Any], args: Tuple[Any, ...]) -> None:
        """托盘线程 → UI 主线程：用 root.after 投递回调。"""
        try:
            self.root.after(0, lambda f=fn, a=tuple(args): f(*a))
        except Exception:
            try:
                fn(*args)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 托盘事件处理
    # ------------------------------------------------------------------
    def show_window(self) -> None:
        """托盘左键 / 菜单「显示主窗口」。"""
        if self._exiting:
            return
        self.window.show()

    def new_from_tray(self) -> None:
        """托盘菜单「新建事务」。"""
        if self._exiting:
            return
        self.window.show()
        self.window.new_memo()

    def toggle_window(self) -> None:
        """全局快捷键按下：窗口可见则最小化回托盘，否则弹出。"""
        if self._exiting:
            return
        try:
            visible = bool(self.window.root.winfo_ismapped())
        except tk.TclError:
            visible = False
        if visible:
            self.window.hide_to_tray()
        else:
            self.window.show()

    def on_unlock(self) -> None:
        """每次解锁屏幕：统计未完成事务数并用托盘气泡提示。"""
        if self._exiting:
            return
        pending = self.store.count_pending()
        text = "还有 %d 条未完成事务" % pending
        self.tray.notify("备忘录 · 解锁提醒", text)
        self.window.set_status_hint(text)
        self.window.refresh()

    def request_exit(self) -> None:
        """真正退出：保存数据、停托盘、关闭窗口。"""
        if self._exiting:
            return
        self._exiting = True
        try:
            self.config.save(self.config_path)
        except OSError:
            pass
        try:
            self.store.save()
        except OSError:
            pass
        try:
            self.tray.unregister_hotkey()
        except Exception:
            pass
        try:
            self.tray.stop()
        except Exception:
            pass
        try:
            self.root.after(120, self._destroy_root)
        except tk.TclError:
            self._destroy_root()

    def _destroy_root(self) -> None:
        """销毁根窗口并结束 mainloop。"""
        try:
            self.root.quit()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def run(self) -> int:
        """启动应用：默认最小化到托盘，进入 tkinter 主循环。"""
        self.window.hide_to_tray()

        tray_started = False
        if TRAY_PLATFORM_OK:
            tray_started = self.tray.start()

        if not tray_started:
            # 非 Windows 或托盘不可用时降级为普通窗口模式，避免程序不可见
            self.window.show()
        else:
            # 启动气泡：便于确认托盘已就绪（解锁时仍会再次提示）
            self.root.after(
                1200,
                lambda: self.tray.notify(
                    "备忘录工具",
                    "已最小化到系统托盘，还有 %d 条未完成事务" %
                    self.store.count_pending(),
                ),
            )

        try:
            self.root.mainloop()
        finally:
            self._exiting = True
            try:
                self.tray.stop()
            except Exception:
                pass
            try:
                self.config.save(self.config_path)
                self.store.save()
            except OSError:
                pass
        return 0


def main() -> int:
    """模块入口。"""
    if sys.platform != "win32":
        print("[提示] 当前非 Windows 平台，系统托盘与解锁监听不可用，将以普通窗口模式运行。")

    guard = SingleInstanceGuard()
    if not guard.acquire():
        # 已有实例在运行：唤起它的窗口（含从托盘恢复）后直接退出本进程
        guard.activate_existing_window()
        return 0

    try:
        app = MemoApp()
        return app.run()
    finally:
        guard.release()


if __name__ == "__main__":
    sys.exit(main())

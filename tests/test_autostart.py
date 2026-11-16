# -*- coding: utf-8 -*-
"""开机自启动回归：HKCU Run 读写、命令生成、自愈与配置字段。

用假的 winreg 替换真实注册表，测试不会改动当前机器任何启动项。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import autostart
import main
from config import AppConfig, DEFAULT_AUTO_START


class FakeKey(object):
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeWinReg(object):
    """足够 autostart 使用的最小 winreg 替身。"""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, initial=None):
        self.store = dict(initial or {})
        self.writes = 0
        self.created = 0

    def OpenKey(self, root, path):
        return FakeKey(self.store)

    def CreateKeyEx(self, root, path, reserved, access):
        self.created += 1
        return FakeKey(self.store)

    def QueryValueEx(self, key, name):
        if name not in key.store:
            raise OSError("missing")
        return key.store[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.writes += 1
        key.store[name] = value

    def DeleteValue(self, key, name):
        if name not in key.store:
            raise FileNotFoundError(name)
        del key.store[name]


def patch_platform(fake):
    return (
        mock.patch.object(autostart, "winreg", fake),
        mock.patch.object(autostart, "is_windows", lambda: True),
    )


class TestStartupCommand(unittest.TestCase):
    def test_frozen_exe_is_quoted(self):
        exe = "D:\\Program Files\\Memo Tool\\MemoTool.exe"
        self.assertEqual(
            autostart.startup_command(executable=exe, frozen=True),
            '"%s"' % exe)

    def test_source_mode_uses_pythonw_when_available(self):
        folder = tempfile.TemporaryDirectory(prefix="memo_auto_")
        self.addCleanup(folder.cleanup)
        python = os.path.join(folder.name, "python.exe")
        pythonw = os.path.join(folder.name, "pythonw.exe")
        script = os.path.join(folder.name, "main.py")
        open(python, "wb").close()
        open(pythonw, "wb").close()
        self.assertEqual(
            autostart.startup_command(executable=python, script=script,
                                      frozen=False),
            '"%s" "%s"' % (pythonw, script))

    def test_source_mode_keeps_python_when_pythonw_missing(self):
        folder = tempfile.TemporaryDirectory(prefix="memo_auto_")
        self.addCleanup(folder.cleanup)
        python = os.path.join(folder.name, "python.exe")
        script = os.path.join(folder.name, "main.py")
        open(python, "wb").close()
        self.assertEqual(
            autostart.startup_command(executable=python, script=script,
                                      frozen=False),
            '"%s" "%s"' % (python, script))


class TestRunKey(unittest.TestCase):
    def setUp(self):
        self.fake = FakeWinReg()
        patches = patch_platform(self.fake)
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_enable_then_disable(self):
        ok, _ = autostart.set_autostart(True, executable="C:\\Memo.exe",
                                        frozen=True)
        self.assertTrue(ok)
        self.assertTrue(autostart.is_autostart_enabled())
        self.assertEqual(autostart.get_autostart_command(),
                         '"C:\\Memo.exe"')
        ok, _ = autostart.set_autostart(False)
        self.assertTrue(ok)
        self.assertFalse(autostart.is_autostart_enabled())

    def test_disable_when_missing_is_success(self):
        ok, message = autostart.set_autostart(False)
        self.assertTrue(ok)
        self.assertIn("已关闭", message)
        self.assertEqual(self.fake.writes, 0)

    def test_ensure_only_rewrites_when_command_differs(self):
        autostart.set_autostart(True, executable="C:\\Memo.exe", frozen=True)
        self.assertEqual(self.fake.writes, 1)
        ok, _ = autostart.ensure_autostart(executable="C:\\Memo.exe",
                                           frozen=True)
        self.assertTrue(ok)
        self.assertEqual(self.fake.writes, 1)  # 一致时不重写
        ok, _ = autostart.ensure_autostart(executable="D:\\Memo.exe",
                                           frozen=True)
        self.assertTrue(ok)
        self.assertEqual(self.fake.writes, 2)
        self.assertEqual(autostart.get_autostart_command(), '"D:\\Memo.exe"')

    def test_registry_error_is_reported_not_raised(self):
        def boom(*args, **kwargs):
            raise OSError("Access denied")

        with mock.patch.object(self.fake, "CreateKeyEx", boom):
            ok, message = autostart.set_autostart(True)
        self.assertFalse(ok)
        self.assertIn("Access denied", message)

    def test_non_windows_is_a_safe_noop(self):
        with mock.patch.object(autostart, "is_windows", lambda: False):
            ok, message = autostart.set_autostart(True)
            self.assertFalse(ok)
            self.assertEqual(autostart.get_autostart_command(), "")
            self.assertFalse(autostart.is_autostart_enabled())


class TestAutoStartConfig(unittest.TestCase):
    def test_default_and_roundtrip(self):
        self.assertIs(DEFAULT_AUTO_START, False)
        self.assertIs(AppConfig().auto_start, False)
        folder = tempfile.TemporaryDirectory(prefix="memo_auto_cfg_")
        self.addCleanup(folder.cleanup)
        path = os.path.join(folder.name, "config.json")
        AppConfig(auto_start=True).save(path)
        self.assertIs(AppConfig.load(path).auto_start, True)

    def test_old_config_without_field_is_false(self):
        cfg = AppConfig()
        cfg.apply_dict({"hotkey": "F9"})
        self.assertIs(cfg.auto_start, False)

    def test_string_false_is_not_treated_as_true(self):
        cfg = AppConfig()
        cfg.apply_dict({"auto_start": "false"})
        self.assertIs(cfg.auto_start, False)
        cfg.apply_dict({"auto_start": "true"})
        self.assertIs(cfg.auto_start, True)

    def test_apply_autostart_reports_failure_to_tray(self):
        tray = SimpleNamespace(notify=mock.Mock())
        app = SimpleNamespace(tray=tray)
        with mock.patch.object(autostart, "set_autostart",
                               return_value=(False, "写入失败")) as setter:
            self.assertFalse(main.MemoApp.apply_autostart(app, True))
        setter.assert_called_once_with(True)
        tray.notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()

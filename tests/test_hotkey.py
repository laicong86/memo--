# -*- coding: utf-8 -*-
"""全局快捷键模块测试：文本解析、规整、配置持久化、托盘注册请求。

不创建任何 Win32 热键 / 托盘线程（在非注册路径下验证逻辑），
GUI 集成部分（设置窗口保存 → apply_hotkey）在 test_gui_smoke 覆盖。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hotkey import (DEFAULT_HOTKEY, HOTKEY_PRESETS, MOD_ALT, MOD_CONTROL,
                    MOD_SHIFT, MOD_WIN, GlobalHotkey, is_valid_hotkey_text,
                    normalize_hotkey_text, parse_hotkey, vk_display_name)
import tray as tray_module
from tray import TrayIcon


class TestParseHotkey(unittest.TestCase):
    def test_default_is_f2(self):
        parsed = parse_hotkey(DEFAULT_HOTKEY)
        self.assertEqual(parsed, (0, 0x71))  # 无修饰键，VK_F2

    def test_function_keys(self):
        self.assertEqual(parse_hotkey("F1"), (0, 0x70))
        self.assertEqual(parse_hotkey("f10"), (0, 0x79))
        self.assertEqual(parse_hotkey("F24"), (0, 0x87))
        self.assertIsNone(parse_hotkey("F25"))
        self.assertIsNone(parse_hotkey("F0"))

    def test_modifier_combinations(self):
        modifiers, vk = parse_hotkey("Ctrl+Alt+M")
        self.assertEqual(modifiers, MOD_CONTROL | MOD_ALT)
        self.assertEqual(vk, ord("M"))
        # 顺序无关、大小写无关
        self.assertEqual(parse_hotkey("alt+ctrl+m"), (MOD_CONTROL | MOD_ALT, ord("M")))
        self.assertEqual(parse_hotkey("SHIFT + F9"), (MOD_SHIFT, 0x78))
        self.assertEqual(parse_hotkey("win+q"), (MOD_WIN, ord("Q")))
        self.assertEqual(parse_hotkey("CTRL+SHIFT+ALT+WIN+K"),
                         (MOD_CONTROL | MOD_SHIFT | MOD_ALT | MOD_WIN, ord("K")))

    def test_special_keys(self):
        self.assertEqual(parse_hotkey("Ctrl+Space"), (MOD_CONTROL, 0x20))
        self.assertEqual(parse_hotkey("Home"), (0, 0x24))
        self.assertEqual(parse_hotkey("ctrl+delete"), (MOD_CONTROL, 0x2E))

    def test_invalid_inputs(self):
        for text in ("", "   ", None, 123, "++", "F2+F3", "Ctrl", "Alt+",
                     "Ctrl++M", "Ctrl+Alt+", "M+Ctrl", "Ctrl+挂"):
            with self.subTest(text=repr(text)):
                self.assertIsNone(parse_hotkey(text))

    def test_fullwidth_plus_accepted(self):
        self.assertEqual(parse_hotkey("Ctrl＋Alt＋M"), (MOD_CONTROL | MOD_ALT, ord("M")))


class TestNormalizeAndDisplay(unittest.TestCase):
    def test_normalize_orders_modifiers(self):
        self.assertEqual(normalize_hotkey_text("alt+shift+ctrl+f5"),
                         "Ctrl+Alt+Shift+F5")
        self.assertEqual(normalize_hotkey_text("win + m"), "Win+M")
        self.assertEqual(normalize_hotkey_text("f2"), "F2")

    def test_normalize_invalid_returns_empty(self):
        self.assertEqual(normalize_hotkey_text("not a key"), "")
        self.assertEqual(normalize_hotkey_text(""), "")

    def test_vk_display_name_roundtrip(self):
        for text in ("F2", "F12", "A", "5", "Space", "Home"):
            parsed = parse_hotkey(text)
            self.assertIsNotNone(parsed, text)
            self.assertEqual(vk_display_name(parsed[1]),
                             "Space" if text == "Space" else text.upper() if len(text) == 1 else text)

    def test_preset_list_all_valid(self):
        self.assertIn(DEFAULT_HOTKEY, HOTKEY_PRESETS)
        for preset in HOTKEY_PRESETS:
            self.assertTrue(is_valid_hotkey_text(preset), preset)


class TestGlobalHotkeyClass(unittest.TestCase):
    def test_unregistered_state(self):
        hk = GlobalHotkey()
        self.assertFalse(hk.is_registered)
        self.assertEqual(hk.vk, 0)

    def test_register_without_hwnd_fails_safely(self):
        hk = GlobalHotkey()
        # hwnd=0 时直接返回 False，不抛异常
        self.assertFalse(hk.register(0, MOD_CONTROL, 0x4D))
        self.assertFalse(hk.is_registered)

    def test_unregister_is_safe_to_repeat(self):
        hk = GlobalHotkey()
        hk.unregister()
        hk.unregister()
        self.assertFalse(hk.is_registered)


class TestTrayHotkeyRequest(unittest.TestCase):
    """TrayIcon.set_hotkey：只验证请求提交逻辑，不开线程不建窗口。"""

    def test_set_hotkey_invalid_text_returns_false(self):
        tray = TrayIcon()
        self.assertFalse(tray.set_hotkey("bogus key"))
        self.assertFalse(tray.is_hotkey_active())

    def test_set_hotkey_without_window_returns_false(self):
        tray = TrayIcon()
        # 托盘线程未启动（_hwnd=0）时无法注册，返回 False 且状态保持未注册
        self.assertFalse(tray.set_hotkey("F2"))
        self.assertFalse(tray.is_hotkey_active())
        tray.unregister_hotkey()
        self.assertFalse(tray.is_hotkey_active())

    def test_wm_hotkey_message_constant(self):
        self.assertEqual(tray_module.hotkey_mod.WM_HOTKEY, 0x0312)
        self.assertEqual(tray_module.WM_APP_HOTKEY, tray_module.WM_APP + 4)


class TestConfigHotkeyPersistence(unittest.TestCase):
    def test_default_hotkey_written(self):
        from config import AppConfig
        cfg = AppConfig()
        self.assertEqual(cfg.hotkey, DEFAULT_HOTKEY)
        data = cfg.as_dict()
        self.assertEqual(data["hotkey"], "F2")

    def test_load_invalid_hotkey_falls_back(self):
        from config import AppConfig
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"hotkey": "not-a-key"}, handle)
            cfg = AppConfig.load(path)
        self.assertEqual(cfg.hotkey, DEFAULT_HOTKEY)

    def test_load_valid_hotkey_normalized(self):
        from config import AppConfig
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"hotkey": "ctrl+alt+m"}, handle)
            cfg = AppConfig.load(path)
        self.assertEqual(cfg.hotkey, "Ctrl+Alt+M")

    def test_roundtrip_preserves_custom_hotkey(self):
        from config import AppConfig
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            cfg = AppConfig(hotkey="Ctrl+Shift+F9")
            cfg.save(path)
            loaded = AppConfig.load(path)
        self.assertEqual(loaded.hotkey, "Ctrl+Shift+F9")


if __name__ == "__main__":
    unittest.main()

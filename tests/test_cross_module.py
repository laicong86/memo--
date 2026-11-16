# -*- coding: utf-8 -*-
"""Cross-module consistency, artifact integrity and requirement-evidence tests.

Verifies that constants agree between modules, that all sources compile and
import independently, that dist/MemoTool.exe is a genuine PE/PyInstaller
binary, and that the documented build commands match the real scripts.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SRC_FILES = (
    "main.py", "models.py", "storage.py", "config.py", "theme.py",
    "ui_main.py", "ui_detail.py", "calendar_picker.py", "tray.py",
    "session_watcher.py", "single_instance.py", "build.py",
)


def _read(relative_path):
    with open(os.path.join(ROOT, relative_path), "r", encoding="utf-8") as handle:
        return handle.read()


class TestSourceCompilesAndImports(unittest.TestCase):
    def test_all_source_files_compile(self):
        for name in SRC_FILES:
            with self.subTest(file=name):
                path = os.path.join(ROOT, name)
                self.assertTrue(os.path.isfile(path), name)
                source = _read(name)
                try:
                    compile(source, path, "exec")
                except SyntaxError as exc:
                    self.fail("%s has a syntax error: %s" % (name, exc))

    def test_each_module_imports_independently_in_a_fresh_interpreter(self):
        # A fresh interpreter per module proves there is no circular import
        # and no import-time side effect that requires a GUI/window.
        for module in ("models", "storage", "config", "theme", "session_watcher",
                       "tray", "calendar_picker", "single_instance", "ui_detail",
                       "ui_main", "main", "build"):
            with self.subTest(module=module):
                proc = subprocess.run(
                    [sys.executable, "-c", "import %s" % module],
                    cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                self.assertEqual(
                    proc.returncode, 0,
                    "import %s failed: %s" % (
                        module, proc.stderr.decode("utf-8", "replace")[-500:]))

    def test_no_third_party_runtime_dependency(self):
        banned = ("import pystray", "import win32gui", "import win32ts",
                  "import win32api", "import PyQt5", "import PySide",
                  "from PIL import")
        for name in SRC_FILES:
            source = _read(name)
            for token in banned:
                with self.subTest(file=name, token=token):
                    self.assertNotIn(token, source)


class TestConstantConsistency(unittest.TestCase):
    def test_urgency_and_status_values_are_shared(self):
        import config
        import storage
        from models import STATUS_DONE, STATUS_PENDING, URGENCY_HIGH, URGENCY_LEVELS
        self.assertEqual(URGENCY_LEVELS, ("高", "中", "低"))
        self.assertEqual(URGENCY_HIGH, "高")
        self.assertEqual(config.URGENCY_HIGH, URGENCY_HIGH)
        self.assertEqual(config.STATUS_DONE, STATUS_DONE)
        self.assertEqual(storage.STATUS_DONE, STATUS_DONE)
        self.assertEqual((STATUS_PENDING, STATUS_DONE), ("未完成", "已完成"))

    def test_sort_modes_consistent_between_models_config_and_ui(self):
        from config import DEFAULT_SORT_MODE
        from models import SORT_MODE_LABELS, SORT_MODES
        from ui_main import LABEL_TO_SORT
        self.assertEqual(len(SORT_MODES), 3)
        self.assertEqual(set(SORT_MODE_LABELS.keys()), {k for k, _ in SORT_MODES})
        self.assertEqual(set(LABEL_TO_SORT.keys()), set(SORT_MODE_LABELS.values()))
        self.assertEqual(set(LABEL_TO_SORT.values()), set(SORT_MODE_LABELS.keys()))
        self.assertIn(DEFAULT_SORT_MODE, SORT_MODE_LABELS)

    def test_color_key_set_is_identical_everywhere(self):
        from config import (COLOR_KEYS, COLOR_KEY_LABELS, COLOR_PRESETS,
                            DEFAULT_COLOR_MAP, is_valid_hex)
        from ui_main import _PRESET_VALUES
        self.assertEqual(len(COLOR_KEYS), 6)
        self.assertEqual(set(COLOR_KEYS), set(COLOR_KEY_LABELS))
        self.assertEqual(set(COLOR_KEYS), set(DEFAULT_COLOR_MAP))
        self.assertEqual(len(_PRESET_VALUES), len(COLOR_PRESETS))
        for value in DEFAULT_COLOR_MAP.values():
            self.assertTrue(is_valid_hex(value))

    def test_config_defaults_are_internally_valid(self):
        from config import (DEFAULT_DUE_SOON_HOURS, DEFAULT_SORT_MODE,
                            _safe_hours)
        from models import SORT_MODE_LABELS
        self.assertIn(DEFAULT_SORT_MODE, SORT_MODE_LABELS)
        self.assertEqual(_safe_hours(DEFAULT_DUE_SOON_HOURS), DEFAULT_DUE_SOON_HOURS)
        self.assertEqual(DEFAULT_DUE_SOON_HOURS, 48)

    def test_time_format_shared_by_models_and_ui_detail(self):
        import ui_detail
        from models import TIME_FORMAT, now_str
        from datetime import datetime
        self.assertEqual(TIME_FORMAT, "%Y-%m-%d %H:%M:%S")
        self.assertEqual(ui_detail.TIME_FORMAT, TIME_FORMAT)
        self.assertIsNotNone(datetime.strptime(now_str(), TIME_FORMAT))

    def test_theme_layout_supports_side_detail_panel(self):
        import theme
        self.assertGreater(theme.CARD_RADIUS, 0)
        self.assertGreater(theme.WIN_DETAIL_W, theme.WIN_LIST_W)
        self.assertGreater(theme.DETAIL_W, 0)
        self.assertGreater(theme.ROW_HEIGHT, 0)        # 展开详情时窗口增加的宽度必须等于面板宽度，否则列表会被挤压或留白
        self.assertEqual(theme.WIN_DETAIL_W - theme.WIN_LIST_W, theme.DETAIL_W)

    def test_storage_version_matches_written_payload(self):
        import json
        import tempfile
        from storage import MemoStore
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "memos.json")
            MemoStore(path).save()
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["version"], MemoStore.VERSION)
            self.assertEqual(data["memos"], [])

    def test_config_version_matches_written_payload(self):
        import json
        import tempfile
        from config import AppConfig, CONFIG_VERSION
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            AppConfig(path=path).save()
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["version"], CONFIG_VERSION)


class TestRequirementEvidence(unittest.TestCase):
    """Static evidence mapping the 6 user requirements to source locations."""

    def test_req1_python_entry_point(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "main.py")))
        self.assertIn('if __name__ == "__main__":', _read("main.py"))

    def test_req2_search_sort_list_layout_and_dividers(self):
        ui = _read("ui_main.py")
        for token in ("搜索", "排序", "round_rect_points", "color_for_memo",
                      "创建 %s", "，预期 %s", "fill=theme.DIVIDER", "create_line",
                      "OVERDUE_TEXT", "color_key_for_memo"):
            with self.subTest(token=token):
                self.assertIn(token, ui)
        self.assertIn("OVERDUE_TEXT =", _read("theme.py"))

    def test_req3_unlock_reminder_includes_pending_count(self):
        source = _read("main.py")
        self.assertIn("def on_unlock", source)
        self.assertIn("self.store.count_pending()", source)
        self.assertIn('"还有 %d 条未完成事务" % pending', source)
        self.assertIn('self.tray.notify("备忘录 · 解锁提醒", text)', source)

    def test_req3_starts_visible_and_tray_ready_before_mainloop(self):
        source = _read("main.py")
        self.assertIn("self.window.show()", source)
        self.assertLess(source.index("self.window.show()"),
                        source.index("self.root.mainloop()"))
        self.assertIn("self.tray.start()", source)
        ui_source = _read("ui_main.py")
        self.assertIn("def hide_to_tray", ui_source)
        self.assertIn("self.root.withdraw()", ui_source)
        self.assertIn("on_hide_to_tray", ui_source)

    def test_first_hide_notifies_hotkey_from_config(self):
        source = _read("main.py")
        self.assertIn("def on_hide_to_tray", source)
        self.assertIn("self._hide_hint_shown", source)
        self.assertIn("self.config.hotkey", source)
        self.assertIn("on_hide_to_tray=self.on_hide_to_tray", source)

    def test_req3_color_mapping_is_user_configurable(self):
        config_source = _read("config.py")
        ui_source = _read("ui_main.py")
        for token in ("def set_color", "def reset_colors", "def apply_dict",
                      "DEFAULT_COLOR_MAP", "def classify_color_key"):
            with self.subTest(token=token):
                self.assertIn(token, config_source)
        for token in ("def open_settings", "def _save_settings", "def _reset_settings",
                      "color_for_memo"):
            with self.subTest(token=token):
                self.assertIn(token, ui_source)

    def test_req4_only_local_system_time_no_utc_no_hardcoded(self):
        for name in SRC_FILES:
            source = _read(name).lower()
            with self.subTest(file=name):
                self.assertNotIn("utcnow", source)
                self.assertNotIn("timezone.utc", source)
                self.assertNotIn("gmtime", source)
        self.assertIn("datetime.now()", _read("models.py"))
        self.assertIn("datetime.now()", _read("config.py"))

    def test_req5_double_click_opens_right_side_detail(self):
        ui = _read("ui_main.py")
        detail = _read("ui_detail.py")
        self.assertIn('<Double-Button-1>', ui)
        self.assertIn("self._on_row_double_click", ui)
        self.assertIn("self.show_detail(memo)", ui)
        self.assertIn("self.grid(row=0, column=2", detail)
        self.assertNotIn("self.txt_content", detail)
        self.assertIn("def _append_log", detail)

    def test_req5_created_at_is_read_only_in_detail(self):
        detail = _read("ui_detail.py")
        self.assertIn("created_at=self._current.created_at", detail)
        self.assertIn("tk.Label(info, textvariable=self.var_created", detail)
        self.assertNotIn("Entry(form, textvariable=self.var_created", detail)
        self.assertNotIn("Spinbox(form, textvariable=self.var_created", detail)

    def test_single_instance_guard_wired_into_main(self):
        guard = _read("single_instance.py")
        main_src = _read("main.py")
        for token in ("CreateMutexW", "ERROR_ALREADY_EXISTS",
                      "activate_existing_window", "FindWindowW",
                      "SetForegroundWindow"):
            with self.subTest(token=token):
                self.assertIn(token, guard)
        for token in ("SingleInstanceGuard()", "guard.acquire()",
                      "guard.activate_existing_window()", "guard.release()"):
            with self.subTest(token=token):
                self.assertIn(token, main_src)

    def test_req3_progress_logs_get_system_timestamp(self):
        detail = _read("ui_detail.py")
        self.assertIn("ProgressLog(text=entry, time=now_str())", detail)

    def test_detail_panel_is_split_into_editor_and_side_properties(self):
        detail = _read("ui_detail.py")
        picker = _read("calendar_picker.py")
        for token in ("self.side", "_build_main", "_build_side", "cmb_urgency",
                      "cmb_status", "spin_progress", "btn_due", "entry_log",
                      'state="readonly"', "CalendarPopup"):
            with self.subTest(token=token):
                self.assertIn(token, detail)
        for token in ("def month_cells", "def build_due_text",
                      "def parse_due_text", "datetime.now()"):
            with self.subTest(token=token):
                self.assertIn(token, picker)

    def test_history_newest_first_and_return_commits_log(self):
        detail = _read("ui_detail.py")
        for token in ("def _display_logs", "reverse=True",
                      "<Return>", "def _on_log_return",
                      "ProgressLog(text=entry, time=now_str())"):
            with self.subTest(token=token):
                self.assertIn(token, detail)


class TestArtifactIntegrity(unittest.TestCase):
    def test_exe_exists_and_is_a_valid_pe_binary(self):
        path = os.path.join(ROOT, "dist", "MemoTool.exe")
        self.assertTrue(os.path.isfile(path), "dist/MemoTool.exe missing")
        self.assertGreater(os.path.getsize(path), 1024 * 1024)
        with open(path, "rb") as handle:
            head = handle.read(1024)
        self.assertEqual(head[:2], b"MZ")
        pe_offset = struct.unpack_from("<I", head, 0x3C)[0]
        self.assertEqual(head[pe_offset:pe_offset + 4], b"PE\x00\x00")
        self.assertEqual(struct.unpack_from("<H", head, pe_offset + 4)[0], 0x8664)
        self.assertGreater(struct.unpack_from("<H", head, pe_offset + 6)[0], 0)

    def test_exe_is_a_real_pyinstaller_onefile_bundle(self):
        path = os.path.join(ROOT, "dist", "MemoTool.exe")
        with open(path, "rb") as handle:
            data = handle.read()
        cookie = data.rfind(b"MEI\x0c\x0b\x0a\x0b\x0e")
        self.assertGreater(cookie, 0, "PyInstaller MEI cookie not found")
        self.assertGreater(cookie, len(data) - 4096, "cookie should be near EOF")

    def test_pyinstaller_intermediate_outputs_exist(self):
        for rel in ("MemoTool.spec", "build/MemoTool/Analysis-00.toc",
                    "build/MemoTool/PKG-00.toc", "build/MemoTool/base_library.zip"):
            with self.subTest(file=rel):
                self.assertTrue(os.path.isfile(os.path.join(ROOT, rel)), rel)


class TestDocumentationConsistency(unittest.TestCase):
    def test_readme_build_command_matches_actual_scripts(self):
        readme = _read("README.md")
        bat = _read("build.bat")
        build_py = _read("build.py")
        for token in ("--onefile", "--noconsole", "--clean", "--name",
                      "MemoTool", "main.py"):
            with self.subTest(token=token):
                self.assertIn(token, readme)
                self.assertIn(token, bat)
                self.assertIn(token, build_py)

    def test_spec_matches_onefile_and_noconsole(self):
        spec = _read("MemoTool.spec")
        self.assertIn("['main.py']", spec)
        self.assertIn("name='MemoTool'", spec)
        self.assertIn("console=False", spec)
        self.assertNotIn("COLLECT(", spec)

    def test_readme_documents_runtime_data_files_and_local_time(self):
        readme = _read("README.md")
        self.assertIn("data/memos.json", readme)
        self.assertIn("data/config.json", readme)
        self.assertIn("datetime.now()", readme)
        self.assertIn("WTS_SESSION_UNLOCK", readme)

    def test_requirements_file_is_comment_only(self):
        for line in _read("requirements.txt").splitlines():
            stripped = line.strip()
            self.assertTrue(not stripped or stripped.startswith("#"),
                            "unexpected installable requirement: %s" % stripped)


class TestGeometryHelper(unittest.TestCase):
    def test_round_rect_points_shape_and_bounds(self):
        from ui_main import round_rect_points
        points = round_rect_points(0, 0, 100, 60, 10, steps=8)
        self.assertEqual(len(points), 2 * 4 * (8 + 1))
        self.assertTrue(all(isinstance(v, float) for v in points))
        for index in range(0, len(points), 2):
            self.assertGreaterEqual(points[index], -0.001)
            self.assertLessEqual(points[index], 100.001)
            self.assertGreaterEqual(points[index + 1], -0.001)
            self.assertLessEqual(points[index + 1], 60.001)

    def test_round_rect_points_clamps_oversized_radius(self):
        from ui_main import round_rect_points
        points = round_rect_points(0, 0, 20, 10, 9999, steps=4)
        self.assertEqual(len(points), 2 * 4 * (4 + 1))
        self.assertTrue(all(-0.001 <= points[i] <= 20.001 for i in range(0, len(points), 2)))
        self.assertTrue(all(-0.001 <= points[i + 1] <= 10.001
                            for i in range(0, len(points), 2)))


if __name__ == "__main__":
    unittest.main()

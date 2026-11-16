# -*- coding: utf-8 -*-
"""Independent unit tests for storage.py (MemoStore).

Covers CRUD, search, all three sort modes, counters, persistence round-trip
and tolerant loading of missing / empty / corrupt JSON. No project source
file is modified. All I/O is isolated in TemporaryDirectory.
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

from models import Memo, STATUS_DONE, SORT_CREATED, SORT_DUE, SORT_URGENCY
from storage import MemoStore


class MemoStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "runtime", "memos.json")
        self.store = MemoStore(self.path)

    def _write_raw(self, text):
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def _memo(self, memo_id, **kwargs):
        values = {
            "id": memo_id,
            "title": "task-" + memo_id,
            "content": "body-" + memo_id,
            "created_at": "2024-01-01 00:00:00",
            "due_at": "",
            "urgency": "中",
            "status": "未完成",
            "progress": 0,
        }
        values.update(kwargs)
        return Memo(**values)


class TestMemoStoreCRUD(MemoStoreTestCase):
    def test_add_and_get(self):
        memo = self._memo("a", title="alpha")
        self.assertIs(self.store.add(memo), memo)
        self.assertIs(self.store.get("a"), memo)
        self.assertEqual(self.store.get("a").title, "alpha")

    def test_add_duplicate_id_updates_instead_of_duplicating(self):
        self.store.add(self._memo("a", title="first"))
        self.store.add(self._memo("a", title="second"))
        self.assertEqual(self.store.count_all(), 1)
        self.assertEqual(self.store.get("a").title, "second")

    def test_update_existing_returns_true(self):
        self.store.add(self._memo("a", title="old"))
        self.assertTrue(self.store.update(self._memo("a", title="new")))
        self.assertEqual(self.store.get("a").title, "new")
        self.assertEqual(self.store.count_all(), 1)

    def test_update_missing_returns_false(self):
        self.assertFalse(self.store.update(self._memo("ghost")))
        self.assertEqual(self.store.count_all(), 0)

    def test_delete_existing_returns_true(self):
        self.store.add(self._memo("a"))
        self.assertTrue(self.store.delete("a"))
        self.assertIsNone(self.store.get("a"))
        self.assertEqual(self.store.count_all(), 0)

    def test_delete_missing_returns_false(self):
        self.assertFalse(self.store.delete("ghost"))
        self.store.add(self._memo("a"))
        self.assertFalse(self.store.delete("ghost"))
        self.assertEqual(self.store.count_all(), 1)

    def test_count_all_and_len(self):
        for index in range(4):
            self.store.add(self._memo("id%d" % index))
        self.assertEqual(self.store.count_all(), 4)
        self.assertEqual(len(self.store), 4)

    def test_count_pending(self):
        self.store.add(self._memo("p1"))
        self.store.add(self._memo("p2", status="未完成"))
        self.store.add(self._memo("d1", status=STATUS_DONE))
        self.store.add(self._memo("d2", status=STATUS_DONE))
        self.assertEqual(self.store.count_pending(), 2)
        self.assertEqual(self.store.count_all(), 4)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get("missing"))

    def test_list_all_returns_a_separate_list(self):
        self.store.add(self._memo("a"))
        rows = self.store.list_all()
        rows.clear()
        self.assertEqual(self.store.count_all(), 1)

    def test_create_memo_factory_sets_system_created_at_without_adding(self):
        memo = self.store.create_memo(title="brand new", urgency="高")
        self.assertEqual(memo.title, "brand new")
        self.assertEqual(memo.urgency, "高")
        self.assertTrue(memo.created_at)
        self.assertEqual(self.store.count_all(), 0)

    def test_operations_on_empty_store_are_safe(self):
        self.assertEqual(self.store.list_all(), [])
        self.assertEqual(self.store.search("anything"), [])
        self.assertEqual(self.store.sorted_by(SORT_CREATED), [])
        self.assertEqual(self.store.count_pending(), 0)
        self.assertFalse(self.store.delete("x"))
        self.assertFalse(self.store.update(self._memo("x")))

    def test_default_path_is_memes_json_and_no_disk_access_until_io(self):
        store = MemoStore()
        self.assertEqual(store.path, "memos.json")
        store.add(self._memo("a"))
        self.assertEqual(store.count_all(), 1)


class TestMemoStoreSearch(MemoStoreTestCase):
    def setUp(self):
        super().setUp()
        self.store.add(self._memo("a", title="Buy MILK", content="from the Shop"))
        self.store.add(self._memo("b", title="Write Report", content="milk note"))
        self.store.add(self._memo("c", title="Call Bob", content="nothing here"))

    def test_search_by_title(self):
        self.assertEqual([m.id for m in self.store.search("Report")], ["b"])

    def test_search_by_content(self):
        self.assertEqual([m.id for m in self.store.search("Shop")], ["a"])

    def test_search_is_case_insensitive(self):
        self.assertEqual([m.id for m in self.store.search("milk")], ["a", "b"])
        self.assertEqual([m.id for m in self.store.search("MILK")], ["a", "b"])

    def test_search_empty_keyword_returns_all(self):
        self.assertEqual(len(self.store.search("")), 3)
        self.assertEqual(len(self.store.search(None)), 3)

    def test_search_blank_keyword_returns_all(self):
        self.assertEqual(len(self.store.search("   ")), 3)

    def test_search_keyword_is_stripped(self):
        self.assertEqual([m.id for m in self.store.search("  report  ")], ["b"])

    def test_search_no_match_returns_empty(self):
        self.assertEqual(self.store.search("zzz-nonexistent"), [])


class TestMemoStoreSorting(MemoStoreTestCase):
    def test_sort_by_created_desc(self):
        self.store.add(self._memo("a", created_at="2024-01-01 00:00:00"))
        self.store.add(self._memo("b", created_at="2024-01-03 00:00:00"))
        self.store.add(self._memo("c", created_at="2024-01-02 00:00:00"))
        self.assertEqual([m.id for m in self.store.sorted_by(SORT_CREATED)],
                         ["b", "c", "a"])

    def test_sort_by_due_asc_with_standard_format(self):
        self.store.add(self._memo("late", due_at="2024-06-01 00:00:00"))
        self.store.add(self._memo("early", due_at="2024-02-01 00:00:00"))
        self.store.add(self._memo("mid", due_at="2024-04-01 00:00:00"))
        self.assertEqual([m.id for m in self.store.sorted_by(SORT_DUE)],
                         ["early", "mid", "late"])

    def test_sort_by_due_places_unset_last(self):
        self.store.add(self._memo("unset", due_at=""))
        self.store.add(self._memo("set", due_at="2024-02-01 00:00:00"))
        self.assertEqual([m.id for m in self.store.sorted_by(SORT_DUE)],
                         ["set", "unset"])

    def test_sort_by_urgency_high_to_low(self):
        self.store.add(self._memo("low", urgency="低"))
        self.store.add(self._memo("high", urgency="高"))
        self.store.add(self._memo("mid", urgency="中"))
        self.assertEqual([m.id for m in self.store.sorted_by(SORT_URGENCY)],
                         ["high", "mid", "low"])

    def test_pending_sorted_before_done_in_every_mode(self):
        self.store.add(self._memo("done", status=STATUS_DONE,
                                  created_at="2024-12-31 00:00:00",
                                  due_at="2024-01-01 00:00:00", urgency="高"))
        self.store.add(self._memo("pending", created_at="2024-01-01 00:00:00",
                                  due_at="2024-12-31 00:00:00", urgency="低"))
        for mode in (SORT_CREATED, SORT_DUE, SORT_URGENCY):
            with self.subTest(mode=mode):
                rows = self.store.sorted_by(mode)
                self.assertEqual(rows[0].id, "pending")
                self.assertEqual(rows[1].id, "done")

    def test_sort_combined_with_keyword_filter(self):
        self.store.add(self._memo("a", title="alpha", created_at="2024-01-01 00:00:00"))
        self.store.add(self._memo("b", title="alpha two", created_at="2024-01-03 00:00:00"))
        self.store.add(self._memo("c", title="beta", created_at="2024-01-02 00:00:00"))
        self.assertEqual([m.id for m in self.store.sorted_by(SORT_CREATED, "alpha")],
                         ["b", "a"])

    def test_unknown_sort_mode_falls_back_to_created(self):
        self.store.add(self._memo("a", created_at="2024-01-01 00:00:00"))
        self.store.add(self._memo("b", created_at="2024-01-03 00:00:00"))
        self.assertEqual([m.id for m in self.store.sorted_by("no_such_mode")],
                         ["b", "a"])

    def test_sorted_by_does_not_mutate_internal_order(self):
        self.store.add(self._memo("a", created_at="2024-01-01 00:00:00"))
        self.store.add(self._memo("b", created_at="2024-01-03 00:00:00"))
        before = [m.id for m in self.store.list_all()]
        self.store.sorted_by(SORT_CREATED)
        self.store.sorted_by(SORT_DUE)
        self.assertEqual([m.id for m in self.store.list_all()], before)


class TestMemoStorePersistence(MemoStoreTestCase):
    def test_save_and_reload_preserves_every_field_including_logs(self):
        memo = self._memo("x", title="标题", content="内容",
                          due_at="2024-02-01 00:00:00", urgency="高",
                          status=STATUS_DONE, progress=88)
        memo.add_progress("进度一", progress=88, time="2024-01-02 00:00:00")
        self.store.add(memo)
        self.assertTrue(self.store.save())
        self.assertTrue(os.path.isfile(self.path))
        fresh = MemoStore(self.path).load()
        self.assertEqual(fresh.count_all(), 1)
        self.assertEqual(fresh.get("x").to_dict(), memo.to_dict())

    def test_save_creates_parent_directories(self):
        self.assertFalse(os.path.isdir(os.path.dirname(self.path)))
        self.store.save()
        self.assertTrue(os.path.isfile(self.path))

    def test_reload_after_delete_persists(self):
        self.store.add(self._memo("a"))
        self.store.add(self._memo("b"))
        self.store.save()
        self.store.delete("a")
        self.store.save()
        fresh = MemoStore(self.path).load()
        self.assertEqual([m.id for m in fresh.list_all()], ["b"])

    def test_load_missing_file_leaves_empty_store(self):
        fresh = MemoStore(self.path).load()
        self.assertEqual(fresh.count_all(), 0)

    def test_load_returns_self_for_chaining(self):
        store = MemoStore(self.path)
        self.assertIs(store.load(), store)

    def test_load_empty_file_is_tolerated(self):
        self._write_raw("")
        self.assertEqual(MemoStore(self.path).load().count_all(), 0)

    def test_load_corrupt_json_is_tolerated(self):
        self._write_raw('{"version": 1, "memos": [ this is broken')
        self.assertEqual(MemoStore(self.path).load().count_all(), 0)

    def test_load_non_dict_root_is_tolerated(self):
        self._write_raw("[]")
        self.assertEqual(MemoStore(self.path).load().count_all(), 0)
        self._write_raw("null")
        self.assertEqual(MemoStore(self.path).load().count_all(), 0)

    def test_load_memos_not_a_list_is_tolerated(self):
        self._write_raw('{"version": 1, "memos": "oops"}')
        self.assertEqual(MemoStore(self.path).load().count_all(), 0)
        self._write_raw('{"version": 1, "memos": {"a": 1}}')
        self.assertEqual(MemoStore(self.path).load().count_all(), 0)

    def test_load_skips_non_dict_items(self):
        payload = {"version": 1, "memos": [1, "x", None, [], {"id": "ok", "title": "OK"}]}
        self._write_raw(json.dumps(payload, ensure_ascii=False))
        store = MemoStore(self.path).load()
        self.assertEqual(store.count_all(), 1)
        self.assertEqual(store.get("ok").title, "OK")

    def test_load_normalizes_invalid_fields(self):
        payload = {"version": 1, "memos": [
            {"id": "z", "urgency": "??", "status": "??", "progress": 500, "logs": None}
        ]}
        self._write_raw(json.dumps(payload, ensure_ascii=False))
        memo = MemoStore(self.path).load().get("z")
        self.assertEqual(memo.urgency, "中")
        self.assertEqual(memo.status, "未完成")
        self.assertEqual(memo.progress, 100)
        self.assertEqual(memo.logs, [])

    def test_load_ignores_foreign_json_keys(self):
        payload = {"version": 99, "memos": [{"id": "a", "title": "A"}], "extra": [1, 2, 3]}
        self._write_raw(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(MemoStore(self.path).load().get("a").title, "A")

    def test_version_constant(self):
        self.assertEqual(MemoStore.VERSION, 1)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""边界健壮性验证中发现的 3 处「源码自身契约违背」回归用例。

重要说明
--------
这三条用例断言的并**不是**额外强加的 QA 标准，而是各模块 docstring
自己公开承诺的行为：

* FIND-001  storage.MemoStore.load 的 docstring：
    「从磁盘载入；文件异常时保持空库，不抛异常。」
* FIND-002  config.is_valid_hex 的 docstring：
    「判断是否为合法的 #RRGGBB 颜色值。」
* FIND-003  storage.MemoStore.sorted_by 的 docstring：
    「due_at 预期完成时间升序（越早到期越靠前，未设置的排最后）」

当前实现未满足上述契约，因此这三条用例**如实地 FAIL**。按 QA 智能路由
规则判定为源码缺陷（ROUTE: engineer），本文件不修改任何项目源码。
工程师修复后无需改动断言，这些用例即可自动转绿，可直接作为回归验证。
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

from config import is_valid_hex
from models import Memo
from storage import MemoStore


class TestContractViolations(unittest.TestCase):
    def test_FIND_001_load_must_not_raise_on_non_finite_progress(self):
        """storage.py:52-53 + models.py:106-116 -- OverflowError escapes load()."""
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "memos.json")
            # 1e999 is legal JSON; json.load turns it into float('inf').
            payload = {
                "version": 1,
                "memos": [{"id": "x", "title": "t", "progress": 1e999}],
            }
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            store = MemoStore(path)
            try:
                store.load()
            except Exception as exc:  # noqa: BLE001 - deliberately surfacing it
                self.fail(
                    "MemoStore.load() promises never to raise on a bad file, but "
                    "raised %s: %s. Root cause: models.normalize_progress only "
                    "catches (TypeError, ValueError); int(float('inf')) raises "
                    "OverflowError, which load() does not catch."
                    % (type(exc).__name__, exc)
                )
            self.assertLessEqual(store.count_all(), 1)

    def test_FIND_002_is_valid_hex_must_reject_non_rrggbb_strings(self):
        """config.py:75-84 -- int(text[1:], 16) accepts 0x/space/plus prefixes."""
        # None of these is a 6-digit #RRGGBB literal.
        suspicious = ("#0x1234", "# 12345", "#+12345")
        accepted = [value for value in suspicious if is_valid_hex(value)]
        self.assertEqual(
            accepted, [],
            "is_valid_hex() claims to validate '#RRGGBB' but accepted %r; "
            "int(text[1:], 16) is too permissive (config.py:81)."
            % (accepted,)
        )

    def test_FIND_003_due_sort_must_order_by_real_time(self):
        """storage.py:109 -- sorts due_at as a raw string, not a parsed datetime."""
        store = MemoStore()
        store.add(Memo(id="june", due_at="2024/6/1",
                       created_at="2024-01-01 00:00:00"))
        store.add(Memo(id="dec", due_at="2024/12/1",
                       created_at="2024-01-02 00:00:00"))
        order = [memo.id for memo in store.sorted_by("due_at")]
        self.assertEqual(
            order, ["june", "dec"],
            "sorted_by('due_at') promises earliest-due-first, but sorts the raw "
            "string: '2024/12/1' < '2024/6/1' lexicographically (storage.py:109). "
            "parse_time() already accepts slash/non-zero-padded formats, so these "
            "values are legal user input."
        )


if __name__ == "__main__":
    unittest.main()

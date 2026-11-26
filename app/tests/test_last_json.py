# -*- coding: utf-8 -*-
"""last_json() 的回归测试。

背景：流水线脚本用 `echo()`（`json.dumps(..., indent=2)`）输出多行 JSON，
旧实现逐行 `json.loads` 永远失败，导致 `order_dir` / `order_id` 走回退值（R8）。
"""

import json
import os
import sys
import tempfile
import unittest

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import server  # noqa: E402


def echo_like(obj):
    """与脚本侧 echo() 完全一致的输出格式。"""
    return json.dumps(obj, ensure_ascii=False, indent=2)


class LastJsonTests(unittest.TestCase):
    def test_multiline_indented(self):
        """echo() 的实际格式：多行、两空格缩进。"""
        payload = {
            "order_dir": "F:/x/orders/小雪",
            "name": "小雪",
            "order_id": "20261125-120000",
            "brief": "brief.json",
            "archived_brief": None,
            "budget_limit": 10,
            "orders": 1,
        }
        self.assertEqual(server.last_json(echo_like(payload)), payload)

    def test_single_line_still_works(self):
        self.assertEqual(server.last_json('{"a": 1}'), {"a": 1})

    def test_surrounded_by_logs(self):
        p1 = {"step": 1}
        p2 = {"order_id": "20261125-120000"}
        text = "start\n%s\nworking...\n%s\ntrailing text\n" % (
            echo_like(p1), echo_like(p2))
        self.assertEqual(server.last_json(text), p2)

    def test_returns_last_of_multiple(self):
        text = echo_like({"n": 1}) + "\n" + echo_like({"n": 2}) + "\n" + echo_like({"n": 3})
        self.assertEqual(server.last_json(text), {"n": 3})

    def test_braces_inside_strings_ignored(self):
        """字符串里的花括号不是行首，不能当候选起点。"""
        payload = {"prompt": 'say {"a": 1} and {b}', "ok": True}
        self.assertEqual(server.last_json(echo_like(payload)), payload)

    def test_multiline_string_value(self):
        """字符串里的换行被转义成 \\n，不会制造新行。"""
        payload = {"prompt": "第一行\n第二行 {\n第三行", "ok": True}
        text = echo_like(payload)
        self.assertNotIn("第二行 {\n第三行", text)  # 确认确实被转义
        self.assertEqual(server.last_json(text), payload)

    def test_broken_block_falls_back_to_earlier_valid(self):
        good = {"ok": True}
        text = echo_like(good) + "\n{\n  this is not json\n"
        self.assertEqual(server.last_json(text), good)

    def test_only_invalid_returns_none(self):
        self.assertIsNone(server.last_json("{\n  broken\n}\n"))
        self.assertIsNone(server.last_json("no json here"))

    def test_empty_returns_none(self):
        self.assertIsNone(server.last_json(""))
        self.assertIsNone(server.last_json(None))

    def test_non_dict_documents_ignored(self):
        """契约是返回 dict；数组或标量不当作结果。"""
        self.assertIsNone(server.last_json('[1, 2, 3]'))
        self.assertIsNone(server.last_json('"just a string"'))


class RoundsStdoutContractTests(unittest.TestCase):
    """确定性地验证：rounds.py 的真实 stdout 能被 last_json 解析。

    上面那些用例用手写文本，即使实现退化也可能碰巧通过；
    这一条直接跑子进程，是 R8 修复的硬证据。
    """

    def setUp(self):
        self._saved = (server.CONFIG, server.CUSTOM_CHAR_DIR,
                       server.CUSTOM_STYLE_DIR)
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        server.CONFIG = dict(server.CONFIG)
        server.CONFIG["orders_root"] = os.path.join(self.tmp, "orders")
        server.CUSTOM_CHAR_DIR = os.path.join(self.tmp, "custom", "characters")
        server.CUSTOM_STYLE_DIR = os.path.join(self.tmp, "custom", "styles")

    def tearDown(self):
        (server.CONFIG, server.CUSTOM_CHAR_DIR,
         server.CUSTOM_STYLE_DIR) = self._saved
        self._tmp.cleanup()

    def test_rounds_new_order_stdout_is_parseable(self):
        rc, out, err = server.run_py(
            os.path.join(server.SCRIPTS, "rounds.py"),
            "--new-order", "--root", self.tmp, "--character", "小雪")
        self.assertEqual(rc, 0, "rounds.py 失败：%s" % err)
        info = server.last_json(out)
        self.assertIsNotNone(info, "rounds.py 的 stdout 无法解析：\n%s" % out)
        self.assertEqual(info.get("name"), "小雪")
        self.assertRegex(str(info.get("order_id")), r"^\d{8}-\d{6}")
        self.assertTrue(os.path.isdir(info["order_dir"]))

    def test_server_order_id_comes_from_rounds(self):
        """建单后 brief.order_id 必须等于 rounds.json 的 order_id。"""
        res = server.create_order({
            "character_id": "小雪", "character_desc": "银白长发",
            "style_id": "tv-anime-cel", "gender": "female",
            "count": 1, "budget": 10,
        })
        self.assertTrue(res.get("ok"), "建单失败：%r" % (res,))
        order_dir = os.path.join(self.tmp, "orders", "小雪")
        with open(os.path.join(order_dir, "brief.json"), encoding="utf-8") as fh:
            brief = json.load(fh)
        with open(os.path.join(order_dir, "rounds.json"), encoding="utf-8") as fh:
            ledger = json.load(fh)
        self.assertEqual(brief["order_id"], ledger["order_id"])
        self.assertEqual(brief["ordered_at"], ledger["ordered_at"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

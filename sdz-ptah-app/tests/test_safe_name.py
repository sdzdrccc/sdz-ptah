# -*- coding: utf-8 -*-
"""safe_name() 的回归测试：中文、路径穿越、保留设备名、幂等、真实建目录。

运行（在 sdz-ptah-app 目录下）：
    py -3 -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import server  # noqa: E402


class SafeNameAsciiTests(unittest.TestCase):
    def test_existing_slugs_unchanged(self):
        """既有订单目录名必须保持逐字节不变，否则历史数据会失联。"""
        for name in ("satoshi", "fox-armor", "tv-anime-cel", "mychar",
                     "char_01", "a.b.c", "x+y"):
            self.assertEqual(server.safe_name(name), name)

    def test_case_preserved(self):
        self.assertEqual(server.safe_name("Satoshi"), "Satoshi")


class SafeNameChineseTests(unittest.TestCase):
    def test_pure_chinese(self):
        self.assertEqual(server.safe_name("小雪"), "小雪")

    def test_chinese_with_separator(self):
        self.assertEqual(server.safe_name("小智/赛璐璐"), "小智-赛璐璐")

    def test_chinese_with_ideographic_full_stop(self):
        # 。U+3002 在 NFKC 下折成 ASCII 句点，再被首尾剥离
        self.assertEqual(server.safe_name("小雪。"), "小雪")

    def test_fullwidth_folds_to_ascii(self):
        self.assertEqual(server.safe_name("Ｑ雪"), "Q雪")

    def test_chinese_dir_really_creatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, server.safe_name("小雪"))
            os.makedirs(target)
            self.assertTrue(os.path.isdir(target))
            self.assertEqual(os.listdir(tmp), ["小雪"])


class SafeNameSecurityTests(unittest.TestCase):
    def test_parent_traversal(self):
        for raw in ("../etc", "..\\windows", "../../root", "a/../../b"):
            out = server.safe_name(raw)
            self.assertNotIn("/", out)
            self.assertNotIn("\\", out)
            self.assertNotIn(out, (".", ".."))

    def test_no_separators_ever(self):
        for raw in ("a/b", "a\\b", "/etc/passwd", "C:\\Windows", "a\x00b"):
            out = server.safe_name(raw)
            self.assertNotIn("/", out)
            self.assertNotIn("\\", out)

    def test_control_chars_removed(self):
        self.assertEqual(server.safe_name("a\x00b"), "a-b")
        self.assertEqual(server.safe_name("a\nb\tc"), "a-b-c")

    def test_empty_rejected(self):
        for raw in ("", "   ", None, "...", "..", "///"):
            with self.assertRaises(ValueError):
                server.safe_name(raw)

    def test_reserved_device_names(self):
        self.assertEqual(server.safe_name("CON"), "_CON")
        self.assertEqual(server.safe_name("con.txt"), "_con.txt")
        self.assertEqual(server.safe_name("nul"), "_nul")
        self.assertEqual(server.safe_name("COM1"), "_COM1")
        self.assertEqual(server.safe_name("LPT9"), "_LPT9")


class SafeNameShapeTests(unittest.TestCase):
    def test_trailing_dot_and_space_stripped(self):
        self.assertEqual(server.safe_name("小雪. "), "小雪")
        self.assertEqual(server.safe_name(" name "), "name")

    def test_dash_collapsed(self):
        self.assertEqual(server.safe_name("a///b"), "a-b")

    def test_length_clamped(self):
        out = server.safe_name("雪" * 200)
        self.assertLessEqual(len(out), server.NAME_MAX_LEN)
        self.assertTrue(out)

    def test_idempotent(self):
        samples = ["小雪", "小智/赛璐璐", "CON", "con.txt", "_CON",
                   "a\x00b", "Ｑ雪", "雪" * 200, "satoshi", "a.b.c"]
        for raw in samples:
            once = server.safe_name(raw)
            twice = server.safe_name(once)
            self.assertEqual(once, twice, "非幂等：%r → %r → %r" % (raw, once, twice))


if __name__ == "__main__":
    unittest.main(verbosity=2)

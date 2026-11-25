# -*- coding: utf-8 -*-
"""端到端验证：中文角色名可以真实建单。

覆盖 safe_name 修复的实际目的——不再在 create_order 处抛 ValueError。
全部读写都重定向到临时目录，不触碰仓库里的 avatar-orders / custom。

运行：
    py -3 -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import server  # noqa: E402


class CreateOrderChineseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._saved = {
            "CONFIG": server.CONFIG,
            "CUSTOM_CHAR_DIR": server.CUSTOM_CHAR_DIR,
            "CUSTOM_STYLE_DIR": server.CUSTOM_STYLE_DIR,
        }
        server.CONFIG = dict(server.CONFIG)
        server.CONFIG["orders_root"] = os.path.join(self.tmp, "orders")
        server.CUSTOM_CHAR_DIR = os.path.join(self.tmp, "custom", "characters")
        server.CUSTOM_STYLE_DIR = os.path.join(self.tmp, "custom", "styles")

    def tearDown(self):
        server.CONFIG = self._saved["CONFIG"]
        server.CUSTOM_CHAR_DIR = self._saved["CUSTOM_CHAR_DIR"]
        server.CUSTOM_STYLE_DIR = self._saved["CUSTOM_STYLE_DIR"]
        self._tmp.cleanup()

    def test_chinese_character_creates_order(self):
        res = server.create_order({
            "character_id": "小雪",
            "character_name": "小雪",
            "character_desc": "银白长发、蓝瞳、清冷感",
            "character_desc_en": "long silver hair, blue eyes, aloof",
            "style_id": "tv-anime-cel",
            "gender": "female",
            "count": 1,
            "budget": 10,
        })
        self.assertTrue(res.get("ok"), "建单失败：%r" % (res,))

        order_dir = os.path.join(self.tmp, "orders", "小雪")
        self.assertTrue(os.path.isdir(order_dir), "中文订单目录未创建")
        self.assertEqual(res.get("order"), "小雪")

        brief_path = os.path.join(order_dir, "brief.json")
        self.assertTrue(os.path.isfile(brief_path))
        with open(brief_path, "r", encoding="utf-8") as fh:
            brief = json.load(fh)
        self.assertEqual(brief["character_id"], "小雪")
        self.assertIn("银白", brief["character_desc"])

        # rounds.py 也要能在这个中文目录里写台账
        self.assertTrue(os.path.isfile(os.path.join(order_dir, "rounds.json")))

        with open(os.path.join(order_dir, "rounds.json"), "r", encoding="utf-8") as fh:
            ledger = json.load(fh)
        self.assertEqual(brief["order_id"], ledger["order_id"],
                         "brief 与 rounds.json 的 order_id 不一致（R8）")
        self.assertEqual(brief["ordered_at"], ledger["ordered_at"],
                         "brief 与 rounds.json 的 ordered_at 不一致（R8）")

    def test_chinese_character_with_separator_normalized(self):
        """带分隔符的中文名折叠成单段，仍可建单。"""
        res = server.create_order({
            "character_id": "小智/赛璐璐",
            "character_desc": "黑发红帽",
            "style_id": "tv-anime-cel",
            "gender": "male",
            "count": 1,
            "budget": 10,
        })
        self.assertTrue(res.get("ok"), "建单失败：%r" % (res,))
        self.assertEqual(res.get("order"), "小智-赛璐璐")
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "orders", "小智-赛璐璐")))


if __name__ == "__main__":
    unittest.main(verbosity=2)

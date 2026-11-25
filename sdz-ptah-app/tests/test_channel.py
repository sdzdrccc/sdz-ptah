# -*- coding: utf-8 -*-
"""双通道设计 4.1 / 4.2：brief.channel 与 confirm.source 映射。

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

SCRIPTS = os.path.join(os.path.dirname(APP_DIR), "skills", "sdz-ptah-avatar", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import confirm_order  # noqa: E402


class SourceMapTests(unittest.TestCase):
    def test_source_map_values(self):
        self.assertEqual(confirm_order.SOURCE_MAP, {
            "chat": "chat_message",
            "card": "confirmation_card",
            "web": "web_form",
            "agent": "agent_tool_call",
        })


class ChannelTests(unittest.TestCase):
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

    def _brief(self, name):
        path = os.path.join(self.tmp, "orders", name, "brief.json")
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _make(self, character, **extra):
        payload = {"character_id": character, "character_desc": "黑发",
                   "style_id": "tv-anime-cel", "gender": "male",
                   "count": 1, "budget": 10}
        payload.update(extra)
        return server.create_order(payload)

    def test_default_channel_is_web(self):
        res = self._make("chan-default")
        self.assertTrue(res.get("ok"), res)
        brief = self._brief(res["order"])
        self.assertEqual(brief["channel"], "web")
        self.assertEqual(brief["channel_source"], "server.py:create_order")

    def test_agent_channel_recorded(self):
        res = self._make("chan-agent", channel="agent")
        self.assertTrue(res.get("ok"), res)
        self.assertEqual(self._brief(res["order"])["channel"], "agent")

    def test_invalid_channel_rejected(self):
        with self.assertRaises(ValueError):
            self._make("chan-bad", channel="telepathy")


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""simple_input（极简出图入口的输入校验）与 generate.parse_render_size。

运行：
    py -3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import simple_input  # noqa: E402


class ParseSizeTests(unittest.TestCase):
    def test_default(self):
        self.assertEqual(simple_input.parse_size(None), (1024, 1024))
        self.assertEqual(simple_input.parse_size(""), (1024, 1024))

    def test_formats(self):
        self.assertEqual(simple_input.parse_size("1024x1536"), (1024, 1536))
        self.assertEqual(simple_input.parse_size("1536X1024"), (1536, 1024))
        self.assertEqual(simple_input.parse_size(" 1024 × 1024 "), (1024, 1024))
        self.assertEqual(simple_input.parse_size([768, 1024]), (768, 1024))

    def test_bad_values(self):
        for bad in ("big", "1024", "0x0", "100x100", "4096x4096", "1025x1024", "1024x2056", "axb"):
            with self.assertRaises(ValueError, msg=bad):
                simple_input.parse_size(bad)

    def test_all_listed_choices_are_valid(self):
        for item in simple_input.SIZE_CHOICES:
            simple_input.parse_size(item["id"])
        self.assertTrue(any(i["verified"] for i in simple_input.SIZE_CHOICES))
        self.assertEqual(simple_input.SIZE_CHOICES[0]["id"], "1024x1024")


class CleanTests(unittest.TestCase):
    def ok(self, **kw):
        body = {"positive": "黑发少年，赛璐璐上色"}
        body.update(kw)
        return simple_input.clean(body)

    def test_defaults(self):
        c = self.ok()
        self.assertEqual((c["name"], c["count"], c["width"], c["height"]), ("avatar", 1, 1024, 1024))
        self.assertEqual((c["channel"], c["dry_run"], c["egress_consent"]), ("web", False, False))
        self.assertIsNone(c["reference"])
        self.assertEqual(c["negative"], "")

    def test_values_are_stripped_and_kept(self):
        c = self.ok(name="  goku ", positive="  A  ", negative="  B ", count="3", size="1024x1536",
                    dry_run=True, egress_consent=True, channel="Agent",
                    reference={"name": "r.png", "data": "data:image/png;base64,AAAA"})
        self.assertEqual((c["name"], c["positive"], c["negative"], c["count"]), ("goku", "A", "B", 3))
        self.assertEqual((c["width"], c["height"], c["channel"]), (1024, 1536, "agent"))
        self.assertTrue(c["dry_run"] and c["egress_consent"])
        self.assertEqual(c["reference"]["name"], "r.png")

    def test_empty_positive_rejected(self):
        for body in ({}, {"positive": "   "}, {"positive": None}):
            with self.assertRaises(ValueError):
                simple_input.clean(body)

    def test_count_bounds(self):
        for bad in (0, -1, 11, "x"):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.ok(count=bad)
        self.assertEqual(self.ok(count=10)["count"], 10)

    def test_bad_channel_size_reference(self):
        with self.assertRaises(ValueError):
            self.ok(channel="telepathy")
        with self.assertRaises(ValueError):
            self.ok(size="huge")
        with self.assertRaises(ValueError):
            self.ok(reference="not-a-dict")

    def test_reference_without_data_is_treated_as_missing(self):
        self.assertIsNone(self.ok(reference={"name": "r.png", "data": ""})["reference"])

    def test_prompt_length_limit(self):
        with self.assertRaises(ValueError):
            self.ok(positive="x" * (simple_input.MAX_PROMPT_CHARS + 1))
        with self.assertRaises(ValueError):
            self.ok(negative="x" * (simple_input.MAX_PROMPT_CHARS + 1))

    def test_preset_and_text2img_fields(self):
        c = self.ok()
        self.assertEqual((c["preset_id"], c["text2img_consent"]), ("", False))
        c = self.ok(preset_id=" modelscope-qwen-2-1 ", text2img_consent=True)
        self.assertEqual((c["preset_id"], c["text2img_consent"]), ("modelscope-qwen-2-1", True))

    def test_model_params(self):
        c = self.ok(model=" modelscope-qwen-2-1 ", seed="123", steps="30", guidance="4.5")
        self.assertEqual((c["model"], c["seed"], c["steps"], c["guidance"]),
                         ("modelscope-qwen-2-1", 123, 30, 4.5))
        c = self.ok(seed=-1, steps="", guidance=None)
        self.assertEqual((c["seed"], c["steps"], c["guidance"]), (None, None, None))
        for kw in ({"seed": -2}, {"seed": 2 ** 32}, {"seed": "x"}, {"steps": 0}, {"steps": 101},
                   {"steps": "a"}, {"guidance": -1}, {"guidance": 21}, {"guidance": "z"}):
            with self.assertRaises(ValueError, msg=str(kw)):
                self.ok(**kw)

    def test_use_reference_switch(self):
        ref = {"name": "r.png", "data": "data:image/png;base64,AAAA"}
        self.assertTrue(self.ok(reference=ref)["use_reference"])
        self.assertFalse(self.ok()["use_reference"])
        c = self.ok(reference=ref, use_reference=False)
        self.assertFalse(c["use_reference"])
        self.assertIsNone(c["reference"], "关掉开关时不得保留参考图")

    def test_ratio_size(self):
        rs = simple_input.ratio_size
        self.assertEqual(rs("1:1", 1024), (1024, 1024))
        self.assertEqual(rs("3:4", 1328), (1000, 1328))
        self.assertEqual(rs("16:9", 1024), (1024, 576))
        for r in simple_input.RATIOS:
            for edge in (1024, 1328):
                w, h = rs(r["id"], edge)
                simple_input.parse_size("%dx%d" % (w, h))
        with self.assertRaises(ValueError):
            rs("5:7", 1024)

    def test_alias_fields(self):
        c = simple_input.clean({"prompt": "P", "negative_prompt": "N", "character_id": "c1"})
        self.assertEqual((c["positive"], c["negative"], c["name"]), ("P", "N", "c1"))


class GenerateRenderSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scripts = os.path.join(os.path.dirname(APP_DIR), "skills", "sdz-ptah-avatar", "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import generate  # noqa: E402
        cls.generate = generate

    def test_default_and_values(self):
        p = self.generate.parse_render_size
        self.assertEqual(p(None), (1024, 1024))
        self.assertEqual(p("1024x1536"), (1024, 1536))
        self.assertEqual(p("1536×1024"), (1536, 1024))
        # 上限 4096 的依据：MiniMax 官方 API 面积上限 2000 万像素，4096² 合法。
        # 魔搭网关只到 2048，由各后端的 capabilities.max_edge 单独把关（见 ②）。
        self.assertEqual(p("4096x4096"), (4096, 4096))

    def test_invalid(self):
        for bad in ("big", "100x100", "1025x1024", "8192x8192"):
            with self.assertRaises(ValueError, msg=bad):
                self.generate.parse_render_size(bad)

    def test_to_request_honors_width_height_and_legacy_plans(self):
        base = {"order_id": "o", "prefix": "p", "start_index": 1, "prompt_zh": "a", "prompt_en": "",
                "negative": None, "references": [], "seed": 1, "size": 1024, "count": 1,
                "egress_consent": False}
        legacy = self.generate.to_request(dict(base))
        self.assertEqual((legacy.width, legacy.height), (1024, 1024))
        new = self.generate.to_request(dict(base, width=1024, height=1536))
        self.assertEqual((new.width, new.height), (1024, 1536))


if __name__ == "__main__":
    unittest.main(verbosity=2)

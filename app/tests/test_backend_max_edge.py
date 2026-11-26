# -*- coding: utf-8 -*-
"""逐后端的画布单边上限（capabilities.max_edge）必须真的挡住超限请求。

背景：全局 RENDER_MAX 抬到 4096 后，只支持 2048 的后端（魔搭网关）
如果不单独设限，会让请求直达服务端才被 HTTP 400 打回，白跑一次排队。
max_edge 就是为了在 validate() 阶段拦住它。

运行：py -3 -m unittest discover -s tests
"""

import os
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_ROOT = os.path.join(os.path.dirname(APP_DIR), "skills", "sdz-ptah-avatar")
if SKILL_ROOT not in sys.path:
    sys.path.insert(0, SKILL_ROOT)

from backends import registry  # noqa: E402
from backends.base import CapabilityError, GenerationRequest  # noqa: E402


class MaxEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = registry.load()

    def _adapter(self, backend_id):
        return registry.get(self.doc, backend_id)

    def test_modelscope_rejects_4096(self):
        a = self._adapter("modelscope-qwen-image-2.1")
        self.assertEqual(a.caps.max_edge, 2048)
        req = GenerationRequest(order_id="t", prefix="t", start_index=1, width=4096, height=4096)
        with self.assertRaises(CapabilityError) as ctx:
            a.check(req)
        self.assertIn("单边上限 2048", str(ctx.exception))

    def test_modelscope_allows_2048(self):
        a = self._adapter("modelscope-qwen-image-2.1")
        req = GenerationRequest(order_id="t", prefix="t", start_index=1, width=2048, height=2048)
        self.assertEqual(a.check(req), [])

    def test_minimax_official_allows_4096(self):
        a = self._adapter("minimax-official-h3")
        self.assertEqual(a.caps.max_edge, 4096)
        req = GenerationRequest(order_id="t", prefix="t", start_index=1, width=4096, height=4096)
        self.assertEqual(a.check(req), [])

    def test_minimax_official_declares_no_unsupported_params(self):
        """官方 API 无 seed / 负向词 / steps / guidance，注册表不能虚标。"""
        caps = self._adapter("minimax-official-h3").caps
        self.assertFalse(caps.seed)
        self.assertFalse(caps.negative_prompt)
        self.assertFalse(caps.steps)
        self.assertFalse(caps.guidance)
        self.assertEqual(caps.reference_max, 16)

    def test_every_registered_engine_has_adapter(self):
        """引擎清单只有一份真相源：出现未实现适配器的引擎应当为零。"""
        missing = sorted({b.get("engine") for b in self.doc.get("backends", [])
                          if b.get("engine") not in registry.ENGINES})
        self.assertEqual(missing, [], "未实现适配器的引擎：%s" % missing)


if __name__ == "__main__":
    unittest.main()

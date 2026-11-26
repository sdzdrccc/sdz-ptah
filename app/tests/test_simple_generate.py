# -*- coding: utf-8 -*-
"""/api/generate 端到端（真实 HTTP 服务 + 真实脚本子进程，只做 dry-run，不访问魔搭）。

依赖：Pillow、项目脚本可运行、默认方案对应的后端在注册表里存在。
测试里把 MODELSCOPE_TOKEN 设成假值，仅为通过"方案可用"检查；dry-run 不会发网络请求。

运行：
    py -3 -m unittest discover -s tests -v
"""
import base64
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from PIL import Image

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import orderlock  # noqa: E402
import server  # noqa: E402


def png_data_url(size=(640, 480), color=(180, 120, 90)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class GenerateEndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._saved = {k: getattr(server, k) for k in
                       ("CONFIG", "CUSTOM_CHAR_DIR", "CUSTOM_STYLE_DIR", "UPLOAD_DIR")}
        self._env = os.environ.get("MODELSCOPE_TOKEN")
        os.environ["MODELSCOPE_TOKEN"] = "test-token-not-real"
        server.CONFIG = dict(server.CONFIG)
        server.CONFIG["orders_root"] = os.path.join(self.tmp, "orders")
        os.makedirs(server.CONFIG["orders_root"], exist_ok=True)
        server.CUSTOM_CHAR_DIR = os.path.join(self.tmp, "custom", "characters")
        server.CUSTOM_STYLE_DIR = os.path.join(self.tmp, "custom", "styles")
        server.UPLOAD_DIR = os.path.join(self.tmp, "uploads")
        os.makedirs(server.UPLOAD_DIR, exist_ok=True)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)
        for k, v in self._saved.items():
            setattr(server, k, v)
        if self._env is None:
            os.environ.pop("MODELSCOPE_TOKEN", None)
        else:
            os.environ["MODELSCOPE_TOKEN"] = self._env
        self._tmp.cleanup()

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
            conn.request(method, path, raw, {"Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def body(self, **kw):
        b = {"name": "simple-a", "positive": "黑发少年，平涂赛璐璐，正脸", "negative": "文字，水印",
             "count": 2, "size": "1024x1536", "egress_consent": True, "dry_run": True,
             "reference": {"name": "ref.png", "data": png_data_url()}}
        b.update(kw)
        return b

    def orders(self):
        return sorted(os.listdir(server.CONFIG["orders_root"]))

    def load(self, order, name):
        with open(os.path.join(server.CONFIG["orders_root"], order, name), "r", encoding="utf-8") as fh:
            return json.load(fh)

    # ------------------------------------------------------------------ 成功路径
    def test_meta_exposes_simple_info(self):
        status, data = self.request("GET", "/api/meta")
        self.assertEqual(status, 200, data)
        meta = data["meta"]
        self.assertEqual(meta["simple"]["preset_id"], "modelscope-qwen-edit-2511")
        self.assertTrue(meta["simple"]["selectable"], meta["simple"])
        self.assertTrue(meta["simple"]["needs_reference"])
        self.assertEqual(meta["sizes"][0]["id"], "1024x1024")
        self.assertEqual(meta["max_count"], 10)

    def test_dry_run_creates_manual_order_and_confirms(self):
        status, data = self.request("POST", "/api/generate", self.body())
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"], data)
        order = data["order"]
        brief = self.load(order, "brief.json")
        self.assertEqual(brief["prompt_source"], "manual")
        self.assertEqual(brief["render_size"], "1024x1536")
        self.assertEqual(brief["channel"], "web")
        self.assertEqual(brief["prompts_input"]["positive"], "黑发少年，平涂赛璐璐，正脸")
        self.assertEqual(brief["prompts_input"]["negative"], "文字，水印")
        self.assertEqual(brief["confirm"]["source"], "web_form")
        self.assertTrue(brief["confirm"]["confirmed"])
        self.assertEqual(brief["generation"]["preset_id"], "modelscope-qwen-edit-2511")
        self.assertTrue(brief["generation"]["egress_consent"])
        self.assertEqual(len(brief["generation"]["reference_images"]), 1)
        prompt = self.load(order, "prompt-input.json")
        self.assertEqual(prompt["prompt_zh"], "黑发少年，平涂赛璐璐，正脸")
        self.assertEqual(prompt["negative"], "文字，水印")
        self.assertIn("1024x1536", data["stdout"], "dry-run 的计划里应带 render_size")
        self.assertIn("calls_needed", data["stdout"])
        # dry-run 不得产生图片或记账
        files = os.listdir(os.path.join(server.CONFIG["orders_root"], order))
        self.assertFalse([f for f in files if "-src." in f], files)
        self.assertFalse(os.path.exists(os.path.join(server.CONFIG["orders_root"], order,
                                                     orderlock.LOCK_NAME)), "锁必须释放")

    def test_empty_negative_is_omitted_from_prompt_file(self):
        status, data = self.request("POST", "/api/generate", self.body(name="simple-b", negative=""))
        self.assertEqual(status, 200, data)
        self.assertNotIn("negative", self.load(data["order"], "prompt-input.json"))

    def test_chinese_name_works(self):
        status, data = self.request("POST", "/api/generate", self.body(name="孙悟空"))
        self.assertEqual(status, 200, data)
        self.assertEqual(data["order"], "孙悟空")

    # ------------------------------------------------------------------ 拒绝路径（不得留下订单）
    def test_missing_reference_is_400_and_no_order(self):
        status, data = self.request("POST", "/api/generate", self.body(reference=None))
        self.assertEqual(status, 400, data)
        self.assertEqual(data["code"], "bad_input")
        self.assertEqual(self.orders(), [])

    def test_no_egress_consent_is_403_and_no_order(self):
        status, data = self.request("POST", "/api/generate", self.body(egress_consent=False))
        self.assertEqual(status, 403, data)
        self.assertEqual(data["code"], "no_egress_consent")
        self.assertEqual(self.orders(), [])

    def test_unknown_preset_is_400_and_no_order(self):
        status, data = self.request("POST", "/api/generate", self.body(preset_id="no-such-preset"))
        self.assertEqual(status, 400, data)
        self.assertEqual(data["code"], "bad_input")
        self.assertEqual(self.orders(), [])

    def test_explicit_preset_is_recorded(self):
        status, data = self.request("POST", "/api/generate",
                                    self.body(name="simple-p", preset_id="modelscope-qwen-2-1"))
        self.assertEqual(status, 200, data)
        self.assertEqual(self.load(data["order"], "brief.json")["generation"]["preset_id"],
                         "modelscope-qwen-2-1")

    def test_presets_endpoint_lists_all_presets(self):
        status, data = self.request("GET", "/api/presets")
        self.assertEqual(status, 200, data)
        ids = [p["id"] for p in data["presets"]]
        self.assertGreaterEqual(len(ids), 5, ids)
        self.assertIn("modelscope-qwen-edit-2511", ids)

    def test_models_endpoint(self):
        status, data = self.request("GET", "/api/models")
        self.assertEqual(status, 200, data)
        rows = {m["id"]: m for m in data["models"]}
        self.assertIn("modelscope-qwen-edit-2511", rows)
        self.assertTrue(rows["modelscope-qwen-edit-2511"]["selectable"], rows["modelscope-qwen-edit-2511"])
        mm = rows.get("modelscope-minimax-h3-image")
        if mm:
            self.assertFalse(mm["caps"]["negative_prompt"])
            self.assertFalse(mm["caps"]["steps"])
            self.assertEqual(mm["credits_per_image"], 2)
        for m in data["models"]:
            if m["engine"] == "host":
                self.assertFalse(m["selectable"], "宿主工具后端在网页里不可选")

    def test_model_with_params_is_recorded(self):
        status, data = self.request("POST", "/api/generate", self.body(
            name="simple-m", model="modelscope-qwen-edit-2511", steps=28, guidance=4.5, seed=77))
        self.assertEqual(status, 200, data)
        brief = self.load(data["order"], "brief.json")
        gen = brief["generation"]
        self.assertEqual(gen["preset_id"], "model:modelscope-qwen-edit-2511")
        self.assertEqual(gen["stages"][0]["backend"], "modelscope-qwen-edit-2511")
        self.assertEqual(gen["stages"][0].get("steps"), 28)
        self.assertEqual(gen["stages"][0].get("guidance"), 4.5)
        self.assertEqual(gen["seed"], 77)
        self.assertEqual(brief["ui_settings"]["model"], "modelscope-qwen-edit-2511")

    def test_unsupported_params_are_dropped(self):
        status, data = self.request("POST", "/api/generate", self.body(
            name="simple-mm", model="modelscope-minimax-h3-image", steps=28, guidance=4.5))
        self.assertEqual(status, 200, data)
        st = self.load(data["order"], "brief.json")["generation"]["stages"][0]
        self.assertNotIn("steps", st)
        self.assertNotIn("guidance", st)

    def test_reference_switch_off_means_text2img(self):
        status, data = self.request("POST", "/api/generate", self.body(
            name="simple-t", model="modelscope-qwen-image-2.1", use_reference=False,
            egress_consent=False))
        self.assertEqual(status, 200, data)
        brief = self.load(data["order"], "brief.json")
        self.assertEqual(brief["generation"]["mode"], "text2img")
        self.assertTrue(brief["generation"]["text2img_consent"])
        self.assertEqual(brief["generation"]["reference_images"], [])

    def test_unknown_model_is_400(self):
        status, data = self.request("POST", "/api/generate", self.body(model="no-such-model"))
        self.assertEqual(status, 400, data)
        self.assertEqual(self.orders(), [])

    def test_empty_positive_is_400(self):
        status, data = self.request("POST", "/api/generate", self.body(positive="  "))
        self.assertEqual(status, 400, data)
        self.assertEqual(self.orders(), [])

    def test_bad_size_and_count_are_400(self):
        for kw in ({"size": "9999x9999"}, {"count": 99}, {"count": 0}):
            status, data = self.request("POST", "/api/generate", self.body(**kw))
            self.assertEqual(status, 400, (kw, data))
        self.assertEqual(self.orders(), [])

    def test_broken_image_is_400_and_no_order(self):
        bad = {"name": "x.png", "data": "data:image/png;base64," +
               base64.b64encode(b"not an image").decode("ascii")}
        status, data = self.request("POST", "/api/generate", self.body(reference=bad))
        self.assertEqual(status, 400, data)
        self.assertEqual(self.orders(), [])

    def test_token_missing_gives_503(self):
        os.environ.pop("MODELSCOPE_TOKEN", None)
        os.environ.pop("MODELSCOPE_API_KEY", None)
        status, data = self.request("POST", "/api/generate", self.body())
        self.assertEqual(status, 503, data)
        self.assertEqual(data["code"], "preset_unavailable")
        self.assertEqual(self.orders(), [])

    def test_busy_name_returns_409(self):
        path = os.path.join(server.CONFIG["orders_root"], "simple-busy")
        with orderlock.order_lock(path, "agent"):
            status, data = self.request("POST", "/api/generate", self.body(name="simple-busy"))
        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "order_busy")

    def test_prompt_with_ellipsis_is_rejected_by_audit(self):
        # generate.py 的 audit_prompts 对手写提示词同样生效：含省略号视为被截断
        status, data = self.request("POST", "/api/generate",
                                    self.body(name="simple-c", positive="黑发少年…"))
        self.assertFalse(data["ok"], data)
        self.assertEqual(status, 400, data)
        self.assertIn("省略号", data.get("error", "") + data.get("stdout", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)

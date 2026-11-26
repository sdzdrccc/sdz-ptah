# -*- coding: utf-8 -*-
"""api_errors 映射表 + 经真实 HTTP 服务验证 409 / 400 状态码。

关键词表来自 generate.py / confirm_order.py 的实际输出；脚本改了提示文字，这里会先失败。

运行：
    py -3 -m unittest discover -s tests -v
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import api_errors  # noqa: E402
import orderlock  # noqa: E402
import server  # noqa: E402


class DescribeTests(unittest.TestCase):
    def check(self, rc, out="", err="", status=None, code=None, path=None):
        d = api_errors.describe(rc, out, err, path)
        self.assertEqual(d.get("http_status"), status, d)
        self.assertEqual(d.get("code"), code, d)
        return d

    def test_success_is_empty(self):
        self.assertEqual(api_errors.describe(0, "ok", ""), {})

    def test_exit2_bad_input(self):
        self.check(2, "brief not found: x", "", 400, "bad_input")

    def test_exit2_pending_exists(self):
        msg = ("有未完成的生成（r1，第 1 轮，编号 1 起 2 张）：用 --resume 继续，"
               "或 --discard-pending 放弃。")
        self.check(2, msg, "", 409, "pending_exists")

    def test_exit2_confirmation_rejected(self):
        self.check(2, "confirmation rejected: 需要明确包含「确认 / 生成 / confirm」",
                   "", 403, "not_confirmed")

    def test_exit2_egress_in_reference_errors(self):
        self.check(2, "reference invalid: 参考图会发往远端，需要 --egress-consent", "",
                   403, "no_egress_consent")

    def test_exit4_not_confirmed(self):
        self.check(4, "未确认：brief.json 缺少 confirm.confirmed=true，先运行 confirm_order.py",
                   "", 403, "not_confirmed")

    def test_exit4_capability(self):
        self.check(4, "generation capability: 后端不支持 reference", "", 403, "capability")

    def test_exit4_egress(self):
        self.check(4, "generation capability: 缺少外发同意 egress_consent", "",
                   403, "no_egress_consent")

    def test_exit5_budget(self):
        d = self.check(5, "预算不足：本次需要 3 次调用", "", 402, "budget_exhausted")
        self.assertFalse(d["retryable"])

    def test_exit8_backend_retryable(self):
        d = self.check(8, "后端调用失败：timeout", "", 502, "backend_error")
        self.assertTrue(d["retryable"])

    def test_exit9_needs_host(self):
        self.check(9, "", "", 501, "needs_host")

    def test_unknown_exit(self):
        self.check(7, "", "Traceback ...", 500, "script_error")

    def test_error_message_uses_tail_and_is_bounded(self):
        d = api_errors.describe(8, "", "\n".join("line %d" % i for i in range(50)) + "x" * 2000)
        self.assertLessEqual(len(d["error"]), 400)
        self.assertTrue(d["error"])

    def test_describe_messages(self):
        d = api_errors.describe_messages(["该方案需要外发同意（egress）"])
        self.assertEqual((d["http_status"], d["code"]), (403, "no_egress_consent"))
        d = api_errors.describe_messages(["参考图数量不足"])
        self.assertEqual((d["http_status"], d["code"]), (400, "bad_input"))


class HttpStatusTests(unittest.TestCase):
    """起一个真实 HTTP 服务（端口 0），验证 _dispatch 的状态码。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._saved = {k: getattr(server, k) for k in
                       ("CONFIG", "CUSTOM_CHAR_DIR", "CUSTOM_STYLE_DIR")}
        server.CONFIG = dict(server.CONFIG)
        server.CONFIG["orders_root"] = os.path.join(self.tmp, "orders")
        os.makedirs(server.CONFIG["orders_root"], exist_ok=True)
        server.CUSTOM_CHAR_DIR = os.path.join(self.tmp, "custom", "characters")
        server.CUSTOM_STYLE_DIR = os.path.join(self.tmp, "custom", "styles")
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
        self._tmp.cleanup()

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, raw, {"Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def new_order(self, character, channel="web"):
        status, data = self.post("/api/orders", {
            "character_id": character, "character_desc": "黑发", "style_id": "tv-anime-cel",
            "gender": "male", "count": 1, "budget": 10, "channel": channel})
        self.assertEqual(status, 200, data)
        return data["order"]

    def test_create_order_ok_and_lock_released(self):
        order = self.new_order("http-a", "agent")
        path = os.path.join(server.CONFIG["orders_root"], order)
        self.assertFalse(os.path.exists(os.path.join(path, orderlock.LOCK_NAME)))

    def test_create_order_busy_returns_409(self):
        path = os.path.join(server.CONFIG["orders_root"], "http-b")
        with orderlock.order_lock(path, "agent"):
            status, data = self.post("/api/orders", {
                "character_id": "http-b", "gender": "male", "channel": "web"})
        self.assertEqual(status, 409, data)
        self.assertEqual(data["code"], "order_busy")
        self.assertEqual(data["holder_channel"], "agent")
        self.assertTrue(data["retryable"])
        self.assertNotIn("http_status", data)

    def test_confirm_and_generate_busy_return_409(self):
        order = self.new_order("http-c")
        path = os.path.join(server.CONFIG["orders_root"], order)
        with orderlock.order_lock(path, "web"):
            for action in ("confirm", "generate"):
                status, data = self.post("/api/orders/%s/%s" % (order, action), {})
                self.assertEqual(status, 409, (action, data))
                self.assertEqual(data["code"], "order_busy")

    def test_busy_does_not_touch_order_files(self):
        order = self.new_order("http-d")
        path = os.path.join(server.CONFIG["orders_root"], order)
        with open(os.path.join(path, "brief.json"), "rb") as fh:
            before = fh.read()
        with orderlock.order_lock(path, "web"):
            self.post("/api/orders", {"character_id": order, "gender": "male"})
        with open(os.path.join(path, "brief.json"), "rb") as fh:
            self.assertEqual(fh.read(), before, "被拒绝的建单不得改动已有 brief")

    def test_unknown_preset_is_400(self):
        order = self.new_order("http-e")
        status, data = self.post("/api/orders/%s/confirm" % order, {"preset_id": "nope"})
        self.assertEqual(status, 400, data)
        self.assertEqual(data["code"], "bad_input")

    def test_generate_unconfirmed_is_403(self):
        order = self.new_order("http-f")
        status, data = self.post("/api/orders/%s/generate" % order, {"dry_run": True})
        self.assertEqual(status, 403, data)
        self.assertIn(data["code"], ("not_confirmed", "capability"))
        self.assertFalse(data["ok"])
        self.assertIn("error", data)  # 前端 api() 依赖 error 字段才不会抛异常

    def test_missing_order_is_400(self):
        status, data = self.post("/api/orders/does-not-exist/generate", {})
        self.assertEqual(status, 400, data)


if __name__ == "__main__":
    unittest.main(verbosity=2)

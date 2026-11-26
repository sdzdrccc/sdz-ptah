# -*- coding: utf-8 -*-
"""orderlock：互斥、陈旧锁接管、只删自己的锁。

运行：
    py -3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import threading
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import orderlock  # noqa: E402

DEAD_PID = 2 ** 31 - 7  # 不可能存在的进程号


class OrderLockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self._tmp.name, "角色甲")
        self.lock = os.path.join(self.dir, orderlock.LOCK_NAME)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_lock(self, **kw):
        os.makedirs(self.dir, exist_ok=True)
        body = {"pid": os.getpid(), "channel": "web", "token": "other",
                "acquired_at": "2026-11-25T00:00:00", "acquired_ts": 1000.0}
        body.update(kw)
        with open(self.lock, "w", encoding="utf-8") as fh:
            json.dump(body, fh)

    def test_acquire_and_release(self):
        with orderlock.order_lock(self.dir, "web") as body:
            self.assertTrue(os.path.isfile(self.lock))
            self.assertEqual(body["channel"], "web")
            self.assertEqual(body["pid"], os.getpid())
        self.assertFalse(os.path.exists(self.lock))

    def test_released_on_exception(self):
        with self.assertRaises(RuntimeError):
            with orderlock.order_lock(self.dir, "agent"):
                raise RuntimeError("boom")
        self.assertFalse(os.path.exists(self.lock))

    def test_busy_when_held(self):
        with orderlock.order_lock(self.dir, "web"):
            with self.assertRaises(orderlock.OrderBusy) as ctx:
                with orderlock.order_lock(self.dir, "agent"):
                    self.fail("不应获取到锁")
            self.assertEqual(ctx.exception.holder.get("channel"), "web")
            # 第二个请求失败后，第一个人的锁必须还在
            self.assertTrue(os.path.isfile(self.lock))
        self.assertFalse(os.path.exists(self.lock))

    def test_busy_across_threads(self):
        results = []
        started, done = threading.Event(), threading.Event()

        def holder():
            with orderlock.order_lock(self.dir, "web"):
                started.set()
                done.wait(5)

        t = threading.Thread(target=holder)
        t.start()
        self.assertTrue(started.wait(5))
        try:
            with orderlock.order_lock(self.dir, "agent"):
                results.append("acquired")
        except orderlock.OrderBusy:
            results.append("busy")
        finally:
            done.set()
            t.join(5)
        self.assertEqual(results, ["busy"])

    def test_different_orders_do_not_block(self):
        other = os.path.join(self._tmp.name, "角色乙")
        with orderlock.order_lock(self.dir, "web"):
            with orderlock.order_lock(other, "agent"):
                self.assertTrue(os.path.isfile(os.path.join(other, orderlock.LOCK_NAME)))

    def test_dead_pid_is_taken_over(self):
        self._write_lock(pid=DEAD_PID, acquired_ts=1e12)  # 时间戳在未来：排除 TTL 干扰
        seen = []
        with orderlock.order_lock(self.dir, "agent",
                                  on_takeover=lambda h, why: seen.append(why)):
            pass
        self.assertEqual(seen, ["dead_pid"])

    def test_ttl_expired_is_taken_over(self):
        self._write_lock(pid=os.getpid(), acquired_ts=1000.0)
        seen = []
        with orderlock.order_lock(self.dir, "agent", ttl_s=60, now=lambda: 1000.0 + 61,
                                  on_takeover=lambda h, why: seen.append(why)):
            pass
        self.assertEqual(seen, ["ttl"])

    def test_within_ttl_and_alive_is_busy(self):
        self._write_lock(pid=os.getpid(), acquired_ts=1000.0)
        with self.assertRaises(orderlock.OrderBusy):
            with orderlock.order_lock(self.dir, "agent", ttl_s=60, now=lambda: 1000.0 + 59):
                pass

    def test_unreadable_lock_is_taken_over(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(self.lock, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        seen = []
        with orderlock.order_lock(self.dir, "web", on_takeover=lambda h, why: seen.append(why)):
            pass
        self.assertEqual(seen, ["unreadable"])

    def test_release_does_not_delete_foreign_lock(self):
        with orderlock.order_lock(self.dir, "web"):
            # 模拟：锁被别人（TTL 接管）换成了另一个 token
            self._write_lock(token="takeover-owner")
        self.assertTrue(os.path.isfile(self.lock), "不应删除接管者的锁")

    def test_pid_alive(self):
        self.assertTrue(orderlock.pid_alive(os.getpid()))
        self.assertFalse(orderlock.pid_alive(DEAD_PID))
        self.assertFalse(orderlock.pid_alive(0))
        self.assertFalse(orderlock.pid_alive("abc"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

# -*- coding: utf-8 -*-
"""订单互斥锁（双通道设计第 6 节）：同一角色目录同一时刻只允许一个写操作。

实现：`<订单目录>/.active-lock` + `O_CREAT|O_EXCL` 原子创建，不依赖第三方库。

规则：
- 无锁文件 → 获取成功；
- 有锁、持有进程存活且未超时 → 抛 OrderBusy（HTTP 409 order_busy）；
- 有锁、持有进程已不存在 → 视为陈旧锁，接管；
- 有锁、超过 ttl_s（默认 30 分钟）→ 接管；
- 退出 with 块时只删除"自己的"锁（按 token 比对），避免误删接管者的锁。

已知不覆盖：多个 server.py 实例跨机共享网络盘时 O_EXCL 语义不保证；
绕过 HTTP 直接运行 generate.py 时锁不生效（设计决策 A 的理由之一）。
"""
import contextlib
import json
import os
import time
import uuid
from datetime import datetime

LOCK_NAME = ".active-lock"
DEFAULT_TTL_S = 1800


class OrderBusy(Exception):
    """订单目录被另一个操作占用。holder 为锁文件内容（可能为空 dict）。"""

    def __init__(self, order, holder):
        self.order = order
        self.holder = holder or {}
        super().__init__("订单 %s 正被占用（%s 通道，自 %s）" % (
            order, self.holder.get("channel") or "未知",
            self.holder.get("acquired_at") or "未知时间"))


def pid_alive(pid):
    """进程是否存活。判断不了时保守返回 True（宁可多等，不抢活锁）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                # 5 = 拒绝访问：进程存在但无权限
                return ctypes.GetLastError() == 5
            kernel32.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _read_holder(lock_path):
    try:
        with open(lock_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _try_create(lock_path, channel, token):
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(lock_path, flags, 0o644)
    body = {"pid": os.getpid(), "channel": channel, "token": token,
            "acquired_at": datetime.now().isoformat(timespec="seconds"),
            "acquired_ts": time.time()}
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(body, fh, ensure_ascii=False)
    return body


def _is_stale(holder, ttl_s, now):
    """返回 (是否陈旧, 原因)。无法解析的锁内容按陈旧处理（写到一半就崩溃的情况）。"""
    if not holder or "pid" not in holder:
        return True, "unreadable"
    ts = holder.get("acquired_ts")
    if isinstance(ts, (int, float)) and now - ts > ttl_s:
        return True, "ttl"
    if not pid_alive(holder.get("pid")):
        return True, "dead_pid"
    return False, ""


@contextlib.contextmanager
def order_lock(order_path, channel, ttl_s=DEFAULT_TTL_S, now=time.time, on_takeover=None):
    """with order_lock(path, "web"): ... ；被占用时抛 OrderBusy。"""
    # 记录目录是否为本次创建：校验失败的写操作不应留下空订单目录。
    dir_created_here = not os.path.isdir(order_path)
    os.makedirs(order_path, exist_ok=True)
    lock_path = os.path.join(order_path, LOCK_NAME)
    token = uuid.uuid4().hex
    body = None
    for _attempt in range(3):
        try:
            body = _try_create(lock_path, channel, token)
            break
        except FileExistsError:
            holder = _read_holder(lock_path)
            stale, why = _is_stale(holder, ttl_s, now())
            if not stale:
                raise OrderBusy(os.path.basename(order_path), holder)
            if on_takeover:
                on_takeover(holder, why)
            try:
                os.remove(lock_path)
            except FileNotFoundError:
                pass
            except OSError:
                raise OrderBusy(os.path.basename(order_path), holder)
    if body is None:
        raise OrderBusy(os.path.basename(order_path), _read_holder(lock_path))
    try:
        yield body
    finally:
        holder = _read_holder(lock_path)
        if holder.get("token") == token:
            try:
                os.remove(lock_path)
            except OSError:
                pass
        # 若目录是本次加锁时才创建、且调用后仍为空（写操作校验失败未建单），
        # 回收这个空目录，避免留下幽灵订单。非空目录（已建单）保持不动。
        if dir_created_here:
            try:
                if not os.listdir(order_path):
                    os.rmdir(order_path)
            except OSError:
                pass

# -*- coding: utf-8 -*-
"""脚本退出码 → HTTP 状态与 code 的映射（双通道设计 5.3）。

只做映射，不改任何脚本行为。关键词来自 generate.py / confirm_order.py 的实际输出，
脚本改了提示文字时，这里的 test_api_errors.py 会先失败。
"""
import os

PENDING_FILE = "generation-pending.json"

# 脚本输出里的关键词
_KW_NOT_CONFIRMED = ("未确认", "confirmation rejected")
_KW_EGRESS = ("egress", "外发")
_KW_PENDING = ("有未完成的生成",)


def _has(text, words):
    low = (text or "").lower()
    return any(w.lower() in low for w in words)


def _tail(out, err, limit=400):
    text = (err or "").strip() or (out or "").strip()
    lines = [x for x in text.splitlines() if x.strip()]
    return " | ".join(lines[-3:])[:limit] if lines else ""


def describe(rc, out="", err="", order_path=None):
    """返回 {"code","http_status","error","retryable"}；rc==0 返回空 dict。"""
    if rc == 0:
        return {}
    text = "%s\n%s" % (out or "", err or "")
    msg = _tail(out, err) or "脚本退出码 %s" % rc

    if rc == 2:
        pending = _has(text, _KW_PENDING) or bool(
            order_path and os.path.isfile(os.path.join(order_path, PENDING_FILE))
            and _has(text, ("--resume", "discard")))
        if pending:
            code, status = "pending_exists", 409
        elif _has(text, _KW_NOT_CONFIRMED):
            code, status = "not_confirmed", 403
        elif _has(text, _KW_EGRESS):
            code, status = "no_egress_consent", 403
        else:
            code, status = "bad_input", 400
    elif rc == 4:
        if _has(text, _KW_NOT_CONFIRMED):
            code = "not_confirmed"
        elif _has(text, _KW_EGRESS):
            code = "no_egress_consent"
        else:
            code = "capability"
        status = 403
    elif rc == 5:
        code, status = "budget_exhausted", 402
    elif rc == 8:
        code, status = "backend_error", 502
    elif rc == 9:
        code, status = "needs_host", 501
    else:
        code, status = "script_error", 500
    return {"code": code, "http_status": status, "error": msg,
            "retryable": code in ("backend_error", "order_busy")}


def describe_messages(messages):
    """前置校验（validate_selection）给出的错误列表，按 rc=2 同规则归类。"""
    text = "; ".join(str(m) for m in (messages or []))
    return describe(2, text, "")


def busy(exc):
    """OrderBusy → 409 order_busy 响应体。"""
    holder = getattr(exc, "holder", {}) or {}
    return {"ok": False, "code": "order_busy", "http_status": 409,
            "error": str(exc), "retryable": True,
            "holder_channel": holder.get("channel"),
            "acquired_at": holder.get("acquired_at")}

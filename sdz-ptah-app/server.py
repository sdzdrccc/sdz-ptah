#!/usr/bin/env python3
"""sdz-ptah 本地网页控制台：只读调用现有流水线脚本，不另造生成逻辑。

只用 Python 标准库。启动：py -3 server.py
浏览器访问：http://127.0.0.1:8765/
"""
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)
WEB_DIR = os.path.join(APP_DIR, "web")
UPLOAD_DIR = os.path.join(APP_DIR, "uploads")
CUSTOM_CHAR_DIR = os.path.join(APP_DIR, "custom", "characters")
CUSTOM_STYLE_DIR = os.path.join(APP_DIR, "custom", "styles")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8765,
    "open_browser": True,
    "orders_root": os.path.join(PROJECT_ROOT, "avatar-orders"),
    "skill_root": os.path.join(PROJECT_ROOT, "skills", "sdz-ptah-avatar"),
}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
DEFAULT_SKIN_SPEC = {
    "base": {"hex": "#E9C1AB"},
    "shadow": {"hex": "#C99A93"},
    "highlight_cap": {"hex": "#F2DCCB"},
}


def load_json(path, fallback=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return fallback


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_PATH, {}) or {})
    os.makedirs(cfg["orders_root"], exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return cfg


CONFIG = load_config()
SKILL_ROOT = CONFIG["skill_root"]
SCRIPTS = os.path.join(SKILL_ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
from list_presets import resolve_presets, validate_selection  # noqa: E402


def orders_root():
    return CONFIG["orders_root"]


def safe_name(value):
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
    if not text or text in (".", ".."):
        raise ValueError("名称不合法：%r" % value)
    return text


def order_dir(name):
    path = os.path.join(orders_root(), safe_name(name))
    if not os.path.isdir(path):
        raise ValueError("订单目录不存在：%s" % name)
    return path


def run_py(script, *args):
    cmd = [sys.executable, script] + [str(a) for a in args]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, cwd=SKILL_ROOT, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def last_json(text):
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def custom_character_path(cid):
    return os.path.join(CUSTOM_CHAR_DIR, safe_name(cid) + ".json")


def custom_style_path(sid):
    return os.path.join(CUSTOM_STYLE_DIR, safe_name(sid) + ".json")


def builtin_character_path(cid):
    return os.path.join(SKILL_ROOT, "references", "characters", safe_name(cid) + ".json")


def builtin_style_path(sid):
    return os.path.join(SKILL_ROOT, "references", "styles", safe_name(sid) + ".json")


def find_character_path(cid):
    custom = custom_character_path(cid)
    if os.path.isfile(custom):
        return custom
    builtin = builtin_character_path(cid)
    return builtin if os.path.isfile(builtin) else None


def find_style_path(sid):
    custom = custom_style_path(sid)
    if os.path.isfile(custom):
        return custom
    builtin = builtin_style_path(sid)
    return builtin if os.path.isfile(builtin) else None


def ensure_character(cid, payload, gender):
    """已有档案直接用；有自由描述则在 custom/ 生成一份，不改 skill 原件。"""
    cid = safe_name(cid)
    base_path = find_character_path(cid)
    base = load_json(base_path, {}) or {} if base_path else {}
    desc = str(payload.get("desc") or "").strip()
    desc_en = str(payload.get("desc_en") or "").strip()
    name = str(payload.get("name") or "").strip()
    if base_path and not desc and not desc_en and not name:
        return base_path
    card = dict(base)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    card.update({
        "schema": "character-card/1.1",
        "character_id": cid,
        "display_name": name or base.get("display_name") or cid,
        "version": "web-%s" % stamp,
        "gender": gender,
        "prompt_block": desc or base.get("prompt_block") or cid,
        "prompt_block_en": desc_en or base.get("prompt_block_en") or desc or cid,
        "skin_spec": base.get("skin_spec") or DEFAULT_SKIN_SPEC,
        "face": base.get("face") or {},
    })
    target = custom_character_path(cid)
    save_json(target, card)
    return target


def ensure_style(sid, payload):
    """已有风格直接用；自由描述生成轻量风格档案，画布与评分门槛沿用默认。"""
    sid = safe_name(sid)
    base_path = find_style_path(sid)
    base = load_json(base_path, {}) or {} if base_path else {}
    desc = str(payload.get("desc") or "").strip()
    name = str(payload.get("name") or "").strip()
    if base_path and not desc and not name:
        return base_path
    default = load_json(builtin_style_path("tv-anime-cel"), {}) or {}
    style = dict(base)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    style.update({
        "schema": "style-profile/1.0",
        "style_id": sid,
        "display_name": name or base.get("display_name") or sid,
        "version": "web-%s" % stamp,
        "status": "active",
        "prompt_anchor": desc or base.get("prompt_anchor") or sid,
        "prompt_forbidden": base.get("prompt_forbidden") or default.get("prompt_forbidden") or [],
        "canvas": base.get("canvas") or default.get("canvas") or {},
        "gate": base.get("gate") or default.get("gate") or {},
    })
    target = custom_style_path(sid)
    save_json(target, style)
    return target


def profile_options(folder, id_key):
    rows = []
    if not os.path.isdir(folder):
        return rows
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".json"):
            continue
        doc = load_json(os.path.join(folder, name), {}) or {}
        pid = doc.get(id_key) or os.path.splitext(name)[0]
        rows.append({
            "id": pid,
            "label": doc.get("display_name") or doc.get("name") or pid,
            "custom": os.path.abspath(folder).startswith(os.path.abspath(APP_DIR)),
        })
    return rows


def read_reference_file(path, base_dir):
    text = str(path or "").strip()
    if not text:
        return None
    if os.path.isabs(text):
        return text if os.path.isfile(text) else None
    candidate = os.path.join(base_dir, safe_name(os.path.basename(text)))
    return candidate if os.path.isfile(candidate) else None


def file_entries(folder):
    out = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isfile(path) and name.lower().endswith(IMAGE_EXTS):
            out.append({"name": name, "size": os.path.getsize(path)})
    return out


def budget_summary(brief, rounds):
    current = (brief or {}).get("order_id") or (rounds or {}).get("order_id")
    used = 0
    for item in ((rounds or {}).get("rounds") or []):
        if current and item.get("order_id") not in (None, current):
            continue
        used += int(item.get("calls") or item.get("count") or 0)
    limit = int((brief or {}).get("budget") or (rounds or {}).get("budget_limit") or 10)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


def list_orders():
    rows = []
    for name in sorted(os.listdir(orders_root())):
        path = os.path.join(orders_root(), name)
        if not os.path.isdir(path):
            continue
        brief = load_json(os.path.join(path, "brief.json"), {}) or {}
        rounds = load_json(os.path.join(path, "rounds.json"), {}) or {}
        status = load_json(os.path.join(path, "status.json"), {}) or {}
        gen = brief.get("generation") or {}
        rows.append({
            "name": name,
            "order_id": brief.get("order_id") or rounds.get("order_id") or "",
            "character_id": brief.get("character_id") or name,
            "confirmed": (brief.get("confirm") or {}).get("confirmed") is True,
            "preset_id": gen.get("preset_id"),
            "backend": gen.get("backend"),
            "stages": ["%s:%s" % (s.get("stage"), s.get("backend"))
                       for s in (gen.get("stages") or []) if isinstance(s, dict)],
            "state": status.get("state") or "idle",
            "phase": status.get("phase") or "",
            "budget": budget_summary(brief, rounds),
            "files": len(file_entries(path)),
            "updated_at": status.get("updated_at"),
        })
    return rows


def order_detail(name):
    path = order_dir(name)
    brief = load_json(os.path.join(path, "brief.json"), {}) or {}
    rounds = load_json(os.path.join(path, "rounds.json"), {}) or {}
    status = load_json(os.path.join(path, "status.json"), {}) or {}
    generation = load_json(os.path.join(path, "generation.json"), {}) or {}
    pending = load_json(os.path.join(path, "generation-pending.json"), None)
    prompts = load_json(os.path.join(path, "prompts.json"), {}) or {}
    gen = brief.get("generation") or {}
    return {
        "name": name,
        "path": path,
        "order_id": brief.get("order_id") or rounds.get("order_id") or "",
        "brief": brief,
        "status": status,
        "rounds": rounds,
        "generation": generation,
        "pending": pending,
        "prompt_count": len(prompts.get("prompts") or []),
        "files": file_entries(path),
        "budget": budget_summary(brief, rounds),
        "confirmed": (brief.get("confirm") or {}).get("confirmed") is True,
        "stages": gen.get("stages") or [],
    }


def merge_options(builtin, custom):
    rows = list(builtin)
    seen = {x.get("id") for x in rows}
    for row in custom:
        if row.get("id") in seen:
            continue
        rows.append(row)
    return rows


def meta_payload():
    characters = merge_options(
        profile_options(os.path.join(SKILL_ROOT, "references", "characters"), "character_id"),
        profile_options(CUSTOM_CHAR_DIR, "character_id"),
    )
    styles = merge_options(
        profile_options(os.path.join(SKILL_ROOT, "references", "styles"), "style_id"),
        profile_options(CUSTOM_STYLE_DIR, "style_id"),
    )
    preset_doc, _ = resolve_presets()
    token_set = any(os.environ.get(k) for k in ("MODELSCOPE_TOKEN", "MODELSCOPE_API_KEY"))
    return {
        "project_root": PROJECT_ROOT,
        "skill_root": SKILL_ROOT,
        "orders_root": orders_root(),
        "characters": characters,
        "styles": styles,
        "preset_version": preset_doc.get("version"),
        "modelscope_token_set": token_set,
        "python": sys.version.split()[0],
    }


def create_order(payload):
    character = safe_name(payload.get("character_id") or payload.get("character") or "satoshi")
    style_id = safe_name(payload.get("style_id") or "tv-anime-cel")
    gender = payload.get("gender") or "unspecified"
    if gender not in ("male", "female", "unspecified"):
        raise ValueError("gender 只能是 male / female / unspecified")
    angle = payload.get("angle") or ""
    if angle and angle not in ("front", "profile_left", "profile_right"):
        raise ValueError("angle 不合法")
    count = max(1, min(20, int(payload.get("count") or 1)))
    budget = max(1, min(200, int(payload.get("budget") or 10)))

    character_desc = str(payload.get("character_desc") or "").strip()
    character_desc_en = str(payload.get("character_desc_en") or "").strip()
    character_name = str(payload.get("character_name") or "").strip()
    style_desc = str(payload.get("style_desc") or "").strip()
    style_name = str(payload.get("style_name") or "").strip()

    card_path = ensure_character(character, {
        "name": character_name, "desc": character_desc, "desc_en": character_desc_en,
    }, gender)
    style_path = ensure_style(style_id, {"name": style_name, "desc": style_desc})
    card = load_json(card_path, {}) or {}

    rc, out, err = run_py(os.path.join(SCRIPTS, "rounds.py"), "--new-order",
                          "--root", orders_root(), "--character", character)
    if rc != 0:
        return {"ok": False, "rc": rc, "stdout": out, "stderr": err}
    info = last_json(out) or {}
    path = info.get("order_dir") or os.path.join(orders_root(), character)
    oid = info.get("order_id") or datetime.now().strftime("%Y%m%d-%H%M%S")
    brief = {
        "order_id": oid,
        "ordered_at": info.get("ordered_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "character_id": character,
        "character": card.get("display_name") or character_name or character,
        "character_path": card_path,
        "character_desc": character_desc,
        "style_id": style_id,
        "style_path": style_path,
        "style_desc": style_desc,
        "gender": gender,
        "gender_source": "user" if payload.get("gender") else "unspecified",
        "angle": angle,
        "angle_source": "user" if angle else "default",
        "count": count,
        "budget": budget,
        "size": "1024",
        "formats": ["1024"],
    }
    save_json(os.path.join(path, "brief.json"), brief)
    return {"ok": True, "order": os.path.basename(path), "detail": order_detail(os.path.basename(path)),
            "stdout": out, "stderr": err}


def upload_reference(name, payload):
    path = order_dir(name)
    raw_name = safe_name(os.path.basename(payload.get("name") or "reference.png"))
    ext = os.path.splitext(raw_name)[1].lower()
    if ext not in IMAGE_EXTS:
        raise ValueError("只支持 png / jpg / jpeg / webp")
    data_url = str(payload.get("data") or "")
    if "," not in data_url or not data_url.startswith("data:"):
        raise ValueError("需要 data URL 格式的文件内容")
    raw = base64.b64decode(data_url.split(",", 1)[1], validate=False)
    if not raw:
        raise ValueError("文件为空")
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("参考图超过 20MB，请先压缩")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    saved_name = "%s-upload-%s%s" % (os.path.basename(path), stamp, ext)
    saved_path = os.path.join(UPLOAD_DIR, saved_name)
    with open(saved_path, "wb") as fh:
        fh.write(raw)
    return {"ok": True, "name": saved_name, "path": saved_path,
            "size": len(raw), "order": os.path.basename(path)}


def confirm_order(name, payload):
    path = order_dir(name)
    brief = load_json(os.path.join(path, "brief.json"), {}) or {}
    preset_id = payload.get("preset_id")
    preset_doc, presets = resolve_presets()
    preset = next((p for p in presets if p.get("id") == preset_id), None)
    if preset is None:
        raise ValueError("未知方案：%s" % preset_id)

    refs = []
    for item in payload.get("references") or []:
        ref = read_reference_file(item, path)
        if ref and ref not in refs:
            refs.append(ref)

    egress = bool(payload.get("egress_consent"))
    text2img = bool(payload.get("text2img_consent"))
    commercial = bool(payload.get("commercial"))
    errors, warnings = validate_selection(preset, refs=len(refs),
                                          egress_consent=egress,
                                          text2img_consent=text2img,
                                          commercial=commercial)
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings}

    budget = int(payload.get("budget") or brief.get("budget") or 10)
    args = ["--brief", os.path.join(path, "brief.json"),
            "--confirm", str(payload.get("confirm_text") or "确认生成"),
            "--source", "card", "--budget", str(budget),
            "--mode", preset.get("mode") or "reference",
            "--stages", preset.get("stages_arg"),
            "--backends", os.path.join(SKILL_ROOT, "references", "backends.v2.json")]
    if payload.get("gender"):
        args += ["--gender", str(payload["gender"])]
    if payload.get("angle"):
        args += ["--angle", str(payload["angle"])]
    if refs:
        args += ["--reference", ",".join(refs)]
    if egress:
        args.append("--egress-consent")
    if text2img:
        args.append("--text2img-consent")
    if payload.get("seed") not in (None, ""):
        args += ["--seed", str(int(payload["seed"]))]

    rc, out, err = run_py(os.path.join(SCRIPTS, "confirm_order.py"), *args)
    result = {"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err,
              "warnings": warnings, "errors": [] if rc == 0 else [err or out]}
    if rc == 0:
        brief = load_json(os.path.join(path, "brief.json"), {}) or {}
        gen = brief.setdefault("generation", {})
        gen["preset_id"] = preset.get("id")
        gen["preset_version"] = preset_doc.get("version")
        gen["preset_snapshot"] = {
            "label": preset.get("label"),
            "stages_arg": preset.get("stages_arg"),
            "contract_sha256": preset.get("contract_sha256"),
            "warnings": warnings,
            "notes": preset.get("notes") or [],
        }
        save_json(os.path.join(path, "brief.json"), brief)
        result["detail"] = order_detail(name)
    return result


def generate_order(name, payload):
    path = order_dir(name)
    extra = []
    compile_out = ""
    if payload.get("resume"):
        extra.append("--resume")
    elif payload.get("discard_pending"):
        extra.append("--discard-pending")
    else:
        brief = load_json(os.path.join(path, "brief.json"), {}) or {}
        gen = brief.get("generation") or {}
        backend = gen.get("backend")
        stages = gen.get("stages") or []
        if not backend and stages:
            backend = stages[0].get("backend")
        backend = backend or "agnes-aigc-img2img"
        character = safe_name(brief.get("character_id") or os.path.basename(path))
        style_id = safe_name(brief.get("style_id") or "tv-anime-cel")
        card = brief.get("character_path") or find_character_path(character)
        style = brief.get("style_path") or find_style_path(style_id)
        if not card or not os.path.isfile(card):
            raise ValueError("角色卡不存在：%s" % card)
        if not style or not os.path.isfile(style):
            style = os.path.join(SKILL_ROOT, "references", "styles", "tv-anime-cel.json")
        prompt_file = os.path.join(path, "prompts.json")
        cp_args = ["--character", card, "--style", style, "--backend", backend,
                   "--angle", brief.get("angle") or "front",
                   "--mode", gen.get("mode") or "reference",
                   "--out", prompt_file]
        crc, cPout, cperr = run_py(os.path.join(SCRIPTS, "compile_prompt.py"), *cp_args)
        compile_out = cPout
        if crc != 0:
            return {"ok": False, "stage": "compile", "rc": crc,
                    "stdout": cPout, "stderr": cperr}
        extra += ["--prompt-file", prompt_file]
        if payload.get("count"):
            extra += ["--count", str(int(payload["count"]))]
    if payload.get("dry_run"):
        extra.append("--dry-run")
    if payload.get("allow_over_budget"):
        extra.append("--allow-over-budget")

    rc, out, err = run_py(os.path.join(SCRIPTS, "generate.py"),
                          "--order", path, *extra)
    return {"ok": rc == 0, "rc": rc, "stdout": compile_out + out, "stderr": err,
            "detail": order_detail(name)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[web] " + (fmt % args))

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return json.loads(raw or "{}")

    def _static(self, path):
        rel = unquote(path).lstrip("/") or "index.html"
        target = os.path.abspath(os.path.join(WEB_DIR, rel))
        if os.path.commonpath([WEB_DIR, target]) != WEB_DIR or not os.path.isfile(target):
            self._json({"ok": False, "error": "not found"}, 404)
            return
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        with open(target, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name, filename):
        try:
            path = os.path.join(order_dir(name), safe_name(os.path.basename(unquote(filename))))
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
            return
        if not os.path.isfile(path):
            self._json({"ok": False, "error": "file not found"}, 404)
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method):
        path = urlparse(self.path).path
        try:
            if method == "GET":
                if path == "/api/meta":
                    return self._json({"ok": True, "meta": meta_payload()})
                if path == "/api/presets":
                    doc, presets = resolve_presets()
                    return self._json({"ok": True, "version": doc.get("version"),
                                       "presets": presets})
                if path == "/api/orders":
                    return self._json({"ok": True, "orders": list_orders()})
                m = re.match(r"^/api/orders/([^/]+)/file/(.+)$", path)
                if m:
                    return self._file(unquote(m.group(1)), m.group(2))
                m = re.match(r"^/api/orders/([^/]+)$", path)
                if m:
                    return self._json({"ok": True, "detail": order_detail(unquote(m.group(1)))})
                return self._static(path)

            payload = self._read_json()
            if path == "/api/orders":
                return self._json(create_order(payload))
            m = re.match(r"^/api/orders/([^/]+)/upload-reference$", path)
            if m:
                return self._json(upload_reference(unquote(m.group(1)), payload))
            m = re.match(r"^/api/orders/([^/]+)/confirm$", path)
            if m:
                return self._json(confirm_order(unquote(m.group(1)), payload))
            m = re.match(r"^/api/orders/([^/]+)/generate$", path)
            if m:
                return self._json(generate_order(unquote(m.group(1)), payload))
            return self._json({"ok": False, "error": "not found"}, 404)
        except ValueError as exc:
            return self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._json({"ok": False, "error": repr(exc)}, 500)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def main():
    host = CONFIG.get("host") or "127.0.0.1"
    port = int(CONFIG.get("port") or 8765)
    url = "http://%s:%d/" % (host, port)
    if CONFIG.get("open_browser"):
        threading.Timer(1.0, webbrowser.open, args=[url]).start()
    server = ThreadingHTTPServer((host, port), Handler)
    print("sdz-ptah web console: " + url)
    print("orders root: " + orders_root())
    print("Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

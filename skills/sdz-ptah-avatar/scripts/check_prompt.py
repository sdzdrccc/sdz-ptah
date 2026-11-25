#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词校验：agent 写完提示词后自查（references/prompt-writing-spec.md 第 8 节）。

只读、不改写提示词，也不调用任何服务。错误（E）必须改完才能提交；警告（W）要看一眼。

用法：
  py -3 check_prompt.py --prompt-file prompt-input.json --backend modelscope-qwen-image-2.1
  py -3 check_prompt.py --prompt-zh "……" --negative "……" --backend modelscope-minimax-h3-image
  加 --mode text2img 表示不用参考图；默认 reference。
  加 --json 只输出机器可读结果。

退出码：0 没有错误；2 有错误或参数错误。
"""
import argparse
import ast
import colorsys
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
GENERATE_PY = os.path.join(HERE, "generate.py")
DEFAULT_BACKENDS = os.path.join(SKILL, "references", "backends.v2.json")
SPEC_VERSION = "prompt-spec-v1"
MAX_CHARS = 4000

CANVAS_EN = "square canvas, no border, no frame, no circular mask, edge-to-edge"
QUALITY_WORDS = ("杰作", "最佳质量", "高清", "超清", "超高清", "8K", "8k", "4K", "4k", "16K",
                 "masterpiece", "best quality", "ultra detailed", "ultra-detailed",
                 "highres", "high res", "absurdres", "hyper detailed")
UNSUPPORTED_PARAMS = ("精确 90°", "精确90°", "同一 seed", "同一seed", "--ar", "--seed", "--v ")
REF_SENTENCE_KEYS = ("唯一权威",)


def banned_vague():
    """直接从 generate.py 读 BANNED_VAGUE，保证与生成端审计同一份词表。"""
    try:
        tree = ast.parse(open(GENERATE_PY, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "BANNED_VAGUE" for t in node.targets):
                return tuple(ast.literal_eval(node.value))
    except Exception:  # noqa: BLE001
        pass
    return ("小麦色", "暖调小麦色", "暖调皮肤", "日晒感", "日晒黑", "健康肤色",
            "自然肤色", "古铜色", "蜜色", "肤色偏暖", "偏黄的皮肤")


def load_backend(path, backend_id):
    if not backend_id:
        return None
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("后端注册表读不了：%s" % exc)
    for b in doc.get("backends") or []:
        if b.get("id") == backend_id:
            return b
    raise ValueError("未知后端 %r（不在 %s）" % (backend_id, path))


def hsv_of(hex_text):
    v = hex_text.lstrip("#")
    r, g, b = [int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    h, s, val = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s * 100.0, val * 100.0


ANCHOR_RE = re.compile(
    r"(#[0-9A-Fa-f]{6})\s*[（(]\s*色相\s*(\d+(?:\.\d+)?)\s*°\s*[、，,]\s*饱和度\s*(\d+(?:\.\d+)?)\s*%"
    r"\s*[、，,]\s*明度\s*(\d+(?:\.\d+)?)\s*%")
HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")


def check(prompt_zh, prompt_en, negative, backend, mode):
    errors, warnings = [], []
    caps = (backend or {}).get("capabilities") or {}
    engine = (backend or {}).get("engine")
    texts = [("prompt_zh", prompt_zh or ""), ("prompt_en", prompt_en or "")]
    positive_all = (prompt_zh or "") + "\n" + (prompt_en or "")

    if not (prompt_zh or prompt_en):
        errors.append("E0 正向提示词为空")

    # E1 模糊色词（与 generate.py 审计同一词表，且不分语境）
    vague = banned_vague()
    for key, text in texts:
        hits = [w for w in vague if w in text]
        if hits:
            errors.append("E1 %s 含模糊色词 %s：改成参考图实测的 hex + 色相/饱和度/明度（ref_palette.py）"
                          % (key, "、".join(hits)))
    # E2 省略号
    for key, text in texts:
        if "…" in text or "..." in text:
            errors.append("E2 %s 含省略号，会被视为截断；写全文" % key)
    # E3 长度
    for key, text in texts + [("negative", negative or "")]:
        if len(text) > MAX_CHARS:
            errors.append("E3 %s 超过 %d 字（%d）" % (key, MAX_CHARS, len(text)))
    # E4 画布条款
    if CANVAS_EN not in positive_all:
        errors.append("E4 缺少固定画布条款：%s" % CANVAS_EN)
    # E5 参考图固定句与尖括号
    if mode == "reference":
        if not any(k in positive_all for k in REF_SENTENCE_KEYS):
            errors.append("E5 参考图模式缺少固定句（“参考图是身份与画风的唯一权威，只改角度、构图与背景。”）")
        if "<image" in positive_all and engine != "host":
            errors.append("E5 非宿主后端不能写 <image 1>，模型会把尖括号当文字；改成“参考图”")
    elif "唯一权威" in positive_all:
        warnings.append("W5 文生图模式不用参考图固定句，删掉以免模型找不到参考图而乱画")
    # E6 负向通道与后端能力
    if backend is not None and negative and not caps.get("negative_prompt"):
        errors.append("E6 后端 %s 不支持负向提示词：negative 必须留空，禁止项改写成完整否定句"
                      "+正向替代并写进正向段" % backend.get("id"))
    # E7 质量词 / tag 堆叠
    low = positive_all.lower()
    hits = [w for w in QUALITY_WORDS if w.lower() in low]
    if hits:
        errors.append("E7 含质量堆叠词 %s：风格靠风格锚点写，不靠“高清/杰作”压" % "、".join(hits))
    # E8 权重语法 / 生成端不支持的参数
    if re.search(r"\([^()]{1,40}:\s*\d+(?:\.\d+)?\)", positive_all):
        errors.append("E8 含权重语法 (词:1.2)，在线生图接口不认，会被当成文字")
    hits = [w for w in UNSUPPORTED_PARAMS if w in positive_all]
    if hits:
        errors.append("E8 含生成端不支持的参数写法 %s" % "、".join(hits))
    # E9 手写 HSV 与 hex 不符（数值必须由 ref_palette.py 给出）
    for m in ANCHOR_RE.finditer(positive_all):
        hx, h, s, v = m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))
        rh, rs, rv = hsv_of(hx)
        dh = min(abs(rh - h), 360 - abs(rh - h))
        if dh > 2 or abs(rs - s) > 2 or abs(rv - v) > 2:
            errors.append("E9 %s 写的是 色相%g° 饱和度%g%% 明度%g%%，实际是 %d° %d%% %d%%：数值别手写"
                          % (hx, h, s, v, round(rh), round(rs), round(rv)))

    # ---- 警告
    anchors = len(ANCHOR_RE.findall(positive_all))
    # “不超过 #hex” 是高光上限写法，只写 hex 即可，不算缺数值
    hexes = len(HEX_RE.findall(re.sub(r"不超过\s*#[0-9A-Fa-f]{6}", "", positive_all)))
    if hexes == 0:
        warnings.append("W1 正向段没有任何 hex 色值：颜色没有锚定，生成端会自由发挥")
    elif hexes > anchors:
        warnings.append("W1 有 %d 个 hex 没跟“色相/饱和度/明度”数值（只有 %d 个完整锚点）" % (hexes - anchors, anchors))
    if ("皮肤" in positive_all or "skin" in low) and anchors < 2:
        warnings.append("W2 写了皮肤但完整锚点少于 2 个：皮肤至少要底色 + 主暗部（ref_palette.py --skin）")
    if not re.search(r"(留出|留).{0,12}\d+\s*%", prompt_zh or "") and not re.search(r"\d+\s*%.{0,20}(blank|empty|headroom)", low):
        warnings.append("W3 没写可量的头顶留白比例（例：画布顶部留出画布高度 8%–10% 的纯色空白）")
    if negative and caps.get("negative_prompt") and "不要" in negative:
        warnings.append("W4 负向词里不要写“不要”，直接列名词短语，用“、”连接")
    if negative:
        hits = [w for w in vague if w in negative]
        if hits:
            warnings.append("W6 负向词含模糊色词 %s：要排除的颜色也写具体，如“高饱和黄橙皮肤”" % "、".join(hits))
    if backend is not None and not caps.get("negative_prompt") and not negative:
        if "不要" not in (prompt_zh or "") and "no " not in low:
            warnings.append("W7 后端不支持负向词，但正向段里没有任何否定句：禁止项可能漏写")
    if prompt_zh and re.search(r"[A-Za-z]{4,}(?:\s+[A-Za-z]{3,}){4,}", prompt_zh.replace(CANVAS_EN, "")):
        warnings.append("W8 中文段里夹了大段英文：中文后端用中文写，只保留固定画布条款为英文")
    return errors, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description="提示词校验（只读）")
    ap.add_argument("--prompt-file", default=None, help="prompt-input.json / prompts.next.json")
    ap.add_argument("--prompt-zh", default=None)
    ap.add_argument("--prompt-en", default=None)
    ap.add_argument("--negative", default=None)
    ap.add_argument("--backend", default=None, help="后端 id（用来查负向词能力）")
    ap.add_argument("--backends", default=DEFAULT_BACKENDS)
    ap.add_argument("--mode", choices=("reference", "text2img"), default="reference")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args(argv)

    doc = {}
    if args.prompt_file:
        try:
            doc = json.load(open(args.prompt_file, encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print("提示词文件读不了：%s" % exc)
            return 2
    zh = args.prompt_zh if args.prompt_zh is not None else doc.get("prompt_zh")
    en = args.prompt_en if args.prompt_en is not None else doc.get("prompt_en")
    neg = args.negative if args.negative is not None else doc.get("negative")
    try:
        backend = load_backend(args.backends, args.backend)
    except ValueError as exc:
        print(str(exc))
        return 2
    if backend is None:
        print("提示：没给 --backend，跳过负向词能力检查（E6/W4/W7）")
    errors, warnings = check(zh, en, neg, backend, args.mode)
    result = {"spec": SPEC_VERSION, "backend": args.backend, "mode": args.mode,
              "ok": not errors, "errors": errors, "warnings": warnings,
              "chars": {"prompt_zh": len(zh or ""), "prompt_en": len(en or ""),
                        "negative": len(neg or "")}}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("提示词校验（%s）后端 %s · 模式 %s" % (SPEC_VERSION, args.backend or "-", args.mode))
        for e in errors:
            print("  ✗ " + e)
        for w in warnings:
            print("  ! " + w)
        print("结果：%s（错误 %d、警告 %d）" % ("可提交" if not errors else "需修改",
                                          len(errors), len(warnings)))
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())

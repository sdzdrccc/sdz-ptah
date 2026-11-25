#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参考图取色：给 agent 写提示词用（references/prompt-writing-spec.md 第 2 节）。

颜色一律从参考图实测，不凭印象写色名。本脚本只读图、不改图、不调用任何服务。

用法：
  # 按部位框选（像素坐标或 0–1 归一化坐标都行；可重复 --region）
  py -3 ref_palette.py --image ref.jpg ^
      --skin face=0.45,0.32,0.62,0.55 ^
      --region hair=0.38,0.25,0.46,0.45 --region cap_red=0.40,0.18,0.72,0.26 ^
      --json palette.json

  # 不知道怎么框时，先看主体整体色板（自动排除四角背景色）
  py -3 ref_palette.py --image ref.jpg --auto

  # 只看画面有效区域（自动去掉视频截图的黑边）
  默认开启；--keep-bars 可关闭

输出：每个部位的色簇（hex、色相°/饱和度%/明度%、Lab、占比）和一句可直接贴进提示词的中文。
--skin 部位额外给出三档锚点：底色 / 主暗部 / 深投影（可无）/ 高光上限。
HSV 换算与 compile_prompt.py 完全一致（colorsys，色相 0–360°，饱和度/明度 0–100%）。

退出码：0 成功；2 参数或图片错误。
"""
import argparse
import colorsys
import json
import sys

from PIL import Image

OUTLINE_V_MAX = 22.0      # 明度低于此视为描边/瞳孔黑，不参与皮肤三档
NEAR_WHITE_V_MIN = 97.0   # 皮肤区里接近纯白的像素视为眼白/高光，不参与底色


# ---------------------------------------------------------------- 颜色换算
def rgb_to_hex(rgb):
    return "#%02X%02X%02X" % tuple(int(round(c)) for c in rgb)


def rgb_to_hsv(rgb):
    r, g, b = [c / 255.0 for c in rgb]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s * 100.0, v * 100.0


def rgb_to_lab(rgb):
    def f(u):
        u /= 255.0
        return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4
    r, g, b = [f(c) for c in rgb]
    X = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    Y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    Z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b
    Xn, Yn, Zn = 0.95047, 1.0, 1.08883

    def g_(t):
        return t ** (1 / 3.0) if t > 0.008856 else (7.787 * t + 16 / 116.0)
    fx, fy, fz = g_(X / Xn), g_(Y / Yn), g_(Z / Zn)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a, b):
    return sum((x - y) ** 2 for x, y in zip(rgb_to_lab(a), rgb_to_lab(b))) ** 0.5


def describe(rgb):
    h, s, v = rgb_to_hsv(rgb)
    L, A, B = rgb_to_lab(rgb)
    return {"hex": rgb_to_hex(rgb), "hue": round(h), "sat": round(s), "val": round(v),
            "lab": [round(L, 1), round(A, 1), round(B, 1)]}


def anchor_text(rgb):
    d = describe(rgb)
    return "%s（色相 %d°、饱和度 %d%%、明度 %d%%）" % (d["hex"], d["hue"], d["sat"], d["val"])


# ---------------------------------------------------------------- 取区域
def content_box(img, thresh=12):
    """去掉视频截图左右/上下的黑边。"""
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    step = max(1, min(w, h) // 200)

    def col_mean(x):
        vals = [px[x, y] for y in range(0, h, step)]
        return sum(vals) / float(len(vals))

    def row_mean(y):
        vals = [px[x, y] for x in range(0, w, step)]
        return sum(vals) / float(len(vals))

    xs = [x for x in range(0, w, step) if col_mean(x) > thresh]
    ys = [y for y in range(0, h, step) if row_mean(y) > thresh]
    if not xs or not ys:
        return (0, 0, w, h)
    return (min(xs), min(ys), min(w, max(xs) + step), min(h, max(ys) + step))


def parse_box(text, frame):
    """'x0,y0,x1,y1'：全部 ≤1 视为相对有效画面的归一化坐标，否则为原图像素坐标。"""
    try:
        vals = [float(v) for v in text.split(",")]
    except ValueError:
        raise ValueError("框坐标应为 x0,y0,x1,y1，收到 %r" % text)
    if len(vals) != 4:
        raise ValueError("框坐标应为 4 个数，收到 %r" % text)
    fx0, fy0, fx1, fy1 = frame
    if all(0.0 <= v <= 1.0 for v in vals):
        fw, fh = fx1 - fx0, fy1 - fy0
        vals = [fx0 + vals[0] * fw, fy0 + vals[1] * fh, fx0 + vals[2] * fw, fy0 + vals[3] * fh]
    x0, y0, x1, y1 = [int(round(v)) for v in vals]
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ValueError("框太小（至少 4×4 像素）：%r" % text)
    return (x0, y0, x1, y1)


def parse_named(item, frame):
    name, sep, box = item.partition("=")
    if not sep or not name.strip():
        raise ValueError("区域写法应为 名称=x0,y0,x1,y1，收到 %r" % item)
    return name.strip(), parse_box(box.strip(), frame)


# ---------------------------------------------------------------- 色簇
def clusters(img, k=5, mask=None):
    """中位切分量化成 k 色，返回 [(rgb, share)]，按占比降序。mask 为 True 的像素才计入。"""
    rgb = img.convert("RGB")
    if mask is not None:
        data = [p for p, m in zip(rgb.getdata(), mask) if m]
        if not data:
            return []
        rgb = Image.new("RGB", (len(data), 1))
        rgb.putdata(data)
    q = rgb.quantize(colors=k, method=Image.MEDIANCUT)
    pal = q.getpalette() or []
    counts = q.getcolors(rgb.size[0] * rgb.size[1]) or []
    total = float(sum(c for c, _ in counts)) or 1.0
    out = []
    for c, idx in sorted(counts, reverse=True):
        col = tuple(pal[idx * 3: idx * 3 + 3])
        out.append((col, c / total))
    return out


def region_report(img, name, box, k=5):
    crop = img.crop(box)
    cl = clusters(crop, k)
    items = [dict(describe(c), share=round(s, 3)) for c, s in cl if s >= 0.02]
    main = items[0] if items else None
    sentence = ("%s：主色 %s" % (name, anchor_text(cl[0][0]))) if cl else ""
    return {"name": name, "box": list(box), "clusters": items, "main": main,
            "sentence_zh": sentence}


def skin_report(img, name, box):
    """皮肤三档：底色 / 主暗部 / 深投影 / 高光上限。排除描边黑与眼白。"""
    crop = img.crop(box).convert("RGB")
    pix = list(crop.getdata())
    keep = []
    for p in pix:
        _, _, v = rgb_to_hsv(p)
        keep.append(OUTLINE_V_MAX < v < NEAR_WHITE_V_MIN)
    cl = [(c, s) for c, s in clusters(crop, 6, keep) if s >= 0.03]
    warnings = []
    if not cl:
        return {"name": name, "box": list(box), "error": "框内没有可用的皮肤像素"}
    base = cl[0][0]
    bh, bs, bv = rgb_to_hsv(base)
    darker = [(c, s) for c, s in cl if rgb_to_hsv(c)[2] <= bv - 2]
    shadow = max(darker, key=lambda x: x[1])[0] if darker else None
    deep = None
    if shadow is not None:
        sv = rgb_to_hsv(shadow)[2]
        deeper = [(c, s) for c, s in cl if rgb_to_hsv(c)[2] <= sv - 5]
        if deeper:
            deep = max(deeper, key=lambda x: x[1])[0]
    # 高光上限：皮肤像素明度 95 分位
    vals = sorted((rgb_to_hsv(p)[2], p) for p, m in zip(pix, keep) if m)
    cap = vals[int(len(vals) * 0.95)][1] if vals else base

    if shadow is None:
        warnings.append("没找到比底色更暗的皮肤色块：框里可能没有暗部，扩大到下颌/颈下再取")
    if shadow is not None and rgb_to_hsv(shadow)[0] - bh > 8:
        warnings.append("主暗部色相比底色高 >8°，生成端容易发橙（protocol 2.6.1）；照实写，但出图后重点看暗部")
    if not (12 <= bh <= 32 and 15 <= bs <= 38 and 80 <= bv <= 97):
        warnings.append("底色超出 protocol 2.6.1 建议区间（色相 12–32°、饱和度 15–38%%、明度 80–97%%）："
                        "按参考图实测照写，不为过区间改色；在提示词备注里说明")

    tiers = {"base": describe(base), "highlight_cap": describe(cap)}
    parts = ["%s：底色 %s" % (name, anchor_text(base))]
    if shadow is not None:
        tiers["shadow"] = describe(shadow)
        parts.append("暗部只在下颌与颈下投影处压一层 %s" % anchor_text(shadow))
    if deep is not None:
        tiers["deep_shadow"] = describe(deep)
        parts.append("只有最深的投影（颈下、下颌线与发际下方）才到 %s，面积要小" % anchor_text(deep))
    parts.append("皮肤最亮处不超过 %s" % describe(cap)["hex"])
    return {"name": name, "box": list(box), "skin": tiers, "warnings": warnings,
            "sentence_zh": "；".join(parts)}


def auto_report(img, frame, k=8):
    """整体主体色板：用四角估背景色，ΔE<12 的像素视为背景排除。"""
    crop = img.crop(frame).convert("RGB")
    w, h = crop.size
    s = max(4, min(w, h) // 20)
    corners = []
    for (x0, y0) in ((0, 0), (w - s, 0), (0, h - s), (w - s, h - s)):
        corners.append(clusters(crop.crop((x0, y0, x0 + s, y0 + s)), 1)[0][0])
    small = crop.resize((max(1, w // 4), max(1, h // 4)))
    mask = [min(delta_e(p, c) for c in corners) >= 12 for p in small.getdata()]
    cl = clusters(small, k, mask)
    return {"background_corners": [rgb_to_hex(c) for c in corners],
            "subject_share": round(sum(mask) / float(len(mask) or 1), 3),
            "clusters": [dict(describe(c), share=round(sh, 3)) for c, sh in cl if sh >= 0.02]}


# ---------------------------------------------------------------- 入口
def main(argv=None):
    ap = argparse.ArgumentParser(description="参考图取色（只读）")
    ap.add_argument("--image", required=True, help="参考图路径")
    ap.add_argument("--region", action="append", default=[], help="名称=x0,y0,x1,y1（可重复）")
    ap.add_argument("--skin", action="append", default=[], help="皮肤区：名称=x0,y0,x1,y1（三档锚点）")
    ap.add_argument("--auto", action="store_true", help="主体整体色板（排除背景）")
    ap.add_argument("--keep-bars", action="store_true", help="不去黑边")
    ap.add_argument("--json", default=None, help="把结果写成 JSON 文件")
    args = ap.parse_args(argv)

    try:
        img = Image.open(args.image).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        print("图片打不开：%s" % exc)
        return 2
    frame = (0, 0) + img.size if args.keep_bars else content_box(img)
    result = {"schema": "ref-palette/1.0", "image": args.image, "size": list(img.size),
              "frame": list(frame), "regions": [], "skin": []}
    try:
        for item in args.skin:
            name, box = parse_named(item, frame)
            result["skin"].append(skin_report(img, name, box))
        for item in args.region:
            name, box = parse_named(item, frame)
            result["regions"].append(region_report(img, name, box))
    except ValueError as exc:
        print("参数错误：%s" % exc)
        return 2
    if args.auto or not (args.skin or args.region):
        result["auto"] = auto_report(img, frame)

    print("有效画面：%s（原图 %dx%d）" % (frame, img.size[0], img.size[1]))
    for s in result["skin"]:
        if s.get("error"):
            print("[皮肤 %s] %s" % (s["name"], s["error"]))
            continue
        print("[皮肤 %s] %s" % (s["name"], s["sentence_zh"]))
        for wmsg in s["warnings"]:
            print("    ! %s" % wmsg)
    for r in result["regions"]:
        print("[%s] %s" % (r["name"], r["sentence_zh"]))
        for c in r["clusters"][1:4]:
            print("    次色 %s 色相 %d° 饱和度 %d%% 明度 %d%% 占比 %.0f%%"
                  % (c["hex"], c["hue"], c["sat"], c["val"], c["share"] * 100))
    if "auto" in result:
        a = result["auto"]
        print("[主体色板] 背景四角 %s，主体占比 %.0f%%" % (" ".join(a["background_corners"]),
                                                   a["subject_share"] * 100))
        for c in a["clusters"]:
            print("    %s 色相 %d° 饱和度 %d%% 明度 %d%% 占比 %.0f%%"
                  % (c["hex"], c["hue"], c["sat"], c["val"], c["share"] * 100))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print("已写出：%s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出图后肤色复测：量底色/暗部/最亮值，与角色卡 skin_spec 锚点比对。

只用标准库 + Pillow。规则见 ../references/protocol.md 第 2.10 节。

用法：
  py -3 skin_check.py --image <图> --character ../references/characters/satoshi.json
  # 连续跟踪（写 skin-history.json，用于两轮不收敛就停调词）：
  py -3 skin_check.py --image <图> --character ... --order <订单目录>

退出码：0 PASS；3 NEAR（可再调一轮）；6 FAIL；7 STOP（连续两轮同一指标未收敛）；4 无法测量。
"""

import argparse
import colorsys
import json
import os
import sys
from collections import deque

from PIL import Image

# 判定门槛（写死，不允许运行时放宽）
DE_PASS, DE_NEAR = 6.0, 12.0
HUE_PASS, HUE_NEAR = 4.0, 8.0
SAT_PASS, SAT_NEAR = 6.0, 10.0
SHADOW_DE_MAX = 8.0
DEEP_DE_MAX = 10.0
HILIGHT_V_TOL = 3.0
STOP_IMPROVE = 2.0          # 新一轮 ΔE 至少改善 2 才算出收敛


def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def hex_to_rgb(value):
    v = value.strip().lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


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


def rgb_to_hsv(rgb):
    h, s, v = colorsys.rgb_to_hsv(*[c / 255.0 for c in rgb])
    return h * 360.0, s * 100.0, v * 100.0


def is_skin(Y, Cb, Cr):
    return (Y >= 100 and 126 <= Cr <= 170 and 85 <= Cb <= 146 and (Cr - Cb) >= 2)


def rgb_to_ycbcr(r, g, b):
    return (0.299 * r + 0.587 * g + 0.114 * b,
            128 - 0.168736 * r - 0.331264 * g + 0.5 * b,
            128 + 0.5 * r - 0.418688 * g - 0.081312 * b)


def bg_color(im):
    W, H = im.size
    px = im.convert("RGB").load()
    r = max(1, int(min(W, H) * 0.04))
    step = max(2, min(W, H) // 200)
    buckets = {}
    for y in range(0, H, step):
        for x in range(0, W, step):
            if not (x < r or x >= W - r or y < r or y >= H - r):
                continue
            rr, gg, bb = px[x, y]
            key = (rr // 16, gg // 16, bb // 16)
            buckets[key] = buckets.get(key, 0) + 1
    if not buckets:
        return None
    key = max(buckets, key=buckets.get)
    return (key[0] * 16 + 8, key[1] * 16 + 8, key[2] * 16 + 8)


def grid_components(marked, cw, ch):
    seen = [[False] * cw for _ in range(ch)]
    comps = []
    for y in range(ch):
        for x in range(cw):
            if not marked[y][x] or seen[y][x]:
                continue
            q = deque([(x, y)])
            seen[y][x] = True
            cells = []
            while q:
                cx, cy = q.popleft()
                cells.append((cx, cy))
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < cw and 0 <= ny < ch and marked[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            q.append((nx, ny))
            comps.append(cells)
    return comps


def auto_face_box(im):
    """最大肤色连通域的上 78% 作为脸部框。"""
    W, H = im.size
    step = max(2, min(W, H) // 256)
    w, h = W // step, H // step
    small = im.convert("YCbCr").resize((w, h), Image.BILINEAR)
    px = small.load()
    bg = bg_color(im)
    marked = []
    for gy in range(h):
        row = []
        for gx in range(w):
            Y, Cb, Cr = px[gx, gy]
            ok = is_skin(Y, Cb, Cr)
            if ok and bg is not None:
                if abs(Y - bg[0]) + abs(Cb - bg[1]) + abs(Cr - bg[2]) <= 30:
                    ok = False
            row.append(1 if ok else 0)
        marked.append(row)
    comps = grid_components(marked, w, h)
    if not comps:
        return None
    comps.sort(key=len, reverse=True)
    cells = comps[0]
    if len(cells) < 200:
        return None
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    return (min(xs) * step, min(ys) * step,
            (max(xs) + 1) * step, int(min(ys) * step + (max(ys) - min(ys) + 1) * step * 0.78))


def clusters(im, box):
    """按明度双峰取底色与暗部：最亮的显著峰=底色，其下最近的显著峰=暗部。

    平涂赛璐璐的皮肤是两个稳定明度峰；按 RGB 桶取最大簇会被渐变/JPEG 摊薄，
    把暗部误当底色（实测 #3 曾量成 L*58）。先按明度分峰，再回取像素。
    """
    x0, y0, x1, y1 = [int(v) for v in box]
    px = im.convert("RGB").load()
    cand = []
    for y in range(max(0, y0), min(im.size[1], y1)):
        for x in range(max(0, x0), min(im.size[0], x1)):
            r, g, b = px[x, y]
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            hd, sd, vd = h * 360, s * 100, v * 100
            # 只按 HSV 筛。不要再加 YCbCr is_skin：高饱和黄橙肤色 Cb 会掉到 85 以下，
            # 被误判成非皮肤后只剩暗部，底色会被量成阴影（实测 #1/#3 曾因此偏暗 20 个 L*）。
            if not (5 <= hd <= 42 and 16 <= sd <= 85 and 55 <= vd <= 98):
                continue
            cand.append(((r, g, b), vd))
    if not cand:
        return None, []
    counts = [0] * 51
    for _, v in cand:
        counts[min(50, int(v // 2))] += 1
    smooth = [counts[i] + counts[max(0, i - 1)] + counts[min(50, i + 1)] for i in range(51)]
    top = max(smooth)
    if top <= 0:
        return None, []
    base_bin = max(i for i, c in enumerate(smooth) if c >= 0.5 * top)
    base_v = base_bin * 2 + 1
    shadow_bin = None
    for i in range(base_bin - 4, -1, -1):
        if smooth[i] >= 0.2 * top:
            shadow_bin = i
            break
    base_px = [c for c, v in cand if abs(v - base_v) <= 4]
    shadow_px = []
    if shadow_bin is not None:
        sv = shadow_bin * 2 + 1
        shadow_px = [c for c, v in cand if abs(v - sv) <= 4]
    # 第三档：主暗部之下再找一层显著峰（深投影）。只在角色卡声明了 deep_shadow
    # 时才会被比较；没有声明时保持旧的「底色 + 一档暗部」行为。
    deep_px = []
    if shadow_bin is not None:
        for i in range(shadow_bin - 6, -1, -1):
            # 深投影面积本来就小（参考图约占首峰 17%，成品实测仅 11%），
            # 门槛必须低于主暗部，否则永远量不到这一档。
            if smooth[i] >= 0.08 * top:
                dv = i * 2 + 1
                deep_px = [c for c, v in cand if abs(v - dv) <= 4]
                break
    return base_px, shadow_px, deep_px


def summarize(pixels):
    if not pixels:
        return None
    n = float(len(pixels))
    rgb = [sum(c[i] for c in pixels) / n for i in range(3)]
    h, s, v = rgb_to_hsv(rgb)
    lab = rgb_to_lab(rgb)
    return {"hex": "#%02X%02X%02X" % tuple(int(round(c)) for c in rgb),
            "rgb": [round(c, 1) for c in rgb],
            "hue": round(h, 1), "sat": round(s, 1), "val": round(v, 1),
            "lab": [round(x, 1) for x in lab], "px": len(pixels)}


def dE76(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def main(argv=None):
    ap = argparse.ArgumentParser(description="肤色复测（不出图）")
    ap.add_argument("--image", required=True)
    ap.add_argument("--character", required=True)
    ap.add_argument("--box", default=None, help="x0,y0,x1,y1，测量失败时手动指定")
    ap.add_argument("--order", default=None, help="订单目录，用于写 skin-history.json 与两轮停判")
    ap.add_argument("--round", type=int, default=None,
                    help="本轮编号（同一轮的 2 张图用同一个编号）；不给则取 1 并提示")
    args = ap.parse_args(argv)

    card = read_json(args.character)
    spec = card.get("skin_spec") or {}
    if not all(spec.get(k) for k in ("base", "shadow", "highlight_cap")):
        print("角色卡缺少 skin_spec 三档锚点，无法复测")
        return 4
    target = {}
    for k in ("base", "shadow", "highlight_cap"):
        item = spec[k]
        hx = item.get("hex") if isinstance(item, dict) else item
        rgb = hex_to_rgb(hx)
        h, s, v = rgb_to_hsv(rgb)
        target[k] = {"hex": hx, "hue": h, "sat": s, "val": v, "lab": rgb_to_lab(rgb)}
    if spec.get("deep_shadow"):
        item = spec["deep_shadow"]
        hx = item.get("hex") if isinstance(item, dict) else item
        rgb = hex_to_rgb(hx)
        h, s, v = rgb_to_hsv(rgb)
        target["deep_shadow"] = {"hex": hx, "hue": h, "sat": s, "val": v,
                                 "lab": rgb_to_lab(rgb)}

    im = Image.open(args.image).convert("RGB")
    box = [int(v) for v in args.box.split(",")] if args.box else auto_face_box(im)
    if not box:
        print("未能自动定位脸部，请用 --box x0,y0,x1,y1 指定")
        return 4
    base_px, shadow_px, deep_px = clusters(im, box)
    base = summarize(base_px)
    if not base or base["px"] < 500:
        print("肤色样本不足（%s 像素），请用 --box 指定更紧的脸部框" % (base and base["px"]))
        return 4
    shadow = summarize(shadow_px)
    vs = sorted(rgb_to_hsv(c)[2] for c in base_px)
    v95 = vs[int(len(vs) * 0.95)]

    de = dE76(base["lab"], target["base"]["lab"])
    dh = base["hue"] - target["base"]["hue"]
    ds = base["sat"] - target["base"]["sat"]
    failing = []
    if de > DE_PASS:
        failing.append("dE")
    if abs(dh) > HUE_PASS:
        failing.append("hue")
    if abs(ds) > SAT_PASS:
        failing.append("sat")
    shadow_de = None
    if shadow:
        shadow_de = dE76(shadow["lab"], target["shadow"]["lab"])
        if shadow_de > SHADOW_DE_MAX:
            failing.append("shadow_dE")
    deep = summarize(deep_px) if target.get("deep_shadow") else None
    deep_de = None
    if deep and target.get("deep_shadow"):
        deep_de = dE76(deep["lab"], target["deep_shadow"]["lab"])
        if deep_de > DEEP_DE_MAX:
            failing.append("deep_shadow_dE")
    if v95 > target["highlight_cap"]["val"] + HILIGHT_V_TOL:
        failing.append("highlight_val")

    if not failing:
        verdict, code = "PASS", 0
    elif (de <= DE_NEAR and abs(dh) <= HUE_NEAR and abs(ds) <= SAT_NEAR
          and set(failing) <= {"shadow_dE", "deep_shadow_dE", "highlight_val"}):
        verdict, code = "NEAR", 3
    elif de <= DE_NEAR and abs(dh) <= HUE_NEAR and abs(ds) <= SAT_NEAR:
        verdict, code = "NEAR", 3
    else:
        verdict, code = "FAIL", 6

    print("image      : %s" % os.path.basename(args.image))
    print("face_box   : %s" % (box,))
    print("base  meas : %s H%.1f S%.1f V%.1f Lab%s (%d px)"
          % (base["hex"], base["hue"], base["sat"], base["val"], base["lab"], base["px"]))
    print("base  target: %s H%.1f S%.1f V%.1f Lab%s"
          % (target["base"]["hex"], target["base"]["hue"], target["base"]["sat"],
             target["base"]["val"], [round(x, 1) for x in target["base"]["lab"]]))
    print("delta      : dE76 %.1f | dHue %+.1f | dSat %+.1f" % (de, dh, ds))
    if shadow:
        print("shadow meas: %s H%.1f S%.1f V%.1f Lab%s -> dE76 %s"
              % (shadow["hex"], shadow["hue"], shadow["sat"], shadow["val"], shadow["lab"],
                 "n/a" if shadow_de is None else "%.1f" % shadow_de))
    else:
        print("shadow meas: <未找到独立暗部簇>")
    if target.get("deep_shadow"):
        if deep:
            print("deep   meas: %s H%.1f S%.1f V%.1f Lab%s -> dE76 %.1f（主暗部之下第三档）"
                  % (deep["hex"], deep["hue"], deep["sat"], deep["val"], deep["lab"],
                     deep_de if deep_de is not None else -1))
        else:
            print("deep   meas: <未找到第三档簇，仅校验主暗部>")
    print("skin p95 V : %.1f（上限 %s +%.0f）"
          % (v95, target["highlight_cap"]["hex"], HILIGHT_V_TOL))
    print("verdict    : %s%s" % (verdict, (" failing=" + ",".join(failing)) if failing else ""))

    stop = False
    if args.order:
        hist_path = os.path.join(args.order, "skin-history.json")
        hist = []
        if os.path.exists(hist_path):
            hist = read_json(hist_path)
        rnd = args.round if args.round is not None else 1
        if args.round is None:
            print("round      : 1（未指定 --round，按第 1 轮记账；同一轮的图请给同一个编号）")
        entry = {"round": rnd, "image": os.path.basename(args.image), "verdict": verdict,
                 "dE76": round(de, 1), "dHue": round(dh, 1), "dSat": round(ds, 1),
                 "shadow_dE76": None if shadow_de is None else round(shadow_de, 1),
                 "deep_shadow_dE76": None if deep_de is None else round(deep_de, 1),
                 "failing": failing}
        hist.append(entry)
        # 停判按「轮」而不是按「图」：同一轮的 2 张候选是同一版提示词，
        # 它们之间的差异是抽样方差，不构成「两轮没收敛」。
        rounds = sorted({e.get("round") for e in hist if e.get("round") is not None})
        if len(rounds) >= 2 and failing:
            cur_r, prev_r = rounds[-1], rounds[-2]
            pick = lambda rr: min([e for e in hist if e.get("round") == rr],
                                  key=lambda e: e.get("dE76") or 999)
            prev, cur = pick(prev_r), pick(cur_r)
            same = set(prev.get("failing") or []) & set(cur.get("failing") or [])
            improved = (prev.get("dE76") or 0) - (cur.get("dE76") or 0) >= STOP_IMPROVE
            if same and not improved:
                stop = True
                entry["stop_prompt_tuning"] = True
                entry["stop_reason"] = ("第 %s 轮→第 %s 轮 %s 未收敛（最佳 dE76 %s -> %s，改善 < %.0f）"
                                        % (prev_r, cur_r, ",".join(sorted(same)),
                                           prev.get("dE76"), cur.get("dE76"), STOP_IMPROVE))
        with open(hist_path, "w", encoding="utf-8") as fh:
            json.dump(hist, fh, ensure_ascii=False, indent=2)
        print("history    : %s（共 %d 条，%d 轮）"
              % (hist_path, len(hist), len(rounds)))
        if stop:
            print("STOP       : " + entry["stop_reason"])
            print("             停止调词，转后处理校色或换底模（protocol 2.10）")
            code = 7
    return code


if __name__ == "__main__":
    sys.exit(main())

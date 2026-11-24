#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词编译器：风格档案 + 角色卡 + 角度 + 后端能力 -> 可审计的正/反向提示词。

只用标准库。规则见 ../references/protocol.md 第 2.6、2.8、2.9 节。

用法（默认只打印，不写文件）：
  py -3 compile_prompt.py --character ../references/characters/satoshi.json \
        --style ../references/styles/tv-anime-cel.json \
        --backend agnes-aigc-img2img --angle front --expression "闭嘴微笑"
  # 写一份给 generate.py 用：
  py -3 compile_prompt.py ... --out prompts.next.json
  # 强制走另一条分支（验证编译器分支用）：
  py -3 compile_prompt.py ... --negatives on|off

退出码：0 正常；2 角色卡缺 skin_spec 或含禁用的模糊色词；3 后端未登记。
"""

import argparse
import colorsys
import hashlib
import json
import os
import re
import sys

COMPILER_VERSION = "1.0.0"
PROMPT_TEMPLATE = "skin-anchor-v1"

# 只能靠联想的肤色词：不得作为肤色主描述（protocol 2.6.1）
BANNED_VAGUE = ("小麦色", "暖调小麦色", "暖调皮肤", "日晒感", "日晒黑", "健康肤色",
                "自然肤色", "古铜色", "蜜色", "肤色偏暖", "偏黄的皮肤")

# 固定反向项：neg 用于支持负向词的后端；zh/en 用于不支持时的正向否定句
FORBIDDEN = [
    {"key": "angle",
     "neg": "侧脸、四分之三脸、扭头、双眼高度不一致、遮挡眼睛",
     "zh": "不要画成侧脸或四分之三脸，要正对镜头、双眼同时可见",
     "en": "not a profile or three-quarter view, face straight to camera with both eyes visible"},
    {"key": "second_char",
     "neg": "第二个人物、其他角色、人物倒影",
     "zh": "画面只有单人主体，不要出现第二个角色或倒影",
     "en": "one single subject only, no second character and no reflection"},
    {"key": "text_marks",
     "neg": "文字、字母、数字、水印、台标、图标、界面元素、黑边",
     "zh": "不要出现任何文字、字母、水印、台标、图标或黑边",
     "en": "no text, letters, watermark, logo bug, icons or letterbox bars"},
    {"key": "frame_crop",
     "neg": "圆形画框、装饰边框、圆角遮罩、切掉头顶、切掉帽顶、切掉肩线",
     "zh": "不要画圆形画框、装饰边框或圆角遮罩，不要切掉头顶、帽顶与肩线",
     "en": "no circular frame, decorative border or rounded mask; head, cap top and shoulders must stay complete"},
    {"key": "head_room",
     "neg": "发尖出画、头发被画布边缘切断、头顶贴住画布上沿、头顶留白为零",
     "zh": "发尖与头顶必须完整留在画布内，上方留出空白，不要贴住上沿",
     "en": "hair spikes and crown stay fully inside the canvas with empty background above, "
           "not touching the top edge"},
    {"key": "background",
     "neg": "山、树、天空、草地、建筑、实景背景、渐变背景、光斑",
     "zh": "背景保持纯净浅色平涂铺满画布，不要山、树、天空或实景",
     "en": "flat pale background filling the square canvas, no landscape, no gradient or light streaks"},
    {"key": "render",
     "neg": "写实、照片、3D 渲染、厚涂、油画、喷枪渐变、镜面高光",
     "zh": "保持平面赛璐璐画风，不要写实、3D 渲染、厚涂或镜面高光",
     "en": "keep flat cel shading; no photorealism, 3D render, impasto or specular highlights"},
    {"key": "skin_color",
     "neg": "高饱和小麦色皮肤、橘黄色皮肤、日晒黑、橙色阴影、棕色饱和阴影、偏黄滤镜",
     "zh": "皮肤不要偏黄、偏橙或高饱和，暗部不要橙色或棕色",
     "en": "skin must not be yellow, orange, tanned or oversaturated; shadows must not be orange or brown"},
    {"key": "line_detail",
     "neg": "逐根发丝、过多内部线条、浓黑粗描边、噪点",
     "zh": "不要逐根发丝、过多内部线条、浓黑粗描边或噪点",
     "en": "no strand-by-strand hair, excessive inner lines, thick pure-black outline or noise"},
    {"key": "age",
     "neg": "青年、成年、长脸、尖下巴、胡须、肌肉线条",
     "zh": "保持 10–12 岁少年感，不要青年或成年脸",
     "en": "keep the childlike 10-12 year old face, not a teen or adult"},
    {"key": "anatomy",
     "neg": "多余手指、畸形手、双头、多头、错位五官",
     "zh": "不要多余手指、畸形手、双头或错位五官",
     "en": "no extra fingers, deformed hands, two heads or misaligned features"},
]

# 年龄与构图两组负向词随角色卡分支：成年角色不能被「不要成年」反噬，
# 无头饰角色不该出现「帽顶」，而应改防「发尖被切」。
AGE_CHILD = {"key": "age",
             "neg": "青年、成年、长脸、尖下巴、胡须、肌肉线条",
             "zh": "保持 10–12 岁少年感，不要青年或成年脸",
             "en": "keep the childlike 10-12 year old face, not a teen or adult"}
AGE_ADULT = {"key": "age",
             "neg": "少年、儿童、幼童、Q版、娃娃脸、圆脸短下巴、稚气五官",
             "zh": "保持成年男性的成熟脸型与体格，不要少年、儿童或 Q 版",
             "en": "keep the mature adult male face and build; not a child, teen or chibi"}


def forbidden_groups(card):
    adult = (card.get("age_group") or "child") == "adult"
    has_cap = bool(card.get("headwear"))
    out = []
    for item in FORBIDDEN:
        if item["key"] == "age":
            out.append(AGE_ADULT if adult else AGE_CHILD)
        elif item["key"] == "frame_crop" and not has_cap:
            out.append({"key": "frame_crop",
                        "neg": "圆形画框、装饰边框、圆角遮罩、切掉头顶、切掉发尖、切掉肩线",
                        "zh": "不要画圆形画框、装饰边框或圆角遮罩，不要切掉头顶、发尖与肩线",
                        "en": "no circular frame, decorative border or rounded mask; "
                              "head, hair spikes and shoulders must stay complete"})
        else:
            out.append(dict(item))
    return out


ANGLE_EN = {
    "front": ("front view, both eyes visible and level, bridge of the nose centered",
              "正脸，双眼可见且水平对称，鼻梁居中"),
    "profile_left": ("near full profile facing image-left, nose pointing left, ear on the right, "
                     "only one eye and one ear visible, no second eye behind the nose bridge",
                     "接近正侧脸，脸朝画面左侧，鼻尖在左、耳朵在右，只出现一只眼睛和一只耳朵，"
                     "鼻梁另一侧不画第二只眼"),
    "profile_right": ("near full profile facing image-right, nose pointing right, ear on the left, "
                      "only one eye and one ear visible, no second eye behind the nose bridge",
                      "接近正侧脸，脸朝画面右侧，鼻尖在右、耳朵在左，只出现一只眼睛和一只耳朵，"
                      "鼻梁另一侧不画第二只眼"),
}

CANVAS_ZH = ("方形满出血，无边框、无圆角、无圆形遮罩；画布顶部留出画布高度 %s 的纯色空白，"
             "画面内容（含最长的发尖）全部位于这条空白线以下，头发/头部轮廓不得与画布上沿、"
             "左沿、右沿相接；%s")
CANVAS_EN = ("square canvas, no border, no frame, no circular mask, edge-to-edge; "
             "keep the top %s of the canvas height as empty background, all subject content "
             "including the longest hair spike stays below that empty band, and no hair or head "
             "silhouette may touch the top, left or right canvas edge; %s")
FRAMING_ZH = {
    "bust": "肩线完整入镜，取景到头部与肩部",
    "head_shoulders": "只取头部与肩部，画面下沿停在锁骨上方一点，胸部不入镜，头部占画面主体",
}
FRAMING_EN = {
    "bust": "shoulder line fully in frame, head-and-shoulders framing",
    "head_shoulders": "head-and-shoulders framing only, bottom edge stops just below the "
                      "collarbone, chest not in frame, head dominates the canvas",
}


def canvas_pair(card, framing="bust"):
    """取景留白按角色卡给数字；没有就按头发体量给默认值。

    这是对 goku 那次「说了镜头拉远、模型仍贴边」的修正：留白必须是可量的比例，
    而不是「一点背景」。
    """
    framing_card = card.get("framing") or {}
    lo = framing_card.get("headroom_min")
    hi = framing_card.get("headroom_max")
    if lo is None:
        hair = card.get("hair") or {}
        vol = hair.get("volume") or {}
        sides = vol.get("edge_contact_sides")
        run = vol.get("top_row_dark_run")
        big = (sides is not None and sides >= 2) or (run is not None and run >= 0.02)
        lo, hi = (0.12, 0.14) if big else (0.08, 0.10)
    hi = hi if hi is not None else lo + 0.02
    scope = framing if framing in FRAMING_ZH else "bust"
    return (CANVAS_ZH % ("%.0f%%–%.0f%%" % (lo * 100, hi * 100), FRAMING_ZH[scope]),
            CANVAS_EN % ("%.0f%%-%.0f%%" % (lo * 100, hi * 100), FRAMING_EN[scope]))

REF_ZH = "%s 是身份与画风的唯一权威，只改角度、构图与背景。"
REF_EN = ("%s is the only authority for identity and art style; "
          "change only the angle, composition and background.")


# ---------------------------------------------------------------- 色彩
def read_json(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def hex_to_rgb(value):
    v = value.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", v):
        raise ValueError("色值必须是 6 位 hex：#RRGGBB，收到 %r" % value)
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


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


def anchor_of(spec, key):
    item = spec[key]
    if isinstance(item, str):
        item = {"hex": item}
    if not isinstance(item, dict) or not item.get("hex"):
        raise ValueError("skin_spec.%s 必须是 {hex: '#RRGGBB'} 或 hex 字符串" % key)
    rgb = hex_to_rgb(item["hex"])
    h, s, v = rgb_to_hsv(rgb)
    lab = rgb_to_lab(rgb)
    return {"name": key, "hex": "#%02X%02X%02X" % rgb, "rgb": list(rgb),
            "hue": round(h, 1), "sat": round(s, 1), "val": round(v, 1),
            "lab": [round(x, 1) for x in lab]}


# ---------------------------------------------------------------- 校验
def check_skin_spec(spec, where):
    errors = []
    if not isinstance(spec, dict):
        return ["%s 缺少 skin_spec（protocol 2.5：角色卡必须给出底色/暗部/高光三档）" % where]
    got = {}
    for key in ("base", "shadow", "highlight_cap"):
        try:
            got[key] = anchor_of(spec, key)
        except (KeyError, ValueError) as exc:
            errors.append("%s.skin_spec.%s 不可用：%s" % (where, key, exc))
    if errors:
        return errors
    base, shadow, cap = got["base"], got["shadow"], got["highlight_cap"]
    if not (12 <= base["hue"] <= 32):
        errors.append("底色色相 %.1f° 越界（要求 12–32° 的暖色肤色）" % base["hue"])
    if not (15 <= base["sat"] <= 38):
        errors.append("底色饱和度 %.1f%% 越界（要求 15–38%%，避免高饱和小麦色）" % base["sat"])
    if not (80 <= base["val"] <= 97):
        errors.append("底色明度 %.1f%% 越界（要求 80–97%%）" % base["val"])
    # 两档暗部：主暗部只是「压一层」，与底色可以很近；深投影才是拉开明度的那一档。
    # 没有深投影时退回旧的单档规则（必须比底色低 ≥5），保证老角色卡行为不变。
    deep = None
    if spec.get("deep_shadow"):
        try:
            deep = anchor_of(spec, "deep_shadow")
        except (KeyError, ValueError) as exc:
            errors.append("%s.skin_spec.deep_shadow 不可用：%s" % (where, exc))
    if deep is not None:
        if shadow["val"] > base["val"] - 2:
            errors.append("主暗部明度必须比底色低至少 2 个点（主暗部 %.1f / 底色 %.1f）"
                          % (shadow["val"], base["val"]))
        if deep["val"] > shadow["val"] - 5:
            errors.append("深投影明度必须比主暗部低至少 5 个点（深投影 %.1f / 主暗部 %.1f）"
                          % (deep["val"], shadow["val"]))
        if deep["val"] < base["val"] - 30:
            errors.append("深投影明度比底色低超过 30 个点（%.1f / %.1f）：那是描边不是阴影"
                          % (deep["val"], base["val"]))
    elif shadow["val"] >= base["val"] - 5:
        errors.append("暗部明度必须比底色低至少 5 个点（暗部 %.1f / 底色 %.1f）"
                      % (shadow["val"], base["val"]))
    if shadow["hue"] > base["hue"] + 8:
        errors.append("暗部色相 %.1f° 比底色 %.1f° 高超过 8°：暗部会发橙"
                      % (shadow["hue"], base["hue"]))
    if cap["val"] < base["val"]:
        errors.append("高光上限明度不能低于底色")
    return errors


def scan_vague(text, where):
    hits = [w for w in BANNED_VAGUE if w in text]
    return ["%s 含模糊色词 %s：必须改成 hex + 色相/饱和度/明度锚点" % (where, "、".join(hits))
            ] if hits else []


def backend_supports_negative(reg, backend):
    if not isinstance(reg, dict):
        return False, "没有后端注册表，按不支持负向词处理"
    for item in reg.get("backends") or []:
        if item.get("id") != backend:
            continue
        caps = item.get("capabilities") or {}
        if "negative_prompt" in caps:
            return bool(caps["negative_prompt"]), "capabilities.negative_prompt"
        limits = item.get("limits") or {}
        if "negative_prompt" in limits:
            return bool(limits["negative_prompt"]), "limits.negative_prompt"
        return False, "该后端未声明 negative_prompt，按不支持处理"
    return False, "后端 %s 未登记，按不支持负向词处理（合法路径需先登记）" % backend


# ---------------------------------------------------------------- 编译
def _shadow_phrase(anchors, card):
    """暗部位置与质感按角色卡分支：无头饰不能写「帽檐投影」，暗部明度/饱和度也不同。"""
    place = "颈下与帽檐投影处" if card.get("headwear") else "颈下与下颌投影处"
    sat = anchors["shadow"]["sat"]
    if sat <= 30:
        tone = "低饱和的灰粉褐"
    elif sat <= 40:
        tone = "低饱和的暖褐"
    else:
        tone = "偏红的暖调暗部"
    return place, tone


def skin_zh(anchors, card):
    b, s, c = anchors["base"], anchors["shadow"], anchors["highlight_cap"]
    place, tone = _shadow_phrase(anchors, card)
    base_cmp = ("比画面里的纯白略深、比帽子的红色明显更灰"
                if card.get("headwear") else "比画面里的纯白略深")
    text = ("肤色按参考图取浅暖蜜桃色：底色 %s（色相 %.0f°、饱和度 %.0f%%、明度 %.0f%%），"
            "%s；暗部只在%s压一层 %s"
            "（色相 %.0f°、饱和度 %.0f%%、明度 %.0f%%），是%s；"
            % (b["hex"], b["hue"], b["sat"], b["val"], base_cmp, place,
               s["hex"], s["hue"], s["sat"], s["val"], tone))
    d = anchors.get("deep_shadow")
    if d:
        text += ("只有最深的投影（颈下、下颌线与发际下方）才到 %s"
                 "（色相 %.0f°、饱和度 %.0f%%、明度 %.0f%%），面积要小；"
                 % (d["hex"], d["hue"], d["sat"], d["val"]))
    return text + ("皮肤最亮处不超过 %s。" % c["hex"])


def skin_en(anchors, card):
    b, s, c = anchors["base"], anchors["shadow"], anchors["highlight_cap"]
    place, tone = _shadow_phrase(anchors, card)
    place_en = ("under the jaw and the cap brim" if card.get("headwear")
                else "under the jaw and the lower face")
    tone_en = {"低饱和的灰粉褐": "a low-saturation dusty pink-brown",
               "低饱和的暖褐": "a low-saturation warm brown",
               "偏红的暖调暗部": "a reddish warm shadow tone"}[tone]
    base_cmp = ("one step darker than the pure white in the reference and clearly "
                "greyer than the red cap"
                if card.get("headwear") else "one step darker than the pure white in the reference")
    text = ("pale warm peach skin matching the reference image: base %s (hue %.0f deg, "
            "saturation %.0f%%, value %.0f%%), %s; one shadow tone only %s, %s "
            "(hue %.0f deg, saturation %.0f%%, value %.0f%%), %s; "
            % (b["hex"], b["hue"], b["sat"], b["val"], base_cmp, place_en,
               s["hex"], s["hue"], s["sat"], s["val"], tone_en))
    d = anchors.get("deep_shadow")
    if d:
        text += ("only the deepest creases and the under-jaw line go down to %s "
                 "(hue %.0f deg, saturation %.0f%%, value %.0f%%), kept small in area; "
                 % (d["hex"], d["hue"], d["sat"], d["val"]))
    return text + ("brightest skin no lighter than %s." % c["hex"])


def compile_prompt(card, style, angles, backend, reg, mode, angle, expression,
                   background, identity, negatives, expression_en="", background_en="",
                   framing="bust", expression_from_reference=False):
    errors = []
    where = "character %s" % card.get("character_id")
    spec = card.get("skin_spec")
    errors += check_skin_spec(spec, where)
    if errors:
        return None, errors
    anchors = {k: anchor_of(spec, k) for k in ("base", "shadow", "highlight_cap")}
    if spec.get("deep_shadow"):
        anchors["deep_shadow"] = anchor_of(spec, "deep_shadow")
    # 角色卡自带的肤色描述也必须通过模糊词检查
    errors += scan_vague(json.dumps(card.get("face", {}), ensure_ascii=False), where + ".face")
    errors += scan_vague(card.get("prompt_block") or "", where + ".prompt_block")
    if errors:
        return None, errors

    angle_row = None
    for a in angles.get("angles") or []:
        if a.get("id") == angle:
            angle_row = a
            break
    if angle_row is None:
        return None, ["angles.json 没有角度 %r" % angle]
    if angle_row.get("status") == "reserved":
        return None, ["角度 %r 为 reserved，不能出词（protocol 2.7）" % angle]

    neg_mode, neg_src = backend_supports_negative(reg, backend)
    if negatives == "on":
        neg_mode = True
    elif negatives == "off":
        neg_mode = False
    supported, neg_reason = neg_mode, neg_src
    warnings = []
    if reg is None or "未登记" in neg_reason:
        warnings.append(neg_reason + "；正式出图前必须先在注册表登记（protocol 2.4）")

    angle_zh = ANGLE_EN[angle][1] if angle in ANGLE_EN else angle_row.get("prompt")
    angle_en = ANGLE_EN[angle][0] if angle in ANGLE_EN else angle_row.get("prompt")
    if expression_from_reference:
        # 参考图是闭眼张嘴笑时，「正脸，双眼可见」会与表情直接冲突；
        # 角度只约束头部朝向，眼睛开合交给表情块。
        angle_zh = "正脸，头部端正不歪，鼻梁居中"
        angle_en = "front view, head upright, nose bridge centered"
        expression = ("表情与参考图完全一致（参考图是闭眼、张嘴笑），不要睁眼、不要闭嘴；"
                      "如与上文的眼形、嘴形描述冲突，一律以参考图为准")
        expression_en = ("expression exactly matches the reference image "
                         "(eyes closed, mouth open laughing); do not open the eyes or close the "
                         "mouth; where the wording above about eye shape or mouth shape conflicts, "
                         "the reference image wins")

    style_line_zh = "平面赛璐璐上色，硬边双层阴影，2–3 像素深棕描边，无渐变，只在眼睛留一点小高光"
    style_line_en = ("flat cel shading, hard-edged two-tone shadow, 2-3 px dark-brown outline, "
                     "no gradient, only a small highlight in the eyes")
    # <image 1> 只在真支持参考图入参的宿主（Agnes）里成立；
    # 开源模型/纯文生图用「参考图」措辞，否则模型会把它当成要画的文字。
    token_zh = "<image 1>" if backend == "agnes-aigc-img2img" else "参考图"
    token_en = "<image 1>" if backend == "agnes-aigc-img2img" else "the reference image"
    identity_zh = card.get("prompt_block") or ""
    identity_en = card.get("prompt_block_en") or ""
    if identity == "compact":
        identity_zh = "角色与 %s 完全一致，不要改动身份特征" % token_zh
        identity_en = "keep the character exactly identical to %s, do not change identity" % token_en
    elif identity == "full" and not identity_en:
        return None, ["--identity full 需要角色卡提供 prompt_block_en"]

    blocks_zh, blocks_en = [], []
    if mode == "reference":
        ref_zh = REF_ZH % token_zh
        if token_zh == "参考图":
            ref_zh = ref_zh.replace("参考图 是", "参考图是")
        blocks_zh.append(ref_zh)
        blocks_en.append(REF_EN % token_en)
    blocks_zh.append(("%s。%s" % (identity_zh.rstrip("。"), skin_zh(anchors, card))) if identity_zh else skin_zh(anchors, card))
    blocks_en.append(("%s. %s" % (identity_en.rstrip("."), skin_en(anchors, card))) if identity_en else skin_en(anchors, card))
    blocks_zh.append(angle_zh + ("；" + expression if expression else "") + "。")
    blocks_en.append(angle_en + ("; " + (expression_en or expression) if expression else "") + ".")
    canvas_zh, canvas_en = canvas_pair(card, framing)
    blocks_zh.append("%s；%s；%s。" % (background, canvas_zh, style_line_zh))
    blocks_en.append("%s; %s; %s." % (background_en or background, canvas_en, style_line_en))

    groups = forbidden_groups(card)
    if expression_from_reference:
        # 闭眼表情下，「双眼高度不一致/遮挡眼睛」会被模型理解成「必须睁眼」，
        # 与表情块直接打架；角度负向只保留「不要侧脸」。
        for item in groups:
            if item["key"] == "angle":
                item["neg"] = "侧脸、四分之三脸、扭头"
                item["zh"] = "不要画成侧脸或四分之三脸，脸要正对镜头"
                item["en"] = "not a profile or three-quarter view, face straight to camera"
    if anchors["shadow"]["sat"] > 40:
        # 参考图本身的暗部就是暖红（Goku 实测 S43.8）时，「暗部不要橙色/棕色」
        # 会和 skin_spec 的正向锚点互相拉扯，只保留「皮肤整体别偏黄偏橙」。
        for item in groups:
            if item["key"] == "skin_color":
                item["neg"] = "高饱和小麦色皮肤、橘黄色皮肤、日晒黑、偏黄滤镜"
                item["zh"] = "皮肤不要偏黄、偏橙或高饱和"
                item["en"] = "skin must not be yellow, orange, tanned or oversaturated"
    if not supported:
        for item in groups:
            blocks_zh.append(item["zh"] + "。")
            blocks_en.append(item["en"] + ".")
        negative = None
    else:
        tokens = [item["neg"] for item in groups]
        parts = [p for t in tokens for p in t.split("、")]
        for extra in style.get("prompt_forbidden") or []:
            if any(extra == p or p in extra for p in parts):
                continue
            tokens.append(extra)
            parts.extend(extra.split("、"))
        negative = "、".join(tokens)

    prompt_zh = "\n".join([b for b in blocks_zh if b])
    prompt_en = " ".join([b for b in blocks_en if b])
    errors += scan_vague(prompt_zh, "prompt_zh")
    if errors:
        return None, errors

    sha = hashlib.sha256((prompt_zh + "\n---\n" + prompt_en).encode("utf-8")).hexdigest()
    return {
        "schema": "compiled-prompt/1.0",
        "compiler_version": COMPILER_VERSION,
        "prompt_template": PROMPT_TEMPLATE,
        "prompt_version": "%s-%s-%s" % (card.get("character_id"), style.get("style_id"),
                                        PROMPT_TEMPLATE),
        "prompt_sha256": sha,
        "style_id": style.get("style_id"), "style_version": style.get("version"),
        "character_id": card.get("character_id"),
        "character_version": card.get("version"),
        "angle": angle, "mode": mode, "backend": backend,
        "identity_mode": identity,
        "negative_mode": "separate" if supported else "inline",
        "negative_reason": neg_reason,
        "skin_anchor": anchors,
        "prompt_zh": prompt_zh,
        "prompt_en": prompt_en,
        "negative": negative,
        "warnings": warnings + ([] if (not expression or expression_en) else
                                ["expression 没有英文对照，prompt_en 混入了中文；请补 --expression-en"]),
    }, []


def main(argv=None):
    ap = argparse.ArgumentParser(description="编译头像提示词（只出文本，不出图）")
    here = os.path.dirname(os.path.abspath(__file__))
    ref = os.path.normpath(os.path.join(here, "..", "references"))
    ap.add_argument("--character", required=True)
    ap.add_argument("--style", default=os.path.join(ref, "styles", "tv-anime-cel.json"))
    ap.add_argument("--angles", default=os.path.join(ref, "angles.json"))
    # 与 generate.py / avatar_pipeline.py 一致：优先 2.0 注册表，缺失才退回 1.0。
    # 1.0 里没有 modelscope-* 条目，误用会把负向词分支判成 inline（静默降级）。
    ap.add_argument("--backends", default=(os.path.join(ref, "backends.v2.json")
                                          if os.path.isfile(os.path.join(ref, "backends.v2.json"))
                                          else os.path.join(ref, "backends.json")))
    ap.add_argument("--backend", required=True)
    ap.add_argument("--angle", default="front")
    ap.add_argument("--expression", default="")
    ap.add_argument("--expression-en", default="", help="英文段的表情对照，缺省沿用中文")
    ap.add_argument("--background", default="纯净浅色平涂背景铺满画布")
    ap.add_argument("--background-en", default="clean flat pale background filling the canvas")
    ap.add_argument("--mode", default="reference", choices=["reference", "text2img"])
    ap.add_argument("--identity", default="auto", choices=["auto", "compact", "full"])
    ap.add_argument("--negatives", default="auto", choices=["auto", "on", "off"])
    ap.add_argument("--framing", default="bust", choices=["bust", "head_shoulders"],
                    help="取景范围：bust=胸像；head_shoulders=头部+肩部，下沿到锁骨上方一点")
    ap.add_argument("--expression-from-reference", action="store_true",
                    help="表情照参考图（闭眼/张嘴等），覆盖角度与身份块里的眼睛开合描述")
    ap.add_argument("--out", default=None, help="写出 JSON 供 generate.py --prompt-file 使用")
    args = ap.parse_args(argv)

    card = read_json(args.character)
    style = read_json(args.style)
    angles = read_json(args.angles)
    reg = read_json(args.backends) if os.path.exists(args.backends) else None

    identity = args.identity
    if identity == "auto":
        identity = "compact" if args.mode == "reference" else "full"
    result, errors = compile_prompt(card, style, angles, args.backend, reg, args.mode,
                                    args.angle, args.expression, args.background,
                                    identity, args.negatives, args.expression_en,
                                    args.background_en, args.framing,
                                    args.expression_from_reference)
    if errors:
        print("COMPILE FAILED")
        for e in errors:
            print(" - " + e)
        return 2

    print("prompt_version : " + result["prompt_version"])
    print("prompt_sha256  : " + result["prompt_sha256"])
    print("style          : %s v%s" % (result["style_id"], result["style_version"]))
    print("character      : %s v%s" % (result["character_id"], result["character_version"]))
    print("backend        : %s" % result["backend"])
    print("negative_mode  : %s（依据 %s）" % (result["negative_mode"], result["negative_reason"]))
    print("skin_anchor    : base %s H%.0f S%.0f V%.0f Lab%s | shadow %s H%.0f S%.0f V%.0f | cap %s"
          % (result["skin_anchor"]["base"]["hex"], result["skin_anchor"]["base"]["hue"],
             result["skin_anchor"]["base"]["sat"], result["skin_anchor"]["base"]["val"],
             result["skin_anchor"]["base"]["lab"],
             result["skin_anchor"]["shadow"]["hex"], result["skin_anchor"]["shadow"]["hue"],
             result["skin_anchor"]["shadow"]["sat"], result["skin_anchor"]["shadow"]["val"],
             result["skin_anchor"]["highlight_cap"]["hex"]))
    print("\n--- prompt_zh ---\n" + result["prompt_zh"])
    print("\n--- prompt_en ---\n" + result["prompt_en"])
    print("\n--- negative ---\n" + (result["negative"] or "<无：该后端不支持负向词，禁止项已改写为正向否定句>"))
    for w in result.get("warnings") or []:
        print("\nWARN: " + w)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print("\nwritten: " + args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

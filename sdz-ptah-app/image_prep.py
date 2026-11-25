# -*- coding: utf-8 -*-
"""参考图入库前处理（双通道设计 7.2 ①）：校验、纠正方向、缩小、压缩。

规则：
- 必须能被 Pillow 解码，否则抛 ValueError（不把任意字节存进订单）；
- 按 EXIF 纠正方向，丢弃 EXIF（含 GPS 等隐私信息）；
- 长边 > max_edge 时等比缩小（LANCZOS）；
- 已经满足「长边 ≤ max_edge 且 体积 ≤ max_bytes」的 PNG/JPEG 原样保留，不二次压缩；
- 有透明通道 → 保留 PNG（体积仍超限时继续缩小）；否则 → JPEG，质量从 88 逐步降到 60；
- 仍超限就把长边再缩 15%，最多 6 轮；仍超限则抛 ValueError。
只依赖 Pillow（项目已有依赖）。
"""
import io

from PIL import Image, ImageOps

DEFAULT_MAX_EDGE = 1536
DEFAULT_MAX_BYTES = 1024 * 1024
MAX_PIXELS = 100_000_000  # 防解压炸弹
QUALITY_STEPS = (88, 82, 76, 70, 64, 60)


def _has_alpha(im):
    if im.mode in ("RGBA", "LA"):
        return im.getchannel("A").getextrema()[0] < 255
    return im.mode == "P" and "transparency" in im.info


def _encode(im, as_png, quality):
    buf = io.BytesIO()
    if as_png:
        im.save(buf, "PNG", optimize=True)
    else:
        im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def prepare(raw, max_edge=DEFAULT_MAX_EDGE, max_bytes=DEFAULT_MAX_BYTES):
    """返回 (bytes, ext, info)。ext 为 '.png' 或 '.jpg'。"""
    if not raw:
        raise ValueError("文件为空")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(io.BytesIO(raw))
        fmt = (im.format or "").upper()
        im.load()
    except Image.DecompressionBombError:
        raise ValueError("图片像素过大（上限 %d 百万像素）" % (MAX_PIXELS // 1_000_000))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("无法解码为图片：%s" % exc)

    orig_size = im.size
    info = {"original_bytes": len(raw), "original_size": list(orig_size),
            "original_format": fmt}

    exif = im.getexif()
    has_exif = len(exif) > 0
    rotated = exif.get(0x0112, 1) not in (None, 1)  # EXIF Orientation
    im = ImageOps.exif_transpose(im)

    alpha = _has_alpha(im)
    long_edge = max(im.size)

    if (fmt in ("PNG", "JPEG") and long_edge <= max_edge and len(raw) <= max_bytes
            and not has_exif):
        info.update(action="kept", final_bytes=len(raw), final_size=list(im.size),
                    format=fmt)
        return raw, (".png" if fmt == "PNG" else ".jpg"), info

    if alpha:
        im = im.convert("RGBA")
    else:
        im = im.convert("RGB")

    edge = min(long_edge, max_edge)
    data = b""
    for _round in range(6):
        if max(im.size) > edge:
            scale = edge / float(max(im.size))
            work = im.resize((max(1, round(im.size[0] * scale)),
                              max(1, round(im.size[1] * scale))), Image.LANCZOS)
        else:
            work = im
        qualities = (None,) if alpha else QUALITY_STEPS
        for q in qualities:
            data = _encode(work, alpha, q or 0)
            if len(data) <= max_bytes:
                info.update(action="recompressed", final_bytes=len(data),
                            final_size=list(work.size),
                            format="PNG" if alpha else "JPEG",
                            quality=q, oriented=rotated, exif_removed=has_exif)
                return data, (".png" if alpha else ".jpg"), info
        edge = int(edge * 0.85)
    raise ValueError("参考图压缩后仍超过 %d KB，请换一张更小的图" % (max_bytes // 1024))

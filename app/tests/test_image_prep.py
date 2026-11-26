# -*- coding: utf-8 -*-
"""image_prep 与 upload_reference：入库前压缩。

运行：
    py -3 -m unittest discover -s tests -v
"""
import base64
import io
import os
import sys
import tempfile
import unittest

from PIL import Image

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import image_prep  # noqa: E402
import server  # noqa: E402


def encode(im, fmt, **kw):
    buf = io.BytesIO()
    im.save(buf, fmt, **kw)
    return buf.getvalue()


def noisy(w, h):
    return Image.effect_noise((w, h), 90).convert("RGB")


class PrepareTests(unittest.TestCase):
    def test_small_jpeg_kept_byte_for_byte(self):
        raw = encode(Image.new("RGB", (800, 600), (200, 150, 100)), "JPEG")
        data, ext, info = image_prep.prepare(raw)
        self.assertEqual(data, raw)
        self.assertEqual(ext, ".jpg")
        self.assertEqual(info["action"], "kept")

    def test_small_png_kept(self):
        raw = encode(Image.new("RGB", (500, 500), (1, 2, 3)), "PNG")
        data, ext, info = image_prep.prepare(raw)
        self.assertEqual((data, ext, info["action"]), (raw, ".png", "kept"))

    def test_large_image_is_shrunk_and_under_limit(self):
        raw = encode(noisy(3200, 2400), "PNG")
        data, ext, info = image_prep.prepare(raw)
        im = Image.open(io.BytesIO(data))
        self.assertEqual(ext, ".jpg")
        self.assertLessEqual(max(im.size), image_prep.DEFAULT_MAX_EDGE)
        self.assertLessEqual(len(data), image_prep.DEFAULT_MAX_BYTES)
        self.assertEqual(round(im.size[0] / im.size[1], 2), round(3200 / 2400, 2),
                         "必须等比缩放")
        self.assertEqual(info["action"], "recompressed")

    def test_alpha_png_stays_png_with_alpha(self):
        im = noisy(1200, 1200).convert("RGBA")
        mask = Image.new("L", im.size, 255)
        mask.paste(0, (0, 0, 400, 400))
        im.putalpha(mask)
        data, ext, info = image_prep.prepare(encode(im, "PNG"), max_bytes=4 * 1024 * 1024)
        out = Image.open(io.BytesIO(data))
        self.assertEqual((ext, out.mode), (".png", "RGBA"))
        self.assertEqual(out.getpixel((10, 10))[3], 0, "透明区域必须保留")

    def test_opaque_rgba_png_becomes_jpeg(self):
        im = noisy(3000, 3000).convert("RGBA")  # alpha 全 255
        data, ext, _ = image_prep.prepare(encode(im, "PNG"))
        self.assertEqual(ext, ".jpg")

    def test_exif_orientation_applied_and_exif_removed(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # 顺时针旋转 90°
        raw = encode(Image.new("RGB", (400, 200), (10, 200, 10)), "JPEG", exif=exif)
        data, ext, info = image_prep.prepare(raw)
        im = Image.open(io.BytesIO(data))
        self.assertEqual(im.size, (200, 400))
        self.assertEqual(len(im.getexif()), 0, "EXIF（含 GPS）必须去掉")
        self.assertTrue(info["oriented"])

    def test_webp_is_accepted_and_converted(self):
        raw = encode(Image.new("RGB", (900, 900), (1, 2, 3)), "WEBP")
        data, ext, info = image_prep.prepare(raw)
        self.assertEqual(ext, ".jpg")
        self.assertEqual(info["original_format"], "WEBP")

    def test_not_an_image_rejected(self):
        with self.assertRaises(ValueError):
            image_prep.prepare(b"definitely not an image")

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            image_prep.prepare(b"")

    def test_impossible_limit_raises(self):
        with self.assertRaises(ValueError) as ctx:
            image_prep.prepare(encode(noisy(1200, 1200), "PNG"), max_bytes=2000)
        self.assertIn("KB", str(ctx.exception))


class UploadReferenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self._saved = {k: getattr(server, k) for k in
                       ("CONFIG", "CUSTOM_CHAR_DIR", "CUSTOM_STYLE_DIR", "UPLOAD_DIR")}
        server.CONFIG = dict(server.CONFIG)
        server.CONFIG["orders_root"] = os.path.join(self.tmp, "orders")
        os.makedirs(server.CONFIG["orders_root"], exist_ok=True)
        server.CUSTOM_CHAR_DIR = os.path.join(self.tmp, "custom", "characters")
        server.CUSTOM_STYLE_DIR = os.path.join(self.tmp, "custom", "styles")
        server.UPLOAD_DIR = os.path.join(self.tmp, "uploads")
        os.makedirs(server.UPLOAD_DIR, exist_ok=True)
        res = server.create_order({"character_id": "up-a", "character_desc": "黑发",
                                   "style_id": "tv-anime-cel", "gender": "male"})
        self.order = res["order"]

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(server, k, v)
        self._tmp.cleanup()

    @staticmethod
    def data_url(raw, mime="image/png"):
        return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))

    def test_big_upload_is_compressed_on_disk(self):
        raw = encode(noisy(3200, 2400), "PNG")
        self.assertGreater(len(raw), 5 * 1024 * 1024)
        res = server.upload_reference(self.order, {"name": "big.png", "data": self.data_url(raw)})
        self.assertTrue(res["ok"])
        self.assertTrue(res["name"].endswith(".jpg"), res["name"])
        self.assertLessEqual(res["size"], image_prep.DEFAULT_MAX_BYTES)
        self.assertEqual(os.path.getsize(res["path"]), res["size"])
        self.assertEqual(res["prepare"]["action"], "recompressed")

    def test_small_upload_unchanged(self):
        raw = encode(Image.new("RGB", (600, 600), (9, 9, 9)), "PNG")
        res = server.upload_reference(self.order, {"name": "s.png", "data": self.data_url(raw)})
        with open(res["path"], "rb") as fh:
            self.assertEqual(fh.read(), raw)

    def test_fake_png_rejected(self):
        with self.assertRaises(ValueError):
            server.upload_reference(self.order, {"name": "x.png",
                                                 "data": self.data_url(b"MZ not an image")})
        self.assertEqual(os.listdir(server.UPLOAD_DIR), [], "解码失败时不得落盘")

    def test_config_limit_respected(self):
        server.CONFIG["image_max_edge"] = 512
        raw = encode(noisy(1500, 1000), "PNG")
        res = server.upload_reference(self.order, {"name": "e.png", "data": self.data_url(raw)})
        self.assertLessEqual(max(Image.open(res["path"]).size), 512)


if __name__ == "__main__":
    unittest.main(verbosity=2)

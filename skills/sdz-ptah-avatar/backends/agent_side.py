# -*- coding: utf-8 -*-
"""宿主侧适配器（Agnes image_to_image / TRAE GenerateImage）。

Python 进程无法直接调用宿主工具，因此分两步：
  1. prepare：校验后写出 `<prefix>-call-<N>.json`（工具名 + 入参），抛 NeedsAgent；
  2. ingest ：宿主执行完把图片放回订单目录后，读回并生成 GenerationResult。
这样宿主后端与本地后端共用同一份请求、校验、台账与 manifest 字段。
"""

import json
import os

from .base import (Adapter, AdapterError, GeneratedImage, GenerationResult,
                   NeedsAgent, image_size, output_name, sha256_of)


class AgentSideAdapter(Adapter):
    kind = "agent_side"
    OUTPUT_EXTS = (".png", ".jpg", ".jpeg", ".webp")

    def __init__(self, spec, root=None):
        super().__init__(spec)
        host = spec.get("host") or {}
        self.tool = host.get("tool")
        self.param_map = host.get("params") or {}

    def existing_output(self, out_dir, req, index):
        """宿主产物可能是 png / jpg（Agnes 原生 1920 JPEG），任一存在即视为已生成。"""
        for ext in self.OUTPUT_EXTS:
            p = os.path.join(out_dir, output_name(req.prefix, index, ext))
            if os.path.isfile(p):
                return p
        return None

    def _args(self, req):
        m = self.param_map
        args = {}
        if "prompt_zh" in m:
            args[m["prompt_zh"]] = req.prompt_zh
        if "prompt_en" in m:
            args[m["prompt_en"]] = req.prompt_en or req.prompt_zh
        if "reference" in m and req.references:
            args[m["reference"]] = req.references[0].path
        if "size" in m:
            args[m["size"]] = str(max(req.width, req.height))
        if "ratio" in m:
            args[m["ratio"]] = "1:1"
        return args

    def call_path(self, out_dir, req, index):
        return os.path.join(out_dir, "%s-call-%d.json" % (req.prefix, index))

    def prepare(self, req, out_dir, only=None):
        """写出待宿主执行的调用单；only 给定时只写这些编号（已有产物的不重复要求）。"""
        warnings = self.check(req)
        files = []
        targets = only if only is not None else [req.start_index + i for i in range(req.count)]
        for idx in targets:
            call = {
                "schema": "sdz-agent-call/1.0",
                "adapter": self.id,
                "tool": self.tool,
                "args": self._args(req),
                "order_id": req.order_id,
                "stage": req.stage,
                "expected_output": output_name(req.prefix, idx),
                "warnings": warnings,
                "instructions": "由宿主调用 tool(args)；把生成图移动到订单目录并重命名为 "
                                "expected_output（宿主产物为 jpg 时扩展名可为 .jpg），"
                                "然后运行 `py -3 scripts/generate.py --order <订单目录> --resume`。",
            }
            p = self.call_path(out_dir, req, idx)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(call, f, ensure_ascii=False, indent=2)
            files.append(p)
        return files

    def generate(self, req, out_dir):
        missing = [req.start_index + i for i in range(req.count)
                   if not self.existing_output(out_dir, req, req.start_index + i)]
        if missing:
            raise NeedsAgent(self.prepare(req, out_dir, only=missing))
        return self.ingest(req, out_dir)

    def ingest(self, req, out_dir):
        images = []
        for i in range(req.count):
            idx = req.start_index + i
            p = self.existing_output(out_dir, req, idx)
            if not p:
                raise AdapterError("宿主产物缺失：%s" % output_name(req.prefix, idx))
            w, h = image_size(p)
            images.append(GeneratedImage(p, idx, None, w, h, sha256_of(p)))
        ok, why = self.reproducibility([None] * req.count, {})
        return GenerationResult(
            adapter_id=self.id, kind=self.kind, stage=req.stage, images=images,
            calls=len(images), params=self._args(req), model=self.model,
            reproducible=ok, reproducible_reason=why, license=self.license,
            env={"engine": "host", "tool": self.tool}, timings={}, warnings=[])

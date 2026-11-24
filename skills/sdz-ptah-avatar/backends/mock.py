# -*- coding: utf-8 -*-
"""Mock 适配器：不依赖 GPU，生成纯色占位图，供 CI 与流水线联调。"""

import os
import time

from .base import (Adapter, GeneratedImage, GenerationResult, output_name,
                   sha256_of)


class MockAdapter(Adapter):
    kind = "executable"

    def __init__(self, spec, root=None):
        super().__init__(spec)

    def generate(self, req, out_dir):
        from PIL import Image
        warnings = self.check(req)
        seeds = self.pick_seeds(req)
        images = []
        for i, seed in enumerate(seeds):
            idx = req.start_index + i
            s = seed or 0
            color = (s % 256, (s >> 8) % 256, (s >> 16) % 256)
            p = os.path.join(out_dir, output_name(req.prefix, idx))
            Image.new("RGB", (req.width, req.height), color).save(p)
            images.append(GeneratedImage(p, idx, seed, req.width, req.height, sha256_of(p)))
        ok, why = self.reproducibility(seeds, {"engine": "mock"})
        return GenerationResult(
            adapter_id=self.id, kind=self.kind, stage=req.stage, images=images,
            calls=len(images), params={"seeds": seeds}, model=self.model,
            reproducible=ok, reproducible_reason=why, license=self.license,
            env={"engine": "mock"}, timings={"total_s": 0.0}, warnings=warnings)

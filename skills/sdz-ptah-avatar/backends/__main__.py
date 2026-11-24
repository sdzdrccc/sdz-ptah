# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile

from . import registry, runner
from .base import AdapterError, GenerationRequest, NeedsAgent


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__import__("backends").__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    path = os.environ.get("SDZ_BACKENDS") or registry.default_path()
    doc = registry.load(path)
    try:
        if cmd == "list":
            _print(registry.summary(doc))
        elif cmd == "probe":
            _print(registry.get(doc, rest[0]).probe())
        elif cmd == "selftest":
            mock = {"id": "mock", "engine": "mock", "available": True,
                    "model": {"name": "mock", "version": "0"},
                    "capabilities": {"seed": True, "negative_prompt": True,
                                     "reference_max": 3, "reference_roles": ["identity", "style"],
                                     "init_image": True, "denoise_range": [0.1, 0.9],
                                     "stages": ["single", "identity", "style"]}}
            doc = {"schema": "generation-backends/2.0", "backends": [mock]}
            out = tempfile.mkdtemp(prefix="sdz-selftest-")
            req = GenerationRequest(order_id="selftest", prefix="t", start_index=1,
                                    prompt_en="1boy, safe", count=2, seed=42)
            r1, c1 = runner.run(req, [{"stage": "single", "backend": "mock"}], out, doc)
            req.start_index = 3
            r2, c2 = runner.run(req, [{"stage": "identity", "backend": "mock"},
                                      {"stage": "style", "backend": "mock", "denoise": 0.35}],
                                out, doc, budget_remaining=10)
            _print({"out_dir": out, "single_calls": c1, "two_stage_calls": c2,
                    "files": sorted(os.listdir(out)),
                    "reproducible": [r.reproducible for r in r1 + r2]})
        else:
            print("未知命令：%s" % cmd)
            return 2
    except NeedsAgent as e:
        _print({"needs_agent": e.call_files})
        return e.exit_code
    except AdapterError as e:
        print("错误：%s" % e, file=sys.stderr)
        return e.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

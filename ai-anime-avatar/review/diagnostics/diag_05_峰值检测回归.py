
import sys, os, importlib.util
from importlib.machinery import SourceFileLoader
SCRIPTS = r"F:\zxc\Project\sdz-ptah\skills\sdz-ptah-avatar\scripts"
BAK = r"F:\zxc\Project\sdz-ptah\archive\backups\2026-11-26\skills\sdz-ptah-avatar\scripts\avatar_pipeline.py.bak-before-peak-split"
sys.path.insert(0, SCRIPTS)
from PIL import Image
import avatar_pipeline as new_ap

IMG = r"F:\zxc\Project\sdz-ptah\avatar-orders\avatar\avatar-avatar-1-src.png"
img = Image.open(IMG).convert("RGB")
peaks, sc, pw, ph = new_ap._peak_eye_candidates(img)
print("== 峰值候选（改后新增，工作图坐标）==")
for p in peaks:
    print("  cx=%.0f cy=%.0f area=%d sat=%.0f" % (p["cx"], p["cy"], p["area"], p["sat"]))
print("眼睛期望位置（工作图）: 左约(112,180)、右约(374,180) 附近")

spec = importlib.util.spec_from_loader("old_ap", SourceFileLoader("old_ap", BAK))
old_ap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old_ap)

def run(mod, path):
    im = Image.open(path).convert("RGB")
    f = mod.detect_face(im)
    if not f:
        return None
    return {"method": f.get("method"), "eyes": len(f.get("eyes") or []),
            "box": [round(v) for v in f["box"]] if f.get("box") else None}

print("== 这张图：改前 vs 改后 ==")
print("改前:", run(old_ap, IMG))
print("改后:", run(new_ap, IMG))

print("== 已通过样本：改前 vs 改后 是否一致 ==")
samples = []
ord_dir = r"F:\zxc\Project\sdz-ptah\avatar-orders"
for root, ds, fs in os.walk(ord_dir):
    for f in fs:
        if f.lower().endswith("-src.png") or f.lower().endswith("-avatar-1.png"):
            samples.append(os.path.join(root, f))
samples = samples[:12]
same = diff = 0
for p in samples:
    try:
        a = run(old_ap, p); b = run(new_ap, p)
    except Exception as e:
        print("  ERR", p, e); continue
    if a == b:
        same += 1
    else:
        diff += 1
        print("  CHANGED", os.path.basename(p), "old:", a, "new:", b)
print("样本数", len(samples), "一致", same, "变化", diff)

import sys
sys.path.insert(0, r"F:\zxc\Project\sdz-ptah\skills\sdz-ptah-avatar\scripts")
import avatar_pipeline as ap
from PIL import Image

IMG = r"F:\zxc\Project\sdz-ptah\orders\satoshi\satoshi-avatar-1-src.png"
img = Image.open(IMG).convert("RGB")
W, H = img.size
work_w, cell = 480, 4
w = min(work_w, W)
h = int(round(H * w / float(W)))
small = img.convert("RGB").resize((w, h), Image.BILINEAR)
px = small.load()
cw, ch = w // cell, h // cell

marked = [[False] * cw for _ in range(ch)]
raw_hit = {}
for gy in range(ch):
    for gx in range(cw):
        hit = tot = 0
        for y in range(gy * cell, (gy + 1) * cell):
            for x in range(gx * cell, (gx + 1) * cell):
                r, g, b = px[x, y]
                mx, mn = max(r, g, b), min(r, g, b)
                tot += 1
                if mx - mn >= ap.SAT_MIN and mx >= ap.VAL_MIN:
                    hit += 1
        raw_hit[(gx, gy)] = hit / float(tot) if tot else 0
        if tot and hit / float(tot) >= 0.25:
            marked[gy][gx] = True

comps = ap._grid_components(marked, cw, ch)
owner = {}
for idx, comp in enumerate(comps):
    for gx, gy in comp:
        owner[(gx, gy)] = idx

def comp_desc(idx):
    c = comps[idx]
    xs = [p[0] for p in c]; ys = [p[1] for p in c]
    gx0, gx1, gy0, gy1 = min(xs), max(xs), min(ys), max(ys)
    bw = (gx1 - gx0 + 1) * cell; bh = (gy1 - gy0 + 1) * cell
    cx = gx0 * cell + bw / 2.0; cy = gy0 * cell + bh / 2.0
    limit = ap.EYE_AREA_MAX * cw * ch
    reasons = []
    if len(c) < 3: reasons.append("cells<3")
    if bw < cell * 1.5 or bh < cell * 1.5: reasons.append("too-small")
    if gx0 <= 0 or gy0 <= 0 or gx1 >= cw - 1 or gy1 >= ch - 1: reasons.append("touch-edge")
    if len(c) > limit: reasons.append("too-big")
    return "组件#%d area=%d bbox=(%d,%d)-(%d,%d) cx=%.0f cy=%.0f %s" % (
        idx, len(c), gx0, gy0, gx1, gy1, cx, cy,
        "KEPT" if not reasons else "REJECT:" + ",".join(reasons))

print("== 全部组件 ==")
for i in range(len(comps)):
    print(" ", comp_desc(i))

def region(name, gx0, gx1, gy0, gy1):
    print()
    print("== %s 格子坐标 gx %d..%d gy %d..%d ==" % (name, gx0, gx1, gy0, gy1))
    tally = {}
    for gy in range(gy0, gy1 + 1):
        row = ""
        for gx in range(gx0, gx1 + 1):
            if marked[gy][gx]:
                idx = owner[(gx, gy)]
                tally[idx] = tally.get(idx, 0) + 1
                row += "%02d " % idx if idx < 100 else "## "
            else:
                row += " . "
        print(" gy=%3d %s" % (gy, row))
    for idx, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("  归属 %d 格 -> %s" % (n, comp_desc(idx)))

# y≈180 采样坐标 => gy≈45；左右眼全分辨率 365..420 / 600..640 => 采样 /2.133 => 格 /4
region("左眼区域 (采样 y≈180)", 42, 50, 44, 48)
region("右眼区域 (采样 y≈180)", 70, 76, 44, 48)

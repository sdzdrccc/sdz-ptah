
import sys
sys.path.insert(0, r"F:\zxc\Project\sdz-ptah\skills\sdz-ptah-avatar\scripts")
import avatar_pipeline as ap
from PIL import Image
IMG = r"F:\zxc\Project\sdz-ptah\avatar-orders\avatar\avatar-avatar-1-src.png"
img = Image.open(IMG).convert("RGB")
W, H = img.size
work_w = 480; cell = 4; ratio = 0.25
w = min(work_w, W); h = int(round(H * w / float(W)))
small = img.convert("RGB").resize((w, h), Image.BILINEAR)
px = small.load()
cw, ch = w // cell, h // cell
print("work", w, h, "grid", cw, ch, "ratio", ratio)
def cell_stats(x0, y0, x1, y1):
    # 眼睛区域在 480 工作图上的坐标（由全分辨率 340..460 / 580..700 缩放 480/1024）
    s = 480.0 / W
    gx0, gx1 = int(x0 * s) // cell, int(x1 * s) // cell
    gy0, gy1 = int(y0 * s) // cell, int(y1 * s) // cell
    total = hit = 0
    for gy in range(gy0, gy1 + 1):
        for gx in range(gx0, gx1 + 1):
            h2 = t2 = 0
            for y in range(gy * cell, (gy + 1) * cell):
                for x in range(gx * cell, (gx + 1) * cell):
                    r, g, b = px[x, y]
                    mx, mn = max(r, g, b), min(r, g, b)
                    t2 += 1
                    if mx - mn >= ap.SAT_MIN and mx >= ap.VAL_MIN:
                        h2 += 1
            total += 1
            if t2 and h2 / float(t2) >= ratio:
                hit += 1
    return total, hit
print("左眼区域格子 (total, 饱和格)", cell_stats(365, 390, 420, 430))
print("右眼区域格子 (total, 饱和格)", cell_stats(600, 390, 640, 430))

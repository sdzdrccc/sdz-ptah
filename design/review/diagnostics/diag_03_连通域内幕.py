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
sat_sum = {}
raw_hit = {}
for gy in range(ch):
    for gx in range(cw):
        hit = tot = 0
        acc = 0
        for y in range(gy * cell, (gy + 1) * cell):
            for x in range(gx * cell, (gx + 1) * cell):
                r, g, b = px[x, y]
                mx, mn = max(r, g, b), min(r, g, b)
                sat = mx - mn
                tot += 1
                if sat >= ap.SAT_MIN and mx >= ap.VAL_MIN:
                    hit += 1
                    acc += sat
        raw_hit[(gx, gy)] = hit / float(tot) if tot else 0
        if tot and hit / float(tot) >= 0.25:
            marked[gy][gx] = True
            sat_sum[(gx, gy)] = acc / float(hit)

print("work", w, h, "grid", cw, ch, "cell", cell)
print("marked total:", sum(sum(row) for row in marked))
print("SAT_MIN", ap.SAT_MIN, "VAL_MIN", ap.VAL_MIN, "ratio 0.25",
      "EYE_AREA_MIN", ap.EYE_AREA_MIN, "EYE_AREA_MAX", ap.EYE_AREA_MAX)

print()
print("眼睛区域标记图 (#=marked, 数字=命中像素数/16)")
print("     " + "".join(str((gx // 10) % 10) for gx in range(38, 81)))
print("     " + "".join(str(gx % 10) for gx in range(38, 81)))
for gy in range(40, 56):
    row = ""
    for gx in range(38, 81):
        if marked[gy][gx]:
            row += "#"
        else:
            v = int(round(raw_hit[(gx, gy)] * 16))
            row += str(v) if v else "."
    print("%3d  %s" % (gy, row))

comps = ap._grid_components(marked, cw, ch)
print()
print("grid_components (过滤前):", len(comps))
limit = ap.EYE_AREA_MAX * cw * ch
for c in sorted(comps, key=lambda c: -len(c)):
    xs = [p[0] for p in c]
    ys = [p[1] for p in c]
    gx0, gx1, gy0, gy1 = min(xs), max(xs), min(ys), max(ys)
    bw = (gx1 - gx0 + 1) * cell
    bh = (gy1 - gy0 + 1) * cell
    cx = gx0 * cell + bw / 2.0
    cy = gy0 * cell + bh / 2.0
    reasons = []
    if len(c) < 3:
        reasons.append("cells<3")
    if bw < cell * 1.5 or bh < cell * 1.5:
        reasons.append("too-small")
    if gx0 <= 0 or gy0 <= 0 or gx1 >= cw - 1 or gy1 >= ch - 1:
        reasons.append("touch-edge")
    if len(c) > limit:
        reasons.append("too-big")
    print("  area=%-4d bbox=(%d,%d)-(%d,%d) cx=%-5.0f cy=%-5.0f %s"
          % (len(c), gx0, gy0, gx1, gy1, cx, cy,
             "KEPT" if not reasons else "REJECT " + ",".join(reasons)))

final, scale, sw, sh = ap._sat_components(img)
print()
print("_sat_components 返回:", len(final), "个")
for c in final:
    print("  area=%-4d sat=%-6.1f cx=%-5.0f cy=%-5.0f" %
          (c["area"], c["sat"], c["cx"], c["cy"]))

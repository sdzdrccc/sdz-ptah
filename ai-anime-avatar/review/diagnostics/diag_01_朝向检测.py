
import sys, os, json
ROOT = r"F:\zxc\Project\sdz-ptah\skills\sdz-ptah-avatar\scripts"
sys.path.insert(0, ROOT)
import avatar_pipeline as ap
from PIL import Image

IMG = r"F:\zxc\Project\sdz-ptah\avatar-orders\avatar\avatar-avatar-1-src.png"
img = Image.open(IMG).convert("RGB")
W, H = img.size
comps, scale, sw, sh = ap._sat_components(img)
print("image", W, H, "scale", round(scale, 3), "sw", sw, "sh", sh)
print("components:", len(comps))
cand = [c for c in comps if c["cy"] < 0.80 * sh and c["area"] >= ap.EYE_AREA_MIN]
cand.sort(key=lambda c: -c["sat"])
cand = cand[:12]
print("candidates after filter:", len(cand))
for c in cand[:6]:
    print("  area", c["area"], "sat", round(c["sat"], 1), "cx", round(c["cx"], 1), "cy", round(c["cy"], 1))

best = None
rejects = {}
for i in range(len(cand)):
    for j in range(i + 1, len(cand)):
        a, b = cand[i], cand[j]
        dx = abs(a["cx"] - b["cx"]); dy = abs(a["cy"] - b["cy"])
        if not (0.05 * sw <= dx <= 0.42 * sw):
            rejects["dx"] = rejects.get("dx", 0) + 1; continue
        if dy > 0.09 * sh:
            rejects["dy"] = rejects.get("dy", 0) + 1; continue
        amin, amax = min(a["area"], b["area"]), max(a["area"], b["area"])
        if amin / float(amax) < 0.20:
            rejects["area"] = rejects.get("area", 0) + 1; continue
        pair_y = (a["cy"] + b["cy"]) / 2.0 / sh
        if not (0.10 <= pair_y <= 0.62):
            rejects["pair_y"] = rejects.get("pair_y", 0) + 1; continue
        print("PAIR OK:", round(dx, 1), round(dy, 1), round(pair_y, 3))
        best = best or (a, b)
print("pair rejects:", rejects)

if best:
    a, b = best
    pa = ap._refine_eye(img, [v * scale for v in a["bbox"]])
    pb = ap._refine_eye(img, [v * scale for v in b["bbox"]])
    print("refine:", pa, pb)
    pairs = [p for p in [(a, pa), (b, pb)] if p[1]]
    print("refined eyes:", len(pairs))
    if len(pairs) == 2:
        pairs.sort(key=lambda p: p[1][0])
        eyes = [p[1] for p in pairs]
        d = abs(eyes[0][0] - eyes[1][0])
        print("eye distance d =", round(d, 1), "need >=", round(W * 0.08, 1))
        if d >= W * 0.08:
            eye_y = (eyes[0][1] + eyes[1][1]) / 2.0
            cx = (eyes[0][0] + eyes[1][0]) / 2.0
            fw = 2.00 * d; fh = 1.65 * d
            raw_top = eye_y - 0.42 * fh
            print("raw_top", round(raw_top, 1), "need >=", round(-0.02 * H, 1), "| fh", round(fh, 1), "need <=", round(0.85 * H, 1))
            ok = raw_top >= -0.02 * H and fh <= 0.85 * H
            print("GEOMETRY_CHECK", ok)
            if ok:
                box = (cx - fw / 2.0, raw_top, fw, fh)
                cov = ap.skin_coverage(img, box)
                print("skin_coverage", round(cov, 3), "need >=0.06")

#!/usr/bin/env python3
"""Directional analysis of aim offsets (exploratory).

Questions:
- global bias: mean (dx, dy) of cursor-at-object-time minus object position
- movement-relative: overshoot (along incoming direction) vs undershoot
- playfield-relative: bias toward/away from screen centre
- angle distribution

Sampled from the analyzed DB (status=ok), processed in filename order for
beatmap-cache locality.
"""
import json
import math
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import parse_osr, decode_mods
from osu_core import parse_beatmap, stacked_position
from ingest import build_beatmap_index, file_md5, get_beatmap, REPLAY_DIR

CENTRE = (256.0, 192.0)
SAMPLE = 1500
ANGLE_MIN_DIST = 12.0  # px — angle of tiny offsets is noise

def compute_vectors(rep, bm, mods):
    mirror_y = "HR" in mods
    frames = rep["frames"]
    if len(frames) < 2:
        return []
    last_t = frames[-1][0]

    from analyze import interpolate
    prev = None
    out = []
    for obj in bm.hit_objects:
        if obj["kind"] == "spinner":
            continue
        t = obj["time"]
        if t > last_t + 50.0:
            continue
        sx, sy = stacked_position(obj, bm.circle_size)
        if mirror_y:
            sy = 384.0 - sy
        x, y, _ = interpolate(frames, t)
        dx, dy = x - sx, y - sy

        rec = {"dx": dx, "dy": dy, "dist": math.hypot(dx, dy)}
        if prev is not None:
            mx, my = sx - prev[0], sy - prev[1]
            ml = math.hypot(mx, my)
            if ml > 1e-6:
                ux, uy = mx / ml, my / ml
                rec["along"] = dx * ux + dy * uy           # + = overshoot past target
                rec["perp"] = dx * (-uy) + dy * ux          # + = right of incoming path
        rx, ry = sx - CENTRE[0], sy - CENTRE[1]
        rl = math.hypot(rx, ry)
        if rl > 1e-6:
            rec["radial"] = (dx * rx + dy * ry) / rl        # + = away from centre
        prev = (sx, sy)
        out.append(rec)
    return out

def main():
    con = sqlite3.connect("data/osu_radar.db")
    rows = con.execute("SELECT filename FROM replays WHERE status='ok'").fetchall()
    random.seed(42)
    sample = sorted(random.sample([r[0] for r in rows], min(SAMPLE, len(rows))))
    print(f"sampling {len(sample)} / {len(rows)} replays")

    index, _, _ = build_beatmap_index()

    n_obj = 0
    sum_dx = sum_dy = 0.0
    sum_along = sum_perp = sum_radial = 0.0
    n_along = n_radial = 0
    comp_hist = {"along": {}, "perp": {}, "radial": {}}  # 1px signed bins
    angle_bins = [0] * 16
    angle_total = 0
    per_rep_mean = []  # per-replay mean vector (robustness check)

    for i, fname in enumerate(sample):
        path = os.path.join(REPLAY_DIR, fname)
        try:
            rep = parse_osr(path)
            bm = get_beatmap(rep["beatmap_md5"], index)
            if bm is None:
                continue
            recs = compute_vectors(rep, bm, decode_mods(rep["mods"]))
        except Exception:
            continue
        if not recs:
            continue
        rep_dx = rep_dy = 0.0
        for r in recs:
            n_obj += 1
            sum_dx += r["dx"]; sum_dy += r["dy"]
            rep_dx += r["dx"]; rep_dy += r["dy"]
            if "along" in r:
                sum_along += r["along"]; sum_perp += r["perp"]; n_along += 1
            if "radial" in r:
                sum_radial += r["radial"]; n_radial += 1
            for k in ("along", "perp", "radial"):
                if k in r:
                    b = int(r[k])
                    comp_hist[k][b] = comp_hist[k].get(b, 0) + 1
            if r["dist"] > ANGLE_MIN_DIST:
                ang = math.atan2(r["dy"], r["dx"])
                sector = int((ang + math.pi) / (2 * math.pi) * 16) % 16
                angle_bins[sector] += 1
                angle_total += 1
        per_rep_mean.append((rep_dx / len(recs), rep_dy / len(recs)))
        if (i + 1) % 300 == 0:
            print(f"  ... {i + 1}/{len(sample)} replays, {n_obj} objects")

    se = lambda s, n: math.sqrt(max(0.0, 1.0 / n))  # rough scale, not true SE (correlated)
    print(f"\nobjects analyzed: {n_obj} (angle-weighted: {angle_total})")

    print("\n== global bias (cursor - object) ==")
    print(f"  mean dx = {sum_dx / n_obj:+7.2f}px   mean dy = {sum_dy / n_obj:+7.2f}px")
    mag = math.hypot(sum_dx, sum_dy) / n_obj
    ang = math.degrees(math.atan2(sum_dy, sum_dx))
    print(f"  mean vector: {mag:.2f}px @ {ang:+.1f}deg (0=right, 90=down, screen coords)")
    # robustness: average of per-replay mean vectors
    prx = sum(m[0] for m in per_rep_mean) / len(per_rep_mean)
    pry = sum(m[1] for m in per_rep_mean) / len(per_rep_mean)
    print(f"  per-replay-mean avg: ({prx:+.2f}, {pry:+.2f})px over {len(per_rep_mean)} replays")

    print("\n== movement-relative (relative to previous object→current direction) ==")
    print(f"  along (overshoot + / undershoot -): mean {sum_along / n_along:+.2f}px")
    print(f"  perp (+ = right of path):            mean {sum_perp / n_along:+.2f}px")
    print(f"  playfield-relative radial (+ = away from centre): mean {sum_radial / n_radial:+.2f}px")

    def comp_pct(name, p):
        h = comp_hist[name]
        total = sum(h.values())
        import math as m
        target = m.ceil(p / 100.0 * total)
        cum = 0
        for b in sorted(h):
            cum += h[b]
            if cum >= target:
                return b + 0.5
        return 0.0

    print("\n== signed component percentiles ==")
    print(f"  {'':8s}{'p5':>8}{'p25':>8}{'p50':>8}{'p75':>8}{'p95':>8}")
    for k in ("along", "perp", "radial"):
        vals = [comp_pct(k, p) for p in (5, 25, 50, 75, 95)]
        print(f"  {k:8s}" + "".join(f"{v:8.1f}" for v in vals))

    print("\n== angle distribution (dist > 12px, 16 sectors, 0=right,90=down) ==")
    mx = max(angle_bins)
    for s in range(0, 16, 2):
        bar = "#" * int(40 * angle_bins[s] / max(1, mx))
        deg = s * 22.5
        print(f"  {deg:5.0f}-{deg + 22.5:5.1f}deg {angle_bins[s] / angle_total * 100:5.1f}% {bar}")

if __name__ == "__main__":
    main()

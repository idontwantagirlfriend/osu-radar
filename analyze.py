#!/usr/bin/env python3
"""osu-radar prototype: per-hit-object aim offset from a replay + its beatmap.

- Parses .osu (v14 and older) hit objects (circles / slider heads; spinners skipped)
- Parses .osr replay frames (zlib or lzma), reconstructs absolute times
- Applies mods (EZ/HR/DT/HT...) to AR/CS and timing
- Interpolates cursor position at each object's hit time, reports offset distance
"""
import datetime
import lzma
import math
import struct
import sys
import zlib

sys.path.insert(0, "/home/devman/dev/osu-radar")
from osu_core import parse_beatmap, stacked_position, ar_to_preempt_ms, scale_from_cs

# ---------------------------------------------------------------- replay (.osr)

class Reader:
    def __init__(self, data):
        self.data = data
        self.pos = 0

    def _fmt(self, fmt, n):
        v = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += n
        return v

    def u8(self): return self._fmt("<B", 1)
    def u16(self): return self._fmt("<H", 2)
    def u32(self): return self._fmt("<I", 4)
    def u64(self): return self._fmt("<Q", 8)
    def boolean(self): return bool(self.u8())

    def string(self):
        if self.u8() == 0x0B:
            ln = self.uleb128()
            v = self.data[self.pos:self.pos + ln].decode("utf-8", "replace")
            self.pos += ln
            return v
        return ""

    def uleb128(self):
        result = shift = 0
        while True:
            b = self.u8()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7


def parse_osr(path):
    r = Reader(open(path, "rb").read())
    rep = {
        "mode": r.u8(),
        "version": r.u32(),
        "beatmap_md5": r.string(),
        "player": r.string(),
        "replay_md5": r.string(),
        "n300": r.u16(), "n100": r.u16(), "n50": r.u16(),
        "geki": r.u16(), "katu": r.u16(), "n_miss": r.u16(),
        "score": r.u32(), "max_combo": r.u16(), "perfect": r.boolean(),
        "mods": r.u32(),
        "life_graph": r.string(),
        "timestamp": r.u64(),
    }
    comp_len = r.u32()
    comp = r.data[r.pos:r.pos + comp_len]
    r.pos += comp_len
    raw = (zlib if comp[:1] == b"x" else lzma).decompress(
        comp, format=lzma.FORMAT_ALONE if comp[:1] != b"x" else zlib.MAX_WBITS
    ).decode() if comp[:1] == b"x" else lzma.decompress(comp, format=lzma.FORMAT_ALONE).decode()
    # frames: "deltaT|x|y|buttons", last "-12345|0|0|seed" is the end marker
    frames = []
    t = 0.0
    seed = None
    for f in raw.split(","):
        if not f:
            continue
        dt, x, y, btn = f.split("|")
        dt = int(dt)
        if dt == -12345:
            seed = int(btn)
            continue
        t += dt
        frames.append((t, float(x), float(y), int(btn)))
    frames.sort(key=lambda f: f[0])  # lead-in 负时间帧可能乱序，插值二分要求有序
    rep["frames"] = frames
    rep["seed"] = seed
    rep["online_id"] = r.u64()
    return rep

# ---------------------------------------------------------------- time

def ticks_to_iso(ticks):
    """.osr timestamp (.NET ticks, 100ns since 0001-01-01) -> 'YYYY-MM-DD HH:MM:SS' UTC.
    Returns None for missing/absurd values."""
    MIN_TICKS = 633509472000000000  # 2007-01-01, before osu! existed
    if not ticks or ticks < MIN_TICKS or ticks > 677680361913200000:  # beyond 2149
        return None
    ts = ticks / 1e7 - 62135596800
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None

def parse_osr_timestamp(path):
    """Header-only read of the .osr play timestamp (no frame decompression)."""
    try:
        r = Reader(open(path, "rb").read())
        r.u8(); r.u32(); r.string(); r.string(); r.string()
        for _ in range(6):
            r.u16()
        r.u32(); r.u16(); r.u8(); r.u32(); r.string()
        return ticks_to_iso(r.u64())
    except (struct.error, IndexError, OSError):
        return None

def since_cutoff(spec, now=None):
    """'6m' / '30d' / '1y' -> ISO cutoff string; 'all' / '' / None -> None (no filter)."""
    if not spec or spec == "all":
        return None
    spec = spec.strip().lower()
    try:
        n, unit = int(spec[:-1]), spec[-1]
    except ValueError:
        return None
    days = {"d": n, "m": n * 30.44, "y": n * 365.25, "w": n * 7}.get(unit)
    if days is None:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

# ---------------------------------------------------------------- beatmap (.osu)

def parse_osu(path):
    section = None
    bm = {"timing_points": [], "hit_objects": []}
    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            continue
        if section == "General":
            k, _, v = line.partition(":")
            v = v.strip()
            if k == "Mode": bm["mode"] = int(v)
            elif k == "StackLeniency": bm["stack_leniency"] = float(v)
        elif section == "Difficulty":
            k, _, v = line.partition(":")
            try:
                bm[k.strip()] = float(v.strip())
            except ValueError:
                pass
        elif section == "Metadata":
            k, _, v = line.partition(":")
            bm[k.strip()] = v.strip()
        elif section == "TimingPoints":
            parts = line.split(",")
            bm["timing_points"].append({
                "time": float(parts[0]),
                "beat_length": float(parts[1]),
                "uninherited": len(parts) < 7 or parts[6] == "1",
            })
        elif section == "HitObjects":
            parts = line.split(",")
            obj = {
                "x": float(parts[0]),
                "y": float(parts[1]),
                "time": float(parts[2]),
                "type": int(parts[3]),
            }
            if obj["type"] & 2:  # slider
                obj["slider_type"] = parts[5].split("|")[0]
            bm["hit_objects"].append(obj)
    return bm

# ---------------------------------------------------------------- mods / AR

MOD_NAMES = {1 << i: n for i, n in enumerate([
    "NF", "EZ", "TD", "HD", "HR", "SD", "DT", "RL", "HT", "NC", "FL", "AT",
    "SO", "AP", "PF"])}

def decode_mods(bits):
    names = {MOD_NAMES[m] for m in MOD_NAMES if bits & m}
    if "NC" in names:
        names.discard("NC"); names |= {"DT", "NF"}
    return names

def ar_to_preempt_ms(ar):
    if ar < 5: return 1200 + 600 * (5 - ar) / 5
    if ar > 5: return 1200 - 750 * (ar - 5) / 5
    return 1200.0

def effective_ar(base_ar, mods):
    ar = base_ar
    if "EZ" in mods: ar = min(ar * 0.5, 10)
    if "HR" in mods: ar = min(ar * 1.4, 10)
    rate = 1.5 if "DT" in mods else 0.75 if "HT" in mods else 1.0
    # lazer: rate change scales preempt, i.e. effectively shifts AR
    preempt = ar_to_preempt_ms(ar) / rate
    # invert back to an equivalent AR value
    if preempt > 1200: return 5 - (preempt - 1200) * 5 / 600
    return 5 + (1200 - preempt) * 5 / 750

def effective_cs(base_cs, mods):
    cs = base_cs
    if "EZ" in mods: cs /= 2
    if "HR" in mods: cs = min(cs * 1.3, 10)
    return cs

def cs_to_radius(cs):
    return 64.0 * scale_from_cs(cs)  # OBJECT_RADIUS * Scale (lazer)

def offset_to_cs(radius_px):
    """Invert radius = 64 * scale_from_cs(cs). CS beyond [0,10] reported as-is."""
    u = radius_px / (64.0 * 1.00041)
    return 5 - (2 * u - 1) / 0.14

def cs_to_radius_cs(cs):
    return 64.0 * ((1 - 0.7 * (cs - 5) / 5) / 2 * 1.00041)

# ---------------------------------------------------------------- analysis

def interpolate(frames, t):
    """Cursor position at absolute time t (frames sorted by time)."""
    lo, hi = 0, len(frames) - 1
    if t <= frames[0][0]: return frames[0][1], frames[0][2], frames[0][3]
    if t >= frames[-1][0]: return frames[-1][1], frames[-1][2], frames[-1][3]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if frames[mid][0] <= t: lo = mid
        else: hi = mid
    t0, x0, y0, _ = frames[lo]
    t1, x1, y1, _ = frames[hi]
    if t1 == t0: return x1, y1, frames[hi][3]
    a = (t - t0) / (t1 - t0)
    # button state: prefer the earlier frame's state at exactly t
    return x0 + (x1 - x0) * a, y0 + (y1 - y0) * a, frames[lo][3]


def compute_offsets(rep, bm, mods):
    """Per-object aim offsets (osu!px) at object hit time — the orthogonal model.
    Returns meta dict + plain offset list. Objects with no replay coverage
    (after death/quit) are excluded."""
    ar = effective_ar(bm.approach_rate, mods)
    cs = effective_cs(bm.circle_size, mods)
    radius = cs_to_radius(cs)
    mirror_y = "HR" in mods  # HR flips the playfield vertically

    frames = rep["frames"]
    last_t = frames[-1][0] if frames else -1e18

    offsets = []
    n_circle = n_slider = n_spinner = n_stacked = 0
    for obj in bm.hit_objects:
        if obj["kind"] == "spinner":
            n_spinner += 1
            continue
        if obj["kind"] == "slider": n_slider += 1
        else: n_circle += 1
        if obj["stack_height"] != 0: n_stacked += 1
        # NB: stable replay frame times are in the beatmap clock — DT/HT do NOT
        # rescale the lookup (verified empirically: last-frame/last-object ~1.0 on DT replays)
        t = obj["time"]
        if t > last_t + 50.0:  # cursor data ends here (failed/quit play)
            continue
        sx, sy = stacked_position(obj, bm.circle_size)
        if mirror_y:
            sy = 384.0 - sy
        x, y, _ = interpolate(frames, t)
        if not (-32 <= x <= 544 and -32 <= y <= 416):
            continue  # 光标在场地外（出生点/未在游玩），不是有效的瞄准采样
        offsets.append(math.hypot(x - sx, y - sy))

    return {
        "offsets": offsets,
        "eff_ar": ar, "eff_cs": cs, "radius": radius,
        "n_circle": n_circle, "n_slider": n_slider,
        "n_spinner": n_spinner, "n_stacked": n_stacked,
        "n_frames": len(frames),
    }

# ------------------------------------------------------------ histogram model

BIN_WIDTH = 1.0   # px
MAX_BIN = 2000    # beyond this everything lands in the last bin

def build_histogram(offsets):
    h = {}
    for o in offsets:
        b = min(int(o / BIN_WIDTH), MAX_BIN)
        h[b] = h.get(b, 0) + 1
    return h

def histogram_from_json(d):
    """JSON round-trips int bin keys as strings — coerce back."""
    return {int(k): v for k, v in d.items()}

def merge_histograms(hists):
    merged = {}
    for h in hists:
        for b, c in h.items():
            merged[b] = merged.get(b, 0) + c
    return merged

def histogram_percentile(hist, p):
    """Nearest-rank percentile over binned counts; returns bin centre (px)."""
    total = sum(hist.values())
    if total == 0:
        return 0.0
    target = p / 100.0 * total
    # nearest-rank: ceil(p/100*N)-th value
    import math as _m
    target = _m.ceil(p / 100.0 * total)
    cum = 0
    for b in sorted(hist):
        cum += hist[b]
        if cum >= target:
            return (b + 0.5) * BIN_WIDTH
    return (max(hist) + 0.5) * BIN_WIDTH

def offsets_percentile(offsets, p):
    s = sorted(offsets)
    import math as _m
    return s[min(len(s) - 1, _m.ceil(p / 100.0 * len(s)) - 1)] if s else 0.0


def main(osr_path, osu_path):
    rep = parse_osr(osr_path)
    bm = parse_beatmap(osu_path)
    mods = decode_mods(rep["mods"])

    import hashlib
    file_md5 = hashlib.md5(open(osu_path, "rb").read()).hexdigest()
    assert file_md5 == rep["beatmap_md5"], "beatmap md5 mismatch!"

    r = compute_offsets(rep, bm, mods)
    offs = sorted(r["offsets"])

    print(f"map:        {bm.metadata.get('Artist')} - {bm.metadata.get('Title')} [{bm.metadata.get('Version')}]  (format v{bm.format_version})")
    print(f"player:     {rep['player']}  mods: {'+'.join(sorted(mods))}")
    print(f"base AR {bm.approach_rate} CS {bm.circle_size} -> effective AR {r['eff_ar']:.2f} CS {r['eff_cs']:.2f} (circle radius {r['radius']:.1f}px)")
    print(f"objects:    {r['n_circle']} circles, {r['n_slider']} sliders, {r['n_spinner']} spinners (analyzed {len(offs)}, {r['n_stacked']} stacked)")
    print(f"replay:     {r['n_frames']} frames")
    print(f"-- cursor offset at object hit time (osu!pixels) --")
    print(f"  {'level':<7}{'offset(px)':>11}{'min CS*':>10}")
    for p in (50, 75, 90, 95, 99, 99.9):
        v = offsets_percentile(r["offsets"], p)
        cs_at = offset_to_cs(v)
        note = "" if 0 <= cs_at <= 10 else "  (>CS0: no circle fits)" if cs_at < 0 else "  (<min radius CS10)"
        print(f"  p{p:<6}{v:11.1f}{cs_at:10.2f}{note}")
    print(f"  mean  {sum(offs)/len(offs):11.1f}{offset_to_cs(sum(offs)/len(offs)):10.2f}")
    print(f"  * min CS: 至此 CS 该等级偏移仍落在圈内（即最大可承受 CS；offset => 所需最小半径）")
    print(f"  reference: CS0 r={cs_to_radius_cs(0):.1f}px  CS2 r={cs_to_radius_cs(2):.1f}  CS4 r={cs_to_radius_cs(4):.1f}  CS5 r={cs_to_radius_cs(5):.1f}  CS7 r={cs_to_radius_cs(7):.1f}  CS10 r={cs_to_radius_cs(10):.1f}")
    print(f"  cursor inside circle at hit time: {sum(1 for o in offs if o <= r['radius'])}/{len(offs)} ({100*sum(1 for o in offs if o <= r['radius'])/max(1,len(offs)):.1f}%)")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

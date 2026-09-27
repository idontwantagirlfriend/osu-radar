#!/usr/bin/env python3
"""osu-radar incremental ingest pipeline.

1. copy new .osr replays from the osu! scores dir into r/ (working copies)
2. maintain a local md5->path beatmap index built from the Songs folder
3. analyze only replays never analyzed before (tracked by replay_md5 in DB)
4. persist per-replay offset histogram tagged with effective AR & CS
   (single row serves both the AR-model and CS-model; each row references
   its replay for traceability / future model merging)
5. print the growing player profile per AR bucket and CS bucket

usage: python3 ingest.py [--limit N] [--no-copy] [--no-index] [--profile-only]
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_env
load_env()  # .env 中的 OSU_SCORES_DIR / OSU_SONGS_DIR 等先于模块级默认值读取
from analyze import (parse_osr, decode_mods, compute_offsets, build_histogram,
                     histogram_from_json, merge_histograms, histogram_percentile,
                     offsets_percentile, offset_to_cs, parse_osr_timestamp,
                     ticks_to_iso, since_cutoff)
from osu_core import parse_beatmap

ROOT = os.path.dirname(os.path.abspath(__file__))


def _maybe_wsl_path(p):
    r"""WSL 下把 Windows 风格路径（D:\x\y）换算为 /mnt/d/x/y，其余原样返回。"""
    import re
    if p and sys.platform.startswith("linux"):
        p2 = p.replace("\\", "/")
        m = re.match(r"^([A-Za-z]):/(.*)$", p2)
        if m and not os.path.isdir(p):
            return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
    return p


def resolve_dirs():
    """路径解析优先级：CLI > OSU_SCORES_DIR/OSU_SONGS_DIR（绝对）> OSU_DIR+相对 > 默认。
    .env 的 OSU_DIR 留空 = 自动检测（交给 tosu discover / 默认路径）。"""
    source = os.environ.get("OSU_SCORES_DIR", "").strip() or None
    songs = None
    songs_env = os.environ.get("OSU_SONGS_DIR", "").strip() or None
    if songs_env:
        songs = [songs_env]
    osu_dir = _maybe_wsl_path(os.environ.get("OSU_DIR", "").strip())
    if osu_dir:
        replays_rel = (os.environ.get("REPLAYS_DIR", "").strip().strip('"\'')
                       or "Data/r")
        songs_rel = (os.environ.get("SONGS_DIR", "").strip().strip('"\'')
                     or "Songs")
        source = source or os.path.join(osu_dir, *filter(None, replays_rel.split("/")))
        songs = songs or [os.path.join(osu_dir, *filter(None, songs_rel.split("/")))]
    if not source:
        source = "/mnt/d/Misc/Game Library/osu!/Data/r"  # 本机默认
    if not songs:
        songs = ["/mnt/d/Misc/Game Library/osu!/Songs"]
    return _maybe_wsl_path(source), [_maybe_wsl_path(s) for s in songs] + [os.path.join(ROOT, "maps")]


SOURCE_DIR, SONGS_DIRS = resolve_dirs()
REPLAY_DIR = os.path.join(ROOT, "r")
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "osu_radar.db")
INDEX_PATH = os.path.join(DATA_DIR, "beatmap_index.json")

PERCENTILES = (50, 75, 90, 95, 99, 99.9)

# ---------------------------------------------------------------- db

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS replays(
        id INTEGER PRIMARY KEY,
        filename TEXT,
        replay_md5 TEXT UNIQUE,
        beatmap_md5 TEXT,
        player TEXT,
        mods TEXT,
        base_ar REAL, base_cs REAL,
        eff_ar REAL, eff_cs REAL,
        n_objects INTEGER,
        percentiles TEXT,
        status TEXT,           -- ok | skipped_mode | missing_beatmap | error
        note TEXT,
        analyzed_at TEXT,
        played_at TEXT
    );
    CREATE TABLE IF NOT EXISTS offset_hist(
        replay_id INTEGER PRIMARY KEY REFERENCES replays(id),
        ar REAL, cs REAL,
        n_objects INTEGER,
        bins TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_hist_ar ON offset_hist(ar);
    CREATE INDEX IF NOT EXISTS idx_hist_cs ON offset_hist(cs);
    """)
    # migrations
    cols = {r[1] for r in con.execute("PRAGMA table_info(replays)")}
    if "played_at" not in cols:
        con.execute("ALTER TABLE replays ADD COLUMN played_at TEXT")
    if "sr_mod" not in cols:
        con.execute("ALTER TABLE replays ADD COLUMN sr_mod REAL")      # rosu-pp 带 mods 星数
    if "mods_bits" not in cols:
        con.execute("ALTER TABLE replays ADD COLUMN mods_bits INTEGER")
    con.commit()
    return con

def backfill_played_at(con):
    """Fill played_at for rows that lack it (header-only read of the .osr)."""
    rows = con.execute(
        "SELECT id, filename FROM replays WHERE played_at IS NULL AND filename IS NOT NULL"
    ).fetchall()
    n = 0
    for rid, fname in rows:
        path = os.path.join(REPLAY_DIR, fname)
        if not os.path.exists(path):
            continue
        ts = parse_osr_timestamp(path)
        con.execute("UPDATE replays SET played_at = ? WHERE id = ?", (ts, rid))
        n += 1
    con.commit()
    return n

# ---------------------------------------------------------------- beatmap index

def _save_index(stat, maps, roots):
    """原子写：先写临时文件再替换，避免 server 并发读到半截 JSON。"""
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"stat": stat, "maps": maps, "roots": roots}, f)
    os.replace(tmp, INDEX_PATH)


def _incremental_update(roots, old_roots, stat, maps):
    """按根目录一级条目做增删：新增条目只扫它自己；消失条目按路径前缀摘除。
    就地更新（同文件夹内 .osu 被 mapper 替换）检测不到——由 replay 找不到图时的
    force 全量重建兜底。返回新哈希的文件数。"""
    hashed = 0
    for d, cur in roots.items():
        old = set(old_roots.get(d, []))
        now = set(cur)
        # 消失的条目：摘除 maps / stat 中以该路径为前缀的条目
        for name in old - now:
            gone = os.path.join(d, name)
            pre = gone + os.sep
            for m in [m for m, p in maps.items() if p == gone or p.startswith(pre)]:
                del maps[m]
            for p in [p for p in stat if p == gone or p.startswith(pre)]:
                del stat[p]
        # 新增的条目：只扫这些路径
        files = []
        for name in now - old:
            p = os.path.join(d, name)
            if os.path.isdir(p):
                for dp, _, ns in os.walk(p):
                    files += [os.path.join(dp, n) for n in ns
                              if n.lower().endswith(".osu")]
            elif name.lower().endswith(".osu"):
                files.append(p)
        for p in files:
            try:
                st = os.stat(p)
                with open(p, "rb") as f:
                    md5 = hashlib.md5(f.read()).hexdigest()
            except OSError:
                continue
            stat[p] = [st.st_size, int(st.st_mtime), md5]
            old_path = maps.get(md5)
            if old_path is None or not os.path.exists(old_path):
                maps[md5] = p  # 已有有效路径（同内容重复文件夹）则不覆盖
            hashed += 1
    return hashed


def build_beatmap_index(force=False):
    """md5 -> path for every .osu under SONGS_DIRS.
    两级增量：根目录条目列表不变 -> 直接跳过（drvfs 上 13k 次 stat 很慢）；
    变了才全量 walk，walk 内部再按 stat 缓存跳过哈希。
    返回 (maps, 文件数, 新哈希数, quick)。"""
    cache = {"stat": {}, "maps": {}}
    if os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH) as f:
                cache = json.load(f)
        except Exception:
            pass
    stat, maps = cache.get("stat", {}), cache.get("maps", {})
    old_roots = cache.get("roots", {})

    # 快速新鲜度检查：各根目录的一级条目列表（Songs 的图集文件夹 / maps 的 .osu）
    roots = {}
    if not force:
        for d in SONGS_DIRS:
            if not os.path.isdir(d):
                continue
            try:
                roots[d] = sorted(os.listdir(d))
            except OSError:
                pass
        if maps and roots and old_roots:
            if roots == old_roots:
                return maps, 0, 0, True  # quick：根未变，跳过全量扫描
            # 文件夹级增量：只扫新增的条目、只摘除消失的条目（成本 O(变化量)）
            hashed = _incremental_update(roots, old_roots, stat, maps)
            _save_index(stat, maps, roots)
            return maps, 0, hashed, False

    files = []
    for d in SONGS_DIRS:
        if not os.path.isdir(d):
            continue
        for dirpath, _, names in os.walk(d):
            for n in names:
                if n.lower().endswith(".osu"):
                    files.append(os.path.join(dirpath, n))

    # 全量重建：maps 从零构建（删除的真正消失，损坏可自愈）。
    # stat 缓存条目为 [size, mtime, md5]：命中则免读文件。
    maps, hashed = {}, 0
    for p in files:
        try:
            st = os.stat(p)
        except OSError:
            continue
        key = [st.st_size, int(st.st_mtime)]
        entry = stat.get(p)
        if entry and entry[:2] == key and len(entry) == 3:
            maps[entry[2]] = p  # 缓存命中（旧 2 元组条目视为未命中，重哈希一次）
            continue
        try:
            with open(p, "rb") as f:
                md5 = hashlib.md5(f.read()).hexdigest()
        except OSError:
            continue
        stat[p] = key + [md5]
        maps[md5] = p
        hashed += 1

    if not roots or force:
        for d in SONGS_DIRS:
            if os.path.isdir(d):
                try:
                    roots[d] = sorted(os.listdir(d))
                except OSError:
                    pass
    _save_index(stat, maps, roots)
    return maps, len(files), hashed, False

# ---------------------------------------------------------------- beatmap cache

_BM_CACHE = {}
_BM_CACHE_MAX = 40

def get_beatmap(md5, index):
    if md5 in _BM_CACHE:
        _BM_CACHE[md5] = _BM_CACHE.pop(md5)  # LRU refresh
        return _BM_CACHE[md5]
    path = index.get(md5)
    if not path:
        return None
    bm = parse_beatmap(path)
    _BM_CACHE[md5] = bm
    if len(_BM_CACHE) > _BM_CACHE_MAX:
        _BM_CACHE.pop(next(iter(_BM_CACHE)))
    return bm

# ---------------------------------------------------------------- copy

def copy_new_replays(limit=None):
    """Copy .osr files from the osu! scores dir into the project r/ dir.
    The source files are only touched by the copy itself — every later step
    (hashing, parsing, analysis) works exclusively on the local copies."""
    if not os.path.isdir(SOURCE_DIR):
        print(f"  scores dir not found: {SOURCE_DIR} (set OSU_SCORES_DIR / --source; skipping copy)")
        return 0
    os.makedirs(REPLAY_DIR, exist_ok=True)
    have = set(os.listdir(REPLAY_DIR))
    copied = 0
    for n in sorted(os.listdir(SOURCE_DIR)):
        if not n.lower().endswith(".osr"):
            continue
        if n in have:
            continue
        try:
            shutil.copy2(os.path.join(SOURCE_DIR, n), os.path.join(REPLAY_DIR, n))
        except OSError as e:
            print(f"  copy failed {n}: {e}")
            continue
        copied += 1
        have.add(n)
        if limit and copied >= limit:
            break
    return copied

# ---------------------------------------------------------------- analysis

def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def analyze_pending(con, index, limit=None, index_quick=False):
    cur = con.cursor()
    full_scan_done = not index_quick  # 快速检查的索引可能陈旧，缺图时全量重建一次
    done_md5 = {r[0] for r in cur.execute("SELECT replay_md5 FROM replays")}
    files = sorted(os.listdir(REPLAY_DIR))
    files = [f for f in files if f.lower().endswith(".osr")]

    analyzed = skipped = missing = errors = dup = 0
    t0 = time.time()
    for i, fname in enumerate(files):
        if limit and analyzed + skipped + missing + errors >= limit:
            break
        path = os.path.join(REPLAY_DIR, fname)
        try:
            md5 = file_md5(path)
        except OSError as e:
            continue
        if md5 in done_md5:
            dup += 1
            continue

        def record(status, note="", rep=None, bm_md5=None):
            cur.execute(
                "INSERT OR IGNORE INTO replays(filename, replay_md5, beatmap_md5, player, mods,"
                " base_ar, base_cs, eff_ar, eff_cs, n_objects, percentiles, status, note,"
                " analyzed_at, played_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fname, md5, bm_md5,
                 rep.get("player", "") if rep else "",
                 "+".join(sorted(decode_mods(rep["mods"]))) if rep else "",
                 None, None, None, None, None, None, status, note,
                 time.strftime("%Y-%m-%d %H:%M:%S"),
                 ticks_to_iso(rep.get("timestamp")) if rep else None))
            con.commit()

        try:
            rep = parse_osr(path)
        except Exception:
            record("error", "osr parse failed")
            errors += 1
            continue

        if rep["mode"] != 0:
            record("skipped_mode", f"mode={rep['mode']}", rep)
            skipped += 1
            continue

        bm_md5 = rep["beatmap_md5"]
        bm = get_beatmap(bm_md5, index)
        if bm is None and not full_scan_done:
            # 索引走了快速检查且没找到图：全量重建一次再试（覆盖图集内部更新的情况）
            full_scan_done = True
            print("  beatmap miss -> full index rescan ...")
            fresh, _, _, _ = build_beatmap_index(force=True)
            index.update(fresh)
            bm = get_beatmap(bm_md5, index)
        if bm is None:
            record("missing_beatmap", "no local .osu with this md5", rep, bm_md5)
            missing += 1
            continue
        if bm.mode != 0:
            record("skipped_mode", f"beatmap mode={bm.mode} (convert)", rep, bm_md5)
            skipped += 1
            continue

        mods = decode_mods(rep["mods"])
        from sr import sr_for
        bm_path = index.get(bm_md5)
        sr_mod = sr_for(bm_path, rep["mods"]) if bm_path else None
        result = compute_offsets(rep, bm, mods)
        offsets = result["offsets"]
        if len(offsets) < 10:
            record("error", f"only {len(offsets)} covered objects", rep, bm_md5)
            errors += 1
            continue

        hist = build_histogram(offsets)
        pct = {str(p): offsets_percentile(offsets, p) for p in PERCENTILES}
        pct["mean"] = sum(offsets) / len(offsets)

        cur.execute(
            "INSERT OR IGNORE INTO replays(filename, replay_md5, beatmap_md5, player, mods,"
            " base_ar, base_cs, eff_ar, eff_cs, n_objects, percentiles, status, note,"
            " analyzed_at, played_at, sr_mod, mods_bits)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fname, md5, bm_md5, rep.get("player", ""),
             "+".join(sorted(mods)),
             bm.approach_rate, bm.circle_size,
             result["eff_ar"], result["eff_cs"],
             len(offsets), json.dumps(pct), "ok", "",
             time.strftime("%Y-%m-%d %H:%M:%S"),
             ticks_to_iso(rep.get("timestamp")),
             sr_mod, rep.get("mods", 0)))
        rid = cur.lastrowid
        cur.execute("INSERT OR REPLACE INTO offset_hist(replay_id, ar, cs, n_objects, bins)"
                    " VALUES(?,?,?,?,?)",
                    (rid, result["eff_ar"], result["eff_cs"], len(offsets),
                     json.dumps({str(k): v for k, v in hist.items()})))
        con.commit()
        analyzed += 1

        if analyzed % 100 == 0:
            el = time.time() - t0
            print(f"  ... {analyzed} analyzed ({el:.0f}s, {el/max(1,analyzed):.2f}s/replay)")

    return analyzed, skipped, missing, errors, dup

# ---------------------------------------------------------------- profile

def print_profile(con, bucket=0.5, model="ar", since="6m"):
    col = "ar" if model == "ar" else "cs"
    cutoff = since_cutoff(since)
    cond = " AND r.played_at >= ?" if cutoff else ""
    params = [cutoff] if cutoff else []
    rows = con.execute(
        f"SELECT h.{col}, h.n_objects, h.bins FROM offset_hist h"
        f" JOIN replays r ON r.id = h.replay_id AND r.status='ok'{cond}", params).fetchall()
    if not rows:
        print(f"-- {model.upper()} profile (since {since or 'all'}): no data --")
        return
    groups = {}
    for val, n, bins in rows:
        key = round(val / bucket) * bucket
        g = groups.setdefault(key, [])
        g.append(histogram_from_json(json.loads(bins)))
    print(f"-- {model.upper()} profile (bucket {bucket}, since {since or 'all'}, {len(rows)} replays) --")
    print(f"  {'val':>5} {'replays':>7} {'objects':>8} {'p50':>7} {'p75':>7} {'p90':>7} {'p95':>7} {'p99':>7} {'p99.9':>7} {'minCS@p95':>10} {'minCS@p99':>10}")
    for key in sorted(groups):
        merged = merge_histograms(groups[key])
        n_obj = sum(sum(h.values()) for h in groups[key])
        vals = [histogram_percentile(merged, p) for p in (50, 75, 90, 95, 99, 99.9)]
        cs95, cs99 = offset_to_cs(vals[3]), offset_to_cs(vals[4])
        print(f"  {key:>5.2f} {len(groups[key]):>7} {n_obj:>8}"
              + "".join(f" {v:>7.1f}" for v in vals)
              + f" {cs95:>10.2f} {cs99:>10.2f}")

# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max replays to analyze this run")
    ap.add_argument("--copy-limit", type=int, default=None, help="max new files to copy this run")
    ap.add_argument("--no-copy", action="store_true")
    ap.add_argument("--no-index", action="store_true")
    ap.add_argument("--profile-only", action="store_true")
    ap.add_argument("--bucket", type=float, default=0.5)
    ap.add_argument("--since", default="6m",
                    help="time window for profiles, e.g. 30d / 6m / 1y / all (default 6m)")
    ap.add_argument("--source", help="osu! scores dir (default $OSU_SCORES_DIR)")
    ap.add_argument("--songs", help="Songs dir (default $OSU_SONGS_DIR); maps/ is always included")
    args = ap.parse_args()

    global SOURCE_DIR, SONGS_DIRS
    if args.source:
        SOURCE_DIR = args.source
    if args.songs:
        SONGS_DIRS = [args.songs, os.path.join(ROOT, "maps")]

    # 目录缺失且未显式配置时，经 tosu 自动定位 osu! 安装（需 osu! 正在运行）
    if (not os.path.isdir(SOURCE_DIR)
            and not os.environ.get("OSU_SCORES_DIR") and not args.source):
        try:
            import tosu_ctl
            found = tosu_ctl.discover(verbose=False)
            if found:
                if found.get("scores") and os.path.isdir(found["scores"]):
                    SOURCE_DIR = found["scores"]
                if (found.get("songs") and os.path.isdir(found["songs"])
                        and not os.environ.get("OSU_SONGS_DIR") and not args.songs):
                    SONGS_DIRS = [found["songs"], os.path.join(ROOT, "maps")]
                print(f"  auto-discovered via tosu: scores={SOURCE_DIR} songs={SONGS_DIRS[0]}")
        except Exception:
            pass

    con = init_db()

    if args.profile_only:
        print_profile(con, args.bucket, "ar", args.since)
        print_profile(con, args.bucket, "cs", args.since)
        return

    if not args.no_copy:
        print("[1/4] copying new replays from osu! scores dir ...")
        n = copy_new_replays(args.copy_limit)
        print(f"  copied {n} new .osr")

    print("[2/4] building beatmap index (incremental) ...")
    index, total, hashed, quick = build_beatmap_index()
    if quick:
        print(f"  quick check ok（Songs 根目录未变，跳过全量扫描；{len(index)} maps）")
    else:
        print(f"  {total} .osu files, {hashed} newly hashed, {len(index)} maps indexed")

    print("[3/4] backfilling play timestamps ...")
    n = backfill_played_at(con)
    print(f"  filled {n} played_at values")

    print("[4/4] analyzing pending replays ...")
    analyzed, skipped, missing, errors, dup = analyze_pending(con, index, args.limit)
    print(f"  analyzed={analyzed} skipped_mode={skipped} missing_beatmap={missing}"
          f" errors={errors} already-done={dup}")

    print()
    print_profile(con, args.bucket, "ar", args.since)
    print()
    print_profile(con, args.bucket, "cs", args.since)

if __name__ == "__main__":
    main()

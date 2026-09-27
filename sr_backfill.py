#!/usr/bin/env python3
"""为已分析的 replay 回填 sr_mod（rosu-pp 本地计算，离线、毫秒级/图）。

用法：uv run python3 sr_backfill.py
"""
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_env
load_env()
import ingest
from sr import sr_for


MOD_BIT = {"NF": 1, "EZ": 2, "TD": 4, "HD": 8, "HR": 16, "SD": 32, "DT": 64,
           "RL": 128, "HT": 256, "NC": 576, "FL": 1024, "AT": 2048, "SO": 4096,
           "AP": 8192, "PF": 16384}


def encode_mods(names):
    bits = 0
    for n in names.split("+"):
        bits |= MOD_BIT.get(n, 0)
    return bits


def main():
    ingest.init_db()
    con = sqlite3.connect(ingest.DB_PATH)
    # 废弃的抓取表（ppy.sh 方案遗留）
    con.executescript("DROP TABLE IF EXISTS beatmaps; DROP TABLE IF EXISTS sr_sets;")
    con.commit()
    rows = con.execute(
        "SELECT id, beatmap_md5, mods, mods_bits FROM replays"
        " WHERE status='ok' AND sr_mod IS NULL AND beatmap_md5 IS NOT NULL").fetchall()
    print(f"待回填 replay: {len(rows)}")
    if not rows:
        return

    index = json.load(open(ingest.INDEX_PATH))["maps"] if os.path.exists(ingest.INDEX_PATH) else {}

    # 按 beatmap 分组：每张图只解析一次；同图同 mods 命中 sr 缓存
    by_map = defaultdict(list)
    for rid, md5, mods, mods_bits in rows:
        path = index.get(md5)
        bits = mods_bits if mods_bits else encode_mods(mods or "")
        if path:
            by_map[path].append((rid, bits))
    skipped = len(rows) - sum(len(v) for v in by_map.values())
    print(f"涉及谱面: {len(by_map)} 张（{skipped} 个 replay 无本地 .osu，跳过）")

    t0, n, milestone = time.time(), 0, 500
    for path, items in by_map.items():
        for rid, mods in items:
            sr = sr_for(path, mods)
            if sr is not None:
                con.execute("UPDATE replays SET sr_mod=? WHERE id=?", (sr, rid))
                n += 1
        con.commit()
        if n >= milestone:
            print(f"  ... {n} replays, {time.time() - t0:.0f}s")
            milestone += 500
    print(f"done: {n}/{len(rows)} replays 已写入 sr_mod，{time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

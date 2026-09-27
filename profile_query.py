#!/usr/bin/env python3
"""Query interface for the osu-radar offset profile.

Usage examples:
  # AR=9.5 bucket curve (default percentiles p50-p99.9)
  python3 profile_query.py --model ar --value 9.5

  # CS=4, custom percentiles
  python3 profile_query.py --model cs --value 4 --percentiles 50,95,99,99.9

  # only DT replays at AR 7.5
  python3 profile_query.py --model ar --value 7.5 --mods DT

  # exclude EZ; single player; machine-readable
  python3 profile_query.py --model cs --value 5 --no-mods EZ --player Tempera --json

  # time window (default 6m — early replays are excluded):
  #   --since 3m | --since 1y | --since all (disable)

  # list all buckets with their weights
  python3 profile_query.py --model ar --list

Programmatic use:
  from profile_query import query_profile
  q = query_profile("ar", 9.5, bucket=0.5)
  print(q["percentiles"]["99"], q["n_objects"])
"""
import argparse
import json
import sqlite3
import os

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import (histogram_from_json, merge_histograms, histogram_percentile,
                     offset_to_cs, since_cutoff)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "osu_radar.db")

DEFAULT_PERCENTILES = (50, 75, 90, 95, 99, 99.9)


def _where(player=None, mods=None, no_mods=None, min_objects=0, cutoff=None,
           sr_center=None, sr_range=0.5):
    """Build SQL filter for replays joined into offset_hist."""
    conds = ["r.status = 'ok'"]
    params = []
    if player:
        conds.append("r.player = ?")
        params.append(player)
    for m in (mods or []):
        conds.append("r.mods LIKE ?")
        params.append(f"%{m}%")
    for m in (no_mods or []):
        conds.append("r.mods NOT LIKE ?")
        params.append(f"%{m}%")
    if min_objects:
        conds.append("h.n_objects >= ?")
        params.append(min_objects)
    if cutoff:
        conds.append("r.played_at >= ?")
        params.append(cutoff)
    if sr_center is not None and sr_range:
        # mod 星数过滤（rosu-pp，与 live 的 tosu stars 同代算法）
        conds.append("r.sr_mod IS NOT NULL AND r.sr_mod BETWEEN ? AND ?")
        params += [sr_center - sr_range, sr_center + sr_range]
    return " AND ".join(conds), params


def query_profile(model, value, bucket=0.5, percentiles=DEFAULT_PERCENTILES,
                  player=None, mods=None, no_mods=None, min_objects=0,
                  since="6m", sr=None, sr_range=0.5, db_path=DB_PATH):
    """Merged weighted percentiles for one AR/CS bucket.

    Returns dict: {model, value, bucket, n_replays, n_objects,
                   percentiles: {p: px}, min_cs: {p: cs}} or None if empty.
    """
    col = "ar" if model == "ar" else "cs"
    lo, hi = value - bucket / 2, value + bucket / 2
    cutoff = since_cutoff(since)
    where, params = _where(player, mods, no_mods, min_objects, cutoff, sr, sr_range)
    con = sqlite3.connect(db_path)
    rows = con.execute(
        f"SELECT h.{col}, h.bins, h.n_objects FROM offset_hist h"
        f" JOIN replays r ON r.id = h.replay_id"
        f" WHERE h.{col} >= ? AND h.{col} < ? AND {where}",
        [lo, hi] + params).fetchall()
    if not rows:
        return None
    hists = [histogram_from_json(json.loads(b)) for _, b, _ in rows]
    merged = merge_histograms(hists)
    n_objects = sum(n for _, _, n in rows)
    pct = {p: histogram_percentile(merged, p) for p in percentiles}
    return {
        "model": model, "value": value, "bucket": bucket,
        "n_replays": len(rows), "n_objects": n_objects,
        "percentiles": pct,
        "min_cs": {p: offset_to_cs(v) for p, v in pct.items()},
    }


def list_buckets(model, bucket=0.5, player=None, mods=None, no_mods=None,
                 min_objects=0, since="6m", sr=None, sr_range=0.5, db_path=DB_PATH):
    """All buckets with replay/object weights (before merging)."""
    col = "ar" if model == "ar" else "cs"
    cutoff = since_cutoff(since)
    where, params = _where(player, mods, no_mods, min_objects, cutoff, sr, sr_range)
    con = sqlite3.connect(db_path)
    rows = con.execute(
        f"SELECT CAST(ROUND(h.{col} / {bucket}) AS INTEGER), COUNT(*), SUM(h.n_objects)"
        f" FROM offset_hist h"
        f" JOIN replays r ON r.id = h.replay_id"
        f" WHERE {where} GROUP BY 1 ORDER BY 1", params).fetchall()
    return [{"value": r[0] * bucket, "n_replays": r[1], "n_objects": r[2]}
            for r in rows]


def _fmt(q):
    line1 = (f"{q['model'].upper()}={q['value']} (bucket {q['bucket']}, "
             f"{q['n_replays']} replays, {q['n_objects']} objects)")
    ps = sorted(q["percentiles"], key=float)
    line2 = "  " + "".join(f"p{p:<8}" for p in ps)
    line3 = "  " + "".join(f"{q['percentiles'][p]:<9.1f}" for p in ps)
    line4 = "  minCS " + "".join(
        f"{q['min_cs'][p]:<9.2f}" if -50 < q["min_cs"][p] < 50 else f"{'n/a':<9}" for p in ps)
    return "\n".join([line1, line2 + "(px)", line3, line4])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=["ar", "cs"], required=True)
    ap.add_argument("--value", type=float, help="AR/CS bucket centre, e.g. 9.5")
    ap.add_argument("--bucket", type=float, default=0.5)
    ap.add_argument("--percentiles", default=",".join(str(p) for p in DEFAULT_PERCENTILES))
    ap.add_argument("--player")
    ap.add_argument("--mods", help="only replays containing these mods (comma-sep)")
    ap.add_argument("--no-mods", help="exclude replays containing these mods (comma-sep)")
    ap.add_argument("--min-objects", type=int, default=0)
    ap.add_argument("--since", default="6m",
                    help="time window: 30d / 6m / 1y / all (default 6m; early replays excluded)")
    ap.add_argument("--sr", type=float, help="mod 星数过滤中心（rosu-pp 带 mods）")
    ap.add_argument("--sr-range", type=float, default=0.5,
                    help="mod 星数过滤半径（默认 0.5；0 关闭）")
    ap.add_argument("--list", action="store_true", help="list all buckets with weights")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    mods = [m.strip() for m in args.mods.split(",")] if args.mods else None
    no_mods = [m.strip() for m in args.no_mods.split(",")] if args.no_mods else None
    percentiles = [float(p) for p in args.percentiles.split(",")]

    if args.list:
        out = list_buckets(args.model, args.bucket, args.player, mods, no_mods,
                           args.min_objects, args.since)
        print(json.dumps(out, indent=2) if args.json else
              "\n".join(f"{b['value']:>7.2f}  {b['n_replays']:>6} replays  {b['n_objects']:>9} objects"
                        for b in out))
        return

    if args.value is None:
        ap.error("--value is required (or use --list)")

    q = query_profile(args.model, args.value, args.bucket, percentiles,
                      args.player, mods, no_mods, args.min_objects, args.since,
                      args.sr, args.sr_range)
    if q is None:
        print("no data for this query")
        return
    print(json.dumps(q, indent=2) if args.json else _fmt(q))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""osu-radar web frontend server.

Serves the built Vue app (frontend/dist) plus a small JSON API backed by
data/osu_radar.db:

  GET  /api/buckets?model=ar|cs&bucket=0.5[&player=&mods=&no_mods=&min_objects=]
       -> [{value, n_replays, n_objects}, ...]
  GET  /api/live?model=ar|cs[&filters]  -> current map (tosu) + profile + grade prediction
  GET  /api/profile?model=ar|cs&value=9.5&bucket=0.5
                   [&player=&mods=&no_mods=&min_objects=&since=6m|all]
       -> {model, value, bucket, n_replays, n_objects,
           percentiles: {p: px}, min_cs: {p: cs}, hist: {bin: count}}
  GET  /api/config   -> 配置页状态：四个路径 + 校验 + 主题色 + 贴图版本
  POST /api/config/paths  {osu, tosu, replays, songs} -> 写入 .env、重置 tosu、后台导入
  GET  /api/theme    -> {theme: {S:[h,s,l],...}, icons_version}
  POST /api/theme    {theme: {...}} -> 持久化到 data/ui.json
  POST /api/icons/{s|a|b|c|d}  (raw image body) -> 覆盖 assets/ 中的 rank 贴图
  GET  /assets/<file> -> 项目根 assets/ 优先（运行时可替换），回退 frontend/dist

usage: python3 server.py [--port 25431] [--host 127.0.0.1] [--no-browser]
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_env, set_env
load_env()
from analyze import (histogram_from_json, merge_histograms, histogram_percentile,
                     offset_to_cs, since_cutoff, decode_mods, effective_ar,
                     effective_cs, cs_to_radius)
from osu_core import parse_beatmap

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "data", "osu_radar.db")
DIST_DIR = os.path.join(ROOT, "frontend", "dist")

PERCENTILES = (50, 75, 90, 95, 99, 99.9)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".map": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}

# ---------------------------------------------------------------- ui config

ASSETS_DIR = os.path.join(ROOT, "assets")
UI_CFG_PATH = os.path.join(ROOT, "data", "ui.json")
RANK_ICON = {g: f"ranking-{g}-small@2x.png" for g in ("s", "a", "b", "c", "d")}
# 与 frontend/src/theme.js 的 DEFAULT_COLORS 保持一致
DEFAULT_THEME = {"D": [347, 75, 60], "C": [272, 60, 62], "B": [214, 75, 60],
                 "A": [121, 70, 45], "S": [56, 85, 55]}


def _maybe_wsl_path(p):
    r"""WSL 下把 D:\x\y 换算为 /mnt/d/x/y，其余原样返回（与 ingest 一致）。"""
    if p and sys.platform.startswith("linux"):
        p2 = p.replace("\\", "/")
        m = re.match(r"^([A-Za-z]):/(.*)$", p2)
        if m and not os.path.isdir(p):
            return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
    return p


def effective_paths():
    """配置页展示的四个有效路径（优先级与 ingest.resolve_dirs 一致）。
    OSU_DIR 为空且 tosu 可达时自动探测（仅展示值，不写 .env；保存时才落盘）。"""
    osu = os.environ.get("OSU_DIR", "").strip()
    replays = os.environ.get("OSU_SCORES_DIR", "").strip()
    songs = os.environ.get("OSU_SONGS_DIR", "").strip()
    if osu:
        rel_r = os.environ.get("REPLAYS_DIR", "").strip().strip("\"'") or "Data/r"
        rel_s = os.environ.get("SONGS_DIR", "").strip().strip("\"'") or "Songs"
        replays = replays or os.path.join(osu, *filter(None, rel_r.split("/")))
        songs = songs or os.path.join(osu, *filter(None, rel_s.split("/")))
    else:
        import tosu_ctl
        found = tosu_ctl.discover(verbose=False)
        if found:
            osu = found.get("game") or ""
            replays = replays or found.get("scores") or ""
            songs = songs or found.get("songs") or ""
    tosu = os.environ.get("TOSU_PATH", "").strip()
    if not tosu:
        # 未配置路径时把自动探测到的运行实例地址作为展示值（UI autofill，无需用户手填）
        import tosu_ctl
        tosu = tosu_ctl.find_url() or ""
    return osu, tosu, replays, songs


def _dir_state(p):
    if not p:
        return "empty"
    return "ok" if os.path.isdir(_maybe_wsl_path(p)) else "missing"


def paths_status(osu, tosu, replays, songs, probe=False):
    import tosu_ctl
    st = {"osu": _dir_state(osu), "replays": _dir_state(replays),
          "songs": _dir_state(songs)}
    if tosu.startswith(("http://", "https://")):
        # 运行实例地址（自动探测回填的展示值）
        st["tosu"] = "ok" if (not probe or tosu_ctl.probe(tosu, timeout=1.2)) else "unreachable"
    elif tosu:
        st["tosu"] = "ok" if os.path.isfile(_maybe_wsl_path(tosu)) else "missing"
    else:
        b = tosu_ctl.bundled_binary()
        st["tosu"] = "ok" if b and os.path.isfile(b) else "empty"
    return st


def config_payload(probe=False):
    osu, tosu, replays, songs = effective_paths()
    st = paths_status(osu, tosu, replays, songs, probe)
    return {
        "paths": {"osu": osu, "tosu": tosu, "replays": replays, "songs": songs},
        "status": st,
        "ready": {"osu": st["osu"] == "ok", "tosu": st["tosu"] == "ok"},
        "theme": load_theme(),
        "icons_version": icons_version(),
    }


def save_paths(body):
    """写入四个路径到 .env，重置 tosu 缓存并后台增量导入（配置立即生效）。"""
    import tosu_ctl
    osu = (body.get("osu") or "").strip()
    tosu = (body.get("tosu") or "").strip()
    replays = (body.get("replays") or "").strip()
    songs = (body.get("songs") or "").strip()

    # tosu 留空 = 内置 runtime（tosu/）；已运行的实例会被自动探测复用，无需配置
    if not osu:  # 留空 = 经 tosu 探测 osu! 安装位置（需 osu! 正在运行）
        found = tosu_ctl.discover(verbose=False)
        if found and found.get("game"):
            osu = found["game"]
            replays = replays or found.get("scores") or ""
            songs = songs or found.get("songs") or ""
    if osu:
        replays = replays or os.path.join(osu, "Data", "r")
        songs = songs or os.path.join(osu, "Songs")

    # http:// 值是自动探测到的实例地址，仅作展示：不落盘（下次仍自动探测复用）
    tosu_path = "" if tosu.startswith(("http://", "https://")) else tosu
    entries = {"OSU_DIR": osu, "OSU_SCORES_DIR": replays, "OSU_SONGS_DIR": songs,
               "TOSU_PATH": tosu_path}
    set_env(entries)

    # 立即生效：tosu 重新解析（后台预热，避免阻塞响应），并后台跑增量导入
    with _TOSU_LOCK:
        _TOSU_CACHE[0] = None
        _TOSU_FAIL_AT[0] = 0.0
    threading.Thread(target=_tosu_url, daemon=True).start()
    kick_ingest()

    osu, tosu, replays, songs = effective_paths()
    st = paths_status(osu, tosu, replays, songs, probe=True)
    return {
        "ok": True,
        "paths": {"osu": osu, "tosu": tosu, "replays": replays, "songs": songs},
        "status": st,
        "ready": {"osu": st["osu"] == "ok", "tosu": st["tosu"] == "ok"},
        "theme": load_theme(),
        "icons_version": icons_version(),
    }


def load_theme():
    t = {k: list(v) for k, v in DEFAULT_THEME.items()}
    try:
        with open(UI_CFG_PATH) as f:
            saved = json.load(f).get("theme", {})
        for g, c in saved.items():
            if g in t and isinstance(c, (list, tuple)) and len(c) == 3:
                t[g] = [float(c[0]), float(c[1]), float(c[2])]
    except Exception:
        pass
    return t


def save_theme(theme):
    os.makedirs(os.path.dirname(UI_CFG_PATH), exist_ok=True)
    tmp = UI_CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"theme": theme}, f)
    os.replace(tmp, UI_CFG_PATH)


def icons_version():
    v = 0
    for name in RANK_ICON.values():
        try:
            v = max(v, os.stat(os.path.join(ASSETS_DIR, name)).st_mtime_ns)
        except OSError:
            pass
    return str(v)


_INGEST_LOCK = threading.Lock()
_INGEST_PROC = [None]


def kick_ingest():
    """后台跑增量导入（配置变更后立即生效，不等下次启动）。已在跑则跳过。"""
    with _INGEST_LOCK:
        if _INGEST_PROC[0] is not None and _INGEST_PROC[0].poll() is None:
            return False
        try:
            log = open(os.path.join(ROOT, "data", "ingest.log"), "ab")
            _INGEST_PROC[0] = subprocess.Popen(
                [sys.executable, os.path.join(ROOT, "ingest.py")], cwd=ROOT,
                stdout=log, stderr=subprocess.STDOUT)
            return True
        except OSError:
            return False

# ---------------------------------------------------------------- db queries

def _where(player=None, mods=None, no_mods=None, min_objects=0, cutoff=None,
           sr_center=None, sr_range=0.5):
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
        conds.append("r.sr_mod IS NOT NULL AND r.sr_mod BETWEEN ? AND ?")
        params += [sr_center - sr_range, sr_center + sr_range]
    return " AND ".join(conds), params


def query_buckets(model, bucket, player=None, mods=None, no_mods=None, min_objects=0,
                  since="6m", sr=None, sr_range=1.0):
    col = "ar" if model == "ar" else "cs"
    where, params = _where(player, mods, no_mods, min_objects, since_cutoff(since),
                           sr, sr_range)
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        f"SELECT CAST(ROUND(h.{col} / ?) AS INTEGER), COUNT(*), SUM(h.n_objects)"
        f" FROM offset_hist h"
        f" JOIN replays r ON r.id = h.replay_id"
        f" WHERE {where} GROUP BY 1 ORDER BY 1", [bucket] + params).fetchall()
    return [{"value": r[0] * bucket, "n_replays": r[1], "n_objects": r[2]}
            for r in rows]


def query_profile(model, value, bucket, player=None, mods=None, no_mods=None, min_objects=0,
                  since="6m", sr=None, sr_range=1.0):
    col = "ar" if model == "ar" else "cs"
    lo, hi = value - bucket / 2, value + bucket / 2
    where, params = _where(player, mods, no_mods, min_objects, since_cutoff(since),
                           sr, sr_range)
    con = sqlite3.connect(DB_PATH)
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
    pct = {p: histogram_percentile(merged, p) for p in PERCENTILES}
    return {
        "model": model, "value": value, "bucket": bucket, "sr": sr,
        "n_replays": len(rows), "n_objects": n_objects,
        "percentiles": pct,
        "min_cs": {p: offset_to_cs(v) for p, v in pct.items()},
        "hist": merged,
        "sample": _sample_replays(con, where, params, col, lo, hi),
    }




# ---------------------------------------------------------------- live (tosu)

GRADES = [("S", 100), ("A", 90), ("B", 80), ("C", 70), ("D", 60)]

INDEX_PATH = os.path.join(ROOT, "data", "beatmap_index.json")
_STATS_CACHE = {}  # md5 -> (base_ar, base_cs)
_TOSU_CACHE = [None]
_TOSU_FAIL_AT = [0.0]  # 上次解析失败时间（冷却期内不重试）
_TOSU_LOCK = threading.Lock()

def _tosu_url():
    """Resolution: running instance (auto-probed) > TOSU_PATH / internal runtime.
    只有成功才缓存；失败冷却 10s 后重试（tosu 后启动也能自动恢复）。"""
    if _TOSU_CACHE[0] is None:
        with _TOSU_LOCK:
            if _TOSU_CACHE[0] is None and time.time() - _TOSU_FAIL_AT[0] >= 10:
                import tosu_ctl
                url = tosu_ctl.ensure()
                if url:
                    _TOSU_CACHE[0] = url
                else:
                    _TOSU_FAIL_AT[0] = time.time()
    return _TOSU_CACHE[0] or ""

_META_CACHE = {}  # md5 -> (title, version)


def _map_meta(md5):
    """谱面标题/难度名（轻量缓存，用于样本清单展示）。"""
    if md5 in _META_CACHE:
        return _META_CACHE[md5]
    path = _index_path(md5)
    if not path:
        return None
    try:
        bm = parse_beatmap(path)
        meta = (bm.metadata.get("Title"), bm.metadata.get("Version"))
    except Exception:
        return None
    _META_CACHE[md5] = meta
    return meta


def _sample_replays(con, where, params, col, lo, hi, limit=10):
    """构成当前画像的最近 N 个 replay（预测透明度）。"""
    rows = con.execute(
        f"SELECT r.beatmap_md5, r.mods, r.sr_mod, r.n_objects, r.played_at"
        f" FROM offset_hist h JOIN replays r ON r.id = h.replay_id"
        f" WHERE h.{col} >= ? AND h.{col} < ? AND {where}"
        f" ORDER BY r.played_at DESC LIMIT ?", [lo, hi] + params + [limit]).fetchall()
    out = []
    for md5, mods, sr, n, played in rows:
        title, version = _map_meta(md5) or ("?", "?")
        out.append({"title": title, "version": version, "mods": mods,
                    "sr": sr, "objects": n, "played_at": played})
    return out


def _index_path(md5):
    try:
        with open(INDEX_PATH) as f:
            return json.load(f)["maps"].get(md5)
    except Exception:
        return None


def _base_stats(md5):
    """base AR/CS from the local Songs index (consistent with the DB pipeline)."""
    if md5 in _STATS_CACHE:
        return _STATS_CACHE[md5]
    path = _index_path(md5)
    if not path:
        return None
    try:
        bm = parse_beatmap(path)
    except Exception:
        return None
    stats = (bm.approach_rate, bm.circle_size)
    _STATS_CACHE[md5] = stats
    return stats

def grade_prediction(hist, cs_eff):
    """Predicted safety grade at an effective CS: the strictest grade whose
    containment percentile fits inside that CS circle."""
    radius = cs_to_radius(cs_eff)
    grades = []
    predicted = None
    for g, p in GRADES:  # strictest first
        px = histogram_percentile(hist, p)
        fits = px <= radius
        grades.append({"grade": g, "p": p, "offset_px": px,
                       "cs": offset_to_cs(px), "fits": fits})
        if fits and predicted is None:
            predicted = g
    return predicted, grades

def query_live(model="ar", bucket=0.5, player=None, mods=None, no_mods=None,
               min_objects=0, since="6m", sr_range=0.5):
    """Current map from tosu -> effective AR/CS -> profile + grade prediction."""
    import urllib.request
    url = _tosu_url()
    if not url:
        return {"error": "tosu 不可用（启动 tosu，或将二进制放入 tosu/、设置 TOSU_PATH）"}, 503
    try:
        with urllib.request.urlopen(f"{url}/json/v2", timeout=2.5) as r:
            data = json.loads(r.read())
    except Exception as e:
        _TOSU_CACHE[0] = None  # re-detect next call
        return {"error": f"tosu fetch failed: {e}"}, 502

    # tosu v2 真实结构（以运行中实例为准）：顶层 beatmap / play / folders / state{number,name}
    bm = data.get("beatmap", {}) or {}
    md5 = (bm.get("checksum") or "").lower()
    stats = bm.get("stats", {}) or {}
    state = data.get("state", {})
    if isinstance(state, dict):
        state = state.get("number")
    mods_num = ((data.get("play") or {}).get("mods") or {}).get("number") or 0
    mods_set = decode_mods(mods_num)

    base = _base_stats(md5)
    if base:
        base_ar, base_cs = base
        eff_ar = effective_ar(base_ar, mods_set)
        eff_cs = effective_cs(base_cs, mods_set)
        source = "local index"
    elif (stats.get("ar") or {}).get("converted") is not None:
        # fallback: tosu's memory-processed values (already mod-applied)
        base_ar = base_cs = None
        eff_ar = float(stats["ar"]["converted"])
        eff_cs = float(stats["cs"]["converted"])
        source = "tosu stats"
    else:
        return {"error": "no beatmap info from tosu"}, 502

    # 当前图的 mod 星数：本地 rosu-pp 计算（与批量 sr_mod 同一代码路径，口径必然一致；
    # tosu 的 stats.stars.total 实测不可靠——不同图返回相同陈旧值，疑似 nomod 名义值）
    cur_sr = None
    if md5:
        path = _index_path(md5)
        if path:
            from sr import sr_for
            cur_sr = sr_for(path, mods_num)
    mode_value = eff_ar if model == "ar" else eff_cs
    prof = query_profile(model, round(mode_value / bucket) * bucket, bucket,
                         player, mods, no_mods, min_objects, since, cur_sr, sr_range)
    out = {
        "tosu": url,
        "state": state,
        "map": {
            "md5": md5,
            "title": bm.get("title"),
            "difficulty": bm.get("version"),
            "base_ar": base_ar, "base_cs": base_cs,
            "eff_ar": eff_ar, "eff_cs": eff_cs,
        },
        "mods": sorted(mods_set),
        "sr": cur_sr,
        "sr_range": sr_range,
        "stats_source": source,
        "profile": prof,  # may be None (no data at this bucket)
        "focus_cs": eff_cs,
        "focus_value": mode_value,
    }
    if prof:
        predicted, grades = grade_prediction(
            {int(k): v for k, v in prof["hist"].items()}, eff_cs)
        out["predicted_grade"] = predicted
        out["grades"] = grades
    return out, 200


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _filters(self, q):
        player = q.get("player", [None])[0] or None
        mods = [m.strip() for m in q.get("mods", [""])[0].split(",") if m.strip()] or None
        no_mods = [m.strip() for m in q.get("no_mods", [""])[0].split(",") if m.strip()] or None
        since = q.get("since", ["6m"])[0] or "all"
        try:
            min_objects = int(q.get("min_objects", ["0"])[0])
        except ValueError:
            min_objects = 0
        try:
            sr = float(q.get("sr", [""])[0]) if q.get("sr", [""])[0] else None
        except ValueError:
            sr = None
        try:
            sr_range = float(q.get("sr_range", ["0.5"])[0])
        except ValueError:
            sr_range = 0.5
        return player, mods, no_mods, min_objects, since, sr, sr_range

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)

        try:
            if url.path == "/api/buckets":
                model = q.get("model", ["ar"])[0]
                if model not in ("ar", "cs"):
                    return self._send_json({"error": "model must be ar|cs"}, 400)
                bucket = float(q.get("bucket", ["0.5"])[0])
                player, mods, no_mods, min_objects, since, sr, sr_range = self._filters(q)
                return self._send_json(query_buckets(model, bucket, player, mods, no_mods,
                                                     min_objects, since, sr, sr_range))

            if url.path == "/api/live":
                model = q.get("model", ["ar"])[0]
                if model not in ("ar", "cs"):
                    return self._send_json({"error": "model must be ar|cs"}, 400)
                bucket = float(q.get("bucket", ["0.5"])[0])
                player, mods, no_mods, min_objects, since, _sr, sr_range = self._filters(q)
                out, status = query_live(model, bucket, player, mods, no_mods,
                                         min_objects, since, sr_range)
                return self._send_json(out, status)

            if url.path == "/api/profile":
                model = q.get("model", ["ar"])[0]
                if model not in ("ar", "cs"):
                    return self._send_json({"error": "model must be ar|cs"}, 400)
                try:
                    value = float(q.get("value", [""])[0])
                    bucket = float(q.get("bucket", ["0.5"])[0])
                except ValueError:
                    return self._send_json({"error": "value/bucket must be numeric"}, 400)
                player, mods, no_mods, min_objects, since, sr, sr_range = self._filters(q)
                out = query_profile(model, value, bucket, player, mods, no_mods, min_objects,
                                    since, sr, sr_range)
                if out is None:
                    return self._send_json({"error": "no data for this query"}, 404)
                return self._send_json(out)

            if url.path == "/api/config":
                return self._send_json(config_payload())

            if url.path == "/api/theme":
                return self._send_json({"theme": load_theme(),
                                        "icons_version": icons_version()})

            return self._serve_static(url.path)
        except Exception as e:  # pragma: no cover
            return self._send_json({"error": f"internal: {e}"}, 500)

    def do_POST(self):
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > 16 * 1024 * 1024:
            return self._send_json({"error": "body too large"}, 413)
        body = self.rfile.read(length) if length else b""

        try:
            if url.path == "/api/config/paths":
                try:
                    data = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    return self._send_json({"error": "invalid json"}, 400)
                return self._send_json(save_paths(data))

            if url.path == "/api/theme":
                try:
                    data = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    return self._send_json({"error": "invalid json"}, 400)
                cur = load_theme()
                for g, c in (data.get("theme") or {}).items():
                    if g in cur and isinstance(c, (list, tuple)) and len(c) == 3:
                        try:
                            h, s, l = float(c[0]), float(c[1]), float(c[2])
                        except (TypeError, ValueError):
                            continue
                        cur[g] = [min(360, max(0, h)), min(100, max(0, s)),
                                  min(100, max(0, l))]
                save_theme(cur)
                return self._send_json({"ok": True, "theme": cur,
                                        "icons_version": icons_version()})

            m = re.fullmatch(r"/api/icons/([sabcd])", url.path)
            if m:
                if not (body.startswith(b"\x89PNG") or body.startswith(b"\xff\xd8")
                        or body.startswith(b"GIF8") or body[:4] == b"RIFF"):
                    return self._send_json(
                        {"error": "not an image (png/jpg/gif/webp)"}, 400)
                with open(os.path.join(ASSETS_DIR, RANK_ICON[m.group(1)]), "wb") as f:
                    f.write(body)
                return self._send_json({"ok": True, "icons_version": icons_version()})

            return self._send_json({"error": "not found"}, 404)
        except Exception as e:  # pragma: no cover
            return self._send_json({"error": f"internal: {e}"}, 500)

    def _send_file(self, full):
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        if path.startswith("/assets/"):
            # 运行时可替换资源（rank 贴图等）优先从项目根 assets/ 提供
            full = os.path.normpath(os.path.join(ASSETS_DIR, path[len("/assets/"):]))
            if full.startswith(ASSETS_DIR + os.sep) and os.path.isfile(full):
                return self._send_file(full)
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(DIST_DIR, rel))
        if not full.startswith(DIST_DIR + os.sep):
            return self.send_error(403)
        if not os.path.isfile(full) and not path.startswith("/api"):
            path = "/index.html"  # SPA fallback（/ar /cs overlay、/debug 路由）
            full = os.path.join(DIST_DIR, "index.html")
        if not os.path.isfile(full):
            if not os.path.isdir(DIST_DIR):
                body = (b"frontend/dist not built yet - run: cd frontend && npm install "
                        b"&& npm run build")
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)
            return
        self._send_file(full)


def _open_browser(url):
    """WSL 下 webbrowser/xdg-open 会退化为 gio 并报 Operation not supported，
    优先用 Windows 侧浏览器；原生平台走 webbrowser。输出全部丢弃。"""
    import shutil
    import subprocess
    devnull = subprocess.DEVNULL
    try:
        if shutil.which("wslview"):
            subprocess.Popen(["wslview", url], stdout=devnull, stderr=devnull)
            return
        try:
            with open("/proc/version") as f:
                is_wsl = "microsoft" in f.read().lower()
        except OSError:
            is_wsl = False
        if is_wsl and shutil.which("explorer.exe"):
            # explorer.exe 打开默认浏览器（即使成功也返回非零，忽略）
            subprocess.Popen(["explorer.exe", url], stdout=devnull, stderr=devnull)
            return
        webbrowser.open(url)
    except Exception:
        pass


PID_FILE = os.path.join(ROOT, "data", "server.pid")
PORT_ATTEMPTS = 10  # 首选端口被占用则依次 +1，最多尝试 10 个


def bind_server(host, preferred):
    """在 preferred..preferred+9 里找第一个空闲端口绑定。"""
    last_err = None
    for port in range(preferred, preferred + PORT_ATTEMPTS):
        try:
            return ThreadingHTTPServer((host, port), Handler), port
        except OSError as e:
            last_err = e
    raise SystemExit(f"端口 {preferred}~{preferred + PORT_ATTEMPTS - 1} 均被占用: {last_err}")


def _remove_pid_file():
    try:
        with open(PID_FILE) as f:
            if f.read().split()[0] == str(os.getpid()):  # 只删自己写的
                os.remove(PID_FILE)
    except (OSError, ValueError, IndexError):
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None,
                    help="显式指定端口（占用即报错）；缺省用 $PORT 或 25431，占用自动顺延 +1")
    ap.add_argument("--no-browser", action="store_true",
                    help="启动后不自动打开浏览器（同 NO_BROWSER=1）")
    args = ap.parse_args()

    preferred = int(os.environ.get("PORT") or 25431)
    if args.port is not None:
        srv = ThreadingHTTPServer((args.host, args.port), Handler)
        port = args.port
    else:
        srv, port = bind_server(args.host, preferred)

    # 记录 PID + 实际端口；Ctrl+C / SIGTERM / 正常退出统一走 teardown：
    # 删自己的 .pid + 停掉本工具拉起的 tosu（用户自启的实例不动）
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(f"{os.getpid()} {port}\n")
    import atexit
    import signal

    def _shutdown():
        _remove_pid_file()
        try:
            import tosu_ctl
            tosu_ctl.stop()
        except Exception:
            pass

    atexit.register(_shutdown)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: sys.exit(0))  # 交给 atexit 统一 teardown

    # 周期增量入库：运行中新打完的图也会自动进模型（快路径下每轮仅数秒；
    # INGEST_INTERVAL 秒数可调，0 关闭）
    interval = float(os.environ.get("INGEST_INTERVAL") or 60)
    if interval > 0:

        def _ingest_loop():
            while True:
                time.sleep(interval)
                kick_ingest()

        threading.Thread(target=_ingest_loop, daemon=True).start()

    url = f"http://{args.host}:{port}/"
    # 仿 uvicorn 启动通告
    print(f"INFO:     Started server process [{os.getpid()}]")
    if port != preferred:
        print(f"INFO:     port {preferred} in use, falling back to {port}")
    print(f"INFO:     osu-radar running on {url} (Press CTRL+C to quit)")
    if os.environ.get("NO_BROWSER") != "1" and not args.no_browser:
        threading.Timer(0.5, lambda: _open_browser(url)).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
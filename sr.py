#!/usr/bin/env python3
"""mod 星数计算（rosu-pp，2025-10 官方算法重做实现，与 tosu 内置同代）。

sr_for(path, mods_bits) -> 带 mods 的 total stars，按 (path, mods) 缓存。
"""
import threading

from rosu_pp_py import Beatmap, Difficulty

_BM_CACHE = {}
_BM_CACHE_MAX = 30
_SR_CACHE = {}
_LOCK = threading.Lock()


def _beatmap(path):
    with _LOCK:
        if path in _BM_CACHE:
            return _BM_CACHE[path]
        bm = Beatmap(path=path)
        _BM_CACHE[path] = bm
        if len(_BM_CACHE) > _BM_CACHE_MAX:
            _BM_CACHE.pop(next(iter(_BM_CACHE)))
        return bm


def sr_for(path, mods_bits):
    """带 mods 的 total stars；失败返回 None。"""
    key = (path, mods_bits)
    if key in _SR_CACHE:
        return _SR_CACHE[key]
    try:
        d = Difficulty(mods=mods_bits).calculate(_beatmap(path))
        sr = round(d.stars, 4)
    except Exception:
        sr = None
    _SR_CACHE[key] = sr
    return sr

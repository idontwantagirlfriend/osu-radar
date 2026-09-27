#!/usr/bin/env python3
"""极简 .env 加载器（零依赖）：KEY=VALUE 行，# 注释，已存在的环境变量不覆盖。"""
import os


def load_env(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.isfile(path):
        return {}
    loaded = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
                loaded[k] = v
    return loaded


def set_env(entries, path=None):
    """更新 .env 中的键值（保留其他行与注释），并同步到当前进程环境。"""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    lines = []
    if os.path.isfile(path):
        lines = open(path, encoding="utf-8").read().splitlines()
    for key, val in entries.items():
        os.environ[key] = val
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}=") and not line.strip().startswith("#"):
                lines[i] = f"{key}={val}"
                found = True
                break
        if not found:
            lines.append(f"{key}={val}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")

#!/usr/bin/env python3
"""tosu 管理器（跨平台：Windows 原生 / WSL / Linux 原生）。

平台模型：
- windows : osu-radar 直接跑在 Windows（Python 原生），tosu.exe 同机直跑
- wsl     : osu-radar 跑在 WSL，tosu.exe 经 interop 在 Windows 侧运行（osu! stable）
- linux   : osu-radar 跑在 Linux 原生，tosu-linux 直跑（osu!lazer Linux）

与 tosu 的耦合策略：
- 已有运行实例（24050）→ 直接复用（"正常安装版"）
- 无实例但内置二进制存在（portable 预装，tosu/ 目录）→ 拉起
- 都没有 → 提示放置二进制

用法：
  uv run python3 tosu_ctl.py status | start | stop | discover
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_env, set_env

load_env()  # .env 中的 TOSU_PATH / OSU_* 优先生效

ROOT = os.path.dirname(os.path.abspath(__file__))
# 内置 runtime 目录（固定；发布包预装 tosu 二进制到这里）
TOSU_DIR = os.path.join(ROOT, "tosu")
PID_FILE = os.path.join(ROOT, "data", "tosu.pid")
LOG_FILE = os.path.join(ROOT, "data", "tosu.log")
TOSU_PORT = 24050
RELEASE_API = "https://api.github.com/repos/tosuapp/tosu/releases/latest"


def detect_platform():
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/version") as f:
                if "microsoft" in f.read().lower():
                    return "wsl"
        except OSError:
            pass
        return "linux"
    return sys.platform  # darwin 等：仅探测，不内置


PLATFORM = detect_platform()


def bundled_binary():
    """优先 .env 的 TOSU_PATH（配置页写入），否则内置 runtime 目录按平台取名。"""
    env_path = os.environ.get("TOSU_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path
    if PLATFORM in ("windows", "wsl"):
        return os.path.join(TOSU_DIR, "tosu.exe")
    if PLATFORM == "linux":
        return os.path.join(TOSU_DIR, "tosu-linux")
    return None


def candidate_hosts():
    hosts = ["127.0.0.1"]
    if PLATFORM == "wsl":
        try:
            out = subprocess.check_output(["ip", "route", "show", "default"],
                                          text=True, timeout=2)
            if "via" in out:
                hosts.append(out.split("via")[1].split()[0])
        except Exception:
            pass
    return hosts


def probe(url, timeout=0.8):
    try:
        urllib.request.urlopen(f"{url}/json/v2", timeout=timeout)
        return True
    except Exception:
        return False


def find_url():
    for host in candidate_hosts():
        url = f"http://{host}:{TOSU_PORT}"
        if probe(url):
            return url
    return None


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def our_pid():
    try:
        return int(open(PID_FILE).read().strip())
    except Exception:
        return None


def start(wait=8):
    """确保 tosu 在跑：有实例复用；没有则拉起 TOSU_PATH / portable 二进制。"""
    url = find_url()
    if url:
        return url
    exe = bundled_binary()
    if not exe or not os.path.isfile(exe):
        print(f"no tosu binary at {exe} — 将二进制放入 tosu/（portable 预装）或设置 TOSU_PATH")
        return None
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    log = open(LOG_FILE, "ab")
    try:
        proc = subprocess.Popen([exe], cwd=os.path.dirname(exe) or ".", stdout=log,
                                 stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        print(f"failed to launch {exe}: {e}")
        return None
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    deadline = time.time() + wait
    while time.time() < deadline:
        url = find_url()
        if url:
            print(f"tosu started (pid {proc.pid}, {PLATFORM}) -> {url}")
            return url
        if proc.poll() is not None:
            print(f"tosu exited immediately (code {proc.returncode}); see {LOG_FILE}")
            break
        time.sleep(0.4)
    return None


def stop():
    """只停掉由本工具启动的实例；用户自己开的 tosu 不动。"""
    pid = our_pid()
    if pid is None:
        print("no tosu started by this tool")
        return
    if _alive(pid):
        try:
            os.kill(pid, 15)
        except OSError:
            if PLATFORM == "wsl":
                subprocess.run(["taskkill.exe", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(0.6)
    os.remove(PID_FILE)
    print("stopped" if not find_url() else "process gone but port still answering (other instance?)")



def _to_wsl_path(p):
    """D:\\osu! -> /mnt/d/osu!（仅 WSL 场景需要）。"""
    if not p:
        return None
    p = p.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if not m:
        return None
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}"


def discover(verbose=True):
    """经 tosu 探测 osu! 安装位置（进程扫描），按平台换算路径供 ingest 使用。
    osu! 未运行时 tosu 无进程可找，返回 None。"""
    url = find_url()
    if not url:
        return None
    try:
        with urllib.request.urlopen(f"{url}/json/v2", timeout=2) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    folders = data.get("folders", {}) or {}
    game = folders.get("game")
    songs = folders.get("songs")
    if not game and not songs:
        if verbose:
            print("tosu reachable but no osu! path yet (osu! not running?)")
        return None
    conv = _to_wsl_path if PLATFORM == "wsl" else (lambda x: x)
    out = {
        "game": conv(game),
        "songs": conv(songs),
        "scores": conv(game.rstrip("\\") + "\\Data\\r") if game else None,
    }
    if verbose:
        for k, v in out.items():
            ok = os.path.isdir(v) if v else False
            print(f"  {k:6s} {v or '-'} {'(exists)' if ok else '(not found)' if v else ''}")
    return out


def ensure():
    """给 server.py 用的入口：运行实例（自动探测）> 拉起 TOSU_PATH / 内置 runtime。"""
    return find_url() or start()


def status():
    url = find_url()
    pid = our_pid()
    exe = bundled_binary()
    print(f"platform         : {PLATFORM}")
    print(f"running instance : {url or 'none'}")
    print(f"bundled binary   : {exe} ({'present' if exe and os.path.isfile(exe) else 'missing'})")
    print(f"our pid          : {pid if pid and _alive(pid) else '-'}")
    print("env TOSU_PATH    :", os.environ.get("TOSU_PATH") or "(unset, = 内置 runtime)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"status": status, "start": lambda: start(), "stop": stop,
     "discover": lambda: (find_url() or start()) and discover()}.get(cmd, status)()

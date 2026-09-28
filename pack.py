#!/usr/bin/env python3
"""打包器（产出 release/ 下的 4 个发布包）。

包型：
- osu-radar-{v}-{platform}.{tar.gz|zip}            纯应用，用户自备 tosu
- osu-radar-{v}-{platform}-portable.{tar.gz|zip}   应用 + 内置该平台的 tosu 二进制

内容规则：
- 基础文件集 = git ls-files（与仓库树一致，天然排除 r/、data/、.env、tosu/、.venv 等）
- portable 在基础集上追加 tosu/ 目录，且只放目标平台的那一个二进制：
    linux   -> tosu/tosu-linux
    windows -> tosu/tosu.exe
  绝不把另一平台的二进制一起塞进去（1.0.0 事故）。

用法：
  uv run python3 pack.py            # 构建全部 4 个包
  uv run python3 pack.py --verify   # 只校验已有包，不重建
"""
import argparse
import os
import subprocess
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
RELEASE_DIR = os.path.join(ROOT, "release")
VERSION = None  # 从 pyproject.toml 读

# 便携包：平台 -> (tosu/ 下唯一的二进制名, 需要可执行位)
PORTABLE_BIN = {
    "linux": ("tosu/tosu-linux", True),
    "windows": ("tosu/tosu.exe", False),
}
# 基础集中需要强制可执行位的文件（tar 保留权限；zip 不保留，start.sh 靠文档/用户 chmod）
EXEC_FILES = {"start.sh", "tosu/tosu-linux"}


def read_version():
    with open(os.path.join(ROOT, "pyproject.toml")) as f:
        for line in f:
            if line.startswith("version"):
                return line.split("=")[1].strip().strip('"').strip("'")
    raise SystemExit("version not found in pyproject.toml")


def base_files():
    """git 跟踪的文件集（依赖干净的语义：工作树里多出来的东西一律不进包）。"""
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    files = [f.decode() for f in out.split(b"\0") if f]
    for f in files:
        if not os.path.isfile(os.path.join(ROOT, f)):
            raise SystemExit(f"tracked file missing on disk: {f}")
    return files


def build_tar(path, files, extra=None):
    extra = extra or {}
    with tarfile.open(path, "w:gz") as tar:
        for name in files + list(extra):
            full = os.path.join(ROOT, name)
            ti = tar.gettarinfo(full, arcname=f"osu-radar/{name}")
            if name in EXEC_FILES:
                ti.mode = 0o755  # start.sh / 内置 tosu 二进制：确保可执行
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            with open(full, "rb") as f:
                tar.addfile(ti, f)


def build_zip(path, files, extra=None):
    extra = extra or {}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in files + list(extra):
            z.write(os.path.join(ROOT, name), f"osu-radar/{name}")


def build(platform):
    """构建一个平台的两个包；portable 二进制缺失则跳过并告警。"""
    files = base_files()
    ext, pack = ("tar.gz", build_tar) if platform == "linux" else ("zip", build_zip)
    plain = os.path.join(RELEASE_DIR, f"osu-radar-{VERSION}-{platform}.{ext}")
    pack(plain, files)
    yield plain

    bin_rel, _ = PORTABLE_BIN[platform]
    if not os.path.isfile(os.path.join(ROOT, bin_rel)):
        print(f"!! {bin_rel} 不存在，跳过 {platform}-portable")
        return
    portable = os.path.join(RELEASE_DIR,
                            f"osu-radar-{VERSION}-{platform}-portable.{ext}")
    pack(portable, files, extra=[bin_rel])
    yield portable


def verify():
    """逐包校验：基础集完整；portable 有且只有一个对应平台的 tosu 二进制。"""
    files = set(base_files())
    all_ok = True
    for platform in PORTABLE_BIN:
        want_bin, _ = PORTABLE_BIN[platform]
        other = [b for b in ("tosu/tosu-linux", "tosu/tosu.exe") if b != want_bin]
        if platform == "windows":
            ext, lister = "zip", list_zip
        else:
            ext, lister = "tar.gz", list_tar
        for kind in ("", "-portable"):
            path = os.path.join(
                RELEASE_DIR, f"osu-radar-{VERSION}-{platform}{kind}.{ext}")
            if not os.path.isfile(path):
                print(f"FAIL {os.path.basename(path)}: 不存在")
                all_ok = False
                continue
            names = set(lister(path))
            base = {f"osu-radar/{f}" for f in files}
            missing = base - names
            extra = names - base
            bad = []
            if missing:
                bad.append(f"缺 {sorted(missing)[:3]}")
            if kind == "-portable":
                if f"osu-radar/{want_bin}" not in names:
                    bad.append(f"缺 {want_bin}")
                for b in other:
                    if f"osu-radar/{b}" in names:
                        bad.append(f"夹带了 {b}")
            elif extra:
                bad.append(f"多了 {sorted(extra)[:3]}")
            if bad:
                print(f"FAIL {os.path.basename(path)}: {'; '.join(bad)}")
                all_ok = False
            else:
                size = os.path.getsize(path) / 1e6
                print(f"OK   {os.path.basename(path)} ({size:.1f} MB)")
    return all_ok


def list_tar(path):
    with tarfile.open(path) as tar:
        return [m.name for m in tar.getmembers() if m.isfile()]


def list_zip(path):
    with zipfile.ZipFile(path) as z:
        return [n for n in z.namelist() if not n.endswith("/")]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="只校验 release/ 已有包")
    args = ap.parse_args()
    VERSION = read_version()
    os.makedirs(RELEASE_DIR, exist_ok=True)

    if args.verify:
        sys.exit(0 if verify() else 1)

    for plat in PORTABLE_BIN:
        for p in build(plat):
            print(f"built {os.path.basename(p)} ({os.path.getsize(p)/1e6:.1f} MB)")
    print("\n--- 校验 ---")
    sys.exit(0 if verify() else 1)

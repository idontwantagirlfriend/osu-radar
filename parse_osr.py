#!/usr/bin/env python3
"""Minimal .osr (osu! replay) parser — quick inspection for osu-radar."""
import lzma
import struct
import sys
import zlib


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def single(self):
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def double(self):
        v = struct.unpack_from("<d", self.data, self.pos)[0]
        self.pos += 8
        return v

    def boolean(self):
        return bool(self.u8())

    def string(self):
        if self.u8() == 0x0B:
            length = self.uleb128()
            v = self.data[self.pos:self.pos + length].decode("utf-8", "replace")
            self.pos += length
            return v
        return ""

    def uleb128(self):
        result = 0
        shift = 0
        while True:
            b = self.u8()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7


def parse(path):
    r = Reader(open(path, "rb").read())
    replay = {
        "game_mode": r.u8(),
        "version": r.u32(),
        "beatmap_md5": r.string(),
        "player": r.string(),
        "replay_md5": r.string(),
        "n300": r.u16(),
        "n100": r.u16(),
        "n50": r.u16(),
        "geki": r.u16(),
        "katu": r.u16(),
        "n_miss": r.u16(),
        "total_score": r.u32(),
        "max_combo": r.u16(),
        "perfect": r.boolean(),
        "mods": r.u32(),
        "life_graph": r.string(),
        "timestamp": r.u64(),
        "compressed_len": r.u32(),
        "compressed_data": r.data[r.pos:r.pos + r.u32.__self__.data[r.pos:r.pos + 4] and 0 or 0],  # placeholder
    }
    # read compressed frames properly
    r.pos = replay["compressed_len"]  # wrong; recompute below
    return replay


def parse_full(path):
    r = Reader(open(path, "rb").read())
    head = {
        "game_mode": r.u8(),
        "version": r.u32(),
        "beatmap_md5": r.string(),
        "player": r.string(),
        "replay_md5": r.string(),
        "n300": r.u16(),
        "n100": r.u16(),
        "n50": r.u16(),
        "geki": r.u16(),
        "katu": r.u16(),
        "n_miss": r.u16(),
        "total_score": r.u32(),
        "max_combo": r.u16(),
        "perfect": r.boolean(),
        "mods": r.u32(),
        "life_graph": r.string(),
        "timestamp": r.u64(),
    }
    comp_len = r.u32()
    comp = r.data[r.pos:r.pos + comp_len]
    r.pos += comp_len
    if comp[:1] == b"x":  # zlib (stable)
        frames_raw = zlib.decompress(comp).decode("utf-8", "replace")
    else:  # lzma alone (lazer)
        frames_raw = lzma.decompress(comp, format=lzma.FORMAT_ALONE).decode("utf-8", "replace")
    frames = [f.split("|") for f in frames_raw.split(",") if f]
    head["n_frames"] = len(frames)
    head["first_frames"] = frames[:3]
    head["last_frames"] = frames[-3:]
    head["online_id"] = r.u64()
    return head


if __name__ == "__main__":
    import json
    info = parse_full(sys.argv[1])
    info["first_frames"] = [list(f) for f in info["first_frames"]]
    info["last_frames"] = [list(f) for f in info["last_frames"]]
    print(json.dumps(info, indent=2, ensure_ascii=False))

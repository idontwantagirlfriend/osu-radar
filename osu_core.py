#!/usr/bin/env python3
"""osu-radar core: faithful Python port of osu!(lazer) beatmap decoding pieces
needed for aim-offset analysis. Ported 1:1 from:

- osu.Game/Rulesets/Objects/SliderPath.cs              (path + length calc)
- osu.Framework/Utils/PathApproximator.cs              (bezier/B-spline/catmull/arc)
- osu.Framework/Utils/CircularArcProperties.cs         (perfect curve)
- osu.Game/Rulesets/Objects/Legacy/ConvertHitObjectParser.cs  (legacy curve string)
- osu.Game.Rulesets.Osu/Beatmaps/OsuBeatmapProcessor.cs (stacking)
- osu.Game.Rulesets.Osu/Objects/{OsuHitObject,Slider}.cs (stack offset, velocity)

Only float32 (C# Vector2) is approximated with float64; positions in legacy
files are integral anyway.
"""
import math

Vec = tuple  # (x, y)

def v_add(a, b): return (a[0] + b[0], a[1] + b[1])
def v_sub(a, b): return (a[0] - b[0], a[1] - b[1])
def v_mul(a, s): return (a[0] * s, a[1] * s)
def v_muli(a, i): return (a[0] * i, a[1] * i)  # int scalar (boehm)
def v_dot(a, b): return a[0] * b[0] + a[1] * b[1]
def v_len(a): return math.hypot(a[0], a[1])
def v_len2(a): return a[0] * a[0] + a[1] * a[1]
def v_dist(a, b): return math.hypot(a[0] - b[0], a[1] - b[1])

# ------------------------------------------------------------ PathApproximator

BEZIER_TOLERANCE = 0.25
CATMULL_DETAIL = 50
CIRCULAR_ARC_TOLERANCE = 0.1
ALMOST_EPSILON = 1e-4  # osuTK Precision.AlmostEquals default

def almost_zero(x):
    return abs(x) <= ALMOST_EPSILON

def linear_to_piecewise_linear(points):
    return list(points)

def b_spline_to_bezier_internal(points, degree):
    """Boehm knot insertion. Returns (segments_in_pop_order, degree).
    Mutates nothing of the caller's list (works on a copy)."""
    degree = min(degree, len(points) - 1)
    point_count = len(points) - 1
    pts = list(points)

    if degree == point_count:
        return [pts], degree

    segments = []
    for i in range(point_count - degree):
        sub = [None] * (degree + 1)
        sub[0] = pts[i]
        for j in range(degree - 1):
            sub[j + 1] = pts[i + 1]
            for k in range(1, degree - j):
                l = min(k, point_count - degree - i)
                pts[i + k] = v_muli(v_add(v_muli(pts[i + k], l), pts[i + k + 1]), 1.0 / (l + 1))
        sub[degree] = pts[i + 1]
        segments.append(sub)
    segments.append(pts[point_count - degree:])
    return segments, degree

def bezier_is_flat_enough(points):
    tol2 = BEZIER_TOLERANCE * BEZIER_TOLERANCE * 4
    for i in range(1, len(points) - 1):
        s = (points[i - 1][0] - 2 * points[i][0] + points[i + 1][0],
             points[i - 1][1] - 2 * points[i][1] + points[i + 1][1])
        if s[0] * s[0] + s[1] * s[1] > tol2:
            return False
    return True

def bezier_subdivide(points):
    count = len(points)
    midpoints = list(points)
    l = [None] * count
    r = [None] * count
    for i in range(count):
        l[i] = midpoints[0]
        r[count - 1 - i] = midpoints[count - 1 - i]
        for j in range(count - 1 - i):
            midpoints[j] = ((midpoints[j][0] + midpoints[j + 1][0]) * 0.5,
                            (midpoints[j][1] + midpoints[j + 1][1]) * 0.5)
    return l, r

def bezier_approximate(points, output):
    count = len(points)
    l, r = bezier_subdivide(points)
    combined = l + r[1:count]  # C#: l[count + i] = r[i + 1]
    output.append(points[0])
    for i in range(1, count - 1):
        idx = 2 * i
        output.append((0.25 * (combined[idx - 1][0] + 2 * combined[idx][0] + combined[idx + 1][0]),
                       0.25 * (combined[idx - 1][1] + 2 * combined[idx][1] + combined[idx + 1][1])))

def b_spline_to_piecewise_linear(control_points, degree):
    if len(control_points) < 2:
        return [] if not control_points else [control_points[0]]
    degree = min(degree, len(control_points) - 1)
    point_count = len(control_points) - 1
    segments, degree = b_spline_to_bezier_internal(control_points, degree)

    output = []
    to_flatten = segments  # list-as-stack; pop() == C# Stack.Pop()
    while to_flatten:
        parent = to_flatten.pop()
        if bezier_is_flat_enough(parent):
            bezier_approximate(parent, output)
            continue
        left, right = bezier_subdivide(parent)
        to_flatten.append(right)
        to_flatten.append(left)
    output.append(control_points[point_count])
    return output

def bezier_to_piecewise_linear(control_points):
    return b_spline_to_piecewise_linear(control_points, max(1, len(control_points) - 1))

def catmull_find_point(v1, v2, v3, v4, t):
    t2 = t * t
    t3 = t * t2
    return tuple(
        0.5 * (2 * v2[i] + (-v1[i] + v3[i]) * t
               + (2 * v1[i] - 5 * v2[i] + 4 * v3[i] - v4[i]) * t2
               + (-v1[i] + 3 * v2[i] - 3 * v3[i] + v4[i]) * t3)
        for i in (0, 1))

def catmull_to_piecewise_linear(points):
    result = []
    for i in range(len(points) - 1):
        v1 = points[i - 1] if i > 0 else points[i]
        v2 = points[i]
        v3 = points[i + 1] if i < len(points) - 1 else v_add(v2, v_sub(v2, v1))
        v4 = points[i + 2] if i < len(points) - 2 else v_add(v3, v_sub(v3, v2))
        for c in range(CATMULL_DETAIL):
            result.append(catmull_find_point(v1, v2, v3, v4, c / CATMULL_DETAIL))
            result.append(catmull_find_point(v1, v2, v3, v4, (c + 1) / CATMULL_DETAIL))
    return result

class CircularArcProperties:
    def __init__(self, points=None):
        self.is_valid = False
        if points is None:
            return
        a, b, c = points[0], points[1], points[2]
        if almost_zero((b[1] - a[1]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[1] - a[1])):
            return
        d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
        a_sq, b_sq, c_sq = v_len2(a), v_len2(b), v_len2(c)
        self.centre = (
            (a_sq * (b[1] - c[1]) + b_sq * (c[1] - a[1]) + c_sq * (a[1] - b[1])) / d,
            (a_sq * (c[0] - b[0]) + b_sq * (a[0] - c[0]) + c_sq * (b[0] - a[0])) / d)
        dA = v_sub(a, self.centre)
        dC = v_sub(c, self.centre)
        self.radius = v_len(dA)
        self.theta_start = math.atan2(dA[1], dA[0])
        theta_end = math.atan2(dC[1], dC[0])
        while theta_end < self.theta_start:
            theta_end += 2 * math.pi
        self.direction = 1.0
        self.theta_range = theta_end - self.theta_start
        ortho = (c[1] - a[1], -(c[0] - a[0]))
        if v_dot(ortho, v_sub(b, a)) < 0:
            self.direction = -self.direction
            self.theta_range = 2 * math.pi - self.theta_range
        self.is_valid = True

def circular_arc_sub_points(pr):
    if 2 * pr.radius <= CIRCULAR_ARC_TOLERANCE:
        return 2
    return max(2, math.ceil(pr.theta_range / (2 * math.acos(1 - CIRCULAR_ARC_TOLERANCE / pr.radius))))

def circular_arc_to_piecewise_linear(points):
    pr = CircularArcProperties(points)
    if not pr.is_valid:
        return bezier_to_piecewise_linear(points)
    amount = circular_arc_sub_points(pr)
    output = []
    for i in range(amount):
        fract = i / (amount - 1)
        theta = pr.theta_start + pr.direction * fract * pr.theta_range
        output.append(
            (pr.centre[0] + math.cos(theta) * pr.radius,
             pr.centre[1] + math.sin(theta) * pr.radius))
    return output

# ------------------------------------------------------------ path types

LINEAR, PERFECT_CURVE, CATMULL, BSPLINE = "L", "P", "C", "B"

def parse_path_type(tok):
    c = tok[0]
    if c == "L": return (LINEAR, None)
    if c == "P": return (PERFECT_CURVE, None)
    if c == "C": return (CATMULL, None)
    # 'B' (and default for unknown letters)
    if len(tok) > 1:
        try:
            deg = int(tok[1:])
            if deg > 0:
                return (BSPLINE, deg)
        except ValueError:
            pass
    return (BSPLINE, None)

# ------------------------------------------------------------ SliderPath

class SliderPath:
    def __init__(self, control_points, expected_distance, optimise_catmull=False):
        # control_points: list of ((x,y), type_or_None) with type on first of segments
        self.control_points = control_points
        self.expected_distance = expected_distance
        self.optimise_catmull = optimise_catmull
        self._calculated_path = None
        self._cumulative_length = None
        self._calculated_length = 0.0

    def _calculate_sub_path(self, points, ptype, degree):
        if ptype == LINEAR:
            return linear_to_piecewise_linear(points)
        if ptype == PERFECT_CURVE:
            if len(points) == 3:
                pr = CircularArcProperties(points)
                if pr.is_valid:
                    sub_points = circular_arc_sub_points(pr)
                    if sub_points < 1000:
                        sub = circular_arc_to_piecewise_linear(points)
                        if sub:
                            return sub
            return b_spline_to_piecewise_linear(points, degree if degree else len(points))
        if ptype == CATMULL:
            sub = catmull_to_piecewise_linear(points)
            if not self.optimise_catmull:
                return sub
            # basic stable optimisation (see SliderPath.cs)
            optimised, last_start, removed_since_start = [], None, 0.0
            for i, p in enumerate(sub):
                if last_start is None:
                    optimised.append(p)
                    last_start = p
                    continue
                dist_from_start = v_dist(last_start, p)
                removed_since_start += v_dist(sub[i - 1], p)
                seg_len = CATMULL_DETAIL * 2
                if dist_from_start > 6 or (i + 1) % seg_len == 0 or i == len(sub) - 1:
                    optimised.append(p)
                    self._optimised_length += removed_since_start - dist_from_start
                    last_start, removed_since_start = None, 0.0
            return optimised
        return b_spline_to_piecewise_linear(points, degree if degree else len(points))

    def _calculate_path(self):
        self._optimised_length = 0.0
        self._calculated_path = []
        if not self.control_points:
            return
        vertices = [cp[0] for cp in self.control_points]
        types = [cp[1] for cp in self.control_points]
        n = len(self.control_points)
        start = 0
        for i in range(n):
            if types[i] is None and i < n - 1:
                continue
            seg = vertices[start:i + 1]
            seg_type = types[start] or (LINEAR, None)
            if len(seg) == 1:
                self._calculated_path.append(seg[0])
            elif len(seg) > 1:
                sub = self._calculate_sub_path(seg, seg_type[0], seg_type[1])
                skip_first = (self._calculated_path and sub and
                              self._calculated_path[-1] == sub[0])
                self._calculated_path.extend(sub[1:] if skip_first else sub)
            start = i

    def _calculate_length(self):
        self._calculated_length = self._optimised_length
        self._cumulative_length = [0.0]
        for i in range(len(self._calculated_path) - 1):
            self._calculated_length += v_dist(self._calculated_path[i], self._calculated_path[i + 1])
            self._cumulative_length.append(self._calculated_length)

        expected = self.expected_distance
        if expected is not None and self._calculated_length != expected:
            path = self._calculated_path
            cum = self._cumulative_length
            if len(path) >= 2 and path[-1] == path[-2] and expected > self._calculated_length:
                cum.append(self._calculated_length)
                return
            cum.pop()  # the last length is always incorrect
            path_end_index = len(path) - 1
            if self._calculated_length > expected:
                while cum and cum[-1] >= expected:
                    cum.pop()
                    path.pop()
                    path_end_index -= 1
            if path_end_index <= 0:
                self._cumulative_length = [0.0]
                return
            diff = v_sub(path[path_end_index], path[path_end_index - 1])
            d = v_len(diff)
            dirv = (diff[0] / d, diff[1] / d) if d > 0 else (0.0, 0.0)
            path[path_end_index] = v_add(path[path_end_index - 1],
                                         v_mul(dirv, expected - cum[-1]))
            cum.append(expected)

    def _ensure_valid(self):
        if self._calculated_path is None:
            self._calculate_path()
            self._calculate_length()

    @property
    def distance(self):
        self._ensure_valid()
        return self._cumulative_length[-1] if self._cumulative_length else 0.0

    @property
    def calculated_length(self):
        self._ensure_valid()
        return self._calculated_length

    @property
    def calculated_path(self):
        self._ensure_valid()
        return self._calculated_path

    def position_at(self, progress):
        self._ensure_valid()
        d = min(max(progress, 0.0), 1.0) * self.distance
        return self._interpolate(self._index_of_distance(d), d)

    def _index_of_distance(self, d):
        import bisect
        return bisect.bisect_left(self._cumulative_length, d)

    def _interpolate(self, i, d):
        path, cum = self._calculated_path, self._cumulative_length
        if not path:
            return (0.0, 0.0)
        if i <= 0:
            return path[0]
        if i >= len(path):
            return path[-1]
        p0, p1 = path[i - 1], path[i]
        d0, d1 = cum[i - 1], cum[i]
        if abs(d0 - d1) < 1e-10:
            return p0
        w = (d - d0) / (d1 - d0)
        return (p0[0] + (p1[0] - p0[0]) * w, p0[1] + (p1[1] - p0[1]) * w)

# ------------------------------------------------------------ legacy curve parsing

FIRST_LAZER_VERSION = 128

def is_linear_pts(p0, p1, p2):
    return almost_zero((p1[1] - p0[1]) * (p2[0] - p0[0]) - (p1[0] - p0[0]) * (p2[1] - p0[1]))

def convert_points(ptype, points, end_point, format_version):
    """Port of ConvertHitObjectParser.convertPoints (implicit segmentation)."""
    vertices = [[p, None] for p in points]

    if ptype == PERFECT_CURVE:
        end_len = 1 if end_point is not None else 0
        if format_version < FIRST_LAZER_VERSION:
            if len(vertices) + end_len != 3:
                ptype = BSPLINE
            elif is_linear_pts(points[0], points[1], end_point if end_point is not None else points[2]):
                ptype = LINEAR
        elif len(vertices) + end_len > 3:
            ptype = BSPLINE

    vertices[0][1] = (ptype, None)

    segments = []
    start_index = 0
    end_index = 0
    while True:
        end_index += 1
        if end_index >= len(vertices):
            break
        if vertices[end_index][0] != vertices[end_index - 1][0]:
            continue
        if ptype == CATMULL and end_index > 1 and format_version < FIRST_LAZER_VERSION:
            continue
        if end_index == len(vertices) - 1:
            continue
        vertices[end_index - 1][1] = (ptype, None)
        segments.append([(v[0], v[1]) for v in vertices[start_index:end_index]])
        start_index = end_index + 1

    if start_index < end_index:
        segments.append([(v[0], v[1]) for v in vertices[start_index:end_index]])
    return segments

def parse_curve_string(point_string, slider_pos, format_version):
    """Port of ConvertHitObjectParser.convertPathString.
    Returns list of (pos, type) control points (pos relative to slider head)."""
    tokens = point_string.split("|")
    points = []
    segment_starts = []  # (type, degree, start_index)
    for s in tokens:
        if not s:
            continue
        if s[0].isalpha():
            segment_starts.append((parse_path_type(s), len(points)))
            if len(points) == 0:
                points.append((0.0, 0.0))
        else:
            x_s, y_s = s.split(":")[:2]
            x, y = float(x_s), float(y_s)
            if format_version < FIRST_LAZER_VERSION:
                x, y = int(x), int(y)
            points.append((x - slider_pos[0], y - slider_pos[1]))

    control_points = []
    for si, ((ptype, degree), start) in enumerate(segment_starts):
        end = segment_starts[si + 1][1] if si + 1 < len(segment_starts) else len(points)
        end_point = points[end] if si + 1 < len(segment_starts) else None
        pts = points[start:end]
        for seg in convert_points(ptype, pts, end_point, format_version):
            control_points.extend(seg)
    return control_points

# ------------------------------------------------------------ beatmap

class Beatmap:
    pass

def parse_beatmap(path):
    section = None
    bm = Beatmap()
    bm.mode = 0
    bm.timing_points = []       # (time, beat_length, uninherited)
    bm.hit_objects = []
    bm.stack_leniency = 0.7
    bm.slider_multiplier = 1.4
    bm.circle_size = 5.0
    bm.approach_rate = None
    bm.overall_difficulty = 5.0
    bm.format_version = 14
    bm.metadata = {}

    for line in open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("osu file format v"):
            bm.format_version = int(line.rsplit("v", 1)[1])
            continue
        if line.startswith("["):
            section = line.strip("[]")
            continue
        if section == "General":
            k, _, v = line.partition(":")
            v = v.strip()
            if k == "Mode":
                bm.mode = int(v)
            elif k == "StackLeniency":
                bm.stack_leniency = float(v)
        elif section == "Difficulty":
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if k == "CircleSize": bm.circle_size = float(v)
            elif k == "ApproachRate": bm.approach_rate = float(v)
            elif k == "OverallDifficulty": bm.overall_difficulty = float(v)
            elif k == "SliderMultiplier":
                bm.slider_multiplier = min(3.6, max(0.4, float(v)))
        elif section == "Metadata":
            k, _, v = line.partition(":")
            bm.metadata[k.strip()] = v.strip()
        elif section == "TimingPoints":
            parts = line.split(",")
            if not parts[1]:
                continue
            try:
                beat_length = float(parts[1])
            except ValueError:
                continue
            bm.timing_points.append({
                "time": float(parts[0]),
                "beat_length": beat_length,
                "uninherited": len(parts) < 7 or parts[6].strip() == "1",
            })
        elif section == "HitObjects":
            bm.hit_objects.append(parse_hit_object(line, bm.format_version))

    if bm.approach_rate is None:
        bm.approach_rate = bm.overall_difficulty
    bm.timing_points.sort(key=lambda t: t["time"])

    for obj in bm.hit_objects:
        if obj["type"] & 2:
            apply_slider_defaults(obj, bm)
    apply_stacking(bm)
    return bm

def parse_hit_object(line, format_version):
    parts = line.split(",")
    x, y, time = float(parts[0]), float(parts[1]), float(parts[2])
    if format_version < FIRST_LAZER_VERSION:
        x, y = int(x), int(y)
    obj = {
        "x": x, "y": y, "time": time,
        "type": int(parts[3]),
        "new_combo": bool(int(parts[3]) & 4),
        "stack_height": 0,
        "end_position": (x, y),
        "end_time": time,
    }
    if obj["type"] & 1:  # circle
        obj["kind"] = "circle"
    elif obj["type"] & 2:  # slider
        obj["kind"] = "slider"
        obj["curve_string"] = parts[5]
        obj["slides"] = int(parts[6])
        obj["pixel_length"] = float(parts[7]) if len(parts) > 7 else None
        obj["path"] = SliderPath(parse_curve_string(parts[5], (x, y), format_version),
                                 obj["pixel_length"], optimise_catmull=True)
    elif obj["type"] & 8:  # spinner
        obj["kind"] = "spinner"
        obj["end_time"] = float(parts[5]) if len(parts) > 5 else time
    return obj

def apply_slider_defaults(obj, bm):
    """Port of Slider.ApplyDefaultsToSelf (velocity / end time / end position)."""
    # find applicable timing point & SV point at start time
    timing_bl = None
    sv = 1.0
    for tp in bm.timing_points:
        if tp["time"] > obj["time"] + 1e-9:  # ControlPointInfo uses start-of-search grouping
            break
        if tp["uninherited"]:
            timing_bl = tp["beat_length"]
        else:
            beat_length = tp["beat_length"]
            sv = (100.0 / -beat_length) if beat_length < 0 else 1.0
    if timing_bl is None:
        timing_bl = 500.0  # lazer default 120bpm... actually default timing point BPM120
    # lazer GetPrecisionAdjustedBeatLength ("osu"): clamp(-(-100/SV), 10, 1000)/100
    bpm_multiplier = 1.0
    if sv != 1.0:
        bpm_multiplier = min(1000.0, max(10.0, 100.0 / sv)) / 100.0
    adjusted = timing_bl * bpm_multiplier
    velocity = 100.0 * bm.slider_multiplier / adjusted

    obj["velocity"] = velocity
    obj["distance"] = obj["path"].distance
    obj["end_time"] = obj["time"] + obj["slides"] * obj["distance"] / velocity
    obj["end_position"] = v_add((obj["x"], obj["y"]), obj["path"].position_at(1))

# ------------------------------------------------------------ stacking

STACK_DISTANCE = 3
BROKEN_GAMEFIELD_ROUNDING_ALLOWANCE = 1.00041

def scale_from_cs(cs):
    return (1.0 - 0.7 * (cs - 5.0) / 5.0) / 2 * BROKEN_GAMEFIELD_ROUNDING_ALLOWANCE

def stack_offset(stack_height, cs):
    s = stack_height * scale_from_cs(cs) * -6.4
    return (s, s)

def ar_to_preempt_ms(ar):
    if ar < 5: return 1200 + 600 * (5 - ar) / 5
    if ar > 5: return 1200 - 750 * (ar - 5) / 5
    return 1200.0

def apply_stacking(bm):
    objs = [o for o in bm.hit_objects if o["kind"] != "spinner"]
    if not objs:
        return
    for o in objs:
        o["stack_height"] = 0
    preempt = ar_to_preempt_ms(bm.approach_rate)
    threshold = int(preempt) * bm.stack_leniency
    if bm.format_version >= 6:
        _apply_stacking(bm, objs, 0, len(objs) - 1, threshold)
    else:
        _apply_stacking_old(bm, objs, threshold)

def _dist_pos(a, b):
    return v_dist((a["x"], a["y"]), (b["x"], b["y"]))

def _dist_endpos(a, b):
    return v_dist(a["end_position"], (b["x"], b["y"]))

def _apply_stacking(bm, objs, start_index, end_index, threshold):
    hit_objects = objs
    extended_end_index = end_index

    if end_index < len(hit_objects) - 1:
        for i in range(end_index, start_index - 1, -1):
            stack_base_index = i
            for n in range(stack_base_index + 1, len(hit_objects)):
                stack_base_object = hit_objects[stack_base_index]
                if stack_base_object["kind"] == "spinner":
                    break
                object_n = hit_objects[n]
                if object_n["kind"] == "spinner":
                    continue
                end_time = stack_base_object["end_time"]
                if object_n["time"] - end_time > threshold:
                    break
                if (_dist_pos(stack_base_object, object_n) < STACK_DISTANCE
                        or (stack_base_object["kind"] == "slider"
                            and _dist_endpos(stack_base_object, object_n) < STACK_DISTANCE)):
                    stack_base_index = n
                    object_n["stack_height"] = 0
            if stack_base_index > extended_end_index:
                extended_end_index = stack_base_index
                if extended_end_index == len(hit_objects) - 1:
                    break

    for i in range(extended_end_index, start_index, -1):
        n = i
        object_i = hit_objects[i]
        if object_i["stack_height"] != 0 or object_i["kind"] == "spinner":
            continue

        if object_i["kind"] == "circle":
            while True:
                n -= 1
                if n < 0:
                    break
                object_n = hit_objects[n]
                if object_n["kind"] == "spinner":
                    continue
                end_time = object_n["end_time"]
                if int(object_i["time"]) - int(end_time) > threshold:
                    break
                if object_n["kind"] == "slider" and _dist_endpos(object_n, object_i) < STACK_DISTANCE:
                    offset = object_i["stack_height"] - object_n["stack_height"] + 1
                    for j in range(n + 1, i + 1):
                        object_j = hit_objects[j]
                        if v_dist(object_n["end_position"], (object_j["x"], object_j["y"])) < STACK_DISTANCE:
                            object_j["stack_height"] -= offset
                    break
                if _dist_pos(object_n, object_i) < STACK_DISTANCE:
                    object_n["stack_height"] = object_i["stack_height"] + 1
                    object_i = object_n
        elif object_i["kind"] == "slider":
            while True:
                n -= 1
                if n < start_index:
                    break
                object_n = hit_objects[n]
                if object_n["kind"] == "spinner":
                    continue
                if object_i["time"] - object_n["time"] > threshold:
                    break
                if v_dist(object_n["end_position"], (object_i["x"], object_i["y"])) < STACK_DISTANCE:
                    object_n["stack_height"] = object_i["stack_height"] + 1
                    object_i = object_n

def _apply_stacking_old(bm, objs, threshold):
    hit_objects = objs
    for i in range(len(hit_objects)):
        curr = hit_objects[i]
        if curr["stack_height"] != 0 and curr["kind"] != "slider":
            continue
        start_time = curr["end_time"]
        slider_stack = 0
        for j in range(i + 1, len(hit_objects)):
            if hit_objects[j]["time"] - threshold > start_time:
                break
            position2 = curr["end_position"] if curr["kind"] == "slider" else (curr["x"], curr["y"])
            if v_dist((hit_objects[j]["x"], hit_objects[j]["y"]), (curr["x"], curr["y"])) < STACK_DISTANCE:
                curr["stack_height"] += 1
                start_time = hit_objects[j]["time"]
            elif v_dist((hit_objects[j]["x"], hit_objects[j]["y"]), position2) < STACK_DISTANCE:
                slider_stack += 1
                hit_objects[j]["stack_height"] -= slider_stack
                start_time = hit_objects[j]["time"]

def stacked_position(obj, cs):
    off = stack_offset(obj["stack_height"], cs)
    return (obj["x"] + off[0], obj["y"] + off[1])

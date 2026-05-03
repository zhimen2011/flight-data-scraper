"""
Flight Trajectory Deviation Analyzer
=====================================
Analyzes planned vs actual flight trajectory data:
- Cross-track deviation (lateral)
- Altitude deviation (vertical) with 3-layer filtering
- Country/region-based segmentation
- Generates self-contained HTML report with Leaflet + ECharts

Usage:
    python analyze_flight.py --flight I99806 --date 2026-04-29
    python analyze_flight.py --flight I99806 --date 2026-04-29 --min-alt-dev 1000 --min-dur 50
    python analyze_flight.py --flight I99806 --multi
"""
import json
import csv
import math
import os
import glob
import argparse
import sys
import webbrowser
from datetime import datetime

# ─── Constants ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_DIR = os.path.join(BASE_DIR, "flight_csv")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
GEOJSON_PATH = os.path.join(BASE_DIR, "countries.geojson")

# Altitudes in the CSV are in METERS — convert to feet
M_TO_FT = 3.28084

# Europe ISO codes for region grouping (EU + EFTA + candidates along our routes)
EUROPE_CODES = {
    "DE", "FR", "GB", "IT", "ES", "PL", "NL", "BE", "CZ", "PT", "GR",
    "HU", "AT", "CH", "BG", "DK", "FI", "SK", "NO", "IE", "HR", "BA",
    "RS", "SE", "RO", "LT", "LV", "EE", "SI", "LU", "MT", "CY", "IS",
    "AL", "MD", "MK", "ME", "UA", "BY", "TR",
}
# Map ISO-A2 codes to regions
REGION_LABELS = {
    "CN": "国内段",
    "MN": "蒙古",
    "RU": "俄罗斯",
    "KZ": "哈萨克斯坦",
}
# Default deviation filter thresholds
DEFAULT_MIN_ALT_DEV_FT = 1000
DEFAULT_MIN_DURATION_NM = 200

def load_config():
    """Load analysis config from JSON file, with fallback to defaults."""
    cfg_path = os.path.join(BASE_DIR, "analysis_config.json")
    cfg = {"min_alt_deviation_ft": DEFAULT_MIN_ALT_DEV_FT,
           "min_duration_nm": DEFAULT_MIN_DURATION_NM}
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r") as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    return cfg

# ─── Geo Math ────────────────────────────────────────────────────────────────

def haversine_distance(lat1, lon1, lat2, lon2):
    """Great-circle distance in nautical miles between two points."""
    R = 3440.065  # Earth radius in nautical miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def project_to_segment(lat, lon, seg_start, seg_end):
    """
    Project a point onto a great-circle segment.
    Returns (along_track_nm, cross_track_nm, fraction_along).
    - along_track_nm: distance from seg_start along the segment to the projection point
    - cross_track_nm: signed perpendicular distance (+ = right of track)
    - fraction_along: 0 at seg_start, 1 at seg_end
    """
    d_seg = haversine_distance(seg_start[0], seg_start[1], seg_end[0], seg_end[1])
    if d_seg < 1e-6:
        d = haversine_distance(seg_start[0], seg_start[1], lat, lon)
        return 0.0, d, 0.0

    lat1, lon1 = math.radians(seg_start[0]), math.radians(seg_start[1])
    lat2, lon2 = math.radians(seg_end[0]), math.radians(seg_end[1])
    lat_p, lon_p = math.radians(lat), math.radians(lon)

    # Angular distance from start to point
    cos_d13 = math.sin(lat1) * math.sin(lat_p) + math.cos(lat1) * math.cos(lat_p) * math.cos(lon_p - lon1)
    cos_d13 = max(-1, min(1, cos_d13))
    d13_ang = math.acos(cos_d13)

    # Angular distance from start to end
    cos_d12 = math.sin(lat1) * math.sin(lat2) + math.cos(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    cos_d12 = max(-1, min(1, cos_d12))
    d12_ang = math.acos(cos_d12)

    if d12_ang < 1e-12:
        return 0.0, d13_ang * 3440.065, 0.0

    # Bearing from start to end
    sin_b12 = math.sin(lon2 - lon1) * math.cos(lat2)
    cos_b12 = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    # Normalize bearing
    bear_12 = math.atan2(sin_b12, cos_b12)

    # Bearing from start to point
    sin_b13 = math.sin(lon_p - lon1) * math.cos(lat_p)
    cos_b13 = math.cos(lat1) * math.sin(lat_p) - math.sin(lat1) * math.cos(lat_p) * math.cos(lon_p - lon1)
    bear_13 = math.atan2(sin_b13, cos_b13)

    # Along-track angular distance
    cos_xt = math.cos(d13_ang) / max(math.cos(d12_ang * 0.01), 1e-12)  # placeholder
    # Better approach: use Clairaut's formula or spherical trig
    cross_ang = math.asin(math.sin(d13_ang) * math.sin(bear_13 - bear_12))
    cross_nm = cross_ang * 3440.065

    # Along-track angular distance
    if abs(math.cos(cross_ang)) < 1e-12:
        along_ang = d12_ang * 0.5  # point is 90 degrees off track, pick mid
    else:
        cos_along = math.cos(d13_ang) / math.cos(cross_ang)
        cos_along = max(-1, min(1, cos_along))
        along_ang = math.acos(cos_along)

    along_nm = along_ang * 3440.065
    fraction = along_nm / d_seg if d_seg > 1e-6 else 0.0

    return along_nm, cross_nm, fraction


# ─── Country Detection ───────────────────────────────────────────────────────

class CountryIndex:
    """Index of country polygons for fast point-in-polygon lookup."""

    def __init__(self, geojson_path):
        self.countries = []  # list of (name, iso_a2, polygons, bbox)
        self._load(geojson_path)
        self._cache = {}  # (rounded lat, lon) -> iso_a2

    def _load(self, path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for feat in data["features"]:
            props = feat["properties"]
            name = props.get("name", "?")
            iso_a2 = props.get("ISO3166-1-Alpha-2", "??")
            geom = feat["geometry"]
            if geom is None or geom.get("type") is None:
                continue
            polygons = self._extract_polygons(geom)
            if not polygons:
                continue
            bbox = self._compute_bbox(polygons)
            self.countries.append((name, iso_a2, polygons, bbox))
        print(f"  Loaded {len(self.countries)} countries from GeoJSON")

    def _extract_polygons(self, geom):
        """Extract list of polygon rings from geometry."""
        result = []
        if geom["type"] == "Polygon":
            result.append(geom["coordinates"])
        elif geom["type"] == "MultiPolygon":
            result.extend(geom["coordinates"])
        return result

    def _compute_bbox(self, polygons):
        """Compute bounding box [min_lon, min_lat, max_lon, max_lat]."""
        min_lat, min_lon = 999, 999
        max_lat, max_lon = -999, -999
        for poly in polygons:
            for ring in poly:
                for lon, lat in ring:
                    min_lat = min(min_lat, lat)
                    max_lat = max(max_lat, lat)
                    min_lon = min(min_lon, lon)
                    max_lon = max(max_lon, lon)
        return (min_lon, min_lat, max_lon, max_lat)

    def _point_in_polygon(self, lat, lon, polygon_rings):
        """Ray casting algorithm. polygon_rings: list of rings (first=outer, rest=holes)."""
        # Test outer ring
        if not self._ring_contains(lat, lon, polygon_rings[0]):
            return False
        # Test holes
        for hole in polygon_rings[1:]:
            if self._ring_contains(lat, lon, hole):
                return False
        return True

    def _ring_contains(self, lat, lon, ring):
        """Ray casting for a single ring."""
        inside = False
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i][0], ring[i][1]  # lon, lat
            xj, yj = ring[j][0], ring[j][1]
            if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    def lookup(self, lat, lon):
        """Return (country_name, iso_a2, region_label) for a given lat/lon."""
        # Use cache (0.05 degree rounding ~5km)
        key = (round(lat * 20) / 20, round(lon * 20) / 20)
        if key in self._cache:
            return self._cache[key]

        result = ("海上/公海", "XX", "海上")
        for name, iso_a2, polygons, bbox in self.countries:
            # Bbox quick reject
            min_lon, min_lat, max_lon, max_lat = bbox
            if lon < min_lon or lon > max_lon or lat < min_lat or lat > max_lat:
                continue
            # Full polygon test
            for poly_rings in polygons:
                if self._point_in_polygon(lat, lon, poly_rings):
                    region = self._iso_to_region(iso_a2)
                    result = (name, iso_a2, region)
                    break
            if result[1] != "XX":
                break

        self._cache[key] = result
        return result

    def _iso_to_region(self, iso_a2):
        if iso_a2 in REGION_LABELS:
            return REGION_LABELS[iso_a2]
        if iso_a2 in EUROPE_CODES:
            return "欧洲"
        return f"其他({iso_a2})"


# ─── Data Loading ────────────────────────────────────────────────────────────

def load_csv(filepath):
    """Load CSV into list of dicts. Returns [] if file missing."""
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def load_plan_track(filepath):
    """
    Load plan track, parse numeric values, sort by dist.
    Returns list of waypoint dicts.
    """
    rows = load_csv(filepath)
    if not rows:
        print(f"  WARNING: Plan track file not found: {filepath}")
        return []
    waypoints = []
    for r in rows:
        try:
            wp = {
                "name": r.get("name", "").strip(),
                "dist": float(r.get("dist", 0)),
                "alt": float(r.get("alt", 0)) * M_TO_FT,  # meters → feet
                "ful": float(r.get("ful", 0)),
                "lat": float(r.get("lat", 0)),
                "lon": float(r.get("lon", 0)),
                "grs": float(r.get("grs", 0)),
            }
            waypoints.append(wp)
        except (ValueError, TypeError):
            continue
    waypoints.sort(key=lambda w: w["dist"])
    # De-duplicate by dist
    seen = set()
    unique = []
    for w in waypoints:
        if w["dist"] not in seen:
            seen.add(w["dist"])
            unique.append(w)
    return unique


def load_actual_track(filepath):
    """
    Load actual track, parse numeric values, sort by gateway_time.
    """
    rows = load_csv(filepath)
    if not rows:
        print(f"  WARNING: Actual track file not found: {filepath}")
        return []
    points = []
    for r in rows:
        try:
            p = {
                "time": r.get("gateway_time", "").strip(),
                "alt": float(r.get("alt", 0)) * M_TO_FT,  # meters → feet
                "dis": float(r.get("dis", 0)),
                "fob": float(r.get("fob", 0)),
                "lat": float(r.get("lat", 0)),
                "lon": float(r.get("lon", 0)),
                "type": r.get("type", "").strip(),
            }
            points.append(p)
        except (ValueError, TypeError):
            continue
    points.sort(key=lambda p: p["time"])
    return points


def find_csv_files(flight, date_str):
    """Find matching plan_track and actual_track CSV files for a flight+date."""
    patterns = [
        os.path.join(CSV_DIR, f"{flight}_{date_str}_plan_track.csv"),
        os.path.join(CSV_DIR, f"{flight}_{date_str}*_plan_track.csv"),
        os.path.join(CSV_DIR, "*", f"{flight}_{date_str}_plan_track.csv"),
        os.path.join(CSV_DIR, "*", f"{flight}_{date_str}*_plan_track.csv"),
    ]
    plan_file = None
    for pat in patterns:
        matches = glob.glob(pat)
        if matches:
            plan_file = matches[0]
            break

    if not plan_file:
        return None, None

    # Derive actual track from plan filename
    actual_file = plan_file.replace("_plan_track.csv", "_actual_track.csv")
    if not os.path.exists(actual_file):
        # Try same directory alternatives
        dirname = os.path.dirname(plan_file)
        basename = os.path.basename(plan_file).replace("_plan_track.csv", "")
        alts = glob.glob(os.path.join(dirname, f"{basename}*actual_track.csv"))
        if alts:
            actual_file = alts[0]

    return plan_file, actual_file


# ─── Phase Detection ─────────────────────────────────────────────────────────

def detect_plan_cruise_segments(plan_waypoints, min_cruise_alt_frac=0.85, rate_threshold=30):
    """
    Use the PLAN to determine which along-track distance ranges are cruise.
    A waypoint segment is "cruise" if:
    - Plan altitude change rate < rate_threshold ft/nm over that segment
    - Altitude is at least min_cruise_alt_frac * max_plan_alt (not climb/descent)

    Returns list of (dist_start, dist_end, plan_cruise_alt) ranges.
    """
    if len(plan_waypoints) < 3:
        return []
    max_alt = max(w["alt"] for w in plan_waypoints)
    min_cruise_alt = max_alt * min_cruise_alt_frac

    # Mark each plan segment as cruise or not
    cruise_ranges = []
    in_cruise = False
    cruise_start = None
    cruise_alts = []

    for i in range(len(plan_waypoints) - 1):
        w1 = plan_waypoints[i]
        w2 = plan_waypoints[i + 1]
        d_dist = w2["dist"] - w1["dist"]
        d_alt = abs(w2["alt"] - w1["alt"])
        rate = d_alt / d_dist if d_dist > 0.1 else 999

        is_cruise_seg = (rate < rate_threshold and
                         w1["alt"] >= min_cruise_alt and
                         w2["alt"] >= min_cruise_alt)

        if is_cruise_seg and not in_cruise:
            in_cruise = True
            cruise_start = w1["dist"]
            cruise_alts = [w1["alt"]]
        if in_cruise:
            cruise_alts.append(w2["alt"])
            if not is_cruise_seg or i == len(plan_waypoints) - 2:
                cruise_end = w2["dist"] if not is_cruise_seg else w2["dist"]
                cruise_ranges.append({
                    "dist_start": cruise_start,
                    "dist_end": cruise_end,
                    "plan_alt": sorted(cruise_alts)[len(cruise_alts) // 2],  # median
                })
                in_cruise = False

    return cruise_ranges


def mark_cruise_points(dev_results, plan_waypoints):
    """
    Mark each deviation point as cruise or not based on plan cruise segments.
    A point is cruise if its along-track distance falls within any plan cruise segment.
    """
    cruise_ranges = detect_plan_cruise_segments(plan_waypoints)
    for r in dev_results:
        r["is_cruise"] = False
        for cr in cruise_ranges:
            if cr["dist_start"] <= r["dis"] <= cr["dist_end"]:
                r["is_cruise"] = True
                break
    return cruise_ranges


# ─── Deviation Computation ───────────────────────────────────────────────────

def find_best_segment(lat, lon, plan_waypoints):
    """
    Find which plan segment the point is closest to by geometric projection.
    Returns (seg_idx, along_track_nm, cross_track_nm, fraction).
    The along_track total distance = plan_wp[seg_idx].dist + along_track_nm.
    """
    if len(plan_waypoints) < 2:
        return None

    best_seg = 0
    best_xt = float("inf")
    best_along = 0.0
    best_frac = 0.0

    for i in range(len(plan_waypoints) - 1):
        wp1 = plan_waypoints[i]
        wp2 = plan_waypoints[i + 1]
        seg_start = (wp1["lat"], wp1["lon"])
        seg_end = (wp2["lat"], wp2["lon"])
        along, xt, frac = project_to_segment(lat, lon, seg_start, seg_end)

        # Check if projection falls within segment bounds (0 <= frac <= 1)
        # For points near segment endpoints, allow slight overshoot
        if -0.1 <= frac <= 1.1:
            if abs(xt) < abs(best_xt):
                best_xt = xt
                best_seg = i
                best_along = along
                best_frac = max(0, min(1, frac))

    # If no good segment found, pick by closest waypoint
    if abs(best_xt) > 100:  # > 100nm off track something is wrong
        best_dist = float("inf")
        for i, wp in enumerate(plan_waypoints):
            d = haversine_distance(lat, lon, wp["lat"], wp["lon"])
            if d < best_dist:
                best_dist = d
                best_seg = max(0, min(i, len(plan_waypoints) - 2))
                best_along = 0
                best_xt = d
                best_frac = 0

    return best_seg, best_along, best_xt, best_frac


def find_plan_at_distance(dist, plan_waypoints):
    """
    Linearly interpolate plan values at a given along-track distance.
    """
    if not plan_waypoints:
        return None
    for i in range(len(plan_waypoints) - 1):
        d1 = plan_waypoints[i]["dist"]
        d2 = plan_waypoints[i + 1]["dist"]
        if d1 <= dist <= d2:
            if d2 - d1 < 1e-6:
                frac = 0
            else:
                frac = (dist - d1) / (d2 - d1)
            wp1 = plan_waypoints[i]
            wp2 = plan_waypoints[i + 1]
            return {
                "alt": wp1["alt"] + (wp2["alt"] - wp1["alt"]) * frac,
                "ful": wp1["ful"] + (wp2["ful"] - wp1["ful"]) * frac,
                "lat": wp1["lat"] + (wp2["lat"] - wp1["lat"]) * frac,
                "lon": wp1["lon"] + (wp2["lon"] - wp1["lon"]) * frac,
                "seg_start": (wp1["lat"], wp1["lon"]),
                "seg_end": (wp2["lat"], wp2["lon"]),
                "seg_idx": i,
            }
    return None


def compute_deviations(actual_points, plan_waypoints, config, country_index):
    """
    For each actual point: project onto plan route to get along-track distance,
    then compute deviations in altitude, fuel, cross-track.
    """
    results = []
    last_seg = 0  # Track last segment for locality optimization

    for i, ap in enumerate(actual_points):
        # Project onto plan route
        seg_result = find_best_segment(ap["lat"], ap["lon"], plan_waypoints)
        if seg_result is None:
            continue
        seg_idx, along_nm, xt_nm, frac = seg_result

        # Total along-track distance from origin
        wp_seg = plan_waypoints[seg_idx]
        total_dist = wp_seg["dist"] + along_nm

        # Interpolate plan values at this along-track distance
        plan = find_plan_at_distance(total_dist, plan_waypoints)
        if plan is None:
            continue

        alt_dev_ft = ap["alt"] - plan["alt"]
        fuel_dev_lbs = ap["fob"] - plan["ful"]

        # Country lookup
        name, iso, region = country_index.lookup(ap["lat"], ap["lon"])

        results.append({
            "time": ap["time"],
            "lat": ap["lat"],
            "lon": ap["lon"],
            "alt": ap["alt"],
            "dis": total_dist,
            "fob": ap["fob"],
            "plan_alt": plan["alt"],
            "plan_ful": plan["ful"],
            "plan_lat": plan["lat"],
            "plan_lon": plan["lon"],
            "cross_track_nm": xt_nm,
            "alt_dev_ft": alt_dev_ft,
            "fuel_dev_lbs": fuel_dev_lbs,
            "country": name,
            "iso": iso,
            "region": region,
            "seg_idx": seg_idx,
            "is_cruise": False,
            "type": ap["type"],
        })

    return results


# ─── 3-Layer Filtering ──────────────────────────────────────────────────────

def apply_filters(dev_results, plan_waypoints, config):
    """
    3-layer filtering:
    1. Cruise-only: mark points within plan cruise segments
    2. Amplitude filter: |alt_dev| >= min_alt_deviation_ft
    3. Duration filter: consecutive qualifying points span >= min_duration_nm
    Returns (events_list, cruise_points, cruise_ranges).
    """
    min_alt_dev = config.get("min_alt_deviation_ft", DEFAULT_MIN_ALT_DEV_FT)
    min_dur_nm = config.get("min_duration_nm", DEFAULT_MIN_DURATION_NM)

    cruise_ranges = mark_cruise_points(dev_results, plan_waypoints)

    cruise_points = [r for r in dev_results if r["is_cruise"]]
    if not cruise_points:
        return [], [], []

    # Layer 2: Amplitude filter
    significant = [r for r in cruise_points if abs(r["alt_dev_ft"]) >= min_alt_dev]

    # Layer 3: Duration filter - group consecutive points by region and deviation sign
    events = _group_events(significant, min_dur_nm)
    return events, cruise_points, cruise_ranges


def _group_events(significant, min_dur_nm):
    """Group consecutive significant points into deviation events."""
    events = []
    if not significant:
        return events

    current = None
    for r in significant:
        sign = 1 if r["alt_dev_ft"] >= 0 else -1
        region = r["region"]

        if current is None:
            current = {"region": region, "sign": sign, "start_dis": r["dis"],
                       "end_dis": r["dis"], "points": [r], "alt_devs": [r["alt_dev_ft"]]}
        elif (current["region"] == region and current["sign"] == sign and
              r["dis"] - current["end_dis"] < 100):
            current["end_dis"] = r["dis"]
            current["points"].append(r)
            current["alt_devs"].append(r["alt_dev_ft"])
        else:
            dur = current["end_dis"] - current["start_dis"]
            if dur >= min_dur_nm:
                _finalize_event(current, dur)
                events.append(current)
            current = {"region": region, "sign": sign, "start_dis": r["dis"],
                       "end_dis": r["dis"], "points": [r], "alt_devs": [r["alt_dev_ft"]]}

    if current:
        dur = current["end_dis"] - current["start_dis"]
        if dur >= min_dur_nm:
            _finalize_event(current, dur)
            events.append(current)

    events.sort(key=lambda e: abs(e["avg_alt_dev"]), reverse=True)
    return events


def _finalize_event(e, dur):
    e["duration_nm"] = round(dur, 1)
    e["avg_alt_dev"] = round(sum(e["alt_devs"]) / len(e["alt_devs"]), 0)
    e["median_alt_dev"] = round(sorted(e["alt_devs"])[len(e["alt_devs"]) // 2], 0)
    e["plan_alt"] = round(e["points"][0]["plan_alt"], 0)
    e["actual_alt_median"] = round(sorted([p["alt"] for p in e["points"]])[len(e["points"]) // 2], 0)


# ─── Region Statistics ───────────────────────────────────────────────────────

def compute_region_stats(dev_results, cruise_points):
    """Compute per-region summary statistics."""
    # Group cruise points by region
    regions = {}
    for r in cruise_points:
        reg = r["region"]
        if reg not in regions:
            regions[reg] = {
                "count": 0,
                "alt_devs": [],
                "xt_devs": [],
                "dist_range": [99999, 0],
            }
        regions[reg]["count"] += 1
        regions[reg]["alt_devs"].append(r["alt_dev_ft"])
        regions[reg]["xt_devs"].append(abs(r["cross_track_nm"]))
        regions[reg]["dist_range"][0] = min(regions[reg]["dist_range"][0], r["dis"])
        regions[reg]["dist_range"][1] = max(regions[reg]["dist_range"][1], r["dis"])

    stats = []
    for reg, data in sorted(regions.items()):
        ad = data["alt_devs"]
        xt = data["xt_devs"]
        stats.append({
            "region": reg,
            "point_count": data["count"],
            "dist_from": round(data["dist_range"][0], 1),
            "dist_to": round(data["dist_range"][1], 1),
            "duration_nm": round(data["dist_range"][1] - data["dist_range"][0], 1),
            "mean_alt_dev": round(sum(ad) / len(ad), 1),
            "median_alt_dev": round(sorted(ad)[len(ad) // 2], 1),
            "max_alt_dev_above": round(max(ad), 1),
            "max_alt_dev_below": round(min(ad), 1),
            "mean_xt_dev": round(sum(xt) / len(xt), 2),
            "max_xt_dev": round(max(xt), 2),
        })
    return stats


# ─── Early Descent Detection ──────────────────────────────────────────────────

def detect_descent_points(plan_waypoints, dev_results, country_index):
    """
    Detect premature descent by comparing plan TOD with actual descent start.
    Algorithm:
      1. Find plan TOD — the LAST waypoint at cruise altitude before final descent
      2. Find actual descent start — where actual altitude begins sustained decrease
      3. Compare distances and report nearest waypoints
    """
    if len(plan_waypoints) < 5:
        return None

    # Step 1: Find plan TOD
    # From the end, scan backwards. The last waypoint with altitude > 80% of max
    # that is followed by a significant descent is the TOD.
    max_alt = max(w["alt"] for w in plan_waypoints)
    plan_tod_idx = None
    for i in range(len(plan_waypoints) - 2, 0, -1):
        curr = plan_waypoints[i]["alt"]
        next_w = plan_waypoints[i + 1]["alt"]
        if curr > max_alt * 0.5 and next_w < curr * 0.5:
            plan_tod_idx = i
            break

    # Fallback: last waypoint > 70% of max
    if plan_tod_idx is None:
        for i in range(len(plan_waypoints) - 1, 0, -1):
            if plan_waypoints[i]["alt"] > max_alt * 0.7:
                plan_tod_idx = i
                break

    if plan_tod_idx is None:
        return None

    plan_tod_wp = plan_waypoints[plan_tod_idx]
    plan_tod_dist = plan_tod_wp["dist"]
    plan_tod_name = plan_tod_wp["name"]

    # Step 2: Find actual descent start
    # Look at actual data within 500nm before plan TOD
    search_start = max(0, plan_tod_dist - 500)
    relevant = [r for r in dev_results if search_start <= r["dis"] <= plan_tod_dist]

    if len(relevant) < 20:
        return None

    # Find the stable cruise altitude in this segment (median of first portion)
    early_alts = [r["alt"] for r in relevant[:min(30, len(relevant)//3)]]
    if not early_alts:
        return None
    stable_alt = sorted(early_alts)[len(early_alts) // 2]

    # Scan forward to find where altitude consistently drops below stable_alt
    actual_descent_start = None
    actual_descent_wp = None
    consecutive_low = 0

    for i, r in enumerate(relevant):
        if r["alt"] < stable_alt - 500:  # 500ft below stable cruise
            consecutive_low += 1
            if consecutive_low >= 5 and actual_descent_start is None:
                # Found descent start — back up to where it started dropping
                actual_descent_start = relevant[max(0, i - 5)]["dis"]
                nearest = min(plan_waypoints, key=lambda w: abs(w["dist"] - actual_descent_start))
                actual_descent_wp = nearest["name"]
                break
        else:
            consecutive_low = 0

    if actual_descent_start is None:
        return None

    descent_diff_nm = plan_tod_dist - actual_descent_start
    is_premature = descent_diff_nm > 50

    # Region info
    mid_pt = relevant[len(relevant) // 2]
    _, _, region = country_index.lookup(mid_pt["lat"], mid_pt["lon"])

    # Waypoints between actual descent and plan TOD
    between_wps = [
        w for w in plan_waypoints
        if actual_descent_start <= w["dist"] <= plan_tod_dist and w["alt"] > 100
    ]

    return {
        "region": region,
        "plan_tod_wp": plan_tod_name,
        "plan_tod_dist": round(plan_tod_dist, 1),
        "plan_tod_alt": round(plan_tod_wp["alt"], 0),
        "actual_descent_start_dist": round(actual_descent_start, 1),
        "actual_descent_start_wp": actual_descent_wp,
        "descent_diff_nm": round(descent_diff_nm, 1),
        "is_premature": is_premature,
        "stable_alt": round(stable_alt, 0),
        "between_waypoints": [w["name"] for w in between_wps[:10]],
    }


# ─── Full Analysis Pipeline ──────────────────────────────────────────────────

def analyze_flight(plan_file, actual_file, config, country_index):
    """Run full analysis for a single flight."""
    print(f"\n  计划文件: {plan_file}")
    print(f"  实际文件: {actual_file}")

    plan_wp = load_plan_track(plan_file)
    actual_pts = load_actual_track(actual_file)

    if not plan_wp:
        print("  ERROR: No plan waypoints loaded")
        return None
    if not actual_pts:
        print("  ERROR: No actual points loaded")
        return None

    print(f"  计划航路点: {len(plan_wp)}, 实际轨迹点: {len(actual_pts)}")

    # Compute per-point deviations
    dev_results = compute_deviations(actual_pts, plan_wp, config, country_index)

    # Apply 3-layer filtering
    events, cruise_points, cruise_ranges = apply_filters(dev_results, plan_wp, config)

    # Region statistics
    region_stats = compute_region_stats(dev_results, cruise_points)

    # Flight metadata
    first_wp = plan_wp[0]
    last_wp = plan_wp[-1]
    first_actual = actual_pts[0]
    last_actual = actual_pts[-1]

    metadata = {
        "plan_waypoints_count": len(plan_wp),
        "actual_points_count": len(actual_pts),
        "cruise_points_count": len(cruise_points),
        "total_distance_nm": round(last_wp["dist"], 1),
        "dep_airport": first_wp["name"],
        "arr_airport": last_wp["name"],
        "dep_time": first_actual["time"],
        "arr_time": last_actual["time"],
        "max_alt_plan": round(max(w["alt"] for w in plan_wp), 0),
        "max_alt_actual": round(max(p["alt"] for p in actual_pts), 0),
    }

    # Compute arrival time deviation
    plan_arrival_ms = None
    for w in plan_wp:
        if w.get("time") and float(w.get("time", 0)) > 0:
            plan_arrival_ms = float(w.get("time", 0))
    time_dev_min = None
    if plan_arrival_ms and last_actual["time"]:
        try:
            plan_dt = datetime.utcfromtimestamp(plan_arrival_ms / 1000)
            actual_dt = datetime.strptime(last_actual["time"], "%Y-%m-%d %H:%M:%S")
            time_dev_min = round((actual_dt - plan_dt).total_seconds() / 60, 1)
        except:
            pass

    metadata["arrival_time_dev_min"] = time_dev_min

    # Early descent detection (for domestic/arrival segment)
    descent_analysis = detect_descent_points(plan_wp, dev_results, country_index)

    # Generate warnings
    warnings = []
    for e in events[:10]:  # Top 10 most significant
        dir_text = "偏高" if e["sign"] > 0 else "偏低"
        warnings.append({
            "region": e["region"],
            "direction": dir_text,
            "start_dist": round(e["start_dis"], 1),
            "end_dist": round(e["end_dis"], 1),
            "duration_nm": round(e["duration_nm"], 1),
            "plan_alt": round(e["plan_alt"], 0),
            "actual_alt": round(e["actual_alt_median"], 0),
            "avg_dev_ft": round(e["avg_alt_dev"], 0),
            "severity": "high" if abs(e["avg_alt_dev"]) >= 2000 else "medium",
        })

    # Summarize deviations across all cruise points
    cruise_alt_devs = [r["alt_dev_ft"] for r in cruise_points]
    cruise_xt_devs = [abs(r["cross_track_nm"]) for r in cruise_points]

    summary = {
        "max_alt_dev_ft": round(max(cruise_alt_devs, default=0), 0),
        "min_alt_dev_ft": round(min(cruise_alt_devs, default=0), 0),
        "mean_alt_dev_ft": round(sum(cruise_alt_devs) / len(cruise_alt_devs), 0) if cruise_alt_devs else 0,
        "max_xt_dev_nm": round(max(cruise_xt_devs, default=0), 2),
        "mean_xt_dev_nm": round(sum(cruise_xt_devs) / len(cruise_xt_devs), 2) if cruise_xt_devs else 0,
        "significant_events": len(warnings),
    }

    # Build result
    result = {
        "metadata": metadata,
        "config": config,
        "summary": summary,
        "region_stats": region_stats,
        "warnings": warnings,
        "descent_analysis": descent_analysis,
        "plan_waypoints": [
            {"name": w["name"], "dist": w["dist"], "alt": w["alt"], "lat": w["lat"], "lon": w["lon"]}
            for w in plan_wp
        ],
        # For the HTML, we pass the full deviation data (simplified for size)
        "deviation_data": [
            {
                "t": r["time"][-8:] if len(r["time"]) >= 8 else r["time"],  # HH:MM:SS
                "lat": round(r["lat"], 4),
                "lon": round(r["lon"], 4),
                "alt": round(r["alt"], 0),
                "dis": round(r["dis"], 1),
                "plan_alt": round(r["plan_alt"], 0),
                "plan_lat": round(r["plan_lat"], 4),
                "plan_lon": round(r["plan_lon"], 4),
                "xt": round(r["cross_track_nm"], 3),
                "alt_dev": round(r["alt_dev_ft"], 0),
                "fuel_dev": round(r["fuel_dev_lbs"], 0),
                "region": r["region"],
                "country": r.get("country", ""),
                "is_cruise": r["is_cruise"],
            }
            for r in dev_results
        ],
    }
    return result


# ─── HTML Generation ─────────────────────────────────────────────────────────

def generate_html_report(result, flight, date_str, output_path, config):
    """Generate self-contained HTML report."""
    # Serialize data as JSON for embedding
    data_json = json.dumps(result, ensure_ascii=False, indent=2)

    # Escape for safe embedding in <script> tag
    data_json_escaped = data_json.replace("\\", "\\\\").replace("</script>", "<\\/script>")

    html = HTML_SINGLE_TEMPLATE.format(
        title=f"{flight} {date_str} 偏差分析",
        flight=flight,
        date=date_str,
        h_threshold=config["min_alt_deviation_ft"],
        v_threshold=config.get("min_duration_nm", DEFAULT_MIN_DURATION_NM),
        data_json=data_json_escaped,
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  报告已生成: {output_path}")
    return output_path


# ─── HTML Template ───────────────────────────────────────────────────────────

HTML_SINGLE_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, "Microsoft YaHei", sans-serif; background:#1a1a2e; color:#e0e0e0; }}
.header {{ background:#16213e; padding:12px 20px; border-bottom:2px solid #0f3460; }}
.header h1 {{ font-size:18px; color:#e94560; }}
.header .meta {{ font-size:12px; color:#888; margin-top:4px; }}
.controls {{ background:#16213e; padding:8px 20px; display:flex; gap:24px; align-items:center; flex-wrap:wrap; border-bottom:1px solid #0f3460; }}
.controls label {{ font-size:12px; color:#aaa; }}
.controls input[type=range] {{ width:140px; accent-color:#e94560; }}
.controls .val {{ color:#e94560; font-weight:bold; font-size:14px; }}
.summary-bar {{ display:flex; gap:16px; padding:10px 20px; background:#0f3460; flex-wrap:wrap; }}
.summary-item {{ background:#16213e; padding:8px 14px; border-radius:6px; font-size:13px; }}
.summary-item .num {{ font-size:20px; font-weight:bold; color:#e94560; }}
.summary-item .label {{ color:#888; font-size:11px; }}
.main {{ display:flex; height:calc(100vh - 175px); }}
.map-panel {{ flex:1; min-width:0; }}
.chart-panel {{ flex:1; display:flex; flex-direction:column; min-width:0; }}
.chart-box {{ flex:1; min-height:200px; }}
.warnings-panel {{ max-height:250px; overflow-y:auto; padding:10px 20px; background:#0f3460; }}
.warnings-panel h3 {{ color:#e94560; margin-bottom:8px; font-size:14px; }}
.warning-item {{ background:#16213e; padding:8px 12px; margin:4px 0; border-radius:4px; font-size:12px; border-left:3px solid #ff4444; }}
.warning-item.medium {{ border-left-color:#ffaa00; }}
.warning-item .reg {{ font-weight:bold; color:#fff; }}
.warning-item .det {{ color:#aaa; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ background:#0f3460; color:#e94560; padding:6px 8px; text-align:left; }}
td {{ padding:4px 8px; border-bottom:1px solid #1a1a2e; }}
.region-stats {{ padding:10px 20px; background:#0f3460; }}
.region-stats h3 {{ color:#e94560; margin-bottom:6px; font-size:14px; }}
</style>
</head>
<body>
<div class="header">
  <h1>✈ {flight} &nbsp; {date}</h1>
  <div class="meta" id="metaInfo">加载中...</div>
</div>
<div class="controls">
  <label>高度偏差阈值: <input type="range" id="altSlider" min="100" max="3000" value="{h_threshold}" step="50">
    <span class="val" id="altVal">{h_threshold}</span> ft</label>
  <label>最小持续距离: <input type="range" id="durSlider" min="10" max="200" value="{v_threshold}" step="5">
    <span class="val" id="durVal">{v_threshold}</span> nm</label>
  <label><input type="checkbox" id="cruiseOnly" checked> 仅巡航段</label>
  <button onclick="resetView()" style="background:#e94560;color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;">重置视图</button>
</div>
<div class="summary-bar" id="summaryBar"></div>
<div class="main">
  <div class="map-panel" id="map"></div>
  <div class="chart-panel">
    <div class="chart-box" id="chartAlt"></div>
    <div class="chart-box" id="chartXT"></div>
  </div>
</div>
<div class="warnings-panel" id="warningsPanel"></div>
<div class="region-stats" id="regionStats"></div>

<script>
const DATA = {data_json};

// ── Config ──
let altThreshold = {h_threshold};
let durThreshold = {v_threshold};
let cruiseOnly = true;

// ── Init Map ──
const map = L.map('map').setView([45, 60], 4);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    attribution: '&copy; OSM', maxZoom: 13
}}).addTo(map);

// ── Plan route (blue) ──
const planCoords = DATA.plan_waypoints.map(w => [w.lat, w.lon]);
const planLine = L.polyline(planCoords, {{color: '#3388ff', weight: 3, opacity: 0.8, dashArray: '6,4'}}).addTo(map);

// Plan waypoint markers
DATA.plan_waypoints.filter(w => w.alt > 100).forEach(w => {{
    L.circleMarker([w.lat, w.lon], {{radius: 3, color: '#3388ff', fillOpacity: 0.6}})
     .bindTooltip(w.name + ' FL' + Math.round(w.alt/100), {{permanent: false}}).addTo(map);
}});

// ── Actual track (colored by alt deviation) ──
const actualPoints = DATA.deviation_data;
function getColor(altDev) {{
    if (Math.abs(altDev) >= altThreshold) return '#ff3333';
    if (Math.abs(altDev) >= altThreshold * 0.5) return '#ffaa00';
    return '#33cc33';
}}

function updateTrack() {{
    // Remove old actual track
    map.eachLayer(l => {{ if (l._isActual) map.removeLayer(l); }});

    const segments = [];
    let currentSeg = [];
    for (let i = 0; i < actualPoints.length; i++) {{
        const p = actualPoints[i];
        const show = cruiseOnly ? p.is_cruise : true;
        const exceeds = Math.abs(p.alt_dev) >= altThreshold;
        if (show || exceeds) {{
            currentSeg.push([p.lat, p.lon]);
        }} else if (currentSeg.length > 0) {{
            segments.push([...currentSeg]);
            currentSeg = [];
        }}
    }}
    if (currentSeg.length > 0) segments.push(currentSeg);

    segments.forEach(seg => {{
        L.polyline(seg, {{color: '#33cc33', weight: 1.5, opacity: 0.6, _isActual: true}}).addTo(map);
    }});

    // Highlight threshold-exceeding segments
    const exceedPts = [];
    let start = null;
    for (let i = 0; i < actualPoints.length; i++) {{
        const p = actualPoints[i];
        const show = cruiseOnly ? p.is_cruise : true;
        if (show && Math.abs(p.alt_dev) >= altThreshold) {{
            if (!start) start = i;
            exceedPts.push([p.lat, p.lon]);
        }} else {{
            if (exceedPts.length > 2) {{
                L.polyline(exceedPts, {{color: '#ff3333', weight: 3, opacity: 0.9, _isActual: true}}).addTo(map);
            }}
            exceedPts.length = 0;
            start = null;
        }}
    }}
    if (exceedPts.length > 2) {{
        L.polyline(exceedPts, {{color: '#ff3333', weight: 3, opacity: 0.9, _isActual: true}}).addTo(map);
    }}
}}

// ── ECharts ──
const chartAlt = echarts.init(document.getElementById('chartAlt'));
const chartXT = echarts.init(document.getElementById('chartXT'));

function updateCharts() {{
    const filter = actualPoints.filter(p => cruiseOnly ? p.is_cruise : true);

    // Altitude profile
    chartAlt.setOption({{
        title: {{text: '高度剖面', left:'center', textStyle:{{color:'#ccc', fontSize:13}}}},
        tooltip: {{trigger:'axis'}},
        legend: {{data:['计划高度','实际高度'], bottom:0, textStyle:{{color:'#aaa'}}}},
        grid: {{top:40, bottom:30, left:50, right:15}},
        xAxis: {{type:'value', name:'沿程距离(nm)', nameTextStyle:{{color:'#888'}},
                axisLine:{{lineStyle:{{color:'#444'}}}}}},
        yAxis: {{type:'value', name:'高度(ft)', nameTextStyle:{{color:'#888'}},
                axisLine:{{lineStyle:{{color:'#444'}}}}}},
        series: [
            {{name:'计划高度', type:'line', data: DATA.plan_waypoints.map(w => [w.dist, w.alt]),
             lineStyle:{{color:'#3388ff', width:1.5}}, symbol:'none'}},
            {{name:'实际高度', type:'line', data: filter.map(p => [p.dis, p.alt]),
             lineStyle:{{color:'#e94560', width:1}}, symbol:'none'}},
        ],
        backgroundColor:'transparent',
    }});

    // Cross-track deviation
    const xtData = filter.map(p => [p.dis, Math.abs(p.xt)]);
    chartXT.setOption({{
        title: {{text:'水平偏差(Cross-track)', left:'center', textStyle:{{color:'#ccc', fontSize:13}}}},
        tooltip: {{trigger:'axis'}},
        grid: {{top:40, bottom:30, left:50, right:15}},
        xAxis: {{type:'value', name:'沿程距离(nm)', nameTextStyle:{{color:'#888'}},
                axisLine:{{lineStyle:{{color:'#444'}}}}}},
        yAxis: {{type:'value', name:'水平偏差(nm)', nameTextStyle:{{color:'#888'}},
                axisLine:{{lineStyle:{{color:'#444'}}}}}},
        series: [
            {{name:'水平偏差', type:'line', data: xtData,
             lineStyle:{{color:'#ffaa00', width:1}}, symbol:'none',
             areaStyle:{{color:'rgba(255,170,0,0.1)'}}}},
        ],
        backgroundColor:'transparent',
    }});
}}

// ── Update UI ──
function updateAll() {{
    updateTrack();
    updateCharts();
    updateSummary();
    updateWarnings();
}}

function updateSummary() {{
    const filter = actualPoints.filter(p => cruiseOnly ? p.is_cruise : true);
    const altDevs = filter.map(p => p.alt_dev);
    const xtDevs = filter.map(p => Math.abs(p.xt));
    const exceedCount = filter.filter(p => Math.abs(p.alt_dev) >= altThreshold).length;
    document.getElementById('summaryBar').innerHTML = `
        <div class="summary-item"><div class="num">${{altDevs.length}}</div><div class="label">巡航数据点</div></div>
        <div class="summary-item"><div class="num">${{Math.max(...altDevs).toFixed(0)}}</div><div class="label">最大偏高(ft)</div></div>
        <div class="summary-item"><div class="num">${{Math.min(...altDevs).toFixed(0)}}</div><div class="label">最大偏低(ft)</div></div>
        <div class="summary-item"><div class="num">${{Math.max(...xtDevs).toFixed(2)}}</div><div class="label">最大水平偏差(nm)</div></div>
        <div class="summary-item"><div class="num" style="color:#ff4444">${{exceedCount}}</div><div class="label">超限点数(>${{altThreshold}}ft)</div></div>
    `;
    document.getElementById('metaInfo').innerHTML =
        `数据点: ${{DATA.metadata.actual_points_count}} | 航路点: ${{DATA.metadata.plan_waypoints_count}} | 总航程: ${{DATA.metadata.total_distance_nm}}nm`;
}}

function updateWarnings() {{
    const events = buildEvents();
    let html = '<h3>⚠ 显著偏差事件 (持续时间≥' + durThreshold + 'nm, 幅度≥' + altThreshold + 'ft)</h3>';
    if (events.length === 0) {{
        html += '<div style="color:#888">当前阈值下无显著偏差事件</div>';
    }}
    events.forEach(e => {{
        const cls = Math.abs(e.avgDev) >= 2000 ? '' : 'medium';
        html += `<div class="warning-item ${{cls}}">
            <span class="reg">${{e.region}}</span> ${{e.dir}}
            <span class="det">| 距离: ${{e.startDist}}-${{e.endDist}}nm (${{e.dur}}nm)
            | 计划: FL${{Math.round(e.planAlt/100)}} 实际: FL${{Math.round(e.actualAlt/100)}}
            | 平均偏差: ${{Math.round(e.avgDev)}}ft</span>
        </div>`;
    }});
    document.getElementById('warningsPanel').innerHTML = html;
}}

function buildEvents() {{
    const filter = actualPoints.filter(p => cruiseOnly ? p.is_cruise : true);
    const events = [];
    let current = null;

    for (let i = 0; i < filter.length; i++) {{
        const p = filter[i];
        if (Math.abs(p.alt_dev) < altThreshold) {{
            if (current) {{
                const dur = current.endDis - current.startDis;
                if (dur >= durThreshold) {{
                    current.dur = dur.toFixed(1);
                    current.avgDev = current.sum / current.count;
                    current.dir = current.avgDev > 0 ? '偏高' : '偏低';
                    current.planAlt = current.planAlts.reduce((a,b)=>a+b)/current.planAlts.length;
                    current.actualAlt = current.actualAlts.reduce((a,b)=>a+b)/current.actualAlts.length;
                    events.push(current);
                }}
                current = null;
            }}
            continue;
        }}
        const sign = p.alt_dev >= 0 ? 1 : -1;
        if (!current || current.region !== p.region || (sign > 0) !== (current.sum > 0)) {{
            if (current) {{
                const dur = current.endDis - current.startDis;
                if (dur >= durThreshold) {{
                    current.dur = dur.toFixed(1);
                    current.avgDev = current.sum / current.count;
                    current.dir = current.avgDev > 0 ? '偏高' : '偏低';
                    current.planAlt = current.planAlts.reduce((a,b)=>a+b)/current.planAlts.length;
                    current.actualAlt = current.actualAlts.reduce((a,b)=>a+b)/current.actualAlts.length;
                    events.push(current);
                }}
            }}
            current = {{
                region: p.region, startDis: p.dis, endDis: p.dis,
                sum: p.alt_dev, count: 1,
                planAlts: [p.plan_alt], actualAlts: [p.alt],
                startDist: p.dis.toFixed(1), endDist: p.dis.toFixed(1),
            }};
        }} else {{
            current.endDis = p.dis;
            current.endDist = p.dis.toFixed(1);
            current.sum += p.alt_dev;
            current.count++;
            current.planAlts.push(p.plan_alt);
            current.actualAlts.push(p.alt);
        }}
    }}
    // Handle last
    if (current) {{
        const dur = current.endDis - current.startDis;
        if (dur >= durThreshold) {{
            current.dur = dur.toFixed(1);
            current.avgDev = current.sum / current.count;
            current.dir = current.avgDev > 0 ? '偏高' : '偏低';
            current.planAlt = current.planAlts.reduce((a,b)=>a+b)/current.planAlts.length;
            current.actualAlt = current.actualAlts.reduce((a,b)=>a+b)/current.actualAlts.length;
            events.push(current);
        }}
    }}
    events.sort((a,b)=>Math.abs(b.avgDev)-Math.abs(a.avgDev));
    return events;
}}

function resetView() {{
    altThreshold = parseInt(document.getElementById('altSlider').value);
    durThreshold = parseInt(document.getElementById('durSlider').value);
    cruiseOnly = document.getElementById('cruiseOnly').checked;
    document.getElementById('altVal').textContent = altThreshold;
    document.getElementById('durVal').textContent = durThreshold;
    updateAll();
}}

// ── Event Listeners ──
document.getElementById('altSlider').addEventListener('input', resetView);
document.getElementById('durSlider').addEventListener('input', resetView);
document.getElementById('cruiseOnly').addEventListener('change', resetView);
window.addEventListener('resize', () => {{ chartAlt.resize(); chartXT.resize(); }});

// ── Region stats table ──
let regionHtml = '<h3>📊 巡航段区域统计</h3><table><tr><th>区域</th><th>点数</th><th>起止距离(nm)</th><th>持续(nm)</th><th>平均高度偏差(ft)</th><th>最大偏高</th><th>最大偏低</th><th>平均水平偏差(nm)</th></tr>';
DATA.region_stats.forEach(s => {{
    regionHtml += `<tr>
        <td>${{s.region}}</td><td>${{s.point_count}}</td><td>${{s.dist_from}}-${{s.dist_to}}</td><td>${{s.duration_nm}}</td>
        <td>${{s.mean_alt_dev}}</td><td>${{s.max_alt_dev_above}}</td><td>${{s.max_alt_dev_below}}</td><td>${{s.mean_xt_dev}}</td>
    </tr>`;
}});
regionHtml += '</table>';
document.getElementById('regionStats').innerHTML = regionHtml;

// ── Init ──
updateAll();

// Fit map to route bounds
if (planCoords.length > 0) {{
    const bounds = L.latLngBounds(planCoords);
    map.fitBounds(bounds, {{padding: [30, 30]}});
}}
</script>
</body>
</html>'''


HTML_MULTI_TEMPLATE = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:-apple-system,"Microsoft YaHei",sans-serif;background:#1a1a2e;color:#e0e0e0;}}
.header{{background:#16213e;padding:12px 20px;border-bottom:2px solid #0f3460;}}
.header h1{{font-size:18px;color:#e94560;}}
.controls{{background:#16213e;padding:8px 20px;display:flex;gap:24px;align-items:center;flex-wrap:wrap;border-bottom:1px solid #0f3460;}}
.controls label{{font-size:12px;color:#aaa;}}
.controls input[type=range]{{width:120px;accent-color:#e94560;}}
.controls .val{{color:#e94560;font-weight:bold;}}
.main{{display:flex;height:calc(100vh - 120px);}}
.map-panel{{flex:1;}}
.chart-panel{{flex:1;display:flex;flex-direction:column;}}
.chart-box{{flex:1;min-height:200px;}}
.summary{{padding:10px 20px;background:#0f3460;overflow-x:auto;}}
.summary h3{{color:#e94560;margin-bottom:6px;}}
table{{width:100%;border-collapse:collapse;font-size:11px;color:#ddd;}}
th{{background:#1a1a2e;color:#e94560;padding:5px 8px;text-align:left;position:sticky;top:0;}}
td{{padding:4px 8px;border-bottom:1px solid #1a1a2e;}}
tr:hover td{{background:#1a1a4e;}}
.danger{{color:#ff4444;font-weight:bold;}}
</style>
</head>
<body>
<div class="header">
  <h1>{flight} 多日偏差对比</h1>
</div>
<div class="controls">
  <label>高度偏差阈值: <input type="range" id="altSlider" min="200" max="3000" value="{h_threshold}" step="50">
    <span class="val" id="altVal">{h_threshold}</span> ft</label>
  <label>最小持续距离: <input type="range" id="durSlider" min="10" max="200" value="{v_threshold}" step="5">
    <span class="val" id="durVal">{v_threshold}</span> nm</label>
  <label><input type="checkbox" id="cruiseOnly" checked> 仅巡航段</label>
  <button onclick="resetView()" style="background:#e94560;color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;">刷新</button>
  <span style="font-size:12px;color:#888;">
    | 日期集: <span id="dateList"></span> | 共<span id="totalFlights"></span>条航班
  </span>
</div>
<div class="main">
  <div class="map-panel" id="map"></div>
  <div class="chart-panel">
    <div class="chart-box" id="chartAlt"></div>
    <div class="chart-box" id="chartXT"></div>
  </div>
</div>
<div class="summary" id="summary"></div>

<script>
const ALL_DATA = {data_json};
const DAY_COLORS = ['#e6194b','#3cb44b','#ffe119','#4363d8','#f58231','#911eb4','#42d4f4','#f032e6','#bfef45','#fabed4',
                    '#469990','#dcbeff','#9A6324','#fffac8','#800000','#aaffc3','#808000','#ffd8b1','#000075','#a9a9a9'];

let altThreshold = {h_threshold};
let durThreshold = {v_threshold};
let cruiseOnly = true;

const map = L.map('map').setView([45,60],4);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:12}}).addTo(map);

// Plan route (only first day's)
const firstDay = Object.values(ALL_DATA)[0];
if (firstDay) {{
    const planCoords = firstDay.plan_waypoints.map(w => [w.lat, w.lon]);
    L.polyline(planCoords, {{color:'#3388ff',weight:3,opacity:0.8,dashArray:'6,4'}}).addTo(map);
    firstDay.plan_waypoints.filter(w=>w.alt>100).forEach(w => {{
        L.circleMarker([w.lat,w.lon],{{radius:2,color:'#3388ff',fillOpacity:0.5}})
         .bindTooltip(w.name,{{permanent:false}}).addTo(map);
    }});
}}

// Draw each day's actual track
const dayKeys = Object.keys(ALL_DATA).sort();
dayKeys.forEach((key, idx) => {{
    const data = ALL_DATA[key];
    const color = DAY_COLORS[idx % DAY_COLORS.length];
    const filter = data.deviation_data.filter(p => cruiseOnly ? p.is_cruise : true);
    if (filter.length > 0) {{
        L.polyline(filter.map(p => [p.lat, p.lon]), {{
            color: color, weight: 1.5, opacity: 0.7,
        }}).bindTooltip(key, {{sticky:true}}).addTo(map);
    }}
}});

// Fit bounds
const allCoords = [];
Object.values(ALL_DATA).forEach(d => d.deviation_data.forEach(p => allCoords.push([p.lat,p.lon])));
if (allCoords.length>0) map.fitBounds(L.latLngBounds(allCoords), {{padding:[20,20]}});

// Charts
const chartAlt = echarts.init(document.getElementById('chartAlt'));
const chartXT = echarts.init(document.getElementById('chartXT'));

function updateCharts() {{
    const altSeries = [];
    const xtSeries = [];
    dayKeys.forEach((key, idx) => {{
        const data = ALL_DATA[key];
        const filter = data.deviation_data.filter(p => cruiseOnly ? p.is_cruise : true);
        const color = DAY_COLORS[idx % DAY_COLORS.length];
        altSeries.push({{name:key, type:'line', data:filter.map(p=>[p.dis,p.alt]),
                        lineStyle:{{color,width:1}}, symbol:'none'}});
        xtSeries.push({{name:key, type:'line', data:filter.map(p=>[p.dis,Math.abs(p.xt)]),
                        lineStyle:{{color,width:1}}, symbol:'none'}});
    }});
    // Add plan
    if (firstDay) {{
        altSeries.push({{name:'计划高度', type:'line',
            data:firstDay.plan_waypoints.map(w=>[w.dist,w.alt]),
            lineStyle:{{color:'#ffaa00',width:2,type:'dashed'}}, symbol:'none'}});
    }}
    chartAlt.setOption({{
        title:{{text:'高度剖面对比',left:'center',textStyle:{{color:'#ccc',fontSize:13}}}},
        tooltip:{{trigger:'axis'}}, legend:{{bottom:0,textStyle:{{color:'#aaa',fontSize:9}}}},
        grid:{{top:35,bottom:35,left:45,right:10}},
        xAxis:{{type:'value',name:'距离(nm)'}},
        yAxis:{{type:'value',name:'高度(ft)'}},
        series:altSeries, backgroundColor:'transparent',
    }});
    chartXT.setOption({{
        title:{{text:'水平偏差对比',left:'center',textStyle:{{color:'#ccc',fontSize:13}}}},
        tooltip:{{trigger:'axis'}}, legend:{{bottom:0,textStyle:{{color:'#aaa',fontSize:9}}}},
        grid:{{top:35,bottom:35,left:45,right:10}},
        xAxis:{{type:'value',name:'距离(nm)'}},
        yAxis:{{type:'value',name:'水平偏差(nm)'}},
        series:xtSeries, backgroundColor:'transparent',
    }});
}}

function updateSummary() {{
    let html = '<h3>多日偏差汇总</h3><table><tr><th>日期</th><th>数据点</th><th>巡航点</th>'
        +'<th>最大偏高(ft)</th><th>最大偏低(ft)</th><th>最大水平偏差(nm)</th><th>主要偏差事件</th></tr>';
    dayKeys.forEach(key => {{
        const data = ALL_DATA[key];
        const filter = data.deviation_data.filter(p => cruiseOnly ? p.is_cruise : true);
        const altDevs = filter.map(p=>p.alt_dev);
        const xtDevs = filter.map(p=>Math.abs(p.xt));
        const maxUp = Math.max(...altDevs).toFixed(0);
        const maxDn = Math.min(...altDevs).toFixed(0);
        const maxXT = Math.max(...xtDevs).toFixed(2);

        // Build events
        const events = [];
        let cur = null;
        filter.forEach(p => {{
            if (Math.abs(p.alt_dev) < altThreshold) {{ if (cur) {{ const d=cur.ed-cur.sd; if(d>=durThreshold){{cur.dur=d;events.push(cur);}} cur=null; }} return; }}
            const sgn = p.alt_dev>=0?1:-1;
            if (!cur||cur.reg!==p.region||cur.sgn!==sgn) {{
                if (cur) {{ const d=cur.ed-cur.sd; if(d>=durThreshold){{cur.dur=d;events.push(cur);}} }}
                cur={{reg:p.region,sgn,sd:p.dis,ed:p.dis,sa:0,n:0,dir:sgn>0?'偏高':'偏低'}};
            }}
            cur.ed=p.dis; cur.sa+=p.alt_dev; cur.n++;
        }});
        if (cur) {{ const d=cur.ed-cur.sd; if(d>=durThreshold){{cur.dur=d;events.push(cur);}} }}

        const eventStr = events.sort((a,b)=>Math.abs(b.sa/b.n)-Math.abs(a.sa/a.n)).slice(0,3)
            .map(e=>`${{e.reg}}${{e.dir}}${{Math.round(e.sa/e.n)}}ft(${{e.dur.toFixed(0)}}nm)`).join(', ');
        html += `<tr><td>${{key}}</td><td>${{data.metadata.actual_points_count}}</td>`
            +`<td>${{filter.length}}</td><td class="danger">${{maxUp}}</td><td class="danger">${{maxDn}}</td>`
            +`<td>${{maxXT}}</td><td style="font-size:10px">${{eventStr}}</td></tr>`;
    }});
    html += '</table>';
    document.getElementById('summary').innerHTML = html;
    document.getElementById('dateList').textContent = dayKeys.join(', ');
    document.getElementById('totalFlights').textContent = dayKeys.length;
}}

function resetView() {{
    altThreshold = parseInt(document.getElementById('altSlider').value);
    durThreshold = parseInt(document.getElementById('durSlider').value);
    cruiseOnly = document.getElementById('cruiseOnly').checked;
    document.getElementById('altVal').textContent = altThreshold;
    document.getElementById('durVal').textContent = durThreshold;
    updateCharts();
    updateSummary();
}}

document.getElementById('altSlider').addEventListener('input', resetView);
document.getElementById('durSlider').addEventListener('input', resetView);
document.getElementById('cruiseOnly').addEventListener('change', resetView);
window.addEventListener('resize',()=>{{chartAlt.resize();chartXT.resize();}});

updateCharts();
updateSummary();
</script>
</body>
</html>'''


def generate_multi_html(all_results, flight, output_path, config):
    """Generate multi-day comparison HTML report."""
    # all_results is a dict keyed by date_str -> analysis_result
    # Strip out heavy raw data, keep what the template needs
    slim = {}
    for date_str, result in all_results.items():
        slim[date_str] = {
            "metadata": result["metadata"],
            "plan_waypoints": result["plan_waypoints"],
            "deviation_data": result["deviation_data"],
            "warnings": result["warnings"],
        }
    data_json = json.dumps(slim, ensure_ascii=False, indent=2)
    data_json = data_json.replace("\\", "\\\\").replace("</script>", "<\\/script>")

    html = HTML_MULTI_TEMPLATE.format(
        title=f"{flight} 多日偏差对比",
        flight=flight,
        h_threshold=config["min_alt_deviation_ft"],
        v_threshold=config.get("min_duration_nm", DEFAULT_MIN_DURATION_NM),
        data_json=data_json,
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  多日对比报告: {output_path}")
    return output_path


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="航班计划 vs 实际轨迹偏差分析工具"
    )
    parser.add_argument("--flight", "-f", required=True, help="航班号 (如 I99806)")
    parser.add_argument("--date", "-d", default=None, help="飞行日期 (如 2026-04-29)")
    parser.add_argument("--dates", default=None, help="多个日期，逗号分隔 (如 2026-04-02,2026-04-06)")
    parser.add_argument("--multi", action="store_true", help="自动扫描所有可用日期")
    parser.add_argument("--min-alt-dev", type=float, default=DEFAULT_MIN_ALT_DEV_FT,
                        help=f"最小高度偏差阈值(ft)，默认{DEFAULT_MIN_ALT_DEV_FT}")
    parser.add_argument("--min-dur", type=float, default=DEFAULT_MIN_DURATION_NM,
                        help=f"最小持续距离阈值(nm)，默认{DEFAULT_MIN_DURATION_NM}")
    parser.add_argument("--output-dir", "-o", default=REPORTS_DIR, help="输出目录")
    parser.add_argument("--open", action="store_true", help="生成后自动打开浏览器")
    args = parser.parse_args()

    config = {
        "min_alt_deviation_ft": args.min_alt_dev,
        "min_duration_nm": args.min_dur,
    }

    flight = args.flight.upper()

    # Determine dates to process
    dates_to_process = []
    if args.multi:
        # Scan all CSV files for this flight
        all_files = glob.glob(os.path.join(CSV_DIR, f"{flight}_*_plan_track.csv"))
        all_files += glob.glob(os.path.join(CSV_DIR, "*", f"{flight}_*_plan_track.csv"))
        dates_seen = set()
        for f in sorted(all_files):
            fname = os.path.basename(f)
            parts = fname.replace(".csv", "").split("_")
            if len(parts) >= 2:
                date_part = parts[1]
                if len(date_part) == 10 and date_part[4] == "-":
                    dates_seen.add(date_part)
        dates_to_process = sorted(dates_seen)
        print(f"扫描到 {len(dates_to_process)} 个日期: {', '.join(dates_to_process)}")
    elif args.dates:
        dates_to_process = [d.strip() for d in args.dates.split(",")]
    elif args.date:
        dates_to_process = [args.date]
    else:
        print("ERROR: 请指定 --date、--dates 或 --multi")
        sys.exit(1)

    # Load country index (once)
    print("加载国界数据...")
    country_index = CountryIndex(GEOJSON_PATH)

    # Process each date, collect results
    all_results = {}
    for date_str in dates_to_process:
        print(f"\n{'='*60}")
        print(f"分析: {flight} {date_str}")
        print(f"{'='*60}")

        plan_file, actual_file = find_csv_files(flight, date_str)
        if not plan_file or not actual_file:
            print(f"  SKIP: 找不到 CSV 文件")
            continue

        result = analyze_flight(plan_file, actual_file, config, country_index)
        if result is None:
            continue

        all_results[date_str] = result

        # Print summary to console
        print(f"\n  ── 分析结果摘要 ──")
        s = result["summary"]
        print(f"  最大高度偏差: +{s['max_alt_dev_ft']}ft / {s['min_alt_dev_ft']}ft")
        print(f"  最大水平偏差: {s['max_xt_dev_nm']}nm")
        print(f"  显著偏差事件: {s['significant_events']} 个")
        for w in result["warnings"][:5]:
            print(f"    [{w['severity']}] {w['region']} {w['direction']} {w['avg_dev_ft']}ft "
                  f"(计划FL{int(w['plan_alt']/100)} 实际FL{int(w['actual_alt']/100)}) "
                  f"持续{w['duration_nm']}nm")

        # Always generate single-day HTML
        output_path = os.path.join(args.output_dir, f"{flight}_{date_str}.html")
        generate_html_report(result, flight, date_str, output_path, config)

        if args.open and len(dates_to_process) == 1:
            webbrowser.open(f"file:///{output_path}")

    # Generate multi-day report if multiple dates
    if len(all_results) > 1:
        multi_path = os.path.join(args.output_dir, f"{flight}_multi.html")
        generate_multi_html(all_results, flight, multi_path, config)
        if args.open:
            webbrowser.open(f"file:///{multi_path}")

    print(f"\n{'='*60}")
    print("全部完成!")


if __name__ == "__main__":
    main()

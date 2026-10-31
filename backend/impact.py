"""Impact prediction engine.

Uses an explainable parametric approach: distance from each district centroid
to the typhoon center at closest approach, CMA wind radii / Holland wind
profile for wind, distance-based rain bands, and rule-based flood / transit /
driving conclusions.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from backend.geo import haversine_km, interpolate_cross_time

KT_TO_MPS = 0.514444

# --- Beaufort scale ------------------------------------------------------
_BEAUFORT_UPPER = [
    (0.3, "无风"), (1.6, "软风"), (3.4, "轻风"), (5.5, "微风"), (8.0, "和风"),
    (10.8, "清风"), (13.9, "强风"), (17.2, "疾风"), (20.8, "大风"),
    (24.5, "烈风"), (28.5, "狂风"), (32.7, "暴风"), (float("inf"), "飓风"),
]


def beaufort_from_mps(mps: float) -> tuple[int, str]:
    """Return (Beaufort level 0-12+, label)."""
    if mps < 0:
        mps = 0.0
    for level, (upper, label) in enumerate(_BEAUFORT_UPPER):
        if mps < upper:
            return level, label
    return 12, "飓风"


def beaufort_label(mps: float) -> str:
    level, label = beaufort_from_mps(mps)
    return f"{level}级·{label}"

# --- Rain bands ----------------------------------------------------------
# (min_distance_km, max_distance_km, level, estimated 24h amount, severity index)
RAIN_BANDS = [
    (0.0, 50.0, "特大暴雨", "≥250mm", 4),
    (50.0, 150.0, "大暴雨", "100–250mm", 3),
    (150.0, 250.0, "暴雨", "50–100mm", 2),
    (250.0, 400.0, "大雨", "25–50mm", 1),
    (400.0, float("inf"), "小到中雨", "0–25mm", 0),
]

RAIN_LEVELS = {r[2]: r for r in RAIN_BANDS}


def rain_band_by_distance(d_km: float, vmax_mps: float) -> dict:
    """Estimate 24h rainfall level from distance to center and typhoon intensity."""
    for lo, hi, level, amount, idx in RAIN_BANDS:
        if lo <= d_km < hi:
            break
    else:
        level, amount, idx = "特大暴雨", "≥250mm", 4
    # 强台风及以上(≥45 m/s)上调一档；减弱为热带低压(<17.2 m/s)下调一档
    if vmax_mps >= 45.0:
        idx = min(4, idx + 1)
    elif vmax_mps < 17.2:
        idx = max(0, idx - 1)
    band = next(b for b in RAIN_BANDS if b[4] == idx)
    return {"level": band[2], "amount": band[3], "severity": idx}

# --- Wind at distance ----------------------------------------------------
def _wind_from_radii(d_km: float, vmax_mps: float, radii: dict) -> float | None:
    """Estimate wind speed (m/s) at distance d using CMA wind radii anchors."""
    anchors = [(0.0, vmax_mps)]
    for kts in sorted(radii.keys(), reverse=True):
        r = min(radii[kts]) if radii[kts] else 0.0
        if r > 0:
            anchors.append((r, kts * KT_TO_MPS))
    anchors.sort(key=lambda a: a[0])
    if len(anchors) < 2:
        return None
    if d_km <= anchors[0][0]:
        return vmax_mps
    for i in range(len(anchors) - 1):
        r1, v1 = anchors[i]
        r2, v2 = anchors[i + 1]
        if r1 <= d_km <= r2:
            if r2 == r1:
                return v2
            t = (d_km - r1) / (r2 - r1)
            return v1 + t * (v2 - v1)
    # beyond outermost radius (30kt ring): radial decay v ~ sqrt(r30/d)
    r_outer, v_outer = anchors[-1]
    if r_outer <= 0:
        return v_outer
    return max(v_outer * math.sqrt(r_outer / d_km), 3.0)


def _wind_from_holland(d_km: float, vmax_mps: float, rmax_km: float = 40.0, b: float = 1.2) -> float:
    """Holland (1980) wind profile fallback when no radii are available."""
    r = max(d_km, rmax_km * 0.25)
    x = (rmax_km / r) ** b
    return vmax_mps * math.sqrt(x * math.exp(1.0 - x))


def wind_speed_at_distance(d_km: float, vmax_mps: float, radii: dict) -> float:
    if d_km < 5.0:
        return vmax_mps
    w = _wind_from_radii(d_km, vmax_mps, radii)
    if w is None:
        w = _wind_from_holland(d_km, vmax_mps)
    return w

# --- Flood / transit / driving rules -------------------------------------
FLOOD_SEVERITY = {"无积水": 0, "轻度积水": 1, "中度积水": 2, "严重积水": 3}


def flood_level(rain_level: str, flood_prone: bool, coastal: bool, wind_beaufort: int) -> tuple[str, int]:
    base = {
        "小到中雨": 0,
        "大雨": 1 if flood_prone else 0,
        "暴雨": 2 if flood_prone else 1,
        "大暴雨": 3 if flood_prone else 2,
        "特大暴雨": 3,
    }.get(rain_level, 0)
    # 沿海 + 10级以上大风 → 风暴潮叠加，积水上调一档
    if coastal and wind_beaufort >= 10:
        base = min(3, base + 1)
    return next(level for level, idx in FLOOD_SEVERITY.items() if idx == base), base


def transit_status(wind_beaufort: int, rain_level: str) -> str:
    if wind_beaufort >= 12:
        return "全市公交、地铁停运"
    if wind_beaufort >= 10:
        return "地铁高架段停运、轮渡停航"
    if rain_level in ("大暴雨", "特大暴雨"):
        return "地铁部分站点关闭、公交改道"
    if rain_level == "暴雨":
        return "公交正常运行但可能延误"
    return "公共交通正常运行"


def driving_advice(flood_level_name: str, wind_beaufort: int) -> tuple[str, str]:
    if flood_level_name == "严重积水" or wind_beaufort >= 12:
        return "不建议驾车", "积水严重或风力过大，不建议驾车出行"
    if flood_level_name == "中度积水" or wind_beaufort >= 10:
        return "谨慎驾驶", "谨慎驾驶，避开低洼及易积水路段"
    return "可以出行", "可以驾车出行，注意低洼路段积水"

# --- Closest approach ----------------------------------------------------
def closest_approach(p_lng: float, p_lat: float, points: list) -> dict:
    """Find the closest approach of a track (history+forecast) to a point.

    Returns distance_km, time, and the nearest track point (as a dict).
    """
    best = None
    for pt in points:
        d = haversine_km(p_lng, p_lat, pt.lng, pt.lat)
        if best is None or d < best["distance_km"]:
            best = {"distance_km": d, "time": pt.time, "point": pt}
    from backend.geo import point_to_segment
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        d, frac = point_to_segment(p_lng, p_lat, a.lng, a.lat, b.lng, b.lat)
        if d < best["distance_km"] - 1e-9:
            t = a.time + (b.time - a.time) * frac
            rep = a if frac < 0.5 else b
            best = {"distance_km": d, "time": t, "point": rep}
    return best

# --- City alert ----------------------------------------------------------
ALERT_NONE = 0
ALERT_POSSIBLE = 1
ALERT_IMMINENT = 2
ALERT_ACTIVE = 3

ALERT_TEXT = {
    ALERT_NONE: "暂无影响",
    ALERT_POSSIBLE: "可能受影响",
    ALERT_IMMINENT: "即将登陆",
    ALERT_ACTIVE: "正在影响/已登陆",
}


def city_alert(city_lng: float, city_lat: float, typhoon, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    all_pts = typhoon.all_points
    fc_pts = typhoon.forecast
    result = {
        "level": "none",
        "level_text": ALERT_TEXT[ALERT_NONE],
        "message": "预报路径暂无接近该城市的趋势",
        "min_distance_km": None,
        "closest_time": None,
        "landfall_time": None,
        "has_forecast": bool(fc_pts),
    }
    if not all_pts:
        result["message"] = "暂无台风轨迹数据"
        return result

    closest = closest_approach(city_lng, city_lat, all_pts)
    result["min_distance_km"] = round(closest["distance_km"], 1)
    result["closest_time"] = closest["time"]

    # distance to forecast track only (with crossing interpolation)
    fc_min = None
    landfall_time = None
    for pt in fc_pts:
        d = haversine_km(city_lng, city_lat, pt.lng, pt.lat)
        if fc_min is None or d < fc_min["distance_km"]:
            fc_min = {"distance_km": d, "time": pt.time}
    for i in range(len(fc_pts) - 1):
        a, b = fc_pts[i], fc_pts[i + 1]
        t = interpolate_cross_time(city_lng, city_lat, a.lng, a.lat, a.time.timestamp(),
                                   b.lng, b.lat, b.time.timestamp(), 100.0)
        if t is not None:
            cross_dt = datetime.fromtimestamp(t, tz=timezone.utc)
            if landfall_time is None or cross_dt < landfall_time:
                landfall_time = cross_dt

    if fc_min is None:
        # no official forecast -> use history only
        if closest["distance_km"] < 100 and closest["time"] <= now:
            return _alert(ALERT_ACTIVE, closest, landfall_time, fc_min, "台风中心已接近或登陆该城市", result)
        result["message"] = "暂无官方预报，仅基于实况轨迹判断暂无接近趋势"
        return result

    result["min_distance_km"] = round(min(closest["distance_km"], fc_min["distance_km"]), 1)
    if landfall_time is None and fc_min["distance_km"] < 100:
        landfall_time = fc_min["time"]

    if closest["distance_km"] < 100 and closest["time"] <= now:
        return _alert(ALERT_ACTIVE, closest, landfall_time, fc_min, "台风中心已接近或登陆该城市，请立即关注官方预警", result)
    if fc_min["distance_km"] < 100:
        return _alert(ALERT_IMMINENT, closest, landfall_time, fc_min, f"据预报，台风将于预计时间内接近该城市", result)
    if fc_min["distance_km"] < 200:
        return _alert(ALERT_POSSIBLE, closest, landfall_time, fc_min, "台风预报路径可能影响该城市，请持续关注", result)
    return _alert(ALERT_NONE, closest, landfall_time, fc_min, "预报路径暂无接近该城市的趋势", result)


def _alert(level: int, closest: dict, landfall_time, fc_min: dict, message: str, result: dict) -> dict:
    result["level"] = {ALERT_NONE: "none", ALERT_POSSIBLE: "possible",
                       ALERT_IMMINENT: "imminent", ALERT_ACTIVE: "active"}[level]
    result["level_text"] = ALERT_TEXT[level]
    result["message"] = message
    result["closest_time"] = closest["time"]
    if landfall_time is not None:
        result["landfall_time"] = landfall_time
    if fc_min is not None:
        result["forecast_min_distance_km"] = round(fc_min["distance_km"], 1)
    return result

# --- Overall severity for coloring ---------------------------------------
def district_severity(wind_bf: int, rain_sev: int, flood_sev: int) -> int:
    if wind_bf >= 13:
        w = 5
    elif wind_bf >= 12:
        w = 4
    elif wind_bf >= 10:
        w = 3
    elif wind_bf >= 8:
        w = 2
    elif wind_bf >= 6:
        w = 1
    else:
        w = 0
    return max(w, rain_sev, flood_sev)


# --- Public entry --------------------------------------------------------
def compute_city_impact(city, typhoon, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    alert = city_alert(city.center_lng, city.center_lat, typhoon, now)
    current_grade = typhoon.history[-1].grade_cn if typhoon.history else ""
    districts = []
    for d in city.districts:
        closest = closest_approach(d.lng, d.lat, typhoon.all_points)
        dist_km = closest["distance_km"]
        pt = closest["point"]
        vmax = pt.wind_mps if pt else typhoon.max_wind_mps()
        wind_mps = wind_speed_at_distance(dist_km, vmax, pt.wind_radii if pt else {})
        bf, _ = beaufort_from_mps(wind_mps)
        rain = rain_band_by_distance(dist_km, vmax)
        flood_name, flood_sev = flood_level(rain["level"], d.flood_prone, d.coastal, bf)
        driving_name, driving_text = driving_advice(flood_name, bf)
        districts.append({
            "name": d.name,
            "adcode": d.adcode,
            "coastal": d.coastal,
            "flood_prone": d.flood_prone,
            "distance_km": round(dist_km, 1),
            "closest_time": closest["time"].isoformat(),
            "wind": {"mps": round(wind_mps, 1), "beaufort": bf, "label": beaufort_label(wind_mps)},
            "rain": rain,
            "flood": {"level": flood_name, "severity": flood_sev},
            "transit": transit_status(bf, rain["level"]),
            "driving": {"level": driving_name, "text": driving_text},
            "severity": district_severity(bf, rain["severity"], flood_sev),
        })
    # sort by severity desc, then distance asc
    districts.sort(key=lambda x: (-x["severity"], x["distance_km"]))
    return {
        "city": {"id": city.id, "name": city.name, "adcode": city.adcode},
        "typhoon": {
            "id": typhoon.id,
            "name_cn": typhoon.name_cn,
            "name_en": typhoon.name_en,
            "number": typhoon.number,
            "current_grade": current_grade,
            "max_wind_mps": round(typhoon.max_wind_mps(), 1),
        },
        "alert": alert,
        "districts": districts,
        "generated_at": now.isoformat(),
    }

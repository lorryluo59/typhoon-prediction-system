"""Geographic helpers: haversine distance, point-to-segment distance, interpolation."""
from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0


def haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """Great-circle distance in kilometers between two (lng, lat) points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _bearing_deg(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """Initial bearing (degrees) from point 1 to point 2."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlmb = math.radians(lng2 - lng1)
    y = math.sin(dlmb) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlmb)
    return math.degrees(math.atan2(y, x)) % 360.0


def point_to_segment(p_lng: float, p_lat: float,
                     a_lng: float, a_lat: float,
                     b_lng: float, b_lat: float) -> tuple:
    """Shortest distance (km) from point P to great-circle segment A->B,
    plus the along-track fraction (0..1) of the projection point on A->B."""
    d13 = haversine_km(a_lng, a_lat, p_lng, p_lat) / EARTH_RADIUS_KM  # angular distance (rad)
    if d13 < 1e-9:
        return 0.0, 0.0
    theta13 = math.radians(_bearing_deg(a_lng, a_lat, p_lng, p_lat))
    theta12 = math.radians(_bearing_deg(a_lng, a_lat, b_lng, b_lat))
    xt = math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(theta13 - theta12))))
    at = math.acos(max(-1.0, min(1.0, math.cos(d13) / math.cos(xt))))
    d12 = haversine_km(a_lng, a_lat, b_lng, b_lat) / EARTH_RADIUS_KM  # angular distance (rad)
    if d12 < 1e-9:
        return haversine_km(p_lng, p_lat, a_lng, a_lat), 0.0
    frac = max(0.0, min(1.0, at / d12))
    dist = abs(xt) * EARTH_RADIUS_KM
    if at > d12:  # projection falls beyond B -> endpoint B
        dist = haversine_km(p_lng, p_lat, b_lng, b_lat)
        frac = 1.0
    elif at < 0:
        dist = haversine_km(p_lng, p_lat, a_lng, a_lat)
        frac = 0.0
    return dist, frac


def point_to_segment_km(p_lng: float, p_lat: float,
                        a_lng: float, a_lat: float,
                        b_lng: float, b_lat: float) -> float:
    """Shortest distance (km) from point P to the great-circle segment A->B."""
    dist, _ = point_to_segment(p_lng, p_lat, a_lng, a_lat, b_lng, b_lat)
    return dist


def interpolate_cross_time(p_lng: float, p_lat: float,
                           a_lng: float, a_lat: float, a_time: float,
                           b_lng: float, b_lat: float, b_time: float,
                           radius_km: float) -> float | None:
    """If the segment A->B crosses distance `radius_km` from point P, return the
    linearly interpolated time (same units as a_time/b_time) of the crossing,
    else None."""
    da = haversine_km(p_lng, p_lat, a_lng, a_lat)
    db = haversine_km(p_lng, p_lat, b_lng, b_lat)
    if (da - radius_km) * (db - radius_km) > 0:
        return None
    if abs(da - db) < 1e-9:
        return a_time
    t = (da - radius_km) / (da - db)
    return a_time + t * (b_time - a_time)

"""CMA (National Meteorological Center) typhoon data client.

Fetches real typhoon track data from the public typhoon network:
  https://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default
  https://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{id}

Responses are JSONP-wrapped (e.g. `typhoon_jsons_list_default(({...}))`);
this module strips the wrapper and caches responses in memory (TTL cache).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

LIST_URL = "https://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
VIEW_URL = "https://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{id}"

# CMA grade codes -> Chinese labels
GRADE_CN = {
    "TD": "热带低压",
    "TS": "热带风暴",
    "STS": "强热带风暴",
    "TY": "台风",
    "STY": "强台风",
    "SuperTY": "超强台风",
}


def strip_jsonp(text: str) -> Any:
    """Remove the JSONP function wrapper `name(({...}))` and return the payload."""
    start = text.find("(")
    end = text.rfind(")")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("invalid JSONP payload")
    content = text[start + 1:end].strip()
    # CMA wraps the JSON object in an extra pair of parentheses
    while content.startswith("(") and content.endswith(")"):
        content = content[1:-1]
    return json.loads(content)


@dataclass
class TrackPoint:
    time: datetime
    lng: float
    lat: float
    pressure: int
    wind_mps: float
    grade: str
    grade_cn: str
    move_dir: str = ""
    move_speed_kmh: float = 0.0
    # wind radii in km: {threshold_kt: (radius_ne, radius_e, radius_s, radius_w)}
    wind_radii: dict = field(default_factory=dict)
    # forecast entries attached to this observed point: {center: [raw entries]}
    forecast_raw: dict = field(default_factory=dict)


@dataclass
class TyphoonSummary:
    id: int
    name_en: str
    name_cn: str
    number: str
    status: str


@dataclass
class TyphoonDetail:
    id: int
    name_en: str
    name_cn: str
    number: str
    status: str
    history: list  # list[TrackPoint]
    forecast: list  # list[TrackPoint]
    all_points: list  # history + forecast, time ordered

    def max_wind_mps(self) -> float:
        return max((p.wind_mps for p in self.all_points), default=0.0)


def _parse_time(ts_ms: int) -> datetime:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


def _parse_track_point(raw: list) -> TrackPoint | None:
    """Parse one CMA track entry (observed point)."""
    try:
        # [id, time_str, ts_ms, grade, lng, lat, pressure, wind_mps, dir, speed_kmh, [radii...], forecast, null]
        _, time_str, ts_ms, grade, lng, lat, pressure, wind_mps, move_dir, move_speed, radii_raw, forecast_raw, _ = raw
        radii: dict = {}
        for entry in radii_raw or []:
            kts = int(str(entry[0]).replace("KTS", ""))
            radii[kts] = (float(entry[1]), float(entry[2]), float(entry[3]), float(entry[4]))
        return TrackPoint(
            time=_parse_time(ts_ms),
            lng=float(lng),
            lat=float(lat),
            pressure=int(pressure),
            wind_mps=float(wind_mps),
            grade=str(grade),
            grade_cn=GRADE_CN.get(str(grade), str(grade)),
            move_dir=str(move_dir),
            move_speed_kmh=float(move_speed or 0),
            wind_radii=radii,
            forecast_raw=forecast_raw or {},
        )
    except (ValueError, TypeError, IndexError) as exc:  # pragma: no cover - defensive
        logger.warning("skip unparsable track point %r: %s", raw, exc)
        return None


def _parse_forecast_point(raw: list, base_time: datetime) -> TrackPoint | None:
    """Parse one CMA forecast entry: [hours, base_time_str, lng, lat, pressure, wind, center, grade]."""
    try:
        hours, _, lng, lat, pressure, wind_mps, _, grade = raw
        forecast_time = base_time + timedelta(hours=int(hours))
        return TrackPoint(
            time=forecast_time,
            lng=float(lng),
            lat=float(lat),
            pressure=int(pressure),
            wind_mps=float(wind_mps),
            grade=str(grade),
            grade_cn=GRADE_CN.get(str(grade), str(grade)),
        )
    except (ValueError, TypeError, IndexError) as exc:  # pragma: no cover - defensive
        logger.warning("skip unparsable forecast point %r: %s", raw, exc)
        return None


class CMAClient:
    def __init__(self, timeout: float = 15.0, cache_ttl: float = 600.0,
                 list_url: str = LIST_URL, view_url: str = VIEW_URL):
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.list_url = list_url
        self.view_url = view_url
        self._cache: dict = {}
        self._lock = threading.Lock()

    # --- caching ---------------------------------------------------------
    def _get_cached(self, key: str):
        with self._lock:
            item = self._cache.get(key)
            if item and item[0] > time.time():
                return item[1]
            return None

    def _store(self, key: str, value: Any) -> None:
        with self._lock:
            self._cache[key] = (time.time() + self.cache_ttl, value)

    def _fetch(self, url: str, key: str) -> Any:
        cached = self._get_cached(key)
        if cached is not None:
            return cached
        try:
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            resp.encoding = "utf-8"  # CMA API serves UTF-8 without charset header
            payload = strip_jsonp(resp.text)
            self._store(key, payload)
            return payload
        except Exception:
            # Graceful degradation: fall back to last cached payload if any.
            if cached is not None:
                return cached
            raise

    # --- public API ------------------------------------------------------
    def list_typhoons(self) -> list[TyphoonSummary]:
        payload = self._fetch(self.list_url, "list_default")
        out: list[TyphoonSummary] = []
        for item in payload.get("typhoonList", []):
            try:
                out.append(TyphoonSummary(
                    id=int(item[0]),
                    name_en=str(item[1]),
                    name_cn=str(item[2]) if item[2] else "",
                    number=str(item[3]),
                    status=str(item[7]) if len(item) > 7 else "",
                ))
            except (ValueError, TypeError, IndexError) as exc:  # pragma: no cover
                logger.warning("skip unparsable typhoon summary %r: %s", item, exc)
        return out

    def get_typhoon(self, typhoon_id: int) -> TyphoonDetail:
        payload = self._fetch(self.view_url.format(id=typhoon_id), f"view_{typhoon_id}")
        typhoon = payload.get("typhoon", [])
        if not typhoon:
            raise ValueError(f"typhoon {typhoon_id} not found in payload")
        tid, name_en, name_cn, number, _, _, _, status = typhoon[:8]
        raw_history = typhoon[8] if len(typhoon) > 8 else []
        history = [p for p in (_parse_track_point(pt) for pt in raw_history) if p]

        # 只取最新一次起报的预报（最近一个带预报的实况点），避免旧起报点污染
        forecast: list[TrackPoint] = []
        for pt in reversed(history):
            if pt.forecast_raw:
                for center, entries in pt.forecast_raw.items():
                    for raw_fc in entries:
                        fp = _parse_forecast_point(raw_fc, pt.time)
                        if fp:
                            forecast.append(fp)
                break
        # de-duplicate & sort by time
        seen = set()
        uniq_forecast = []
        for fp in sorted(forecast, key=lambda p: p.time):
            if fp.time not in seen:
                seen.add(fp.time)
                uniq_forecast.append(fp)
        all_points = sorted(history + uniq_forecast, key=lambda p: p.time)
        return TyphoonDetail(
            id=int(tid),
            name_en=str(name_en),
            name_cn=str(name_cn) if name_cn else "",
            number=str(number),
            status=str(status),
            history=history,
            forecast=uniq_forecast,
            all_points=all_points,
        )

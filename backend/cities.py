"""City / district data loader.

Loads bundled district GeoJSON (data/cities/*.json) plus hand-curated
per-district attributes (coastal, flood-prone) from data/cities/cities.json.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cities")


@dataclass
class District:
    name: str
    adcode: int
    lng: float
    lat: float
    coastal: bool = False
    flood_prone: bool = False
    geometry: dict = field(default_factory=dict)


@dataclass
class City:
    id: str
    name: str
    adcode: int
    center_lng: float
    center_lat: float
    districts: list = field(default_factory=list)

    def district_by_name(self, name: str) -> District | None:
        for d in self.districts:
            if d.name == name:
                return d
        return None


def _centroid_of(properties: dict, geometry: dict) -> tuple[float, float] | None:
    """Extract centroid from GeoJSON properties or compute from geometry."""
    if properties.get("centroid"):
        return float(properties["centroid"][0]), float(properties["centroid"][1])
    if properties.get("center"):
        return float(properties["center"][0]), float(properties["center"][1])
    # fallback: average of polygon ring coordinates (MultiPolygon aware)
    coords = geometry.get("coordinates", [])
    pts = []
    if geometry.get("type") == "MultiPolygon":
        for poly in coords:
            ring = poly[0]
            pts.extend(ring)
    elif geometry.get("type") == "Polygon":
        pts = coords[0]
    if pts:
        n = len(pts)
        return sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n
    return None


class CityRegistry:
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        self.cities: list[City] = []
        self._load()

    def _load(self) -> None:
        meta_path = os.path.join(self.data_dir, "cities.json")
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        for item in meta["cities"]:
            geo_path = os.path.join(self.data_dir, item["file"])
            with open(geo_path, encoding="utf-8") as f:
                geojson = json.load(f)
            attrs = item.get("districts", {})
            districts = []
            for feat in geojson.get("features", []):
                props = feat.get("properties", {})
                name = props.get("name", "")
                a = attrs.get(name, {})
                centroid = _centroid_of(props, feat.get("geometry", {}))
                if not centroid:
                    continue
                districts.append(District(
                    name=name,
                    adcode=int(props.get("adcode", 0)),
                    lng=centroid[0],
                    lat=centroid[1],
                    coastal=bool(a.get("coastal", False)),
                    flood_prone=bool(a.get("flood_prone", False)),
                    geometry=feat.get("geometry", {}),
                ))
            self.cities.append(City(
                id=item["id"],
                name=item["name"],
                adcode=int(item["adcode"]),
                center_lng=float(item["center"][0]),
                center_lat=float(item["center"][1]),
                districts=districts,
            ))

    def get(self, city_id: str) -> City | None:
        for c in self.cities:
            if c.id == city_id:
                return c
        return None

    def get_by_adcode(self, adcode: int) -> City | None:
        for c in self.cities:
            if c.adcode == adcode:
                return c
        return None

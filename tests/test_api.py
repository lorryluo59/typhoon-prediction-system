import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.cma_client import TrackPoint, TyphoonDetail, TyphoonSummary
from backend.cities import CityRegistry
from datetime import datetime, timezone


def _tp(h, lng, lat, wind=20, grade="TS"):
    return TrackPoint(time=datetime(2026, 8, 25, tzinfo=timezone.utc), lng=lng, lat=lat,
                      pressure=990, wind_mps=wind, grade=grade, grade_cn=grade)


class FakeCMA:
    def __init__(self):
        self.detail = TyphoonDetail(
            3314447, "ATSANI", "艾莎尼", "2621", "start",
            [_tp(0, 130.0, 20.0)],
            [_tp(12, 115.5, 21.5), _tp(24, 115.2, 22.0)],
            [_tp(0, 130.0, 20.0), _tp(12, 115.5, 21.5), _tp(24, 115.2, 22.0)],
        )
    def list_typhoons(self):
        return [TyphoonSummary(3314447, "ATSANI", "艾莎尼", "2621", "start")]
    def get_typhoon(self, tid):
        if tid != 3314447:
            raise ValueError("not found")
        return self.detail


class BoomCMA:
    def list_typhoons(self):
        raise RuntimeError("network down")
    def get_typhoon(self, tid):
        raise RuntimeError("network down")


@pytest.fixture(scope="module")
def client():
    app = create_app(cma=FakeCMA(), cities=CityRegistry())
    return TestClient(app)


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "台风预测系统" in r.text


def test_api_typhoons(client):
    r = client.get("/api/typhoons")
    assert r.status_code == 200
    data = r.json()
    assert data["typhoons"][0]["name_cn"] == "艾莎尼"


def test_api_typhoon_detail(client):
    r = client.get("/api/typhoons/3314447")
    assert r.status_code == 200
    t = r.json()["typhoon"]
    assert len(t["history"]) == 1
    assert len(t["forecast"]) == 2


def test_api_typhoon_404(client):
    r = client.get("/api/typhoons/999999")
    assert r.status_code == 404


def test_api_cities(client):
    r = client.get("/api/cities")
    assert r.status_code == 200
    cities = r.json()["cities"]
    assert len(cities) == 10
    shenzhen = next(c for c in cities if c["id"] == "shenzhen")
    assert shenzhen["name"] == "深圳"
    assert len(shenzhen["districts"]) == 9
    assert "geometry" in shenzhen["districts"][0]


def test_api_impact(client):
    r = client.get("/api/impact?city=shenzhen&typhoon=3314447")
    assert r.status_code == 200
    data = r.json()
    assert data["city"]["name"] == "深圳"
    assert data["typhoon"]["name_cn"] == "艾莎尼"
    assert data["alert"]["level"] in ("imminent", "possible", "none", "active")
    assert len(data["districts"]) == 9
    d0 = data["districts"][0]
    for key in ("wind", "rain", "flood", "transit", "driving", "severity", "distance_km"):
        assert key in d0


def test_api_impact_bad_city(client):
    r = client.get("/api/impact?city=nowhere&typhoon=3314447")
    assert r.status_code == 404


def test_api_degradation():
    app = create_app(cma=BoomCMA(), cities=CityRegistry())
    c = TestClient(app)
    r = c.get("/api/typhoons")
    assert r.status_code == 502
    r = c.get("/api/impact?city=shenzhen&typhoon=3314447")
    assert r.status_code == 502

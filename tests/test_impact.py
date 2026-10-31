import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.impact import (
    beaufort_from_mps, beaufort_label, rain_band_by_distance,
    wind_speed_at_distance, flood_level, transit_status, driving_advice,
    city_alert, compute_city_impact,
)
from backend.cma_client import TrackPoint, TyphoonDetail
from backend.cities import City, District
from datetime import datetime, timedelta, timezone


def test_beaufort_boundaries():
    assert beaufort_from_mps(13.8)[0] == 6
    assert beaufort_from_mps(13.9)[0] == 7
    assert beaufort_from_mps(17.2)[0] == 8
    assert beaufort_from_mps(24.5)[0] == 10
    assert beaufort_from_mps(32.7)[0] == 12
    assert beaufort_from_mps(50.0)[0] == 12
    assert beaufort_label(18.0) == "8级·大风"


def test_rain_bands():
    assert rain_band_by_distance(10, 20)["level"] == "特大暴雨"
    assert rain_band_by_distance(100, 20)["level"] == "大暴雨"
    assert rain_band_by_distance(200, 20)["level"] == "暴雨"
    assert rain_band_by_distance(300, 20)["level"] == "大雨"
    assert rain_band_by_distance(500, 20)["level"] == "小到中雨"


def test_rain_boost_and_downgrade():
    # 强台风 (>=45m/s) 上调一档：150-250 暴雨 -> 大暴雨
    assert rain_band_by_distance(200, 50)["level"] == "大暴雨"
    # 减弱为热带低压 (<17.2) 下调一档：50-150 大暴雨 -> 暴雨
    assert rain_band_by_distance(100, 15)["level"] == "暴雨"
    # 边界不越界
    assert rain_band_by_distance(10, 50)["level"] == "特大暴雨"
    assert rain_band_by_distance(500, 15)["level"] == "小到中雨"


def test_wind_from_radii():
    radii = {30: (200, 200, 200, 200), 50: (100, 100, 100, 100), 64: (60, 60, 60, 60)}
    # at center -> vmax
    assert wind_speed_at_distance(1, 40, radii) == 40
    # inside R64 -> high
    assert wind_speed_at_distance(30, 40, radii) > 30
    # at R30 ring -> ~30kt = 15.4 m/s
    assert abs(wind_speed_at_distance(200, 40, radii) - 15.4) < 1.5
    # far outside -> decay below 15.4
    assert wind_speed_at_distance(400, 40, radii) < 12


def test_wind_holland_fallback():
    v = wind_speed_at_distance(200, 40, {})
    assert 5 < v < 40
    v2 = wind_speed_at_distance(2000, 40, {})
    assert v2 < v


def test_flood_rules():
    assert flood_level("小到中雨", False, False, 5) == ("无积水", 0)
    assert flood_level("大雨", True, False, 5) == ("轻度积水", 1)
    assert flood_level("暴雨", False, False, 5) == ("轻度积水", 1)
    assert flood_level("暴雨", True, False, 5) == ("中度积水", 2)
    assert flood_level("大暴雨", False, False, 5) == ("中度积水", 2)
    assert flood_level("大暴雨", True, False, 5) == ("严重积水", 3)
    assert flood_level("特大暴雨", False, False, 5) == ("严重积水", 3)
    # 沿海 + 10级大风 -> 风暴潮上调一档
    assert flood_level("大雨", True, True, 10) == ("中度积水", 2)


def test_transit_rules():
    assert transit_status(12, "暴雨") == "全市公交、地铁停运"
    assert transit_status(10, "大雨") == "地铁高架段停运、轮渡停航"
    assert transit_status(8, "大暴雨") == "地铁部分站点关闭、公交改道"
    assert transit_status(8, "特大暴雨") == "地铁部分站点关闭、公交改道"
    assert transit_status(8, "暴雨") == "公交正常运行但可能延误"
    assert transit_status(5, "大雨") == "公共交通正常运行"


def test_driving_rules():
    assert driving_advice("严重积水", 8) == ("不建议驾车", "积水严重或风力过大，不建议驾车出行")
    assert driving_advice("无积水", 12) == ("不建议驾车", "积水严重或风力过大，不建议驾车出行")
    assert driving_advice("中度积水", 6)[0] == "谨慎驾驶"
    assert driving_advice("无积水", 10)[0] == "谨慎驾驶"
    assert driving_advice("无积水", 5)[0] == "可以出行"


def _tp(hour_offset, lng, lat, wind=20, grade="TS"):
    return TrackPoint(
        time=datetime(2026, 8, 25, tzinfo=timezone.utc) + timedelta(hours=hour_offset),
        lng=lng, lat=lat, pressure=990, wind_mps=wind, grade=grade, grade_cn=grade,
    )


def _typhoon(history=None, forecast=None):
    history = history or [_tp(-6, 130.0, 20.0)]
    forecast = forecast or [_tp(12, 130.0, 20.0)]
    all_pts = sorted(history + forecast, key=lambda p: p.time)
    return TyphoonDetail(1, "TEST", "测试", "2601", "start", history, forecast, all_pts)


def test_alert_none():
    d = _typhoon(forecast=[_tp(12, 125.0, 30.0)])  # far north, ~700km from (115,22)
    a = city_alert(115.0, 22.0, d, now=datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert a["level"] == "none"


def test_alert_imminent_point():
    d = _typhoon(forecast=[_tp(12, 114.5, 21.5)])  # ~79km -> imminent
    a = city_alert(115.0, 22.0, d, now=datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert a["level"] == "imminent"


def test_alert_possible():
    d = _typhoon(forecast=[_tp(12, 113.5, 21.0)])  # ~180km -> possible
    a = city_alert(115.0, 22.0, d, now=datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert a["level"] == "possible"


def test_alert_imminent_and_landfall_time():
    # segment crosses from 250km to 30km -> imminent, landfall time between 12h and 24h
    d = _typhoon(forecast=[_tp(12, 117.3, 22.0), _tp(24, 115.3, 22.0)])
    a = city_alert(115.0, 22.0, d, now=datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert a["level"] == "imminent"
    assert a["landfall_time"] is not None
    assert a["landfall_time"] < _tp(24, 115.3, 22.0).time


def test_alert_active_history():
    # typhoon center already within 100km at a past time
    d = _typhoon(history=[_tp(-6, 115.4, 22.1)], forecast=[_tp(12, 115.6, 22.2)])
    a = city_alert(115.0, 22.0, d, now=datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert a["level"] == "active"


def test_compute_city_impact_shape():
    city = City("test", "测试市", 999999, 115.0, 22.0, districts=[
        District("东区", 1, 115.1, 22.1, coastal=True, flood_prone=True),
        District("西区", 2, 115.5, 22.5, coastal=False, flood_prone=False),
    ])
    d = _typhoon(
        history=[_tp(-6, 130.0, 20.0), _tp(0, 116.5, 21.5)],
        forecast=[_tp(12, 115.5, 21.8), _tp(24, 115.2, 22.2)],
    )
    out = compute_city_impact(city, d, now=datetime(2026, 8, 25, tzinfo=timezone.utc))
    assert out["city"]["name"] == "测试市"
    assert out["alert"]["level"] in ("imminent", "active", "possible", "none")
    assert len(out["districts"]) == 2
    for dd in out["districts"]:
        for key in ("wind", "rain", "flood", "transit", "driving", "severity", "distance_km"):
            assert key in dd
    # sorted by severity desc
    sevs = [dd["severity"] for dd in out["districts"]]
    assert sevs == sorted(sevs, reverse=True)

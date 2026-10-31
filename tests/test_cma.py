import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from backend.cma_client import CMAClient, strip_jsonp, _parse_track_point, _parse_forecast_point
from datetime import datetime, timezone

LIST_JSONP = """typhoon_jsons_list_default(({"typhoonList":[[3314447,"ATSANI","艾莎尼","2621","2621",null,"闪电","start"],[3302529,"NANGKA","浪卡","2617","2617",null,"菠萝蜜","stop"]]}))"""

VIEW_JSONP = 'typhoon_jsons_view_3314447({"typhoon": [3314447, "ATSANI", "艾莎尼", "2621", "2621", null, "闪电", "start", [[3314491, "202608240000", 1787529600000, "TS", 137.2, 15.2, 998, 18, "NNE", 19, [["30KTS", 170, 280, 170, 180, 3314491]], {"BABJ": [[12, "202608240000", 137.9, 17.1, 998, 18, "BABJ", "TS"], [24, "202608240000", 137.9, 19.5, 1000, 15, "BABJ", "TD"]]}, null], [3314792, "202608240300", 1787540400000, "TS", 137.5, 15.4, 998, 18, "NNE", 20, [["30KTS", 170, 280, 170, 180, 3314792]], {"BABJ": [[12, "202608240300", 138.0, 17.5, 998, 18, "BABJ", "TS"]]}, null]], [[3304364, {"0": [79, 80]}]]]})'


def test_strip_jsonp():
    payload = strip_jsonp(LIST_JSONP)
    assert payload["typhoonList"][0][1] == "ATSANI"


def test_strip_jsonp_invalid():
    with pytest.raises(ValueError):
        strip_jsonp("not jsonp")


def test_parse_track_point():
    raw = [3314491, "202608240000", 1787529600000, "TS", 137.2, 15.2, 998, 18, "NNE", 19,
           [["30KTS", 170, 280, 170, 180, 3314491]], {}, None]
    pt = _parse_track_point(raw)
    assert pt.lng == 137.2 and pt.lat == 15.2
    assert pt.wind_mps == 18.0
    assert pt.grade_cn == "热带风暴"
    assert pt.wind_radii[30] == (170.0, 280.0, 170.0, 180.0)
    assert pt.time == datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc)


def test_parse_forecast_point():
    base = datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc)
    fp = _parse_forecast_point([12, "202608240000", 137.9, 17.1, 998, 18, "BABJ", "TS"], base)
    assert fp.time == datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    assert fp.lng == 137.9 and fp.wind_mps == 18.0


class _Resp:
    def __init__(self, text):
        self.text = text
        self.encoding = "utf-8"
    def raise_for_status(self):
        pass


def test_client_parses_list(monkeypatch):
    c = CMAClient()
    monkeypatch.setattr("backend.cma_client.requests.get", lambda url, timeout: _Resp(LIST_JSONP))
    ty = c.list_typhoons()
    assert len(ty) == 2
    assert ty[0].id == 3314447
    assert ty[0].name_cn == "艾莎尼"
    assert ty[0].status == "start"


def test_client_parses_detail(monkeypatch):
    c = CMAClient()
    monkeypatch.setattr("backend.cma_client.requests.get", lambda url, timeout: _Resp(VIEW_JSONP))
    d = c.get_typhoon(3314447)
    assert d.name_cn == "艾莎尼"
    assert len(d.history) == 2
    # 只保留最新一次起报（第二个实况点 03:00）的预报：+12h -> 15:00
    assert len(d.forecast) == 1
    assert d.forecast[0].time == d.history[1].time + __import__("datetime").timedelta(hours=12)
    assert (d.forecast[0].lng, d.forecast[0].lat) == (138.0, 17.5)
    assert d.history[0].wind_radii[30][0] == 170.0


def test_latest_forecast_run_fallback(monkeypatch):
    # 最新实况点无预报时，回退到最近一个带预报的实况点
    payload = {"typhoon": [1, "X", "测试", "2601", "2601", None, None, "start", [
        [10, "202608240000", 1787529600000, "TS", 130.0, 20.0, 998, 18, "N", 20,
         [], {"BABJ": [[12, "202608240000", 131.0, 21.0, 998, 18, "BABJ", "TS"]]}, None],
        [11, "202608240300", 1787540400000, "TS", 130.5, 20.5, 998, 18, "N", 20,
         [], {}, None],
    ], []]}
    import json
    body = "x(" + json.dumps(payload, ensure_ascii=False) + ")"
    class R:
        encoding = "utf-8"
        text = body
        def raise_for_status(self): pass
    c = CMAClient()
    monkeypatch.setattr("backend.cma_client.requests.get", lambda url, timeout: R())
    d = c.get_typhoon(1)
    assert len(d.forecast) == 1
    assert d.forecast[0].time.hour == 12  # +12h from the first point


def test_client_cache_fallback(monkeypatch):
    c = CMAClient(cache_ttl=600)
    # prime cache manually
    c._store("list_default", {"typhoonList": [[1, "X", "测试", "1", "1", None, None, "start"]]})
    def boom(url, timeout):
        raise RuntimeError("network down")
    monkeypatch.setattr("backend.cma_client.requests.get", boom)
    ty = c.list_typhoons()  # should fall back to cache, not raise
    assert ty[0].id == 1


def test_client_no_cache_raises(monkeypatch):
    c = CMAClient()
    def boom(url, timeout):
        raise RuntimeError("network down")
    monkeypatch.setattr("backend.cma_client.requests.get", boom)
    with pytest.raises(RuntimeError):
        c.list_typhoons()

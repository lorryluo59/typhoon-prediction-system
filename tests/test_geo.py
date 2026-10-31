import math
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.geo import haversine_km, point_to_segment, interpolate_cross_time


def test_haversine_known_distance():
    # Beijing -> Shanghai approx 1068 km
    d = haversine_km(116.4074, 39.9042, 121.4737, 31.2304)
    assert 1000 < d < 1150


def test_haversine_zero():
    assert haversine_km(114.0, 22.0, 114.0, 22.0) == 0.0


def test_point_on_segment():
    # 经线段落即大圆路径，中点应几乎在线上
    a = (115.0, 20.0)
    b = (115.0, 30.0)
    p = (115.0, 25.0)
    d, frac = point_to_segment(p[0], p[1], a[0], a[1], b[0], b[1])
    assert d < 0.5
    assert 0.49 < frac < 0.51


def test_point_on_latitude_line_uses_great_circle():
    # 同纬度线段不是大圆：跨轨距离应显著 > 0（纬度越高偏差越大）
    a = (110.0, 60.0)
    b = (120.0, 60.0)
    p = (115.0, 60.0)
    d, frac = point_to_segment(p[0], p[1], a[0], a[1], b[0], b[1])
    assert 5 < d < 25


def test_point_beyond_segment_uses_endpoint():
    a = (110.0, 20.0)
    b = (120.0, 20.0)
    p = (125.0, 20.0)  # beyond B
    d, frac = point_to_segment(p[0], p[1], a[0], a[1], b[0], b[1])
    assert frac == 1.0
    assert abs(d - haversine_km(120.0, 20.0, 125.0, 20.0)) < 0.1


def test_interpolate_cross_time():
    # segment (110,20)->(120,20); point (118,21) is ~111km north of the line
    a = (110.0, 20.0)
    b = (120.0, 20.0)
    p = (118.0, 21.0)
    # distance from p to A ~896km, to B ~249km; radius 500 crosses the segment
    t = interpolate_cross_time(p[0], p[1], a[0], a[1], 0.0, b[0], b[1], 10.0, radius_km=500.0)
    assert t is not None and 5.5 < t < 6.8
    # radius below both endpoint distances -> no crossing
    assert interpolate_cross_time(p[0], p[1], a[0], a[1], 0.0, b[0], b[1], 10.0, radius_km=100.0) is None

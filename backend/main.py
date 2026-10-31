"""FastAPI application: typhoon prediction dashboard backend."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.cities import CityRegistry
from backend.cma_client import CMAClient
from backend.impact import compute_city_impact

logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def _summary_dict(t) -> dict:
    return {
        "id": t.id,
        "name_en": t.name_en,
        "name_cn": t.name_cn,
        "number": t.number,
        "status": t.status,
    }


def _point_dict(p) -> dict:
    return {
        "time": p.time.isoformat(),
        "lng": p.lng,
        "lat": p.lat,
        "pressure": p.pressure,
        "wind_mps": p.wind_mps,
        "grade": p.grade,
        "grade_cn": p.grade_cn,
        "move_dir": p.move_dir,
        "move_speed_kmh": p.move_speed_kmh,
        "wind_radii": {str(k): v for k, v in p.wind_radii.items()},
    }


def create_app(cma: CMAClient | None = None, cities: CityRegistry | None = None) -> FastAPI:
    app = FastAPI(title="台风预测系统", version="1.0.0")
    cma_client = cma or CMAClient()
    registry = cities or CityRegistry()

    @app.exception_handler(Exception)
    async def unhandled(request, exc):
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/api/typhoons")
    def typhoons():
        try:
            items = cma_client.list_typhoons()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"获取台风列表失败: {exc}")
        return {"typhoons": [_summary_dict(t) for t in items]}

    @app.get("/api/typhoons/{typhoon_id}")
    def typhoon_detail(typhoon_id: int):
        try:
            d = cma_client.get_typhoon(typhoon_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="台风不存在")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"获取台风数据失败: {exc}")
        return {
            "typhoon": {
                "id": d.id,
                "name_en": d.name_en,
                "name_cn": d.name_cn,
                "number": d.number,
                "status": d.status,
                "history": [_point_dict(p) for p in d.history],
                "forecast": [_point_dict(p) for p in d.forecast],
            }
        }

    @app.get("/api/cities")
    def cities():
        out = []
        for c in registry.cities:
            out.append({
                "id": c.id,
                "name": c.name,
                "adcode": c.adcode,
                "center": [c.center_lng, c.center_lat],
                "districts": [{
                    "name": d.name,
                    "adcode": d.adcode,
                    "coastal": d.coastal,
                    "flood_prone": d.flood_prone,
                    "centroid": [d.lng, d.lat],
                    "geometry": d.geometry,
                } for d in c.districts],
            })
        return {"cities": out}

    @app.get("/api/impact")
    def impact(city: str = Query(..., description="城市 id，如 shenzhen"),
               typhoon: int = Query(..., description="台风 id")):
        c = registry.get(city)
        if c is None:
            raise HTTPException(status_code=404, detail=f"不支持的城市: {city}")
        try:
            d = cma_client.get_typhoon(typhoon)
        except ValueError:
            raise HTTPException(status_code=404, detail="台风不存在")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"获取台风数据失败: {exc}")
        return compute_city_impact(c, d)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)

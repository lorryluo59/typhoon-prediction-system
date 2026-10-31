/* 台风预测系统 前端逻辑 */
(() => {
  "use strict";

  const state = {
    cities: [],
    typhoons: [],
    cityId: "",
    typhoonId: null,
    colorMode: "severity",
    impact: null,
    track: null,
    impactMap: {},
    districtLayer: null,
    districtLayers: {},
    trackLayer: null,
    cityLabelLayer: null,
    selectedLayer: null,
    selectedDistrict: null,
  };

  const els = {
    citySelect: document.getElementById("citySelect"),
    typhoonSelect: document.getElementById("typhoonSelect"),
    colorMode: document.getElementById("colorMode"),
    refreshBtn: document.getElementById("refreshBtn"),
    alertBanner: document.getElementById("alertBanner"),
    alertText: document.getElementById("alertText"),
    alertMeta: document.getElementById("alertMeta"),
    errorBanner: document.getElementById("errorBanner"),
    districtCards: document.getElementById("districtCards"),
    generatedAt: document.getElementById("generatedAt"),
    panelTitle: document.getElementById("panelTitle"),
    legend: document.getElementById("legend"),
    trackInfo: document.getElementById("trackInfo"),
  };

  const map = L.map("map").setView([23.0, 115.0], 6);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 18,
  }).addTo(map);

  // ---------- helpers ----------
  const fmtTime = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getMonth() + 1}月${d.getDate()}日 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };

  function showError(msg) {
    els.errorBanner.textContent = msg;
    els.errorBanner.classList.remove("hidden");
  }
  function clearError() {
    els.errorBanner.classList.add("hidden");
  }

  async function fetchJSON(url) {
    const resp = await fetch(url);
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }
    return resp.json();
  }

  // 球面距离（km）
  function haversineKm(lng1, lat1, lng2, lat2) {
    const R = 6371.0;
    const toRad = (x) => (x * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLng = toRad(lng2 - lng1);
    const a = Math.sin(dLat / 2) ** 2 +
              Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  // 台风未来影响评分：按最新预报路径最近距离排序（未来影响）；
  // 预报缺失时用当前位置；台风中心已贴近城市（<100km）视为正在影响
  function typhoonScore(detail, city) {
    const last = detail.history[detail.history.length - 1];
    const pos = last ? haversineKm(city.center[0], city.center[1], last.lng, last.lat) : Infinity;
    if (detail.forecast && detail.forecast.length) {
      let fcMin = Infinity;
      detail.forecast.forEach(pt => {
        fcMin = Math.min(fcMin, haversineKm(city.center[0], city.center[1], pt.lng, pt.lat));
      });
      if (pos < 100) return { score: Math.min(fcMin, pos), metric: "current" };
      return { score: fcMin, metric: "future" };
    }
    return { score: pos, metric: "current" };
  }

  // 选择离当前城市未来影响最大的台风（优先活跃台风）
  async function nearestTyphoonForCity() {
    const city = state.cities.find(c => c.id === state.cityId);
    if (!city || !state.typhoons.length) return null;
    const active = state.typhoons.filter(t => t.status === "start");
    const pool = active.length ? active : state.typhoons;
    const details = await Promise.all(pool.map(t =>
      fetchJSON(`/api/typhoons/${t.id}`).then(d => d.typhoon).catch(() => null)));
    const infos = {};
    let best = null;
    details.forEach((d, i) => {
      if (!d || !d.history || !d.history.length) return;
      const s = typhoonScore(d, city);
      infos[pool[i].id] = s;
      if (!best || s.score < best.score) best = { id: pool[i].id, score: s.score };
    });
    if (!best) return null;
    return { id: best.id, infos };
  }

  // ---------- color scales ----------
  const SEV_COLORS = ["#10b981", "#65a30d", "#f59e0b", "#f97316", "#ef4444", "#b91c1c"];
  const SEV_LABELS = ["无影响", "轻度影响", "中度影响", "较强影响", "严重影响", "极严重"];
  const WIND_COLORS = ["#6b7280", "#65a30d", "#f59e0b", "#f97316", "#ef4444", "#991b1b"];
  const WIND_LABELS = ["<6级", "6-7级", "8-9级", "10-11级", "12级", "≥13级"];
  const RAIN_COLORS = ["#10b981", "#65a30d", "#f59e0b", "#f97316", "#ef4444"];
  const RAIN_LABELS = ["小到中雨", "大雨", "暴雨", "大暴雨", "特大暴雨"];

  function windIdx(bf) {
    if (bf >= 13) return 5;
    if (bf >= 12) return 4;
    if (bf >= 10) return 3;
    if (bf >= 8) return 2;
    if (bf >= 6) return 1;
    return 0;
  }
  function rainIdx(level) {
    const i = RAIN_LABELS.indexOf(level);
    return i < 0 ? 0 : i;
  }
  function colorFor(d) {
    const mode = state.colorMode;
    if (mode === "wind") return WIND_COLORS[windIdx(d.wind.beaufort)];
    if (mode === "rain") return RAIN_COLORS[rainIdx(d.rain.level)];
    return SEV_COLORS[d.severity] || "#10b981";
  }
  function legendHtml() {
    const mode = state.colorMode;
    if (mode === "wind") {
      return `<div class="l-row" style="font-weight:600">风力等级</div>` + WIND_COLORS.map((c, i) =>
        `<div class="l-row"><span class="swatch" style="background:${c}"></span>${WIND_LABELS[i]}</div>`).join("");
    }
    if (mode === "rain") {
      return `<div class="l-row" style="font-weight:600">降水等级</div>` + RAIN_COLORS.map((c, i) =>
        `<div class="l-row"><span class="swatch" style="background:${c}"></span>${RAIN_LABELS[i]}</div>`).join("");
    }
    return `<div class="l-row" style="font-weight:600">综合影响</div>` + SEV_COLORS.map((c, i) =>
      `<div class="l-row"><span class="swatch" style="background:${c}"></span>${SEV_LABELS[i]}</div>`).join("");
  }

  // ---------- typhoon track ----------
  function renderTrack(detail) {
    if (state.trackLayer) { state.trackLayer.clearLayers(); } else { state.trackLayer = L.layerGroup().addTo(map); }
    if (!detail) return;
    const hist = (detail.history || []).map(p => [p.lat, p.lng]);
    const fc = (detail.forecast || []).map(p => [p.lat, p.lng]);
    if (hist.length >= 2) {
      L.polyline(hist, { color: "#f43f5e", weight: 3 }).addTo(state.trackLayer);
    }
    if (fc.length >= 2) {
      L.polyline(fc, { color: "#f59e0b", weight: 3, dashArray: "6 6" }).addTo(state.trackLayer);
    }
    detail.history.forEach(p => {
      L.circleMarker([p.lat, p.lng], { radius: 5, color: "#fff", weight: 1.5, fillColor: "#f43f5e", fillOpacity: .9 })
        .addTo(state.trackLayer)
        .bindTooltip(`${p.grade_cn} ${p.wind_mps}m/s<br>${fmtTime(p.time)}`, { className: "citytip" });
    });
    detail.forecast.forEach(p => {
      L.circleMarker([p.lat, p.lng], { radius: 4, color: "#fff", weight: 1, fillColor: "#f59e0b", fillOpacity: .8 })
        .addTo(state.trackLayer)
        .bindTooltip(`预报 ${p.grade_cn} ${p.wind_mps}m/s<br>${fmtTime(p.time)}`, { className: "citytip" });
    });
    const last = detail.history[detail.history.length - 1];
    if (last) {
      const name = `${detail.name_cn || detail.name_en} ${detail.number}`;
      const icon = L.divIcon({
        className: "typhoon-pill-wrap",
        html: `<div class="typhoon-pill">🌀 ${name}</div>`,
        iconSize: null,
        iconAnchor: null,
      });
      L.marker([last.lat, last.lng], { icon, zIndexOffset: 1000 }).addTo(state.trackLayer);
      const lastFc = detail.forecast[detail.forecast.length - 1];
      els.trackInfo.innerHTML = `
        <div class="ti" style="font-weight:600">${detail.name_cn || detail.name_en}（${detail.number}）</div>
        <div class="ti">当前：${last.grade_cn} · ${last.wind_mps}m/s · ${fmtTime(last.time)}</div>
        ${lastFc ? `<div class="ti">预报最远：${lastFc.grade_cn} · ${fmtTime(lastFc.time)}</div>` : ""}
        <div class="ti">红实线=实况，橙虚线=官方预报</div>`;
    }
  }

  // ---------- districts ----------
  function baseStyle(name) {
    const im = state.impactMap && state.impactMap[name];
    const color = im ? colorFor(im) : "#d1d5db";
    return { color: "#ffffff", weight: 1, fillColor: color, fillOpacity: 0.5 };
  }

  function clearDistrictSelection() {
    if (state.selectedLayer) {
      const name = state.selectedLayer.feature && state.selectedLayer.feature.properties.name;
      if (name) state.selectedLayer.setStyle(baseStyle(name));
      state.selectedLayer.unbindTooltip();
      state.selectedLayer = null;
    }
    state.selectedDistrict = null;
    document.querySelectorAll(".card.active").forEach(c => c.classList.remove("active"));
  }

  function selectDistrictOnMap(name) {
    const layer = state.districtLayers[name];
    if (!layer) return;
    if (state.selectedLayer === layer) { clearDistrictSelection(); return; }
    if (state.selectedLayer) {
      const prevName = state.selectedLayer.feature && state.selectedLayer.feature.properties.name;
      if (prevName) state.selectedLayer.setStyle(baseStyle(prevName));
      state.selectedLayer.unbindTooltip();
    }
    state.selectedLayer = layer;
    state.selectedDistrict = name;
    layer.setStyle({ weight: 3, color: "#2563eb", fillOpacity: 0.8 });
    layer.bindTooltip(name, { permanent: true, direction: "center", className: "district-label" }).openTooltip();
    document.querySelectorAll(".card.active").forEach(c => c.classList.remove("active"));
    const card = document.querySelector(`.card[data-name="${CSS.escape(name)}"]`);
    if (card) card.classList.add("active");
  }

  function renderDistricts(city) {
    if (state.districtLayer) { map.removeLayer(state.districtLayer); }
    state.districtLayer = L.layerGroup().addTo(map);
    state.districtLayers = {};
    const impactMap = {};
    (state.impact && state.impact.districts || []).forEach(d => { impactMap[d.name] = d; });
    state.impactMap = impactMap;
    const features = city.districts.map(d => ({
      type: "Feature",
      properties: { name: d.name, ...d },
      geometry: d.geometry,
    }));
    L.geoJSON({ type: "FeatureCollection", features }, {
      style: (f) => baseStyle(f.properties.name),
      onEachFeature: (f, layer) => {
        const name = f.properties.name;
        state.districtLayers[name] = layer;
        const im = impactMap[name];
        const label = im ? `${name}（${im.wind.label}，${im.rain.level}）` : name;
        layer.bindTooltip(label, { className: "citytip" });
        layer.on({
          click: (e) => {
            L.DomEvent.stopPropagation(e);
            selectDistrictOnMap(name);
            const c = state.cities.find(c => c.id === state.cityId);
            const feat = c && c.districts.find(x => x.name === name);
            if (feat) map.flyTo([feat.centroid[1], feat.centroid[0]], 10);
          },
          mouseover: () => highlightCard(name, true),
          mouseout: () => highlightCard(name, false),
        });
      },
    }).addTo(state.districtLayer);
  }

  // 城市名称标签
  function renderCityLabel(city) {
    if (state.cityLabelLayer) { map.removeLayer(state.cityLabelLayer); }
    state.cityLabelLayer = L.layerGroup().addTo(map);
    const icon = L.divIcon({
      className: "city-pill-wrap",
      html: `<div class="city-pill">📍 ${city.name}</div>`,
      iconSize: null,
      iconAnchor: null,
    });
    L.marker([city.center[1], city.center[0]], { icon, zIndexOffset: 900 }).addTo(state.cityLabelLayer);
  }

  function highlightCard(name, on) {
    const card = document.querySelector(`.card[data-name="${CSS.escape(name || "")}"]`);
    if (card) card.classList.toggle("hover", on);
  }
  function scrollToCard(name) {
    const card = document.querySelector(`.card[data-name="${CSS.escape(name || "")}"]`);
    if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // ---------- cards ----------
  function renderCards(impact) {
    els.districtCards.innerHTML = "";
    impact.districts.forEach(d => {
      const sevClass = "sev" + d.severity;
      const card = document.createElement("div");
      card.className = `card ${sevClass}`;
      card.dataset.name = d.name;
      card.innerHTML = `
        <div class="card-head">
          <h3>${d.name}</h3>
          <span class="badge ${sevClass}">${SEV_LABELS[d.severity] || ""}</span>
        </div>
        <div class="dist">距台风中心约 ${d.distance_km} km（最接近 ${fmtTime(d.closest_time)}）${d.coastal ? " · 沿海" : ""}${d.flood_prone ? " · 低洼易涝" : ""}</div>
        <div class="metric">
          <div class="m"><span class="k">风力</span><span class="v">${d.wind.label}（${d.wind.mps} m/s）</span></div>
          <div class="m"><span class="k">降水量（24h估）</span><span class="v">${d.rain.level} ${d.rain.amount}</span></div>
          <div class="m"><span class="k">街道积水</span><span class="v">${d.flood.level}</span></div>
          <div class="m"><span class="k">公共交通</span><span class="v">${d.transit}</span></div>
          <div class="m"><span class="k">驾车建议</span><span class="v">${d.driving.level}（${d.driving.text}）</span></div>
        </div>`;
      card.addEventListener("click", () => {
        selectDistrictOnMap(d.name);
        const c = state.cities.find(c => c.id === state.cityId);
        const feat = c && c.districts.find(x => x.name === d.name);
        if (feat) map.flyTo([feat.centroid[1], feat.centroid[0]], 10);
        scrollToCard(d.name);
      });
      els.districtCards.appendChild(card);
    });
    els.panelTitle.textContent = `各区域影响预测（${impact.city.name}）`;
    els.generatedAt.textContent = `更新于 ${fmtTime(impact.generated_at)}`;
  }

  // ---------- alert banner ----------
  function renderAlert(alert) {
    els.alertBanner.className = "alert " + alert.level;
    const meta = [];
    if (alert.forecast_min_distance_km != null) meta.push(`预报最近约 ${alert.forecast_min_distance_km} km`);
    if (alert.landfall_time) meta.push(`预计接近时间 ${fmtTime(alert.landfall_time)}`);
    if (alert.closest_time) meta.push(`最接近 ${fmtTime(alert.closest_time)}`);
    els.alertText.textContent = `【${alert.level_text}】${alert.message}`;
    els.alertMeta.textContent = meta.join(" · ");
  }

  // ---------- data loading ----------
  async function loadImpact() {
    if (!state.cityId || !state.typhoonId) return;
    clearError();
    try {
      const [impact, detail] = await Promise.all([
        fetchJSON(`/api/impact?city=${encodeURIComponent(state.cityId)}&typhoon=${state.typhoonId}`),
        fetchJSON(`/api/typhoons/${state.typhoonId}`),
      ]);
      state.impact = impact;
      state.track = detail.typhoon;
      renderAlert(impact.alert);
      renderCards(impact);
      renderTrack(detail.typhoon);
      const city = state.cities.find(c => c.id === state.cityId);
      if (city) {
        renderDistricts(city);
        renderCityLabel(city);
        clearDistrictSelection();
        const bounds = L.latLngBounds([city.center[1], city.center[0]]);
        city.districts.forEach(d => bounds.extend([d.centroid[1], d.centroid[0]]));
        const tb = [];
        detail.typhoon.history.forEach(p => tb.push([p.lat, p.lng]));
        detail.typhoon.forecast.forEach(p => tb.push([p.lat, p.lng]));
        if (tb.length) tb.forEach(p => bounds.extend(p));
        map.fitBounds(bounds, { padding: [40, 40], maxZoom: 9 });
      }
      els.legend.innerHTML = legendHtml();
    } catch (e) {
      showError("加载预测失败：" + e.message + "（请确认后端已启动且网络可访问中央气象台）");
    }
  }

  async function loadTyphoons(preferNearest = false) {
    try {
      const data = await fetchJSON("/api/typhoons");
      state.typhoons = data.typhoons;
      const sel = els.typhoonSelect;
      sel.innerHTML = "";
      state.typhoons.forEach(t => {
        const opt = document.createElement("option");
        opt.value = t.id;
        opt.dataset.base = `${t.name_cn || t.name_en} ${t.number}${t.status === "start" ? "（活跃）" : "（已停编）"}`;
        opt.textContent = opt.dataset.base;
        sel.appendChild(opt);
      });
      if (state.typhoons.length) {
        let chosen = null;
        if (preferNearest) {
          const near = await nearestTyphoonForCity();
          if (near) {
            chosen = near.id;
            const city = state.cities.find(c => c.id === state.cityId);
            state.typhoons.forEach(t => {
              const opt = sel.querySelector(`option[value="${t.id}"]`);
              const info = near.infos[t.id];
              if (opt && info && isFinite(info.score)) {
                const label = info.metric === "future" ? "未来最近" : "当前";
                opt.textContent = `${opt.dataset.base}（距${city ? city.name : ""}·${label}约${Math.round(info.score)}km）`;
              }
            });
          }
        }
        if (chosen === null) {
          if (state.typhoonId !== null && state.typhoons.some(t => t.id === state.typhoonId)) {
            chosen = state.typhoonId;
          } else {
            const active = state.typhoons.find(t => t.status === "start") || state.typhoons[0];
            chosen = active.id;
          }
        }
        state.typhoonId = chosen;
        sel.value = String(chosen);
        await loadImpact();
      } else {
        state.typhoonId = null;
        els.alertBanner.className = "alert none";
        els.alertText.textContent = "当前无台风活动";
        els.alertMeta.textContent = "";
      }
    } catch (e) {
      showError("获取台风列表失败：" + e.message);
    }
  }

  async function init() {
    try {
      const data = await fetchJSON("/api/cities");
      state.cities = data.cities;
      const sel = els.citySelect;
      sel.innerHTML = "";
      state.cities.forEach(c => {
        const opt = document.createElement("option");
        opt.value = c.id;
        opt.textContent = c.name;
        sel.appendChild(opt);
      });
      const saved = localStorage.getItem("typhoon_city");
      state.cityId = state.cities.some(c => c.id === saved) ? saved : (state.cities[0] && state.cities[0].id);
      sel.value = state.cityId;
    } catch (e) {
      showError("获取城市数据失败：" + e.message);
    }
    await loadTyphoons(true);   // 初始默认选择未来影响最大的台风
  }

  // ---------- events ----------
  els.citySelect.addEventListener("change", () => {
    state.cityId = els.citySelect.value;
    localStorage.setItem("typhoon_city", state.cityId);
    loadTyphoons(true);   // 切换城市后默认选择未来影响最大的台风
  });
  els.typhoonSelect.addEventListener("change", () => {
    state.typhoonId = parseInt(els.typhoonSelect.value, 10);
    loadImpact();
  });
  els.colorMode.addEventListener("change", () => {
    state.colorMode = els.colorMode.value;
    const city = state.cities.find(c => c.id === state.cityId);
    if (city) { renderDistricts(city); els.legend.innerHTML = legendHtml(); }
  });
  els.refreshBtn.addEventListener("click", () => {
    loadTyphoons(false);   // 刷新保留当前手动选择
  });

  // 点击地图空白处取消分区选中
  map.on("click", () => clearDistrictSelection());

  // 自动刷新（10 分钟）
  setInterval(() => loadImpact(), 10 * 60 * 1000);
  init();
})();

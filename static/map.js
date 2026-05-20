/* =========================================================
   GREENWATER ENTERPRISE WEBGIS - ENHANCED VERSION
   map_ai.js
   NDVI • NDWI • AI FORECAST • GEE • WEATHER ANALYTICS
========================================================= */

/* =========================================================
   GLOBAL STATE
========================================================= */

let map;
let boundaryLayer;
let boundarySource;
let activeLayer = null;
let compareLayer = null;
let changeLayer = null;
let charts = {};
let currentForecastChart = null;
let currentWeatherChart = null;

let comparisonMode = false;
let currentLegendData = null;
let currentForecastData = null;
let isRunningAnalysis = false;
let isRunningForecast = false;
/* =========================================================
   OPENLAYERS MAP INIT
========================================================= */

boundarySource = new ol.source.Vector();

boundaryLayer = new ol.layer.Vector({
    source: boundarySource,
    style: new ol.style.Style({
        stroke: new ol.style.Stroke({
            color: "#ef4444",
            width: 2
        }),
        fill: new ol.style.Fill({
            color: "rgba(239,68,68,0.05)"
        })
    })
});
boundaryLayer.setZIndex(20);
map = new ol.Map({
    target: "map",
    layers: [
        new ol.layer.Tile({
            source: new ol.source.OSM()
        }),
        boundaryLayer
    ],
view: new ol.View({
    center: ol.proj.fromLonLat([106.6, 16.0]),
    zoom: 5,
    minZoom: 2,
    maxZoom: 10
})
});

map.addControl(new ol.control.ScaleLine());

/* =========================================================
   HELPERS
========================================================= */

function qs(id) {
    return document.getElementById(id);
}

function showLoading(state = true) {
    const loading = qs("loading");
    if (!loading) return;
    loading.style.display = state ? "flex" : "none";
}

function safeNumber(v, fallback = 0) {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
}

async function getJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`API Error: ${res.status}`);
    return await res.json();
}

function destroyChart(id) {
    const c = Chart.getChart(id);
    if (c) c.destroy();
}

function destroyAllCharts() {
    [
        "barChart",
        "lineChart",
        "scatterChart",
        "forecastChart",
        "weatherChart",
        "precipChart",
        "humidityChart",
        "changeChart"
    ].forEach(destroyChart);
}

function getInputs() {
    return {
        country: qs("country").value,
        province: qs("province").value,
        year: qs("year").value,
        month: qs("month").value,
        index: qs("index").value
    };
}
function apiParams(obj) {
    return new URLSearchParams(obj).toString();
}
function formatDate(dateStr) {
    const d = new Date(dateStr);
    return d.toLocaleDateString("vi-VN", { weekday: "short", month: "short", day: "numeric" });
}

/* =========================================================
   COUNTRY + PROVINCE
========================================================= */

async function loadCountries() {
    const countries = await getJSON("/countries");

    const select = qs("country");
    select.innerHTML = `
        <option value="">-- Chọn Quốc gia --</option>
    `;

    countries.forEach(c => {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        select.appendChild(opt);
    });
}

async function loadProvinces(country) {
    if (!country) return;

    const provinces = await getJSON(
        `/provinces?country=${encodeURIComponent(country)}`
    );

    const select = qs("province");
    select.innerHTML = `<option value="">-- Chọn Tỉnh / Thành phố --</option>`;

    provinces.forEach(p => {
        const opt = document.createElement("option");
        opt.value = p;
        opt.textContent = p;
        select.appendChild(opt);
    });
}

/* =========================================================
   LOAD BOUNDARY
========================================================= */

async function loadBoundary(country, province) {
    boundarySource.clear();

    if (!country || !province) return;

    const geo = await getJSON(
        `/province_boundary?country=${encodeURIComponent(country)}&province=${encodeURIComponent(province)}`
    );

    const features = new ol.format.GeoJSON().readFeatures(geo, {
        dataProjection: "EPSG:4326",
        featureProjection: "EPSG:3857"
    });

    boundarySource.addFeatures(features);

    if (features.length > 0) {
map.getView().fit(
    boundarySource.getExtent(),
    {
        duration: 800,
        padding: [60, 60, 60, 60],
        maxZoom: 8
    }
);
    }
}

/* =========================================================
   MAP TILE
========================================================= */

let lastMapTileKey = "";

async function loadMapTile(params) {
    const tileKey = JSON.stringify(params);

    // Nếu đang gọi lại đúng lớp cũ thì không tải lại tile GEE nữa
    if (tileKey === lastMapTileKey && activeLayer) {
        updateLayerControl(params.index, "Google Earth Engine - cached", "-");
        return;
    }

    lastMapTileKey = tileKey;

    const data = await getJSON(
        `/map?${apiParams(params)}`
    );

    if (!data || !data.tile) {
        alert(data.message || "Không có dữ liệu vệ tinh.");
        return;
    }

    if (activeLayer) {
        map.removeLayer(activeLayer);

        const oldSource = activeLayer.getSource();
        if (oldSource && oldSource.clear) {
            oldSource.clear();
        }

        activeLayer = null;
    }

const geeSource = new ol.source.XYZ({
    url: data.tile,
    crossOrigin: "anonymous",
    wrapX: false,
    transition: 0,
    maxZoom: 10,
    cacheSize: 128
});

activeLayer = new ol.layer.Tile({
    source: geeSource,
    opacity: 0.86,
    visible: true
});

activeLayer.setZIndex(5);
boundaryLayer.setZIndex(20);

    map.addLayer(activeLayer);
if (map.getView().getZoom() > 8) {
    map.getView().setZoom(8);
}
    updateLayerControl(params.index, data.source, data.image_count);
}
/* =========================================================
   LEGEND
========================================================= */

function renderLegend(legend) {
    const el = qs("legend");

    if (!legend || !legend.items) {
        el.innerHTML = "Không có legend.";
        return;
    }

    el.innerHTML = `
        <div style="display:grid;gap:10px;">
            ${
                legend.items.map(i => `
                    <div style="
                        display:flex;
                        align-items:center;
                        gap:10px;
                        font-size:13px;
                        color:white;
                    ">
                        <span style="
                            width:18px;
                            height:18px;
                            border-radius:6px;
                            background:${i.color};
                            display:inline-block;
                        "></span>
                        <span>${i.label}</span>
                        <span style="margin-left:auto;color:#94a3b8;">
                            ${i.value !== null ? Number(i.value).toFixed(3) : "-"}
                        </span>
                    </div>
                `).join("")
            }
        </div>
    `;
}

/* =========================================================
   STATS
========================================================= */

function renderStats(bundle) {
    const stats = bundle.stats || {};
    const summary = bundle.summary || {};

    qs("stats").innerHTML = `
        <div style="display:grid;gap:8px;">
            <div><b>Mean:</b> ${safeNumber(stats.mean).toFixed(3)}</div>
            <div><b>Min:</b> ${safeNumber(stats.min).toFixed(3)}</div>
            <div><b>Max:</b> ${safeNumber(stats.max).toFixed(3)}</div>
            <div><b>StdDev:</b> ${safeNumber(stats.stdDev).toFixed(3)}</div>
            <div style="
                margin-top:10px;
                padding:10px;
                border-radius:12px;
                background:rgba(56,189,248,0.08);
                border:1px solid rgba(56,189,248,0.15);
            ">
                ${summary.text || "Không có mô tả"}
            </div>
        </div>
    `;
}

/* =========================================================
   ANALYSIS TABLE
========================================================= */

function renderAnalysisTable(bundle) {
    const stats = bundle.stats || {};
    const corr = bundle.correlation || {};

    qs("analysisTable").innerHTML = `
        <h3>Bảng thuộc tính <span>Real Data</span></h3>
        <div style="display:grid;gap:12px;">
            <div class="mini-card">
                <div class="mini-title">Chỉ số trung bình</div>
                <div class="mini-value">${safeNumber(stats.mean).toFixed(3)}</div>
            </div>
            <div class="mini-card">
                <div class="mini-title">Tương quan NDVI - NDWI</div>
                <div class="mini-value">${safeNumber(corr.ndvi_ndwi).toFixed(2)}</div>
            </div>
            <div class="mini-card">
                <div class="mini-title">Tương quan NDVI - EVI</div>
                <div class="mini-value">${safeNumber(corr.ndvi_evi).toFixed(2)}</div>
            </div>
            <div class="mini-card">
                <div class="mini-title">Độ lệch chuẩn</div>
                <div class="mini-value">${safeNumber(stats.stdDev).toFixed(3)}</div>
            </div>
        </div>
    `;
}

/* =========================================================
   CHARTS
========================================================= */

function renderCharts(bundle) {
    destroyAllCharts();

    const stats = bundle.stats || {};
    const ts = bundle.timeseries || [];
    const scatter = bundle.scatter || [];

    /* BAR */
    new Chart(
        qs("barChart"),
        {
            type: "bar",
            data: {
                labels: ["Min", "Mean", "Max"],
                datasets: [{
                    label: bundle.index,
                    data: [
                        safeNumber(stats.min),
                        safeNumber(stats.mean),
                        safeNumber(stats.max)
                    ],
                    backgroundColor: ["#ef4444", "#facc15", "#22c55e"]
                }]
            },
            options: {
                responsive: true,
                animation: false,
                plugins: {
                    legend: { labels: { color: "white" } }
                }
            }
        }
    );

    /* LINE */
    new Chart(
        qs("lineChart"),
        {
            type: "line",
            data: {
                labels: ts.map(x => x.year),
                datasets: [{
                    label: "Biến động thời gian",
                    data: ts.map(x => x.value),
                    fill: true,
                    tension: 0.3,
                    borderColor: "#3b82f6",
                    backgroundColor: "rgba(59, 130, 246, 0.1)"
                }]
            },
            options: {
                responsive: true,
                animation: false,
                plugins: {
                    legend: { labels: { color: "white" } }
                }
            }
        }
    );

    /* SCATTER */
    new Chart(
        qs("scatterChart"),
        {
            type: "scatter",
            data: {
                datasets: [{
                    label: "NDVI vs EVI",
                    data: scatter,
                    backgroundColor: "rgba(139, 92, 246, 0.6)"
                }]
            },
            options: {
                responsive: true,
                animation: false,
                plugins: {
                    legend: { labels: { color: "white" } }
                }
            }
        }
    );
}

/* =========================================================
   ENHANCED WEATHER FORECAST
========================================================= */

async function loadForecast() {
    if (isRunningForecast) return;

    const {
        country,
        province,
        year,
        month
    } = getInputs();

    if (!country || !province) {
        alert("Chọn quốc gia và tỉnh trước.");
        return;
    }

    isRunningForecast = true;

    showLoading(true);

    try {
        const data = await getJSON(
            `/arima_ndvi_ndwi_7days?${apiParams({
                country,
                province,
                year,
                month
            })}`
        );

        if (data.error) {
            alert(data.error + "\n" + (data.detail || ""));
            return;
        }

        currentForecastData = data;
        renderNdviNdwiForecast(data);
        switchTab("forecast");

    } catch (e) {
        console.error(e);
        alert("Lỗi AI dự báo NDVI/NDWI: " + e.message);
    } finally {
        isRunningForecast = false;
        showLoading(false);
    }
}

function renderEnhancedForecast(data) {
    if (!data || !data.forecast) return;

    destroyAllCharts();

    const forecast = data.forecast || [];
    const insight = data.insight || {};
if (qs("forecastMeta")) {
    qs("forecastMeta").innerHTML = `
        <b>Nguồn:</b> ${data.source?.provider || "Weather API"}<br>
        <b>Loại dữ liệu:</b> ${data.source?.type || "forecast"}<br>
        <b>Truy xuất:</b> ${data.source?.retrieved_at || "-"}<br>
        <small>${data.source?.model_note || ""}</small>
    `;
}
    /* Temperature Chart */
    new Chart(
        qs("forecastChart"),
        {
            type: "line",
            data: {
                labels: forecast.map(x => formatDate(x.date)),
                datasets: [
                    {
                        label: "Nhiệt độ cao",
                        data: forecast.map(x => x.tmax),
                        borderColor: "#dc2626",
                        backgroundColor: "rgba(220, 38, 38, 0.1)",
                        tension: 0.3,
                        fill: false
                    },
                    {
                        label: "Nhiệt độ thấp",
                        data: forecast.map(x => x.tmin),
                        borderColor: "#0ea5e9",
                        backgroundColor: "rgba(14, 165, 233, 0.1)",
                        tension: 0.3,
                        fill: false
                    },
                    {
                        label: "Nhiệt độ trung bình",
                        data: forecast.map(x => x.tmean),
                        borderColor: "#facc15",
                        borderWidth: 2,
                        tension: 0.3,
                        fill: false
                    }
                ]
            },
            options: {
                responsive: true,
                animation: false,
                plugins: {
                    legend: { labels: { color: "white" } },
                    title: { display: true, text: "Dự báo nhiệt độ 7 ngày", color: "white" }
                },
                scales: {
                    y: { ticks: { color: "white" }, grid: { color: "rgba(255,255,255,0.1)" } },
                    x: { ticks: { color: "white" }, grid: { color: "rgba(255,255,255,0.1)" } }
                }
            }
        }
    );

    /* Precipitation Chart */
    new Chart(
        qs("precipChart"),
        {
            type: "bar",
            data: {
                labels: forecast.map(x => formatDate(x.date)),
datasets: [
    {
        label: "Lượng mưa (mm)",
        data: forecast.map(x => x.precip),
        backgroundColor: "rgba(59, 130, 246, 0.8)"
    },
    {
        label: "Xác suất mưa (%)",
        data: forecast.map(x => x.precip_probability || 0),
        backgroundColor: "rgba(14, 165, 233, 0.35)"
    }
]
            },
            options: {
                responsive: true,
                animation: false,
                plugins: {
                    legend: { labels: { color: "white" } },
                    title: { display: true, text: "Dự báo lượng mưa", color: "white" }
                },
                scales: {
                    y: { ticks: { color: "white" }, grid: { color: "rgba(255,255,255,0.1)" } },
                    x: { ticks: { color: "white" }, grid: { color: "rgba(255,255,255,0.1)" } }
                }
            }
        }
    );

    /* Humidity Chart */
    new Chart(
        qs("humidityChart"),
        {
            type: "line",
            data: {
                labels: forecast.map(x => formatDate(x.date)),
                datasets: [{
                    label: "Độ ẩm (%)",
                    data: forecast.map(x => x.humidity),
                    borderColor: "#10b981",
                    backgroundColor: "rgba(16, 185, 129, 0.1)",
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                animation: false,
                plugins: {
                    legend: { labels: { color: "white" } },
                    title: { display: true, text: "Dự báo độ ẩm", color: "white" }
                },
                scales: {
                    y: {
                        min: 0,
                        max: 100,
                        ticks: { color: "white" },
                        grid: { color: "rgba(255,255,255,0.1)" }
                    },
                    x: { ticks: { color: "white" }, grid: { color: "rgba(255,255,255,0.1)" } }
                }
            }
        }
    );

    /* Forecast Table */
    const forecastHTML = `
        <div class="forecast-container">
            <h3 style="color: white; margin-bottom: 15px;">Dự báo 7 ngày chi tiết</h3>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 20px;">
                ${forecast.map(day => `
                    <div class="forecast-card">
                        <div class="forecast-date">${formatDate(day.date)}</div>
                        <div class="forecast-temp">
                            <span class="temp-high">${day.tmax}°</span>
                            <span class="temp-low">${day.tmin}°</span>
                        </div>
                        <div class="forecast-info">
                        <div>🌧️ ${day.precip}mm • ${day.precip_probability || 0}%</div>
                        <div>💨 ${day.windspeed}km/h</div>
                        <div>💧 ${day.humidity}%</div>
                        <div>☀️ UV ${day.uv_index ?? "-"}</div>
                        </div>
                    </div>
                `).join("")}
            </div>
        </div>
    `;

    /* AI Insight */
    const insightHTML = `
        <div class="ai-insight-container">
            <h3 style="color: white;">🤖 Phân tích AI</h3>
            
            <div class="insight-card">
                <div class="insight-headline">${insight.headline || "-"}</div>
                <div class="insight-summary">${insight.summary || "-"}</div>
                <div class="insight-confidence">
                    Độ tin cậy: <strong>${insight.confidence || 0}%</strong>
                </div>
            </div>

            ${insight.metrics ? `
                <div class="metrics-grid">
                    <div class="metric-item">
                        <span>Phạm vi nhiệt độ:</span>
                        <strong>${insight.metrics.temp_range}</strong>
                    </div>
                    <div class="metric-item">
                        <span>Tổng lượng mưa:</span>
                        <strong>${insight.metrics.total_precip}</strong>
                    </div>
                    <div class="metric-item">
                        <span>Độ ẩm trung bình:</span>
                        <strong>${insight.metrics.avg_humidity}</strong>
                    </div>
                </div>
            ` : ""}

            ${insight.warnings && insight.warnings.length > 0 ? `
                <div class="warnings-section">
                    <h4>⚠️ Cảnh báo</h4>
                    ${insight.warnings.map(w => `<div class="warning-item">• ${w}</div>`).join("")}
                </div>
            ` : ""}
        </div>
    `;

    qs("forecastTable").innerHTML = forecastHTML + insightHTML;
}
function renderNdviNdwiForecast(data) {
    if (!data || !data.forecast) return;

    destroyAllCharts();

    const forecast = data.forecast || [];
    const insight = data.insight || {};
    const p = data.analysis_period || {};
    const model = data.model_info || {};
    const last = data.last_observed || {};

    if (qs("forecastMeta")) {
        qs("forecastMeta").innerHTML = `
            <b>Mô hình:</b> ${model.model || "ARIMA / SARIMA"}<br>
            <b>Thuật toán:</b> ${model.algorithm || "-"}<br>
            <b>Dữ liệu:</b> ${model.data_source || "-"}<br>
            <b>Database:</b> ${model.database || "No database"}<br>
            <b>Kỳ phân tích:</b> ${p.month || "-"} / ${p.year || "-"}<br>
            <b>Dự báo:</b> ${p.start_7days || "-"} → ${p.end_7days || "-"}<br>
            <b>NDVI gần nhất:</b> ${safeNumber(last.ndvi).toFixed(4)} •
            <b>NDWI gần nhất:</b> ${safeNumber(last.ndwi).toFixed(4)}<br>
            <small>${data.source?.note || ""}</small>
        `;
    }

    new Chart(qs("forecastChart"), {
        type: "line",
        data: {
            labels: forecast.map(x => formatDate(x.date)),
            datasets: [
                {
                    label: "NDVI dự báo",
                    data: forecast.map(x => x.ndvi_pred),
                    borderColor: "#22c55e",
                    backgroundColor: "rgba(34,197,94,0.12)",
                    tension: 0.35,
                    fill: true
                },
                {
                    label: "NDWI dự báo",
                    data: forecast.map(x => x.ndwi_pred),
                    borderColor: "#0ea5e9",
                    backgroundColor: "rgba(14,165,233,0.12)",
                    tension: 0.35,
                    fill: true
                }
            ]
        },
        options: {
            responsive: true,
            animation: false,
            plugins: {
                legend: { labels: { color: "white" } },
                title: {
                    display: true,
                    text: "AI ARIMA dự báo NDVI / NDWI 7 ngày",
                    color: "white"
                }
            },
            scales: {
                y: {
                    min: -1,
                    max: 1,
                    ticks: { color: "white" },
                    grid: { color: "rgba(255,255,255,0.1)" }
                },
                x: {
                    ticks: { color: "white" },
                    grid: { color: "rgba(255,255,255,0.1)" }
                }
            }
        }
    });

    new Chart(qs("precipChart"), {
        type: "bar",
        data: {
            labels: forecast.map(x => formatDate(x.date)),
            datasets: [
                {
                    label: "ΔNDVI",
                    data: forecast.map(x => x.ndvi_change),
                    backgroundColor: "rgba(34,197,94,0.75)"
                },
                {
                    label: "ΔNDWI",
                    data: forecast.map(x => x.ndwi_change),
                    backgroundColor: "rgba(14,165,233,0.75)"
                }
            ]
        },
        options: {
            responsive: true,
            animation: false,
            plugins: {
                legend: { labels: { color: "white" } },
                title: {
                    display: true,
                    text: "Biến động dự báo so với giá trị gần nhất",
                    color: "white"
                }
            },
            scales: {
                y: {
                    ticks: { color: "white" },
                    grid: { color: "rgba(255,255,255,0.1)" }
                },
                x: {
                    ticks: { color: "white" },
                    grid: { color: "rgba(255,255,255,0.1)" }
                }
            }
        }
    });

    new Chart(qs("humidityChart"), {
        type: "line",
        data: {
            labels: forecast.map(x => formatDate(x.date)),
            datasets: [
                {
                    label: "NDVI",
                    data: forecast.map(x => x.ndvi_pred),
                    borderColor: "#16a34a",
                    tension: 0.3
                },
                {
                    label: "NDWI",
                    data: forecast.map(x => x.ndwi_pred),
                    borderColor: "#2563eb",
                    tension: 0.3
                }
            ]
        },
        options: {
            responsive: true,
            animation: false,
            plugins: {
                legend: { labels: { color: "white" } },
                title: {
                    display: true,
                    text: "Xu hướng chỉ số dự báo",
                    color: "white"
                }
            },
            scales: {
                y: {
                    min: -1,
                    max: 1,
                    ticks: { color: "white" },
                    grid: { color: "rgba(255,255,255,0.1)" }
                },
                x: {
                    ticks: { color: "white" },
                    grid: { color: "rgba(255,255,255,0.1)" }
                }
            }
        }
    });

    const metrics = insight.metrics || {};

    qs("forecastTable").innerHTML = `
        <div class="forecast-container">
            <h3 style="color:white;margin-bottom:15px;">AI dự báo NDVI / NDWI 7 ngày</h3>

            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:20px;">
                ${forecast.map(day => `
                    <div class="forecast-card">
                        <div class="forecast-date">${formatDate(day.date)}</div>
                        <div class="forecast-info">
                            <div>🌿 NDVI: <b>${safeNumber(day.ndvi_pred).toFixed(4)}</b></div>
                            <div>💧 NDWI: <b>${safeNumber(day.ndwi_pred).toFixed(4)}</b></div>
                            <div>📈 ΔNDVI: <b>${safeNumber(day.ndvi_change).toFixed(4)}</b></div>
                            <div>📉 ΔNDWI: <b>${safeNumber(day.ndwi_change).toFixed(4)}</b></div>
                            <div>📅 ${day.date}</div>
                        </div>
                    </div>
                `).join("")}
            </div>
        </div>

        <div class="ai-insight-container">
            <h3 style="color:white;">🤖 AI Insight NDVI / NDWI</h3>

            <div class="insight-card">
                <div class="insight-headline">${insight.headline || "-"}</div>
                <div class="insight-summary">${insight.summary || "-"}</div>
                <div class="insight-confidence">
                    Độ tin cậy mô hình: <strong>${insight.confidence || 0}%</strong>
                </div>
            </div>

            <div class="metrics-grid">
                <div class="metric-item">
                    <span>NDVI trung bình</span>
                    <strong>${metrics.avg_ndvi ?? "-"}</strong>
                </div>

                <div class="metric-item">
                    <span>NDVI min / max</span>
                    <strong>${metrics.min_ndvi ?? "-"} / ${metrics.max_ndvi ?? "-"}</strong>
                </div>

                <div class="metric-item">
                    <span>NDWI trung bình</span>
                    <strong>${metrics.avg_ndwi ?? "-"}</strong>
                </div>

                <div class="metric-item">
                    <span>NDWI min / max</span>
                    <strong>${metrics.min_ndwi ?? "-"} / ${metrics.max_ndwi ?? "-"}</strong>
                </div>

                <div class="metric-item">
                    <span>ΔNDVI</span>
                    <strong>${metrics.ndvi_delta ?? "-"}</strong>
                </div>

                <div class="metric-item">
                    <span>ΔNDWI</span>
                    <strong>${metrics.ndwi_delta ?? "-"}</strong>
                </div>
            </div>

            ${insight.warnings && insight.warnings.length > 0 ? `
                <div class="warnings-section">
                    <h4>⚠️ Cảnh báo</h4>
                    ${insight.warnings.map(w => `<div class="warning-item">• ${w}</div>`).join("")}
                </div>
            ` : ""}

            <div class="source-box" style="margin-top:15px;">
                ${insight.note || ""}
            </div>
        </div>
    `;
}
/* =========================================================
   RUN ANALYSIS
========================================================= */

async function runAnalysis() {
    if (isRunningAnalysis) return;

    const params = getInputs();

    if (!params.country) {
        alert("Chọn quốc gia.");
        return;
    }

    if (!params.province) {
        alert("Chọn tỉnh.");
        return;
    }

    isRunningAnalysis = true;

    showLoading(true);

    try {
        await loadBoundary(params.country, params.province);
        await loadMapTile(params);
        await new Promise(r => setTimeout(r, 300));

        const bundle = await getJSON(
            `/analysis_bundle?country=${encodeURIComponent(params.country)}&province=${encodeURIComponent(params.province)}&year=${encodeURIComponent(params.year)}&month=${encodeURIComponent(params.month)}&index=${encodeURIComponent(params.index)}`
        );

        if (bundle.error) {
            alert(bundle.error);
            showLoading(false);
            return;
        }

        renderStats(bundle);
        renderAnalysisTable(bundle);
        renderCharts(bundle);

        if (bundle.legend) {
            currentLegendData = bundle.legend;
            renderLegend(bundle.legend);
        }

// await loadForecast();

    } catch (e) {
        console.error(e);
        alert("Lỗi khi phân tích dữ liệu: " + e.message);
    } finally {
        isRunningAnalysis = false;
        showLoading(false);
    }
}
/* =========================================================
   NDVI / NDWI CHANGE MONITORING
========================================================= */

async function runChangeMonitor() {
    const base = getInputs();

    if (!base.country || !base.province) {
        alert("Chọn quốc gia và tỉnh trước.");
        return;
    }

    const params = {
        country: base.country,
        province: base.province,
        index: base.index,
        y1: qs("year1").value,
        m1: qs("month1").value,
        y2: qs("year2").value,
        m2: qs("month2").value
    };

    showLoading(true);

    try {
        await loadBoundary(base.country, base.province);

        const data = await getJSON(
            `/change_monitor?${apiParams(params)}`
        );

        if (data.error) {
            alert(data.error);
            return;
        }

        if (changeLayer) {
            map.removeLayer(changeLayer);
        }

        changeLayer = new ol.layer.Tile({
            source: new ol.source.XYZ({
                url: data.tile,
                crossOrigin: "anonymous"
            }),
            opacity: 0.82
        });

        map.addLayer(changeLayer);

        if (qs("changeSummary")) {
            qs("changeSummary").innerHTML = `
                <h3 style="color:white;">🛰️ AI Change Monitoring</h3>

                <div class="insight-card">
                    <div class="insight-headline">${data.ai.status}</div>
                    <div class="insight-summary">${data.ai.summary}</div>
                    <div class="insight-confidence">
                        Rủi ro: <strong>${data.ai.risk}</strong>
                    </div>
                </div>

                <div class="metrics-grid">
                    <div class="metric-item">
                        <span>Mean Δ</span>
                        <strong>${safeNumber(data.stats.mean_delta).toFixed(4)}</strong>
                    </div>
                    <div class="metric-item">
                        <span>Min Δ</span>
                        <strong>${safeNumber(data.stats.min_delta).toFixed(4)}</strong>
                    </div>
                    <div class="metric-item">
                        <span>Max Δ</span>
                        <strong>${safeNumber(data.stats.max_delta).toFixed(4)}</strong>
                    </div>
                    <div class="metric-item">
                        <span>StdDev</span>
                        <strong>${safeNumber(data.stats.stdDev).toFixed(4)}</strong>
                    </div>
                </div>

                <div class="source-box">
                    <b>Nguồn:</b> ${data.source}<br>
                    <b>Kỳ:</b> ${data.period_1} → ${data.period_2}<br>
                    <b>Số ảnh:</b> ${data.image_count.period_1} / ${data.image_count.period_2}<br>
                    <b>Generated:</b> ${data.generated_at}
                </div>
            `;
        }

        destroyChart("changeChart");

        new Chart(
            qs("changeChart"),
            {
                type: "bar",
                data: {
                    labels: ["Mean Δ", "Min Δ", "Max Δ", "StdDev"],
                    datasets: [{
                        label: `Biến động ${data.index}`,
                        data: [
                            data.stats.mean_delta,
                            data.stats.min_delta,
                            data.stats.max_delta,
                            data.stats.stdDev
                        ],
                        backgroundColor: [
                            "rgba(59,130,246,0.8)",
                            "rgba(239,68,68,0.8)",
                            "rgba(34,197,94,0.8)",
                            "rgba(250,204,21,0.8)"
                        ]
                    }]
                },
                options: {
                    responsive: true,
                    animation: false,
                    plugins: {
                        legend: { labels: { color: "white" } },
                        title: {
                            display: true,
                            text: "Giám sát biến động NDVI / NDWI",
                            color: "white"
                        }
                    },
                    scales: {
                        y: {
                            ticks: { color: "white" },
                            grid: { color: "rgba(255,255,255,0.1)" }
                        },
                        x: {
                            ticks: { color: "white" },
                            grid: { color: "rgba(255,255,255,0.1)" }
                        }
                    }
                }
            }
        );

        if (data.legend) {
            renderLegend(data.legend);
        }

        switchTab("change");
        updateLayerControl(
            `${data.index} Change`,
            data.source,
            `${data.image_count.period_1}/${data.image_count.period_2}`
        );

    } catch (e) {
        console.error(e);
        alert("Lỗi giám sát biến động: " + e.message);
    } finally {
        showLoading(false);
    }
}
/* =========================================================
   EXPORT
========================================================= */

async function exportImage() {
    const params = getInputs();

    const data = await getJSON(
        `/export?country=${encodeURIComponent(params.country)}&province=${encodeURIComponent(params.province)}&year=${encodeURIComponent(params.year)}&month=${encodeURIComponent(params.month)}&index=${encodeURIComponent(params.index)}`
    );

    if (data.url) {
        window.open(data.url, "_blank");
    } else {
        alert("Không export được.");
    }
}

/* =========================================================
   IMAGE LIST
========================================================= */

async function searchImages() {
    const params = getInputs();

    const list = await getJSON(
        `/images?country=${encodeURIComponent(params.country)}&province=${encodeURIComponent(params.province)}&year=${encodeURIComponent(params.year)}&month=${encodeURIComponent(params.month)}`
    );

    const select = qs("imageList");
    select.innerHTML = "";

    list.forEach(id => {
        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = id;
        select.appendChild(opt);
    });
}

/* =========================================================
   PIXEL INSPECT
========================================================= */

map.on("click", async function(evt) {
    const coord = ol.proj.toLonLat(evt.coordinate);
    const lon = coord[0];
    const lat = coord[1];
    const params = getInputs();

    const data = await getJSON(
        `/pixel?lat=${lat}&lon=${lon}&year=${encodeURIComponent(params.year)}&month=${encodeURIComponent(params.month)}&index=${encodeURIComponent(params.index)}`
    );

    const val = Object.values(data)[0];

    if (val === undefined) return;

    qs("stats").innerHTML += `
        <div style="
            margin-top:12px;
            padding:10px;
            border-radius:12px;
            background:rgba(34,197,94,0.08);
            border:1px solid rgba(34,197,94,0.2);
        ">
            <b>Pixel:</b> ${Number(val).toFixed(3)}
        </div>
    `;
});

/* =========================================================
   LAYER CONTROL
========================================================= */

function updateLayerControl(index, source = "Real data", imageCount = "-") {
    const el = qs("layerControl");

    el.innerHTML = `
        <div class="layer-control-title">Quản lý lớp</div>

        <div style="display:grid;gap:8px;">
            <div><b>Lớp:</b> ${index}</div>
            <div><b>Nguồn:</b> ${source}</div>
            <div><b>Số ảnh:</b> ${imageCount}</div>

            <button class="btn btn-outline" onclick="toggleLayerVisibility()">
                Hiện / Ẩn lớp
            </button>
        </div>
    `;
}

function toggleLayerVisibility() {
    if (activeLayer) {
        activeLayer.setVisible(!activeLayer.getVisible());
    }

    if (changeLayer) {
        changeLayer.setVisible(!changeLayer.getVisible());
    }
}

/* =========================================================
   DATE SELECTS INIT
========================================================= */

(function initDateSelects() {
    const currentYear = new Date().getFullYear();
    const currentMonth = new Date().getMonth() + 1;

    const yearIds = ["year", "year1", "year2"];
    const monthIds = ["month", "month1", "month2"];

    yearIds.forEach(id => {
        const el = qs(id);
        if (!el) return;

        el.innerHTML = "";

        for (let y = 2016; y <= currentYear; y++) {
            const opt = document.createElement("option");
            opt.value = y;
            opt.textContent = y;

            if (y === currentYear) {
                opt.selected = true;
            }

            el.appendChild(opt);
        }
    });

    monthIds.forEach(id => {
        const el = qs(id);
        if (!el) return;

        el.innerHTML = "";

        for (let m = 1; m <= 12; m++) {
            const opt = document.createElement("option");
            opt.value = m;
            opt.textContent = "Tháng " + m;

            if (m === currentMonth) {
                opt.selected = true;
            }

            el.appendChild(opt);
        }
    });

    if (qs("month1")) {
        qs("month1").value = String(Math.max(1, currentMonth - 1));
    }

    if (qs("year1")) {
        qs("year1").value = String(currentYear);
    }

    if (qs("year2")) {
        qs("year2").value = String(currentYear);
    }

    if (qs("month2")) {
        qs("month2").value = String(currentMonth);
    }
})();
/* =========================================================
   EVENTS
========================================================= */

qs("country").addEventListener(
    "change",
    async function() {
        await loadProvinces(this.value);
        boundarySource.clear();
    }
);

qs("province").addEventListener(
    "change",
    async function() {
        const country = qs("country").value;
        if (!country || !this.value) return;
        await loadBoundary(country, this.value);
    }
);
/* =========================================================
   MAP FOCUS / DASHBOARD TOGGLE
========================================================= */

function toggleDashboard() {
    document.body.classList.toggle("map-focus");

    const btn = qs("dashboardToggleBtn");

    if (document.body.classList.contains("map-focus")) {
        if (btn) btn.textContent = "📊 Hiện Dashboard";
    } else {
        if (btn) btn.textContent = "🗺️ Ẩn Dashboard / Xem Map";
    }

    setTimeout(() => {
        if (map) {
            map.updateSize();
        }
    }, 250);
}

function resetMapView() {
    if (!map) return;

    if (boundarySource && boundarySource.getFeatures().length > 0) {
        map.getView().fit(
            boundarySource.getExtent(),
            {
                duration: 700,
                padding: [60, 60, 60, 60]
            }
        );
    } else {
        map.getView().animate({
            center: ol.proj.fromLonLat([106.6, 16.0]),
            zoom: 5,
            duration: 700
        });
    }

    setTimeout(() => {
        map.updateSize();
    }, 250);
}
/* =========================================================
   STARTUP
========================================================= */

loadCountries();
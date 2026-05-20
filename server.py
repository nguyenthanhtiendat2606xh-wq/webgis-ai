import os
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

import requests
import numpy as np
import json

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.preprocessing import StandardScaler
except Exception:
    RandomForestRegressor = None
    StandardScaler = None
import warnings
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX
from sklearn.metrics import mean_absolute_error, mean_squared_error
import ee
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
# ============================================
# PERFORMANCE CACHE
# ============================================

HTTP = requests.Session()
CACHE = {}
CACHE_TTL_SECONDS = 60 * 30  # 30 phút

def cache_get(key):
    item = CACHE.get(key)
    if not item:
        return None

    value, expires_at = item
    if time.time() > expires_at:
        CACHE.pop(key, None)
        return None

    return value

def cache_set(key, value, ttl=CACHE_TTL_SECONDS):
    CACHE[key] = (value, time.time() + ttl)
    return value

def json_cache(ttl=CACHE_TTL_SECONDS):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = request.path + "?" + "&".join(
                f"{k}={v}" for k, v in sorted(request.args.items())
            )
            cached = cache_get(key)
            if cached is not None:
                return jsonify(cached)
            resp = fn(*args, **kwargs)
            if isinstance(resp, tuple):
                return resp
            try:
                data = resp.get_json()
                cache_set(key, data, ttl)
            except Exception:
                pass
            return resp
        return wrapper
    return deco
# ============================================
# GOOGLE EARTH ENGINE
# ============================================

service_account = os.getenv(
    "GEE_SERVICE_ACCOUNT",
    "tiendat123-65@centered-inn-471103-g0.iam.gserviceaccount.com"
)
import tempfile

private_key_json = os.getenv("GEE_PRIVATE_KEY_JSON")

if private_key_json:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        f.write(private_key_json)
        private_key_path = f.name
else:
    private_key_path = os.getenv("GEE_PRIVATE_KEY_PATH", "private-key.json")

credentials = ee.ServiceAccountCredentials(
    service_account,
    private_key_path
)

ee.Initialize(credentials)
# ============================================
# DATA
# ============================================

admin0 = ee.FeatureCollection("FAO/GAUL/2015/level0")
admin1 = ee.FeatureCollection("FAO/GAUL/2015/level1")

# ============================================
# CLOUD MASK
# ============================================

def maskLandsat(image):
    qa = image.select("QA_PIXEL")
    cloud = qa.bitwiseAnd(1 << 3).eq(0)
    shadow = qa.bitwiseAnd(1 << 4).eq(0)
    cirrus = qa.bitwiseAnd(1 << 2).eq(0)
    mask = cloud.And(shadow).And(cirrus)
    optical = image.select("SR_B.*")\
        .multiply(0.0000275)\
        .add(-0.2)
    return optical.updateMask(mask)

def calc_index(img, index):
    bands = img.bandNames().getInfo()
    if "SR_B5" not in bands:
        return ee.Image.constant(0).rename(index)

    if index == "NDVI":
        return img.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI")
    elif index == "NDWI":
        return img.normalizedDifference(["SR_B3", "SR_B5"]).rename("NDWI")
    elif index == "NBR":
        return img.normalizedDifference(["SR_B5", "SR_B7"]).rename("NBR")
    elif index == "NDMI":
        return img.normalizedDifference(["SR_B5", "SR_B6"]).rename("NDMI")
    elif index == "EVI":
        return img.expression(
            '2.5*((NIR-RED)/(NIR+6*RED-7.5*BLUE+1))',
            {
                'NIR': img.select("SR_B5"),
                'RED': img.select("SR_B4"),
                'BLUE': img.select("SR_B2")
            }
        ).rename("EVI")
    elif index == "SAVI":
        return img.expression(
            '((NIR-RED)/(NIR+RED+0.5))*1.5',
            {
                'NIR': img.select("SR_B5"),
                'RED': img.select("SR_B4")
            }
        ).rename("SAVI")

    return img.normalizedDifference(["SR_B5","SR_B4"]).rename("NDVI")

def get_vis(index):
    palettes = {
        "NDVI": {
            "min": -0.2,
            "max": 0.9,
            "palette": ["#7f1d1d", "#f59e0b", "#fef3c7", "#86efac", "#16a34a", "#065f46"]
        },
        "NDWI": {
            "min": -0.6,
            "max": 0.6,
            "palette": ["#92400e", "#fef3c7", "#bae6fd", "#0ea5e9", "#1d4ed8"]
        },
        "EVI": {
            "min": -0.2,
            "max": 0.8,
            "palette": ["#581c87", "#f5f3ff", "#a7f3d0", "#059669"]
        },
        "NBR": {
            "min": -0.8,
            "max": 0.8,
            "palette": ["#7f1d1d", "#f97316", "#fefce8", "#22c55e"]
        },
        "NDMI": {
            "min": -0.6,
            "max": 0.6,
            "palette": ["#7c2d12", "#fef3c7", "#67e8f9", "#0891b2"]
        },
        "SAVI": {
            "min": -0.2,
            "max": 0.9,
            "palette": ["#78350f", "#fde68a", "#bbf7d0", "#15803d"]
        }
    }
    return palettes.get(index, palettes["NDVI"])

def get_roi_centroid(country, province_text):
    provinces = [p.strip() for p in province_text.split(",") if p.strip()]
    fc = admin1.filter(
        ee.Filter.And(
            ee.Filter.eq("ADM0_NAME", country),
            ee.Filter.inList("ADM1_NAME", provinces)
        )
    )
    coords = fc.geometry().centroid(1).coordinates().getInfo()
    return float(coords[1]), float(coords[0])  # lat, lon
def get_roi(country, province_text):
    provinces = [p.strip() for p in province_text.split(",") if p.strip()]
    return admin1.filter(
        ee.Filter.And(
            ee.Filter.eq("ADM0_NAME", country),
            ee.Filter.inList("ADM1_NAME", provinces)
        )
    )

def landsat_collection(geom, start, end, cloud=40):
    return (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .merge(ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
        .filterBounds(geom)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUD_COVER", cloud))
        .map(maskLandsat)
    )
# ============================================
# SENTINEL-2 10M COLLECTION - SMOOTHER MAP TILE
# ============================================

def maskSentinel2(image):
    """
    Sentinel-2 SR Harmonized.
    Đổi band Sentinel về cùng tên SR_B* để dùng lại calc_index().
    """
    scl = image.select("SCL")

    # Loại mây, bóng mây, cirrus, snow
    valid = (
        scl.neq(3)   # cloud shadow
        .And(scl.neq(8))   # cloud medium probability
        .And(scl.neq(9))   # cloud high probability
        .And(scl.neq(10))  # cirrus
        .And(scl.neq(11))  # snow/ice
    )

    optical = (
        image
        .select(
            ["B2", "B3", "B4", "B8", "B11", "B12"],
            ["SR_B2", "SR_B3", "SR_B4", "SR_B5", "SR_B6", "SR_B7"]
        )
        .multiply(0.0001)
        .updateMask(valid)
    )

    return optical.copyProperties(image, image.propertyNames())


def sentinel2_collection(geom, start, end, cloud=70):
    return (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(geom)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud))
        .map(maskSentinel2)
    )


def build_best_index_image(geom, start, end, index):
    """
    Ưu tiên Sentinel-2 10m.
    Nếu Sentinel-2 không có ảnh thì fallback Landsat 8/9.
    Trả về: image_index, image_count, source, scale
    """

    s2 = sentinel2_collection(geom, start, end, cloud=75)
    s2_count = s2.size()

    def use_sentinel():
        img = s2.median()
        idx = calc_index(img, index).clip(geom)
        return idx.set({
            "image_count": s2_count,
            "source": "Google Earth Engine - Sentinel-2 SR Harmonized 10m"
        })

    def use_landsat():
        ls = landsat_collection(geom, start, end, cloud=55)
        ls_count = ls.size()

        img = ls.median()
        idx = calc_index(img, index).clip(geom)
        return idx.set({
            "image_count": ls_count,
            "source": "Google Earth Engine - Landsat 8/9 Collection 2 Level 2"
        })

    index_img = ee.Image(
        ee.Algorithms.If(
            s2_count.gt(0),
            use_sentinel(),
            use_landsat()
        )
    )

    return index_img


def build_best_base_image(geom, start, end):
    """
    Tạo ảnh nền đã mask và chuẩn hóa band.
    Ưu tiên Sentinel-2 10m, nếu không có ảnh phù hợp thì fallback Landsat 8/9.
    Trả về ee.Image có property: image_count, source, native_scale.
    """
    s2 = sentinel2_collection(geom, start, end, cloud=75)
    s2_count = s2.size()

    def use_sentinel():
        return s2.median().clip(geom).set({
            "image_count": s2_count,
            "source": "Google Earth Engine - Sentinel-2 SR Harmonized 10m",
            "native_scale": 10
        })

    def use_landsat():
        ls = landsat_collection(geom, start, end, cloud=55)
        ls_count = ls.size()
        return ls.median().clip(geom).set({
            "image_count": ls_count,
            "source": "Google Earth Engine - Landsat 8/9 Collection 2 Level 2",
            "native_scale": 30
        })

    return ee.Image(
        ee.Algorithms.If(
            s2_count.gt(0),
            use_sentinel(),
            use_landsat()
        )
    )


def get_period_dates(year, month):
    start = ee.Date.fromYMD(int(year), int(month), 1)
    end = start.advance(1, "month")
    return start, end


def get_native_scale(img, default=30):
    try:
        scale = img.get("native_scale").getInfo()
        if scale:
            return int(scale)
    except Exception:
        pass
    return default
def safe_reduce(img, roi, index, scale=250):
    stat = img.reduceRegion(
        reducer=ee.Reducer.mean()
        .combine(reducer2=ee.Reducer.minMax(), sharedInputs=True)
        .combine(reducer2=ee.Reducer.stdDev(), sharedInputs=True),
        geometry=roi,
        scale=scale,
        bestEffort=True,
        maxPixels=1e10,
        tileScale=4
    ).getInfo()

    return {
        "mean": stat.get(index + "_mean"),
        "min": stat.get(index + "_min"),
        "max": stat.get(index + "_max"),
        "stdDev": stat.get(index + "_stdDev")
    }
# ============================================
# ENHANCED WEATHER DATA FETCHING
# ============================================

# ============================================
# REAL WEATHER FORECAST FROM OPEN-METEO
# ============================================

def fetch_openmeteo_forecast(lat, lon):
    """
    Lấy dự báo 7 ngày thật từ Open-Meteo Forecast API.
    Đây là dữ liệu forecast từ mô hình thời tiết, không phải dữ liệu tự bịa.
    """
    url = "https://api.open-meteo.com/v1/forecast"

    params = {
        "latitude": lat,
        "longitude": lon,
        "timezone": "Asia/Ho_Chi_Minh",
        "forecast_days": 7,
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "temperature_2m_mean",
            "precipitation_sum",
            "precipitation_probability_max",
            "wind_speed_10m_max",
            "uv_index_max"
        ]),
        "hourly": ",".join([
            "relative_humidity_2m"
        ])
    }

    r = HTTP.get(url, params=params, timeout=25)
    r.raise_for_status()
    return r.json()

def daily_humidity_from_hourly(payload):
    """
    Open-Meteo trả độ ẩm theo hourly ổn định hơn.
    Hàm này gom hourly relative_humidity_2m thành trung bình theo ngày.
    """
    hourly = payload.get("hourly", {})
    times = hourly.get("time", [])
    values = hourly.get("relative_humidity_2m", [])

    buckets = {}

    for t, v in zip(times, values):
        if v is None:
            continue
        day = t[:10]
        buckets.setdefault(day, []).append(float(v))

    return {
        day: round(float(np.mean(vals)), 1)
        for day, vals in buckets.items()
        if vals
    }

def ai_weather_insight_real(forecast):
    """
    AI insight chỉ phân tích dữ liệu forecast thật.
    Không tự sinh dữ liệu thời tiết.
    """
    if not forecast:
        return {
            "headline": "Không có dữ liệu dự báo",
            "summary": "API không trả dữ liệu dự báo.",
            "confidence": 0,
            "warnings": [],
            "note": "Không có dữ liệu đầu vào."
        }

    tmean = [x["tmean"] for x in forecast if x.get("tmean") is not None]
    precip = [x["precip"] for x in forecast if x.get("precip") is not None]
    rain_prob = [x["precip_probability"] for x in forecast if x.get("precip_probability") is not None]
    humidity = [x["humidity"] for x in forecast if x.get("humidity") is not None]
    wind = [x["windspeed"] for x in forecast if x.get("windspeed") is not None]

    total_rain = round(float(np.sum(precip)), 1) if precip else 0
    avg_temp = round(float(np.mean(tmean)), 1) if tmean else None
    max_temp = round(float(np.max(tmean)), 1) if tmean else None
    avg_humidity = round(float(np.mean(humidity)), 1) if humidity else None
    max_rain_prob = round(float(np.max(rain_prob)), 1) if rain_prob else 0
    max_wind = round(float(np.max(wind)), 1) if wind else 0

    temp_trend = "ổn định"
    if len(tmean) >= 2:
        delta = tmean[-1] - tmean[0]
        if delta >= 2:
            temp_trend = "tăng"
        elif delta <= -2:
            temp_trend = "giảm"

    rain_level = "ít mưa"
    if total_rain >= 80:
        rain_level = "mưa rất lớn"
    elif total_rain >= 40:
        rain_level = "mưa lớn"
    elif total_rain >= 15:
        rain_level = "mưa vừa"
    elif total_rain >= 3:
        rain_level = "mưa nhẹ"

    warnings = []

    if total_rain >= 80 or max_rain_prob >= 85:
        warnings.append("Nguy cơ mưa lớn/ngập úng, cần theo dõi khu vực trũng thấp.")

    if max_temp is not None and max_temp >= 37:
        warnings.append("Nhiệt độ cao, nguy cơ khô hạn/cháy thực bì tăng.")

    if avg_humidity is not None and avg_humidity >= 85:
        warnings.append("Độ ẩm cao, có thể tăng nguy cơ sâu bệnh/nấm trên cây trồng.")

    if max_wind >= 45:
        warnings.append("Gió mạnh, cần chú ý công trình tạm và vùng ven biển.")

    confidence = 88
    if max_rain_prob >= 80:
        confidence = 82
    if total_rain >= 80:
        confidence = 78

    headline = f"Nhiệt độ {temp_trend}, {rain_level}, xác suất mưa cao nhất {max_rain_prob}%"

    summary = (
        f"Dự báo 7 ngày theo dữ liệu mô hình thời tiết: "
        f"nhiệt độ trung bình khoảng {avg_temp}°C, "
        f"tổng lượng mưa khoảng {total_rain}mm, "
        f"độ ẩm trung bình khoảng {avg_humidity}%, "
        f"gió mạnh nhất khoảng {max_wind} km/h."
    )

    return {
        "headline": headline,
        "summary": summary,
        "confidence": confidence,
        "warnings": warnings,
        "metrics": {
            "avg_temp": f"{avg_temp}°C",
            "max_temp": f"{max_temp}°C",
            "total_precip": f"{total_rain}mm",
            "max_rain_probability": f"{max_rain_prob}%",
            "avg_humidity": f"{avg_humidity}%",
            "max_wind": f"{max_wind} km/h"
        },
        "note": (
            "Dữ liệu dự báo lấy trực tiếp từ Open-Meteo Forecast API. "
            "AI chỉ phân tích xu hướng/rủi ro từ dữ liệu thật, không tự tạo số liệu thời tiết."
        )
    }
@app.route("/favicon.ico")
def favicon():
    return "", 204
# ============================================
# HOME
# ============================================

@app.route("/")
def index():
    return render_template("index.html")

# ============================================
# COUNTRIES
# ============================================

@app.route("/countries")
def countries():
    names = admin0.aggregate_array("ADM0_NAME")
    return jsonify(names.getInfo())

# ============================================
# PROVINCES
# ============================================

@app.route("/provinces")
def provinces():
    country = request.args.get("country")
    fc = admin1.filter(ee.Filter.eq("ADM0_NAME", country))
    names = fc.aggregate_array("ADM1_NAME")
    return jsonify(names.getInfo())

# ============================================
# COUNTRY BOUNDARY
# ============================================

@app.route("/country_boundary")
def country_boundary():
    country = request.args.get("country")
    fc = admin0.filter(ee.Filter.eq("ADM0_NAME", country))
    geo = fc.geometry().getInfo()
    return jsonify({
        "type":"FeatureCollection",
        "features":[{"type":"Feature","geometry":geo,"properties":{"name":country}}]
    })

# ============================================
# PROVINCE BOUNDARY
# ============================================

@app.route("/province_boundary")
def province_boundary():
    country = request.args.get("country")
    province = request.args.get("province")
    provinces=[p.strip() for p in province.split(",")]
    fc = admin1.filter(
        ee.Filter.And(
            ee.Filter.eq("ADM0_NAME",country),
            ee.Filter.inList("ADM1_NAME",provinces)
        )
    )
    geo = fc.geometry().getInfo()
    return jsonify(geo)

# ============================================
# MAP TILE
# ============================================

# ============================================
# MAP TILE - SENTINEL-2 FIRST, LANDSAT FALLBACK
# ============================================

@app.route("/map")
@json_cache(ttl=60 * 60)
def map_tile():
    try:
        index = request.args.get("index")
        country = request.args.get("country")
        province = request.args.get("province")
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))

        region = get_roi(country, province)
        geom = region.geometry()

        start = ee.Date.fromYMD(year, month, 1)
        end = start.advance(1, "month")

        index_img = build_best_index_image(
            geom=geom,
            start=start,
            end=end,
            index=index
        )

        image_count = index_img.get("image_count").getInfo()
        source = index_img.get("source").getInfo()

        if image_count is None or int(image_count) == 0:
            return jsonify({
                "tile": None,
                "message": "Không có ảnh Sentinel-2 hoặc Landsat phù hợp trong kỳ đã chọn.",
                "image_count": 0,
                "source": "Google Earth Engine"
            })

        mapid = index_img.getMapId(get_vis(index))

        return jsonify({
            "tile": mapid["tile_fetcher"].url_format,
            "image_count": int(image_count),
            "source": source,
            "resolution_note": (
                "Ưu tiên Sentinel-2 10m để layer mịn hơn. "
                "Nếu không có Sentinel-2 thì hệ thống tự dùng Landsat 8/9."
            )
        })

    except Exception as e:
        return jsonify({
            "tile": None,
            "message": "Lỗi tạo layer bản đồ.",
            "detail": str(e),
            "source": "Google Earth Engine"
        }), 500
# ============================================
# STATS
# ============================================


@app.route("/stats")
@json_cache(ttl=60 * 30)
def stats():
    try:
        index = request.args.get("index")
        country = request.args.get("country")
        province = request.args.get("province")
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))

        roi = get_roi(country, province).geometry()
        start, end = get_period_dates(year, month)

        img = build_best_index_image(roi, start, end, index)
        count = img.get("image_count").getInfo()
        source = img.get("source").getInfo()

        if count is None or int(count) == 0:
            return jsonify({
                "mean": None,
                "min": None,
                "max": None,
                "stdDev": None,
                "image_count": 0,
                "source": source or "Google Earth Engine",
                "message": "Không có ảnh Sentinel-2 hoặc Landsat phù hợp."
            })

        stats_json = safe_reduce(img, roi, index, scale=30)
        stats_json.update({
            "image_count": int(count),
            "source": source,
            "resolution_note": "Thống kê dùng cùng ảnh với bản đồ: Sentinel-2 ưu tiên, Landsat fallback."
        })
        return jsonify(stats_json)

    except Exception as e:
        return jsonify({"error": "stats failed", "detail": str(e)}), 500

# ============================================
# TIME SERIES
# ============================================


@app.route("/timeseries")
@json_cache(ttl=60 * 60)
def timeseries():
    try:
        country = request.args.get("country")
        province = request.args.get("province")
        index = request.args.get("index")

        roi = get_roi(country, province).geometry()
        current_year = datetime.now().year
        years = list(range(max(2016, current_year - 7), current_year + 1))
        result = []

        for y in years:
            y_start = ee.Date.fromYMD(y, 1, 1)
            y_end = y_start.advance(1, "year")
            img = build_best_index_image(roi, y_start, y_end, index)
            count = img.get("image_count").getInfo()
            source = img.get("source").getInfo()

            if count is None or int(count) == 0:
                result.append({"year": y, "value": None, "image_count": 0, "source": source})
                continue

            stat = img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=roi,
                scale=250,
                bestEffort=True,
                maxPixels=1e10,
                tileScale=4
            ).getInfo()

            result.append({
                "year": y,
                "value": stat.get(index),
                "image_count": int(count),
                "source": source
            })

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": "timeseries failed", "detail": str(e)}), 500

@app.route("/zoom_province")
def zoom_province():
    country = request.args.get("country")
    province = request.args.get("province")
    provinces=[p.strip() for p in province.split(",")]
    fc = admin1.filter(
        ee.Filter.And(
            ee.Filter.eq("ADM0_NAME",country),
            ee.Filter.inList("ADM1_NAME",provinces)
        )
    )
    bounds = fc.geometry().bounds().getInfo()
    return jsonify(bounds)

# ============================================
# CORRELATION
# ============================================


@app.route("/correlation")
@json_cache(ttl=60 * 30)
def correlation():
    try:
        country = request.args.get("country")
        province = request.args.get("province")
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))

        roi = get_roi(country, province).geometry()
        start, end = get_period_dates(year, month)

        base_image = build_best_base_image(roi, start, end)
        count = base_image.get("image_count").getInfo()
        source = base_image.get("source").getInfo()

        if count is None or int(count) == 0:
            return jsonify({
                "ndvi_ndwi": 0,
                "ndvi_evi": 0,
                "ndvi_nbr": 0,
                "image_count": 0,
                "source": source or "Google Earth Engine"
            })

        ndvi = calc_index(base_image, "NDVI")
        ndwi = calc_index(base_image, "NDWI")
        evi = calc_index(base_image, "EVI")
        nbr = calc_index(base_image, "NBR")

        stack = ndvi.addBands([ndwi, evi, nbr]).rename(["NDVI", "NDWI", "EVI", "NBR"])
        samples = stack.sample(
            region=roi,
            scale=60,
            numPixels=400,
            geometries=False,
            tileScale=4
        ).getInfo()

        ndvi_vals, ndwi_vals, evi_vals, nbr_vals = [], [], [], []
        for f in samples.get("features", []):
            props = f.get("properties", {})
            if all(k in props and props[k] is not None for k in ["NDVI", "NDWI", "EVI", "NBR"]):
                ndvi_vals.append(props["NDVI"])
                ndwi_vals.append(props["NDWI"])
                evi_vals.append(props["EVI"])
                nbr_vals.append(props["NBR"])

        def corr(a, b):
            if len(a) < 3 or len(b) < 3:
                return 0
            value = float(np.corrcoef(a, b)[0, 1])
            return 0 if np.isnan(value) else value

        return jsonify({
            "ndvi_ndwi": corr(ndvi_vals, ndwi_vals),
            "ndvi_evi": corr(ndvi_vals, evi_vals),
            "ndvi_nbr": corr(ndvi_vals, nbr_vals),
            "sample_count": len(ndvi_vals),
            "image_count": int(count),
            "source": source
        })

    except Exception as e:
        return jsonify({"error": "correlation failed", "detail": str(e)}), 500

# ============================================
# SCATTER DATA
# ============================================


@app.route("/scatter")
@json_cache(ttl=60 * 30)
def scatter():
    try:
        country = request.args.get("country")
        province = request.args.get("province")
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))

        roi = get_roi(country, province).geometry()
        start, end = get_period_dates(year, month)

        base_image = build_best_base_image(roi, start, end)
        count = base_image.get("image_count").getInfo()

        if count is None or int(count) == 0:
            return jsonify([])

        ndvi = calc_index(base_image, "NDVI")
        evi = calc_index(base_image, "EVI")
        stack = ndvi.addBands(evi).rename(["NDVI", "EVI"])

        samples = stack.sample(
            region=roi,
            scale=60,
            numPixels=350,
            geometries=False,
            tileScale=4
        ).getInfo()

        points = []
        for f in samples.get("features", []):
            props = f.get("properties", {})
            if props.get("NDVI") is not None and props.get("EVI") is not None:
                points.append({"x": props["NDVI"], "y": props["EVI"]})

        return jsonify(points)

    except Exception as e:
        return jsonify({"error": "scatter failed", "detail": str(e)}), 500

# ============================================
# PIXEL VALUE
# ============================================


@app.route("/pixel")
@json_cache(ttl=60 * 10)
def pixel():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))
        index = request.args.get("index")

        point = ee.Geometry.Point([lon, lat])
        geom = point.buffer(1500)
        start, end = get_period_dates(year, month)

        img = build_best_index_image(geom, start, end, index)
        count = img.get("image_count").getInfo()
        source = img.get("source").getInfo()

        if count is None or int(count) == 0:
            return jsonify({"value": None, "image_count": 0, "source": source or "Google Earth Engine"})

        val = img.reduceRegion(
            reducer=ee.Reducer.first(),
            geometry=point,
            scale=20,
            bestEffort=True,
            maxPixels=1e8,
            tileScale=4
        ).getInfo()

        return jsonify({
            "value": val.get(index),
            "raw": val,
            "image_count": int(count),
            "source": source
        })

    except Exception as e:
        return jsonify({"error": "pixel failed", "detail": str(e)}), 500

# ============================================
# EXPORT GEOTIFF
# ============================================


@app.route("/export")
def export():
    try:
        index = request.args.get("index")
        country = request.args.get("country")
        province = request.args.get("province")
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))

        region = get_roi(country, province)
        geom = region.geometry()
        start, end = get_period_dates(year, month)

        img = build_best_index_image(geom, start, end, index)
        count = img.get("image_count").getInfo()
        source = img.get("source").getInfo()

        if count is None or int(count) == 0:
            return jsonify({
                "url": None,
                "error": "Không có ảnh phù hợp để xuất GeoTIFF.",
                "image_count": 0,
                "source": source or "Google Earth Engine"
            }), 404

        url = img.getDownloadURL({
            "scale": 30,
            "region": geom,
            "format": "GEO_TIFF"
        })

        return jsonify({"url": url, "image_count": int(count), "source": source})

    except Exception as e:
        return jsonify({"error": "export failed", "detail": str(e)}), 500


@app.route("/images")
@json_cache(ttl=60 * 30)
def get_images():
    try:
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))
        country = request.args.get("country")
        province = request.args.get("province")

        region = get_roi(country, province)
        geom = region.geometry()
        start, end = get_period_dates(year, month)

        s2 = sentinel2_collection(geom, start, end, cloud=75)
        s2_count = s2.size().getInfo()

        if s2_count and int(s2_count) > 0:
            ids = s2.aggregate_array("system:index").getInfo() or []
            return jsonify({
                "source": "Sentinel-2 SR Harmonized",
                "image_count": int(s2_count),
                "ids": ids[:80]
            })

        ls = landsat_collection(geom, start, end, cloud=55)
        ls_count = ls.size().getInfo()
        ids = ls.aggregate_array("LANDSAT_PRODUCT_ID").getInfo() or []

        return jsonify({
            "source": "Landsat 8/9 Collection 2 Level 2",
            "image_count": int(ls_count or 0),
            "ids": ids[:80]
        })

    except Exception as e:
        return jsonify({"error": "images failed", "detail": str(e)}), 500

# ============================================
# DIFFERENCE ANALYSIS
# ============================================


@app.route("/difference")
@json_cache(ttl=60 * 30)
def difference():
    try:
        index = request.args.get("index")
        country = request.args.get("country")
        province = request.args.get("province")

        y1, m1 = int(request.args.get("y1")), int(request.args.get("m1"))
        y2, m2 = int(request.args.get("y2")), int(request.args.get("m2"))

        roi = get_roi(country, province).geometry()

        s1, e1 = get_period_dates(y1, m1)
        s2, e2 = get_period_dates(y2, m2)

        img1 = build_best_index_image(roi, s1, e1, index)
        img2 = build_best_index_image(roi, s2, e2, index)

        count1 = img1.get("image_count").getInfo()
        count2 = img2.get("image_count").getInfo()

        if count1 is None or int(count1) == 0 or count2 is None or int(count2) == 0:
            return jsonify({
                "tile": None,
                "error": "Không đủ ảnh vệ tinh cho một trong hai kỳ so sánh.",
                "count_period_1": int(count1 or 0),
                "count_period_2": int(count2 or 0)
            }), 404

        diff = img2.subtract(img1).rename(index + "_delta").clip(roi)
        vis = {"min": -0.35, "max": 0.35, "palette": ["#b91c1c", "#fef3c7", "#ffffff", "#bbf7d0", "#15803d"]}
        mapid = diff.getMapId(vis)

        return jsonify({
            "tile": mapid["tile_fetcher"].url_format,
            "count_period_1": int(count1),
            "count_period_2": int(count2),
            "source_period_1": img1.get("source").getInfo(),
            "source_period_2": img2.get("source").getInfo()
        })

    except Exception as e:
        return jsonify({"error": "difference failed", "detail": str(e)}), 500

# ============================================
# ENHANCED WEATHER FORECAST - 7 DAYS
# ============================================

@app.route("/forecast_7days")
@json_cache(ttl=60 * 20)
def forecast_7days():
    country = request.args.get("country")
    province = request.args.get("province")

    if not country or not province:
        return jsonify({"error": "Thiếu country hoặc province"}), 400

    try:
        lat, lon = get_roi_centroid(country, province)

        payload = fetch_openmeteo_forecast(lat, lon)
        daily = payload.get("daily", {})
        humidity_by_day = daily_humidity_from_hourly(payload)

        dates = daily.get("time", [])

        forecast = []

        for i, d in enumerate(dates[:7]):
            tmax = daily.get("temperature_2m_max", [None] * 7)[i]
            tmin = daily.get("temperature_2m_min", [None] * 7)[i]
            tmean = daily.get("temperature_2m_mean", [None] * 7)[i]
            precip = daily.get("precipitation_sum", [None] * 7)[i]
            prob = daily.get("precipitation_probability_max", [None] * 7)[i]
            wind = daily.get("wind_speed_10m_max", [None] * 7)[i]
            uv = daily.get("uv_index_max", [None] * 7)[i]

            forecast.append({
                "date": d,
                "day_name": datetime.fromisoformat(d).strftime("%A"),
                "tmin": round(float(tmin), 1) if tmin is not None else None,
                "tmax": round(float(tmax), 1) if tmax is not None else None,
                "tmean": round(float(tmean), 1) if tmean is not None else None,
                "precip": round(float(precip), 1) if precip is not None else None,
                "precip_probability": round(float(prob), 1) if prob is not None else None,
                "humidity": humidity_by_day.get(d),
                "windspeed": round(float(wind), 1) if wind is not None else None,
                "uv_index": round(float(uv), 1) if uv is not None else None
            })

        insight = ai_weather_insight_real(forecast)

        return jsonify({
            "location": {
                "lat": lat,
                "lon": lon,
                "country": country,
                "province": province
            },
            "forecast": forecast,
            "insight": insight,
            "source": {
                "provider": "Open-Meteo Forecast API",
                "type": "real forecast model output",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "timezone": "Asia/Ho_Chi_Minh",
                "model_note": (
                    "Dự báo là dữ liệu mô hình thời tiết thật. "
                    "Không thể cam kết đúng 100% vì tương lai luôn có sai số dự báo."
                )
            }
        })

    except Exception as e:
        print(f"Forecast error: {e}")
        return jsonify({
            "error": "forecast failed",
            "detail": str(e)
        }), 500
# ============================================
# ANALYSIS BUNDLE
# ============================================

# ============================================
# ANALYSIS BUNDLE - SENTINEL-2 FIRST, LANDSAT FALLBACK
# ============================================

@app.route("/analysis_bundle")
@json_cache(ttl=60 * 30)
def analysis_bundle():
    try:
        index = request.args.get("index")
        country = request.args.get("country")
        province = request.args.get("province")
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))

        region = get_roi(country, province)
        roi = region.geometry()

        start = ee.Date.fromYMD(year, month, 1)
        end = start.advance(1, "month")

        # =====================================================
        # 1. TẠO ẢNH CHỈ SỐ: ƯU TIÊN SENTINEL-2, FALLBACK LANDSAT
        # =====================================================

        img = build_best_index_image(
            geom=roi,
            start=start,
            end=end,
            index=index
        )

        count = img.get("image_count").getInfo()
        source = img.get("source").getInfo()

        if count is None or int(count) == 0:
            return jsonify({
                "error": "Không có dữ liệu Sentinel-2 hoặc Landsat cho kỳ đã chọn."
            })

        count = int(count)

        # Sentinel-2 mịn hơn; scale 60 cân bằng giữa độ nét và tốc độ trên Render Free
        stats_json = safe_reduce(
            img=img,
            roi=roi,
            index=index,
            scale=60
        )

        # =====================================================
        # 2. TIME SERIES THEO NĂM
        # =====================================================

        current_year = datetime.now().year
        years = list(range(max(2016, current_year - 7), current_year + 1))

        ts = []

        for y in years:
            y_start = ee.Date.fromYMD(y, 1, 1)
            y_end = y_start.advance(1, "year")

            y_img = build_best_index_image(
                geom=roi,
                start=y_start,
                end=y_end,
                index=index
            )

            y_count = y_img.get("image_count").getInfo()

            if y_count is None or int(y_count) == 0:
                ts.append({
                    "year": y,
                    "value": None,
                    "image_count": 0
                })
                continue

            y_stat = y_img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=roi,
                scale=250,
                bestEffort=True,
                maxPixels=1e10,
                tileScale=4
            ).getInfo()

            ts.append({
                "year": y,
                "value": y_stat.get(index),
                "image_count": int(y_count)
            })

        # =====================================================
        # 3. CORRELATION NDVI / NDWI / EVI
        # =====================================================

        # Để tính tương quan, cần lấy lại ảnh nguồn tốt nhất theo tháng.
        # Dùng Sentinel-2 nếu có, nếu không dùng Landsat.

        s2_col = sentinel2_collection(roi, start, end, cloud=75)
        s2_count = s2_col.size().getInfo()

        if s2_count and int(s2_count) > 0:
            base_image = s2_col.median()
            corr_source = "Sentinel-2 SR Harmonized"
        else:
            ls_col = landsat_collection(roi, start, end, cloud=55)
            ls_count = ls_col.size().getInfo()

            if ls_count is None or int(ls_count) == 0:
                base_image = None
                corr_source = "Không đủ dữ liệu"
            else:
                base_image = ls_col.median()
                corr_source = "Landsat 8/9 Collection 2 Level 2"

        scatter = []
        ndvi_vals = []
        ndwi_vals = []
        evi_vals = []

        if base_image is not None:
            ndvi = calc_index(base_image, "NDVI")
            ndwi = calc_index(base_image, "NDWI")
            evi = calc_index(base_image, "EVI")

            stack = ndvi.addBands([ndwi, evi]).rename(["NDVI", "NDWI", "EVI"])

            sample = stack.sample(
                region=roi,
                scale=60,
                numPixels=250,
                geometries=False,
                tileScale=4
            ).getInfo()

            for f in sample.get("features", []):
                p = f.get("properties", {})

                if all(k in p for k in ["NDVI", "NDWI", "EVI"]):
                    if (
                        p["NDVI"] is not None and
                        p["NDWI"] is not None and
                        p["EVI"] is not None
                    ):
                        ndvi_vals.append(p["NDVI"])
                        ndwi_vals.append(p["NDWI"])
                        evi_vals.append(p["EVI"])

                        scatter.append({
                            "x": p["NDVI"],
                            "y": p["EVI"]
                        })

        def corr(a, b):
            if len(a) < 3 or len(b) < 3:
                return 0
            return float(np.corrcoef(a, b)[0, 1])

        # =====================================================
        # 4. AI SUMMARY
        # =====================================================

        mean_val = stats_json.get("mean")
        status = "không đủ dữ liệu"

        if mean_val is not None:
            if index == "NDVI":
                if mean_val < 0.2:
                    status = "thảm thực vật suy giảm mạnh"
                elif mean_val < 0.4:
                    status = "thảm thực vật mức trung bình"
                else:
                    status = "thảm thực vật tốt"

            elif index == "NDWI":
                if mean_val < -0.1:
                    status = "ít nước/khô"
                elif mean_val < 0.2:
                    status = "ẩm hoặc nước mức trung bình"
                else:
                    status = "nước/độ ẩm cao"

            elif index == "NDMI":
                if mean_val < -0.1:
                    status = "độ ẩm thực vật thấp"
                elif mean_val < 0.2:
                    status = "độ ẩm thực vật trung bình"
                else:
                    status = "độ ẩm thực vật tốt"

            elif index == "NBR":
                if mean_val < -0.1:
                    status = "có dấu hiệu khu vực bị tác động/cháy hoặc suy giảm"
                elif mean_val < 0.2:
                    status = "mức phục hồi trung bình"
                else:
                    status = "khu vực ổn định hoặc phục hồi tốt"

            else:
                status = "chỉ số ổn định"

        summary = {
            "text": (
                f"AI phân tích từ dữ liệu vệ tinh thật: khu vực có {status}. "
                f"Chỉ số {index} trung bình đạt "
                f"{round(mean_val, 3) if mean_val is not None else 'N/A'}. "
                f"Số ảnh dùng trong kỳ: {count}. "
                f"Nguồn ảnh: {source}."
            )
        }

        # =====================================================
        # 5. LEGEND
        # =====================================================

        legend = {
            "items": [
                {
                    "label": "Thấp",
                    "color": "#ef4444",
                    "value": stats_json.get("min")
                },
                {
                    "label": "Trung bình",
                    "color": "#facc15",
                    "value": stats_json.get("mean")
                },
                {
                    "label": "Cao",
                    "color": "#22c55e",
                    "value": stats_json.get("max")
                }
            ]
        }

        # =====================================================
        # 6. RETURN JSON
        # =====================================================

        return jsonify({
            "index": index,
            "stats": stats_json,
            "timeseries": ts,
            "scatter": scatter,
            "legend": legend,
            "summary": summary,
            "correlation": {
                "ndvi_ndwi": corr(ndvi_vals, ndwi_vals),
                "ndvi_evi": corr(ndvi_vals, evi_vals)
            },
            "source": source,
            "correlation_source": corr_source,
            "image_count": count,
            "resolution_note": (
                "Hệ thống ưu tiên Sentinel-2 SR Harmonized 10m để layer và thống kê mịn hơn. "
                "Nếu Sentinel-2 không có ảnh phù hợp thì tự động fallback sang Landsat 8/9."
            ),
            "generated_at": datetime.now(timezone.utc).isoformat()
        })

    except Exception as e:
        return jsonify({
            "error": "analysis bundle failed",
            "detail": str(e)
        }), 500
# ============================================
# NDVI / NDWI CHANGE MONITORING
# ============================================


@app.route("/change_monitor")
@json_cache(ttl=60 * 30)
def change_monitor():
    try:
        index = request.args.get("index", "NDVI")
        country = request.args.get("country")
        province = request.args.get("province")

        y1 = int(request.args.get("y1"))
        m1 = int(request.args.get("m1"))
        y2 = int(request.args.get("y2"))
        m2 = int(request.args.get("m2"))

        if index not in ["NDVI", "NDWI", "EVI", "NDMI", "NBR", "SAVI"]:
            return jsonify({"error": "Index không hợp lệ"}), 400

        roi = get_roi(country, province).geometry()
        s1, e1 = get_period_dates(y1, m1)
        s2, e2 = get_period_dates(y2, m2)

        img1 = build_best_index_image(roi, s1, e1, index)
        img2 = build_best_index_image(roi, s2, e2, index)

        count1 = img1.get("image_count").getInfo()
        count2 = img2.get("image_count").getInfo()
        source1 = img1.get("source").getInfo()
        source2 = img2.get("source").getInfo()

        if count1 is None or int(count1) == 0 or count2 is None or int(count2) == 0:
            return jsonify({
                "error": "Không đủ ảnh vệ tinh cho một trong hai kỳ so sánh.",
                "count_period_1": int(count1 or 0),
                "count_period_2": int(count2 or 0),
                "source_period_1": source1,
                "source_period_2": source2
            }), 404

        diff = img2.subtract(img1).rename(index + "_delta").clip(roi)
        band = index + "_delta"

        stat = diff.reduceRegion(
            reducer=ee.Reducer.mean()
            .combine(reducer2=ee.Reducer.minMax(), sharedInputs=True)
            .combine(reducer2=ee.Reducer.stdDev(), sharedInputs=True),
            geometry=roi,
            scale=60,
            bestEffort=True,
            maxPixels=1e10,
            tileScale=4
        ).getInfo()

        mean_delta = stat.get(band + "_mean")
        min_delta = stat.get(band + "_min")
        max_delta = stat.get(band + "_max")
        std_delta = stat.get(band + "_stdDev")

        vis = {
            "min": -0.35,
            "max": 0.35,
            "palette": ["#b91c1c", "#fef3c7", "#ffffff", "#bbf7d0", "#15803d"]
        }
        mapid = diff.getMapId(vis)

        status = "biến động ổn định"
        risk = "thấp"

        if mean_delta is not None:
            if mean_delta <= -0.15:
                status = "suy giảm mạnh"
                risk = "cao"
            elif mean_delta <= -0.05:
                status = "suy giảm nhẹ"
                risk = "trung bình"
            elif mean_delta >= 0.15:
                status = "phục hồi/tăng mạnh"
                risk = "thấp"
            elif mean_delta >= 0.05:
                status = "tăng nhẹ"
                risk = "thấp"

        subject = "mặt nước/độ ẩm bề mặt" if index == "NDWI" else "thảm thực vật" if index == "NDVI" else "chỉ số vệ tinh"
        ai_summary = (
            f"AI Change Monitoring đánh giá {subject} có trạng thái {status}. "
            f"Giá trị biến động trung bình {round(mean_delta, 4) if mean_delta is not None else 'N/A'}. "
            f"Mức rủi ro: {risk}. "
            f"Kết quả tính từ cùng pipeline ảnh của bản đồ: Sentinel-2 ưu tiên, Landsat fallback."
        )

        return jsonify({
            "index": index,
            "tile": mapid["tile_fetcher"].url_format,
            "period_1": f"{y1}-{m1:02d}",
            "period_2": f"{y2}-{m2:02d}",
            "image_count": {
                "period_1": int(count1),
                "period_2": int(count2)
            },
            "stats": {
                "mean_delta": mean_delta,
                "min_delta": min_delta,
                "max_delta": max_delta,
                "stdDev": std_delta
            },
            "ai": {
                "status": status,
                "risk": risk,
                "summary": ai_summary
            },
            "legend": {
                "items": [
                    {"label": "Suy giảm", "color": "#b91c1c", "value": -0.35},
                    {"label": "Ổn định", "color": "#ffffff", "value": 0},
                    {"label": "Tăng/phục hồi", "color": "#15803d", "value": 0.35}
                ]
            },
            "source": {
                "period_1": source1,
                "period_2": source2
            },
            "generated_at": datetime.now(timezone.utc).isoformat()
        })

    except Exception as e:
        return jsonify({"error": "change monitor failed", "detail": str(e)}), 500

    # ============================================
# AI ARIMA NDVI / NDWI FORECAST - LOCAL CSV
# ============================================

AI_CSV_PATH = os.path.join("data", "weather_training.csv")

def load_ndvi_ndwi_csv():
    if not os.path.exists(AI_CSV_PATH):
        raise FileNotFoundError(
            f"Không tìm thấy {AI_CSV_PATH}. Hãy chạy clean_ndvi_ndwi_csv.py trước."
        )

    df = pd.read_csv(AI_CSV_PATH)
    df = df.replace(-9999, np.nan)

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "country", "province"])

    df["country"] = df["country"].astype(str).str.strip()
    df["province"] = df["province"].astype(str).str.strip()

    for c in ["lat", "lon", "year", "month", "day", "ndvi", "ndwi"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df.loc[(df["ndvi"] < -1) | (df["ndvi"] > 1), "ndvi"] = np.nan
    df.loc[(df["ndwi"] < -1) | (df["ndwi"] > 1), "ndwi"] = np.nan

    df = (
        df.groupby(["date", "country", "province"], as_index=False)
        .agg({
            "lat": "mean",
            "lon": "mean",
            "year": "first",
            "month": "first",
            "day": "first",
            "ndvi": "mean",
            "ndwi": "mean"
        })
    )

    return df.sort_values(["country", "province", "date"])


def filter_ndvi_ndwi_region(df, country, province):
    sub = df[
        (df["country"].astype(str).str.lower() == str(country).lower()) &
        (df["province"].astype(str).str.lower() == str(province).lower())
    ].copy()

    if sub.empty:
        same_country = df[
            df["country"].astype(str).str.lower() == str(country).lower()
        ]

        available = same_country["province"].drop_duplicates().head(20).tolist()

        raise ValueError(
            f"Không có dữ liệu CSV cho {country} - {province}. "
            f"Một số province có trong CSV: {available}"
        )

    return sub.sort_values("date")


def prepare_arima_series(sub, target):
    s = sub[["date", target]].copy()
    s[target] = pd.to_numeric(s[target], errors="coerce")
    s = s.dropna(subset=[target])

    if s.empty:
        raise ValueError(f"Không có dữ liệu hợp lệ cho {target}")

    s = s.set_index("date").sort_index()
    s = s.groupby(s.index).mean()
    s = s.asfreq("D")

    s[target] = s[target].interpolate(method="time")
    s[target] = s[target].ffill().bfill()

    return s[target]


def forecast_arima_index(series, start_date, target_name, steps=7):
    train = series[series.index < start_date].copy()
    train = train.tail(730)

    if len(train) < 45:
        raise ValueError(
            f"Không đủ dữ liệu trước {start_date.date()} để train ARIMA cho {target_name}. "
            f"Hiện có {len(train)} ngày, cần tối thiểu 45 ngày. "
            f"Hãy chọn tháng sau hơn hoặc tải thêm các tháng trước."
        )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        model = SARIMAX(
            train,
            order=(2, 1, 2),
            seasonal_order=(1, 0, 1, 7),
            enforce_stationarity=False,
            enforce_invertibility=False
        )

        result = model.fit(disp=False)

    pred = result.get_forecast(steps=steps).predicted_mean
    pred = pd.Series(pred).replace([np.inf, -np.inf], np.nan)
    pred = pred.ffill().bfill().fillna(float(train.iloc[-1]))
    pred = pred.clip(-1, 1)

    resid = result.resid.dropna()
    internal_mae = float(np.mean(np.abs(resid))) if len(resid) else None

    return {
        "prediction": [float(x) for x in pred.values],
        "internal_mae": internal_mae
    }


def build_ndvi_ndwi_insight(forecast_rows, last_ndvi, last_ndwi):
    ndvi_values = [x["ndvi_pred"] for x in forecast_rows]
    ndwi_values = [x["ndwi_pred"] for x in forecast_rows]

    avg_ndvi = round(float(np.mean(ndvi_values)), 4)
    avg_ndwi = round(float(np.mean(ndwi_values)), 4)

    min_ndvi = round(float(np.min(ndvi_values)), 4)
    max_ndvi = round(float(np.max(ndvi_values)), 4)
    min_ndwi = round(float(np.min(ndwi_values)), 4)
    max_ndwi = round(float(np.max(ndwi_values)), 4)

    ndvi_delta = round(avg_ndvi - float(last_ndvi), 4) if last_ndvi is not None else None
    ndwi_delta = round(avg_ndwi - float(last_ndwi), 4) if last_ndwi is not None else None

    warnings_list = []

    if avg_ndvi < 0.2:
        warnings_list.append("NDVI dự báo thấp, thảm thực vật có nguy cơ suy giảm.")
    elif avg_ndvi >= 0.4:
        warnings_list.append("NDVI dự báo ở mức tốt, thảm thực vật tương đối ổn định.")

    if avg_ndwi < -0.1:
        warnings_list.append("NDWI dự báo thấp, có nguy cơ khô hoặc thiếu nước bề mặt.")
    elif avg_ndwi >= 0.2:
        warnings_list.append("NDWI dự báo cao, độ ẩm/nước bề mặt có xu hướng tăng.")

    if ndvi_delta is not None and ndvi_delta <= -0.05:
        warnings_list.append("NDVI có xu hướng giảm so với giá trị gần nhất.")
    elif ndvi_delta is not None and ndvi_delta >= 0.05:
        warnings_list.append("NDVI có xu hướng tăng so với giá trị gần nhất.")

    if ndwi_delta is not None and ndwi_delta <= -0.05:
        warnings_list.append("NDWI có xu hướng giảm, khả năng độ ẩm bề mặt suy giảm.")
    elif ndwi_delta is not None and ndwi_delta >= 0.05:
        warnings_list.append("NDWI có xu hướng tăng, khả năng độ ẩm bề mặt tăng.")

    headline = f"AI ARIMA dự báo NDVI TB {avg_ndvi}, NDWI TB {avg_ndwi}"

    summary = (
        f"Trong 7 ngày sau kỳ phân tích, mô hình ARIMA dự báo NDVI trung bình khoảng {avg_ndvi}, "
        f"dao động từ {min_ndvi} đến {max_ndvi}. "
        f"NDWI trung bình khoảng {avg_ndwi}, dao động từ {min_ndwi} đến {max_ndwi}. "
        f"So với giá trị gần nhất trước kỳ dự báo, NDVI thay đổi {ndvi_delta}, "
        f"NDWI thay đổi {ndwi_delta}."
    )

    return {
        "headline": headline,
        "summary": summary,
        "confidence": 70,
        "warnings": warnings_list,
        "metrics": {
            "avg_ndvi": avg_ndvi,
            "min_ndvi": min_ndvi,
            "max_ndvi": max_ndvi,
            "avg_ndwi": avg_ndwi,
            "min_ndwi": min_ndwi,
            "max_ndwi": max_ndwi,
            "ndvi_delta": ndvi_delta,
            "ndwi_delta": ndwi_delta
        },
        "note": (
            "Kết quả là dự báo của mô hình ARIMA từ dữ liệu NDVI/NDWI lịch sử trong CSV local. "
            "Đây không phải ảnh vệ tinh thực tế 100% của tương lai."
        )
    }


@app.route("/arima_ndvi_ndwi_7days")
def arima_ndvi_ndwi_7days():
    try:
        country = request.args.get("country")
        province = request.args.get("province")
        year = int(request.args.get("year"))
        month = int(request.args.get("month"))

        if not country or not province:
            return jsonify({"error": "Thiếu country hoặc province"}), 400

        start_date = pd.Timestamp(datetime(year, month, 1))

        df = load_ndvi_ndwi_csv()
        sub = filter_ndvi_ndwi_region(df, country, province)

        ndvi_series = prepare_arima_series(sub, "ndvi")
        ndwi_series = prepare_arima_series(sub, "ndwi")

        ndvi_result = forecast_arima_index(ndvi_series, start_date, "NDVI", 7)
        ndwi_result = forecast_arima_index(ndwi_series, start_date, "NDWI", 7)

        before_ndvi = ndvi_series[ndvi_series.index < start_date].dropna()
        before_ndwi = ndwi_series[ndwi_series.index < start_date].dropna()

        last_ndvi = float(before_ndvi.iloc[-1]) if len(before_ndvi) else None
        last_ndwi = float(before_ndwi.iloc[-1]) if len(before_ndwi) else None

        forecast_rows = []

        for i in range(7):
            d = start_date + timedelta(days=i)

            ndvi_pred = round(float(ndvi_result["prediction"][i]), 4)
            ndwi_pred = round(float(ndwi_result["prediction"][i]), 4)

            forecast_rows.append({
                "date": d.strftime("%Y-%m-%d"),
                "day_name": d.strftime("%A"),
                "ndvi_pred": ndvi_pred,
                "ndwi_pred": ndwi_pred,
                "ndvi_change": round(ndvi_pred - last_ndvi, 4) if last_ndvi is not None else None,
                "ndwi_change": round(ndwi_pred - last_ndwi, 4) if last_ndwi is not None else None
            })

        insight = build_ndvi_ndwi_insight(
            forecast_rows=forecast_rows,
            last_ndvi=last_ndvi,
            last_ndwi=last_ndwi
        )

        return jsonify({
            "mode": "arima_ndvi_ndwi_forecast",
            "model_info": {
                "model": "ARIMA / SARIMA",
                "algorithm": "SARIMAX(order=(2,1,2), seasonal_order=(1,0,1,7))",
                "database": "No database",
                "data_source": "data/weather_training.csv",
                "internal_mae": {
                    "NDVI": ndvi_result["internal_mae"],
                    "NDWI": ndwi_result["internal_mae"]
                }
            },
            "location": {
                "country": country,
                "province": province
            },
            "analysis_period": {
                "year": year,
                "month": month,
                "start_7days": forecast_rows[0]["date"],
                "end_7days": forecast_rows[-1]["date"]
            },
            "last_observed": {
                "ndvi": last_ndvi,
                "ndwi": last_ndwi
            },
            "forecast": forecast_rows,
            "insight": insight,
            "source": {
                "provider": "Local CSV + ARIMA",
                "type": "NDVI/NDWI time-series forecast",
                "note": "Mô hình học từ CSV local, không dùng database."
            }
        })

    except Exception as e:
        return jsonify({
            "error": "arima ndvi ndwi forecast failed",
            "detail": str(e)
        }), 500
# ============================================
# RUN SERVER
# ============================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        use_reloader=False
    )

    
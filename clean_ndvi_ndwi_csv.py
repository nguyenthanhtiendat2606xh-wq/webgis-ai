import os
import glob
import numpy as np
import pandas as pd

RAW_DIR = "data/raw"
OUT_FILE = "data/weather_training.csv"
REPORT_FILE = "data/ndvi_ndwi_clean_report.csv"
REGION_REPORT_FILE = "data/region_quality_report.csv"

REQUIRED_COLUMNS = [
    "date",
    "country",
    "province",
    "lat",
    "lon",
    "year",
    "month",
    "day",
    "ndvi",
    "ndwi"
]

NUMERIC_COLUMNS = [
    "lat",
    "lon",
    "year",
    "month",
    "day",
    "ndvi",
    "ndwi"
]


def normalize_text(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def main():
    os.makedirs("data", exist_ok=True)

    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.csv")))

    if not files:
        raise FileNotFoundError(
            f"Không tìm thấy file CSV trong {RAW_DIR}. "
            f"Hãy bỏ các file CSV tháng vào thư mục data/raw."
        )

    print("======================================")
    print("ĐANG ĐỌC FILE CSV")
    print("======================================")
    print(f"Số file tìm thấy: {len(files)}")

    frames = []

    for file in files:
        print("Reading:", file)
        df = pd.read_csv(file)

        missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            raise ValueError(
                f"File {file} thiếu cột: {missing_cols}"
            )

        df = df[REQUIRED_COLUMNS].copy()
        df["source_file"] = os.path.basename(file)
        frames.append(df)

    data = pd.concat(frames, ignore_index=True)

    print("\n======================================")
    print("THÔNG TIN TRƯỚC KHI LỌC")
    print("======================================")
    print("Tổng dòng thô:", len(data))
    print("Tổng file:", len(files))

    # Chuẩn hóa text
    data["country"] = data["country"].apply(normalize_text)
    data["province"] = data["province"].apply(normalize_text)

    # Chuẩn hóa ngày
    data["date"] = pd.to_datetime(data["date"], errors="coerce")

    # Chuyển số
    for col in NUMERIC_COLUMNS:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    # Thay mã thiếu dữ liệu
    data = data.replace(-9999, np.nan)

    # Loại dòng thiếu định danh
    data = data.dropna(subset=["date"])
    data = data[
        (data["country"] != "") &
        (data["province"] != "")
    ].copy()

    # Lọc NDVI/NDWI hợp lệ trong khoảng [-1, 1]
    data.loc[(data["ndvi"] < -1) | (data["ndvi"] > 1), "ndvi"] = np.nan
    data.loc[(data["ndwi"] < -1) | (data["ndwi"] > 1), "ndwi"] = np.nan

    # Nếu bị trùng date + country + province thì gộp trung bình
    before_dup = len(data)

    clean = (
        data
        .groupby(["date", "country", "province"], as_index=False)
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

    after_dup = len(clean)
    duplicate_removed = before_dup - after_dup

    # Sắp xếp
    clean = clean.sort_values(["country", "province", "date"]).reset_index(drop=True)

    # Điền year/month/day lại từ date cho chắc
    clean["year"] = clean["date"].dt.year
    clean["month"] = clean["date"].dt.month
    clean["day"] = clean["date"].dt.day

    # Tạo report theo file gốc
    report_rows = []

    for file in files:
        name = os.path.basename(file)
        sub = data[data["source_file"] == name].copy()

        report_rows.append({
            "file": name,
            "raw_rows_after_basic_clean": len(sub),
            "countries": sub["country"].nunique(),
            "provinces": sub[["country", "province"]].drop_duplicates().shape[0],
            "start_date": sub["date"].min(),
            "end_date": sub["date"].max(),
            "days": sub["date"].nunique(),
            "ndvi_missing_percent": round(sub["ndvi"].isna().mean() * 100, 2),
            "ndwi_missing_percent": round(sub["ndwi"].isna().mean() * 100, 2),
            "valid_ndvi_ndwi_rows": int(sub.dropna(subset=["ndvi", "ndwi"]).shape[0])
        })

    report = pd.DataFrame(report_rows)
    report.to_csv(REPORT_FILE, index=False, encoding="utf-8-sig")

    # Report theo vùng
    region_quality = (
        clean
        .groupby(["country", "province"], as_index=False)
        .agg(
            rows=("date", "count"),
            unique_days=("date", "nunique"),
            start_date=("date", "min"),
            end_date=("date", "max"),
            ndvi_missing=("ndvi", lambda x: x.isna().sum()),
            ndwi_missing=("ndwi", lambda x: x.isna().sum()),
            ndvi_mean=("ndvi", "mean"),
            ndwi_mean=("ndwi", "mean"),
            lat=("lat", "mean"),
            lon=("lon", "mean")
        )
    )

    region_quality["ndvi_missing_percent"] = (
        region_quality["ndvi_missing"] / region_quality["rows"] * 100
    ).round(2)

    region_quality["ndwi_missing_percent"] = (
        region_quality["ndwi_missing"] / region_quality["rows"] * 100
    ).round(2)

    region_quality["valid_days_both"] = region_quality["rows"] - np.maximum(
        region_quality["ndvi_missing"],
        region_quality["ndwi_missing"]
    )

    region_quality["quality"] = np.where(
        region_quality["valid_days_both"] >= 300,
        "GOOD_FOR_ARIMA_1YEAR",
        np.where(
            region_quality["valid_days_both"] >= 180,
            "OK_BUT_SHOULD_DOWNLOAD_MORE",
            np.where(
                region_quality["valid_days_both"] >= 90,
                "WEAK_TEST_ONLY",
                "NOT_ENOUGH"
            )
        )
    )

    region_quality = region_quality.sort_values(
        ["quality", "valid_days_both"],
        ascending=[True, False]
    )

    region_quality.to_csv(REGION_REPORT_FILE, index=False, encoding="utf-8-sig")

    # Lưu file clean cho WebGIS/ARIMA
    clean.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

    print("\n======================================")
    print("KẾT QUẢ SAU KHI GỘP + LỌC")
    print("======================================")
    print("Tổng dòng sau clean:", len(clean))
    print("Dòng trùng đã gộp:", duplicate_removed)
    print("Số quốc gia:", clean["country"].nunique())
    print("Số province:", clean[["country", "province"]].drop_duplicates().shape[0])
    print("Khoảng ngày:", clean["date"].min(), "→", clean["date"].max())
    print("Số ngày:", clean["date"].nunique())
    print("NDVI thiếu:", round(clean["ndvi"].isna().mean() * 100, 2), "%")
    print("NDWI thiếu:", round(clean["ndwi"].isna().mean() * 100, 2), "%")

    print("\nĐã tạo:")
    print("-", OUT_FILE)
    print("-", REPORT_FILE)
    print("-", REGION_REPORT_FILE)

    print("\n======================================")
    print("ĐÁNH GIÁ NHANH")
    print("======================================")

    total_days = clean["date"].nunique()

    if total_days >= 365:
        print("✅ Dữ liệu 1 năm: có thể test ARIMA khá ổn.")
    elif total_days >= 180:
        print("⚠️ Dữ liệu khoảng nửa năm: test được, nhưng nên tải thêm.")
    elif total_days >= 90:
        print("⚠️ Dữ liệu trên 3 tháng: đủ test kỹ thuật ARIMA, chưa đủ kết luận tốt.")
    else:
        print("❌ Dữ liệu còn ngắn: chỉ test pipeline, chưa nên đánh giá mô hình.")

    good_regions = region_quality[
        region_quality["valid_days_both"] >= max(45, int(total_days * 0.7))
    ]

    print("Số vùng có dữ liệu tương đối đủ:", len(good_regions))

    if len(good_regions) > 0:
        print("\nVí dụ 10 vùng dữ liệu tốt:")
        print(
            good_regions[
                [
                    "country",
                    "province",
                    "valid_days_both",
                    "ndvi_missing_percent",
                    "ndwi_missing_percent"
                ]
            ].head(10).to_string(index=False)
        )


if __name__ == "__main__":
    main()
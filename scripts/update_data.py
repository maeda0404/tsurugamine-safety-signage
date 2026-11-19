#!/usr/bin/env python3
"""鶴ヶ峰安全サイネージ用データ生成。

天気データ:
    Open-Meteo

警報・注意報:
    気象庁 VPWS50 集約通報
    横浜市北部の項目だけを判定する
"""

import gzip
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


# =========================================================
# 基本設定
# =========================================================

LAT = 35.474917
LON = 139.549250

OUT = Path(__file__).resolve().parents[1] / "data" / "current.json"

WEATHER = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&current=temperature_2m,precipitation,weather_code,wind_speed_10m"
    "&hourly=precipitation_probability"
    "&daily=temperature_2m_min,sunset"
    "&timezone=Asia%2FTokyo"
    "&forecast_days=2"
)

FORECAST = (
    "https://www.jma.go.jp/bosai/forecast/data/forecast/140000.json"
)

EXTRA_FEED = (
    "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"
)

REGULAR_FEED = (
    "https://www.data.jma.go.jp/developer/xml/feed/regular.xml"
)

# 天気予報で使用する神奈川県東部のコード
FORECAST_AREA_CODE = "140010"

# 鶴ヶ峰が属する警報・注意報の対象地域
#
# 重要:
# 「東部」「神奈川県東部」「140010」は指定しない。
# 指定すると、横浜市北部以外の警報まで拾う可能性がある。
TARGET_WARNING_NAMES = {
    "横浜市北部",
}

DEFAULT_WARNINGS = {
    "dry": False,
    "thunder": False,
    "thunderForecast": False,
    "heavyRain": False,
    "landslide": False,
    "landslideAdvisory": False,
    "storm": False,
}

# VPWS50が取得できない場合だけ使用する個別電文
INDIVIDUAL_TYPES = {
    "VPWW55": ("heavyRain",),
    "VPWW56": ("landslide", "landslideAdvisory"),
    "VPWW58": ("storm",),
    "VPWW61": ("thunder", "dry"),
    "VXWW50": ("landslide",),
}

ALL_OFFICIAL_FLAGS = (
    "dry",
    "thunder",
    "heavyRain",
    "landslide",
    "landslideAdvisory",
    "storm",
)

DATA_URL_RE = re.compile(
    r"https://www\.data\.jma\.go\.jp/developer/xml/data/"
    r"(\d{14})_\d+_([A-Z0-9]+)_(\d{6})\.xml"
)


# =========================================================
# 通信処理
# =========================================================

def request_bytes(url):
    """URLからデータを取得する。gzip圧縮にも対応。"""

    sep = "&" if "?" in url else "?"

    req = urllib.request.Request(
        url + f"{sep}_={int(datetime.now(timezone.utc).timestamp())}",
        headers={
            "User-Agent": "tsurugamine-safety-signage/4.1",
            "Accept": "application/xml,application/json,*/*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )

    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read()

    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)

    return raw


def get_json(url):
    return json.loads(request_bytes(url).decode("utf-8"))


# =========================================================
# XML共通処理
# =========================================================

def lname(element):
    """XML名前空間を除いたタグ名を返す。"""

    return element.tag.rsplit("}", 1)[-1]


def child_text(element, local_name):
    """直下の子要素から文字列を取得する。"""

    for child in element:
        if lname(child) == local_name:
            return (child.text or "").strip()

    return ""


def latest_url(feed_bytes, message_type, area_code):
    """フィード内から指定電文の最新版URLを取得する。"""

    text = feed_bytes.decode("utf-8", errors="replace")

    found = [
        (
            timestamp,
            (
                "https://www.data.jma.go.jp/developer/xml/data/"
                f"{timestamp}_0_{xml_type}_{area}.xml"
            ),
        )
        for timestamp, xml_type, area in DATA_URL_RE.findall(text)
        if xml_type == message_type and area == area_code
    ]

    return max(found, key=lambda value: value[0]) if found else None


def latest_individual_urls(feed_bytes):
    """個別電文の種類ごとに最新版URLを取得する。"""

    text = feed_bytes.decode("utf-8", errors="replace")
    latest = {}

    for timestamp, xml_type, area in DATA_URL_RE.findall(text):
        if area != "140000":
            continue

        if xml_type not in INDIVIDUAL_TYPES:
            continue

        if (
            xml_type not in latest
            or timestamp > latest[xml_type][0]
        ):
            latest[xml_type] = (
                timestamp,
                (
                    "https://www.data.jma.go.jp/developer/xml/data/"
                    f"{timestamp}_0_{xml_type}_{area}.xml"
                ),
            )

    return latest


# =========================================================
# 対象地域判定
# =========================================================

def get_item_area_names(item):
    """Item内に含まれるArea名をすべて取得する。"""

    area_names = []

    for element in item.iter():
        if lname(element) != "Area":
            continue

        name = child_text(element, "Name")

        if name:
            area_names.append(name)

    return area_names


def area_matches(item):
    """横浜市北部のItemだけを対象にする。"""

    area_names = get_item_area_names(item)

    return any(
        name in TARGET_WARNING_NAMES
        for name in area_names
    )


# =========================================================
# 警報名からサイネージ用フラグへ変換
# =========================================================

def name_to_flags(name):
    flags = set()

    if "雷" in name:
        flags.add("thunder")

    if "乾燥" in name:
        flags.add("dry")

    if "土砂災害" in name:
        if "注意報" in name and "警報" not in name:
            flags.add("landslideAdvisory")
        else:
            flags.add("landslide")

    if "大雨" in name and "警報" in name:
        flags.add("heavyRain")

    # 「強風注意報」ではstormを立てない。
    # 「暴風警報」または「暴風特別警報」だけ対象。
    if "暴風" in name and "警報" in name:
        flags.add("storm")

    return flags


def parse_warning_xml(
    xml_bytes,
    allowed_flags,
    allow_empty_status=False,
):
    """対象地域の有効な警報・注意報フラグを返す。"""

    root = ET.fromstring(xml_bytes)

    report_datetime = ""

    for element in root.iter():
        if lname(element) == "ReportDateTime" and element.text:
            report_datetime = element.text.strip()
            break

    active_flags = set()
    active_names = []
    matched_areas = []

    inactive_statuses = {
        "解除",
        "なし",
        "発表警報・注意報はなし",
        "警報・注意報はなし",
    }

    for item in (
        element
        for element in root.iter()
        if lname(element) == "Item"
    ):
        if not area_matches(item):
            continue

        item_area_names = get_item_area_names(item)

        for area_name in item_area_names:
            if area_name not in matched_areas:
                matched_areas.append(area_name)

        for kind in (
            element
            for element in item.iter()
            if lname(element) == "Kind"
        ):
            name = child_text(kind, "Name")
            status = child_text(kind, "Status")

            if not name:
                continue

            if status in inactive_statuses:
                continue

            # VPWS50ではStatusが空の場合があるため、
            # VPWS50だけ空Statusを有効として扱う。
            if not status and not allow_empty_status:
                continue

            flags = name_to_flags(name) & set(allowed_flags)

            if flags:
                active_flags |= flags

                if name not in active_names:
                    active_names.append(name)

    return (
        active_flags,
        active_names,
        report_datetime,
        matched_areas,
    )


# =========================================================
# 前回データ
# =========================================================

def load_previous():
    try:
        obj = json.loads(
            OUT.read_text(encoding="utf-8")
        )

        if isinstance(obj, dict):
            return obj.get("warnings", {})

    except Exception:
        pass

    return {}


# =========================================================
# 気象庁警報・注意報取得
# =========================================================

def fetch_official_warnings(previous):
    result = dict(DEFAULT_WARNINGS)

    # データ取得自体に失敗した場合に備え、
    # 最初は前回値を保持する。
    for key in result:
        if key in previous:
            result[key] = bool(previous[key])

    debug = {
        "aggregate": None,
        "reports": [],
        "error": None,
    }

    # -----------------------------------------------------
    # 主データ: VPWS50全国集約通報
    # -----------------------------------------------------

    aggregate = latest_url(
        request_bytes(REGULAR_FEED),
        "VPWS50",
        "010000",
    )

    if aggregate:
        _, url = aggregate

        (
            flags,
            names,
            report_datetime,
            matched_areas,
        ) = parse_warning_xml(
            request_bytes(url),
            ALL_OFFICIAL_FLAGS,
            allow_empty_status=True,
        )

        # VPWS50取得成功時は、前回値を残さず現在値で確定する。
        for key in ALL_OFFICIAL_FLAGS:
            result[key] = key in flags

        debug["aggregate"] = {
            "type": "VPWS50",
            "reportDateTime": report_datetime,
            "targetNames": sorted(TARGET_WARNING_NAMES),
            "matchedAreas": matched_areas,
            "activeNames": names,
            "sourceFile": url.rsplit("/", 1)[-1],
            "emptyStatusAccepted": True,
            "resultLocked": True,
        }

        # VPWS50成功時は古い個別電文で上書きしない。
        return result, debug

    # -----------------------------------------------------
    # VPWS50取得失敗時のみ個別電文を使用
    # -----------------------------------------------------

    debug["aggregate"] = {
        "error": "VPWS50_NOT_FOUND",
    }

    try:
        latest = latest_individual_urls(
            request_bytes(EXTRA_FEED)
        )

        if not latest:
            debug["error"] = (
                "NO_INDIVIDUAL_REPORTS_KEEP_PREVIOUS"
            )
            return result, debug

        for xml_type, (_, url) in sorted(latest.items()):
            allowed_flags = INDIVIDUAL_TYPES[xml_type]

            (
                flags,
                names,
                report_datetime,
                matched_areas,
            ) = parse_warning_xml(
                request_bytes(url),
                allowed_flags,
                allow_empty_status=False,
            )

            for key in allowed_flags:
                result[key] = key in flags

            debug["reports"].append(
                {
                    "type": xml_type,
                    "reportDateTime": report_datetime,
                    "matchedAreas": matched_areas,
                    "activeNames": names,
                    "sourceFile": url.rsplit("/", 1)[-1],
                }
            )

    except Exception as exc:
        debug["error"] = (
            f"{type(exc).__name__}: {exc}"
        )

    return result, debug


# =========================================================
# 天気予報文の雷判定
# =========================================================

def detect_forecast_thunder(forecast):
    """神奈川県東部の予報文に「雷」があるか確認する。"""

    try:
        for block in forecast:
            for series in block.get("timeSeries", []):
                for area in series.get("areas", []):
                    area_code = (
                        area.get("area", {}).get("code")
                    )

                    if area_code != FORECAST_AREA_CODE:
                        continue

                    weathers = area.get("weathers", []) or []

                    if any(
                        "雷" in weather_text
                        for weather_text in weathers
                    ):
                        return True

    except Exception:
        pass

    return False


# =========================================================
# メイン処理
# =========================================================

def main():
    weather_data = get_json(WEATHER)

    current = weather_data["current"]
    hourly = weather_data["hourly"]
    daily = weather_data["daily"]

    observed = current["time"]
    hour_key = observed[:13] + ":00"

    if hour_key in hourly["time"]:
        hourly_index = hourly["time"].index(hour_key)
    else:
        hourly_index = 0

    previous = load_previous()

    try:
        warnings, debug = fetch_official_warnings(previous)

    except Exception as exc:
        warnings = dict(DEFAULT_WARNINGS)

        # 通信障害などの場合だけ前回値を保持する。
        for key in warnings:
            if key in previous:
                warnings[key] = bool(previous[key])

        debug = {
            "aggregate": None,
            "reports": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    # 正式な雷注意報がない場合だけ、
    # 天気予報文の「雷」を補助表示に使用する。
    if warnings.get("thunder"):
        warnings["thunderForecast"] = False

    else:
        try:
            forecast_data = get_json(FORECAST)

            warnings["thunderForecast"] = (
                detect_forecast_thunder(forecast_data)
            )

        except Exception:
            warnings["thunderForecast"] = bool(
                previous.get("thunderForecast", False)
            )

    rain_probability = (
        hourly["precipitation_probability"][hourly_index]
    )

    if rain_probability is None:
        rain_probability = 0

    payload = {
        "schemaVersion": 1,
        "generatedAt": (
            datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "location": {
            "name": "鶴ヶ峰",
            "latitude": LAT,
            "longitude": LON,
        },
        "weather": {
            "observedAt": observed,
            "temperature": current["temperature_2m"],
            "precipitation": current["precipitation"],
            "weatherCode": current["weather_code"],
            "windSpeed": current["wind_speed_10m"],
            "rainProbability": rain_probability,
            "minTemperature": daily[
                "temperature_2m_min"
            ][0],
            "sunset": daily["sunset"][0],
        },
        "warnings": warnings,
        "warningSource": "jma-vpws50-yokohama-north",
        "warningDebug": debug,
    }

    OUT.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUT.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        "updated",
        OUT,
        "/ warnings:",
        warnings,
        "/ debug:",
        debug,
    )


if __name__ == "__main__":
    main()

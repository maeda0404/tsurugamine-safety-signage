#!/usr/bin/env python3
"""鶴ヶ峰安全サイネージ用データ生成。

天気データはOpen-Meteo、警報・注意報は気象庁VPWS50を使用する。
警報判定は鶴ヶ峰が属する「横浜市北部」に限定する。
「強風注意報」と「暴風警報」は別フラグとして出力する。
"""

import gzip
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


LAT = 35.474917
LON = 139.549250

OUT = Path(__file__).resolve().parents[1] / "data" / "current.json"

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&current=temperature_2m,precipitation,weather_code,wind_speed_10m"
    "&hourly=precipitation_probability"
    "&daily=temperature_2m_min,sunset"
    "&timezone=Asia%2FTokyo"
    "&forecast_days=2"
)

FORECAST_URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/140000.json"
REGULAR_FEED_URL = "https://www.data.jma.go.jp/developer/xml/feed/regular.xml"
EXTRA_FEED_URL = "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"

# 天気予報文の補助判定用。神奈川県東部。
FORECAST_AREA_CODE = "140010"

# 鶴ヶ峰の警報・注意報判定対象。
# 「東部」「神奈川県東部」「140010」は指定しない。
TARGET_WARNING_NAMES = {"横浜市北部"}

DEFAULT_WARNINGS = {
    "dry": False,
    "thunder": False,
    "thunderForecast": False,
    "heavyRain": False,
    "landslide": False,
    "landslideAdvisory": False,
    "strongWind": False,
    "storm": False,
}

OFFICIAL_FLAGS = (
    "dry",
    "thunder",
    "heavyRain",
    "landslide",
    "landslideAdvisory",
    "strongWind",
    "storm",
)

# VPWS50が取得できない場合のみ使用する個別電文。
# 強風注意報と暴風警報は同じ風系電文から名称で振り分ける。
INDIVIDUAL_TYPES = {
    "VPWW55": ("heavyRain",),
    "VPWW56": ("landslide", "landslideAdvisory"),
    "VPWW58": ("strongWind", "storm"),
    "VPWW61": ("thunder", "dry"),
    "VXWW50": ("landslide",),
}

DATA_URL_RE = re.compile(
    r"https://www\.data\.jma\.go\.jp/developer/xml/data/"
    r"(\d{14})_\d+_([A-Z0-9]+)_(\d{6})\.xml"
)


def request_bytes(url):
    """キャッシュ回避付きでURLを取得し、gzipなら展開する。"""
    separator = "&" if "?" in url else "?"
    cache_buster = int(datetime.now(timezone.utc).timestamp())

    request = urllib.request.Request(
        f"{url}{separator}_={cache_buster}",
        headers={
            "User-Agent": "tsurugamine-safety-signage/4.2",
            "Accept": "application/xml,application/json,*/*",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()

    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def get_json(url):
    return json.loads(request_bytes(url).decode("utf-8"))


def local_name(element):
    """XML名前空間を除いたタグ名を返す。"""
    return element.tag.rsplit("}", 1)[-1]


def child_text(element, wanted_name):
    """直下の子要素から文字列を取得する。"""
    for child in element:
        if local_name(child) == wanted_name:
            return (child.text or "").strip()
    return ""


def latest_feed_url(feed_bytes, message_type, area_code):
    """フィードから指定形式・地域コードの最新電文URLを返す。"""
    feed_text = feed_bytes.decode("utf-8", errors="replace")
    matches = []

    for timestamp, xml_type, area in DATA_URL_RE.findall(feed_text):
        if xml_type != message_type or area != area_code:
            continue

        url = (
            "https://www.data.jma.go.jp/developer/xml/data/"
            f"{timestamp}_0_{xml_type}_{area}.xml"
        )
        matches.append((timestamp, url))

    return max(matches, key=lambda value: value[0]) if matches else None


def latest_individual_urls(feed_bytes):
    """神奈川県の個別電文を種類ごとに1件取得する。"""
    feed_text = feed_bytes.decode("utf-8", errors="replace")
    latest = {}

    for timestamp, xml_type, area in DATA_URL_RE.findall(feed_text):
        if area != "140000" or xml_type not in INDIVIDUAL_TYPES:
            continue

        if xml_type not in latest or timestamp > latest[xml_type][0]:
            url = (
                "https://www.data.jma.go.jp/developer/xml/data/"
                f"{timestamp}_0_{xml_type}_{area}.xml"
            )
            latest[xml_type] = (timestamp, url)

    return latest


def item_area_names(item):
    """Item内に含まれるArea名を重複なしで返す。"""
    names = []

    for element in item.iter():
        if local_name(element) != "Area":
            continue

        name = child_text(element, "Name")
        if name and name not in names:
            names.append(name)

    return names


def area_matches(item):
    """Itemが横浜市北部の情報か判定する。"""
    return any(name in TARGET_WARNING_NAMES for name in item_area_names(item))


def warning_name_to_flags(name):
    """気象庁の警報・注意報名をサイネージ用フラグに変換する。"""
    flags = set()

    if "雷" in name and "注意報" in name:
        flags.add("thunder")

    if "乾燥" in name and "注意報" in name:
        flags.add("dry")

    if "土砂災害" in name:
        if "注意報" in name and "警報" not in name:
            flags.add("landslideAdvisory")
        elif "警報" in name:
            flags.add("landslide")

    if "大雨" in name and "警報" in name:
        flags.add("heavyRain")

    # 強風注意報と暴風警報を必ず別々に扱う。
    if "強風" in name and "注意報" in name:
        flags.add("strongWind")

    if "暴風" in name and "警報" in name:
        flags.add("storm")

    return flags


def parse_warning_xml(xml_bytes, allowed_flags, allow_empty_status=False):
    """横浜市北部の有効な警報・注意報だけを解析する。"""
    root = ET.fromstring(xml_bytes)
    report_datetime = ""

    for element in root.iter():
        if local_name(element) == "ReportDateTime" and element.text:
            report_datetime = element.text.strip()
            break

    inactive_statuses = {
        "解除",
        "なし",
        "発表警報・注意報はなし",
        "警報・注意報はなし",
    }

    allowed = set(allowed_flags)
    active_flags = set()
    active_names = []
    matched_areas = []

    for item in (element for element in root.iter() if local_name(element) == "Item"):
        if not area_matches(item):
            continue

        for area_name in item_area_names(item):
            if area_name in TARGET_WARNING_NAMES and area_name not in matched_areas:
                matched_areas.append(area_name)

        for kind in (element for element in item.iter() if local_name(element) == "Kind"):
            name = child_text(kind, "Name")
            status = child_text(kind, "Status")

            if not name or status in inactive_statuses:
                continue

            # VPWS50ではStatusが空のKindがあるため、VPWS50だけ許可する。
            if not status and not allow_empty_status:
                continue

            flags = warning_name_to_flags(name) & allowed
            if not flags:
                continue

            active_flags.update(flags)
            if name not in active_names:
                active_names.append(name)

    return active_flags, active_names, report_datetime, matched_areas


def load_previous_warnings():
    """通信失敗時の保持用に前回値を読む。"""
    try:
        payload = json.loads(OUT.read_text(encoding="utf-8"))
        warnings = payload.get("warnings", {})
        return warnings if isinstance(warnings, dict) else {}
    except Exception:
        return {}


def previous_or_default(previous):
    result = dict(DEFAULT_WARNINGS)
    for key in result:
        if key in previous:
            result[key] = bool(previous[key])
    return result


def fetch_official_warnings(previous):
    """VPWS50を優先し、失敗時のみ個別電文を使う。"""
    result = previous_or_default(previous)
    debug = {"aggregate": None, "reports": [], "error": None}

    aggregate = latest_feed_url(
        request_bytes(REGULAR_FEED_URL),
        "VPWS50",
        "010000",
    )

    if aggregate:
        _, url = aggregate
        flags, names, report_datetime, matched_areas = parse_warning_xml(
            request_bytes(url),
            OFFICIAL_FLAGS,
            allow_empty_status=True,
        )

        # 取得成功時は前回値を残さず、最新電文で全フラグを確定する。
        for key in OFFICIAL_FLAGS:
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
        return result, debug

    debug["aggregate"] = {"error": "VPWS50_NOT_FOUND"}

    try:
        latest = latest_individual_urls(request_bytes(EXTRA_FEED_URL))
        if not latest:
            debug["error"] = "NO_INDIVIDUAL_REPORTS_KEEP_PREVIOUS"
            return result, debug

        for xml_type, (_, url) in sorted(latest.items()):
            allowed = INDIVIDUAL_TYPES[xml_type]
            flags, names, report_datetime, matched_areas = parse_warning_xml(
                request_bytes(url),
                allowed,
                allow_empty_status=False,
            )

            for key in allowed:
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
        debug["error"] = f"{type(exc).__name__}: {exc}"

    return result, debug


def detect_forecast_thunder(forecast):
    """神奈川県東部の予報文に雷があるか確認する。"""
    try:
        for block in forecast:
            for series in block.get("timeSeries", []):
                for area in series.get("areas", []):
                    code = area.get("area", {}).get("code")
                    if code != FORECAST_AREA_CODE:
                        continue

                    weather_texts = area.get("weathers", []) or []
                    if any("雷" in text for text in weather_texts):
                        return True
    except Exception:
        pass

    return False


def main():
    weather_data = get_json(WEATHER_URL)
    current = weather_data["current"]
    hourly = weather_data["hourly"]
    daily = weather_data["daily"]

    observed_at = current["time"]
    current_hour = observed_at[:13] + ":00"

    try:
        hourly_index = hourly["time"].index(current_hour)
    except ValueError:
        hourly_index = 0

    previous = load_previous_warnings()

    try:
        warnings, warning_debug = fetch_official_warnings(previous)
    except Exception as exc:
        warnings = previous_or_default(previous)
        warning_debug = {
            "aggregate": None,
            "reports": [],
            "error": f"{type(exc).__name__}: {exc}",
        }

    # 正式な雷注意報がない場合だけ予報文を補助表示に使う。
    if warnings.get("thunder"):
        warnings["thunderForecast"] = False
    else:
        try:
            warnings["thunderForecast"] = detect_forecast_thunder(
                get_json(FORECAST_URL)
            )
        except Exception:
            warnings["thunderForecast"] = bool(
                previous.get("thunderForecast", False)
            )

    rain_probability = hourly["precipitation_probability"][hourly_index]
    if rain_probability is None:
        rain_probability = 0

    payload = {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "location": {
            "name": "鶴ヶ峰",
            "latitude": LAT,
            "longitude": LON,
        },
        "weather": {
            "observedAt": observed_at,
            "temperature": current["temperature_2m"],
            "precipitation": current["precipitation"],
            "weatherCode": current["weather_code"],
            "windSpeed": current["wind_speed_10m"],
            "rainProbability": rain_probability,
            "minTemperature": daily["temperature_2m_min"][0],
            "sunset": daily["sunset"][0],
        },
        "warnings": warnings,
        "warningSource": "jma-vpws50-yokohama-north",
        "warningDebug": warning_debug,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    print(
        "updated",
        OUT,
        "/ warnings:",
        warnings,
        "/ debug:",
        warning_debug,
    )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""鶴ヶ峰安全サイネージ用データ生成。VPWS50を主データに利用する修正版。"""
import gzip
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

LAT, LON = 35.474917, 139.549250
OUT = Path(__file__).resolve().parents[1] / 'data' / 'current.json'
WEATHER = ('https://api.open-meteo.com/v1/forecast'
           f'?latitude={LAT}&longitude={LON}'
           '&current=temperature_2m,precipitation,weather_code,wind_speed_10m'
           '&hourly=precipitation_probability&daily=temperature_2m_min,sunset'
           '&timezone=Asia%2FTokyo&forecast_days=2')
FORECAST = 'https://www.jma.go.jp/bosai/forecast/data/forecast/140000.json'
EXTRA_FEED = 'https://www.data.jma.go.jp/developer/xml/feed/extra.xml'
REGULAR_FEED = 'https://www.data.jma.go.jp/developer/xml/feed/regular.xml'
FORECAST_AREA_CODE = '140010'
TARGET_CODES = {'140010'}  # 神奈川県東部。VPWS50はこの単位で収録
TARGET_NAMES = {'東部', '神奈川県東部'}

DEFAULT_WARNINGS = {
    'dry': False, 'thunder': False, 'thunderForecast': False,
    'heavyRain': False, 'landslide': False,
    'landslideAdvisory': False, 'storm': False,
}

# VPWS50が取れない場合だけ使用する個別電文
INDIVIDUAL_TYPES = {
    'VPWW55': ('heavyRain',),
    'VPWW56': ('landslide', 'landslideAdvisory'),
    'VPWW58': ('storm',),
    'VPWW61': ('thunder', 'dry'),
    'VXWW50': ('landslide',),
}
ALL_OFFICIAL_FLAGS = ('dry', 'thunder', 'heavyRain', 'landslide',
                      'landslideAdvisory', 'storm')
DATA_URL_RE = re.compile(
    r'https://www\.data\.jma\.go\.jp/developer/xml/data/'
    r'(\d{14})_\d+_([A-Z0-9]+)_(\d{6})\.xml')


def request_bytes(url):
    sep = '&' if '?' in url else '?'
    req = urllib.request.Request(
        url + f'{sep}_={int(datetime.now(timezone.utc).timestamp())}',
        headers={'User-Agent': 'tsurugamine-safety-signage/4.0',
                 'Accept': 'application/xml,application/json,*/*',
                 'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read()
    return gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw


def get_json(url):
    return json.loads(request_bytes(url).decode('utf-8'))


def lname(el):
    return el.tag.rsplit('}', 1)[-1]


def child_text(el, local_name):
    for child in el:
        if lname(child) == local_name:
            return (child.text or '').strip()
    return ''


def latest_url(feed_bytes, message_type, area_code):
    text = feed_bytes.decode('utf-8', errors='replace')
    found = [(ts, f'https://www.data.jma.go.jp/developer/xml/data/'
                  f'{ts}_0_{typ}_{area}.xml')
             for ts, typ, area in DATA_URL_RE.findall(text)
             if typ == message_type and area == area_code]
    return max(found, key=lambda x: x[0]) if found else None


def latest_individual_urls(feed_bytes):
    text = feed_bytes.decode('utf-8', errors='replace')
    latest = {}
    for ts, typ, area in DATA_URL_RE.findall(text):
        if area != '140000' or typ not in INDIVIDUAL_TYPES:
            continue
        if typ not in latest or ts > latest[typ][0]:
            latest[typ] = (ts, f'https://www.data.jma.go.jp/developer/xml/data/'
                                f'{ts}_0_{typ}_{area}.xml')
    return latest


def area_matches(item):
    for el in item.iter():
        if lname(el) != 'Area':
            continue
        name, code = child_text(el, 'Name'), child_text(el, 'Code')
        if code in TARGET_CODES or name in TARGET_NAMES:
            return True
    return False


def name_to_flags(name):
    flags = set()
    if '雷' in name:
        flags.add('thunder')
    if '乾燥' in name:
        flags.add('dry')
    if '土砂災害' in name:
        if '注意報' in name and '警報' not in name:
            flags.add('landslideAdvisory')
        else:
            flags.add('landslide')
    if '大雨' in name and '警報' in name:
        flags.add('heavyRain')
    if '暴風' in name and '警報' in name:
        flags.add('storm')
    return flags


def parse_warning_xml(xml_bytes, allowed_flags, allow_empty_status=False):
    """対象地域の有効フラグを返す。VPWS50では空Statusを有効として扱う。"""
    root = ET.fromstring(xml_bytes)
    report_dt = ''
    for el in root.iter():
        if lname(el) == 'ReportDateTime' and el.text:
            report_dt = el.text.strip()
            break

    active_flags, active_names = set(), []
    inactive = {'解除', 'なし', '発表警報・注意報はなし', '警報・注意報はなし'}
    for item in (el for el in root.iter() if lname(el) == 'Item'):
        if not area_matches(item):
            continue
        for kind in (el for el in item.iter() if lname(el) == 'Kind'):
            name = child_text(kind, 'Name')
            status = child_text(kind, 'Status')
            if not name or status in inactive:
                continue
            # 個別電文では空Statusを採用しない。VPWS50だけ採用する。
            if not status and not allow_empty_status:
                continue
            flags = name_to_flags(name) & set(allowed_flags)
            if flags:
                active_flags |= flags
                active_names.append(name)
    return active_flags, active_names, report_dt


def load_previous():
    try:
        obj = json.loads(OUT.read_text(encoding='utf-8'))
        return obj.get('warnings', {}) if isinstance(obj, dict) else {}
    except Exception:
        return {}


def fetch_official_warnings(previous):
    result = dict(DEFAULT_WARNINGS)
    for key in result:
        if key in previous:
            result[key] = bool(previous[key])
    debug = {'aggregate': None, 'reports': [], 'error': None}

    # 主データ。取得できたら、この結果を確定値として返す。
    aggregate = latest_url(request_bytes(REGULAR_FEED), 'VPWS50', '010000')
    if aggregate:
        _, url = aggregate
        flags, names, report_dt = parse_warning_xml(
            request_bytes(url), ALL_OFFICIAL_FLAGS, allow_empty_status=True)
        for key in ALL_OFFICIAL_FLAGS:
            result[key] = key in flags
        debug['aggregate'] = {
            'type': 'VPWS50', 'reportDateTime': report_dt,
            'activeNames': names,
            'sourceFile': url.rsplit('/', 1)[-1],
            'emptyStatusAccepted': True,
            'resultLocked': True,
        }
        # 重要: VPWS50成功時は古い個別電文で上書きしない。
        return result, debug

    # VPWS50が取得できなかった場合のみ個別電文へフォールバック。
    debug['aggregate'] = {'error': 'VPWS50_NOT_FOUND'}
    try:
        latest = latest_individual_urls(request_bytes(EXTRA_FEED))
        if not latest:
            debug['error'] = 'NO_INDIVIDUAL_REPORTS_KEEP_PREVIOUS'
            return result, debug
        for typ, (_, url) in sorted(latest.items()):
            allowed = INDIVIDUAL_TYPES[typ]
            flags, names, report_dt = parse_warning_xml(
                request_bytes(url), allowed, allow_empty_status=False)
            for key in allowed:
                result[key] = key in flags
            debug['reports'].append({
                'type': typ, 'reportDateTime': report_dt,
                'activeNames': names,
            })
    except Exception as exc:
        debug['error'] = type(exc).__name__
    return result, debug


def detect_forecast_thunder(forecast):
    try:
        for block in forecast:
            for series in block.get('timeSeries', []):
                for area in series.get('areas', []):
                    if area.get('area', {}).get('code') != FORECAST_AREA_CODE:
                        continue
                    if any('雷' in text for text in area.get('weathers', []) or []):
                        return True
    except Exception:
        pass
    return False


def main():
    weather_data = get_json(WEATHER)
    current, hourly, daily = (weather_data['current'],
                              weather_data['hourly'], weather_data['daily'])
    observed = current['time']
    hour_key = observed[:13] + ':00'
    idx = hourly['time'].index(hour_key) if hour_key in hourly['time'] else 0

    previous = load_previous()
    try:
        warnings, debug = fetch_official_warnings(previous)
    except Exception as exc:
        warnings = dict(DEFAULT_WARNINGS)
        for key in warnings:
            if key in previous:
                warnings[key] = bool(previous[key])
        debug = {'aggregate': None, 'reports': [], 'error': type(exc).__name__}

    # 正式な雷注意報がない場合のみ、予報文を補助表示に使う。
    if warnings.get('thunder'):
        warnings['thunderForecast'] = False
    else:
        try:
            warnings['thunderForecast'] = detect_forecast_thunder(get_json(FORECAST))
        except Exception:
            warnings['thunderForecast'] = bool(previous.get('thunderForecast', False))

    payload = {
        'schemaVersion': 1,
        'generatedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'location': {'name': '鶴ヶ峰', 'latitude': LAT, 'longitude': LON},
        'weather': {
            'observedAt': observed,
            'temperature': current['temperature_2m'],
            'precipitation': current['precipitation'],
            'weatherCode': current['weather_code'],
            'windSpeed': current['wind_speed_10m'],
            'rainProbability': hourly['precipitation_probability'][idx] or 0,
            'minTemperature': daily['temperature_2m_min'][0],
            'sunset': daily['sunset'][0],
        },
        'warnings': warnings,
        'warningSource': 'jma-vpws50-primary',
        'warningDebug': debug,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n',
                   encoding='utf-8')
    print('updated', OUT, '/ warnings:', warnings, '/ debug:', debug)


if __name__ == '__main__':
    main()

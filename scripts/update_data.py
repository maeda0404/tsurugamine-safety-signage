#!/usr/bin/env python3
"""
鶴ヶ峰（横浜市旭区）安全サイネージ用データ生成スクリプト（気象庁XMLフィード版）

- 気象データ : Open-Meteo
- 警報・注意報: 気象庁防災情報XML「随時（extra.xml）」フィード
- 雷予報(補助): 気象庁府県予報 forecast/140000.json

【この版の要点】
従来の warning/140000.json は古い版（例：5月23日）を返し続けるため、
警報・注意報の取得元を気象庁防災情報XMLフィードへ切り替える。

extra.xml には、各都道府県の警報・注意報XMLが随時掲載される。
令和8年体系では災害種別ごとに電文が分かれている。
  VPWW55 : 大雨
  VPWW56 : 土砂災害
  VPWW58 : 暴風・強風
  VPWW61 : その他（雷・乾燥・濃霧 等）
  VXWW50 : 土砂災害警戒情報（警報級）
  VPWW53 / VPWW54 : 旧・気象警報注意報（移行期の予備。全要素）

警報XMLには数字コードだけでなく日本語名（例:「雷注意報」）が入るため、
名称のキーワードで判定する。従来JSON（コードのみ）より堅牢。

【解除の扱い】
各災害種別の「最新XML」はその種別の完全なスナップショット。
掲載が無い種別は、前回の current.json の値を維持する（誤って消さない）。
"""

import json
import re
import gzip
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

LAT = 35.474917
LON = 139.549250
OUT = Path(__file__).resolve().parents[1] / 'data' / 'current.json'

# 神奈川県の都道府県コード（防災情報XMLのファイル名に含まれる）
PREF_CODE = '140000'

# 随時フィード（警報・注意報が随時掲載される）
FEED_EXTRA = 'https://www.data.jma.go.jp/developer/xml/feed/extra.xml'
# 定時フィード（全要素の集約定時通報 VPWS50 が約10分ごとに掲載される）
FEED_REGULAR = 'https://www.data.jma.go.jp/developer/xml/feed/regular.xml'
# VPWS50（集約通報）は気象庁本庁が全国分を発表するため area=010000
AGGREGATE_TYPE = 'VPWS50'
AGGREGATE_AREA = '010000'
AGGREGATE_FLAGS = ('thunder', 'dry', 'heavyRain',
                   'landslide', 'landslideAdvisory', 'storm')

# Open-Meteo（気象数値）
WEATHER = (
    'https://api.open-meteo.com/v1/forecast'
    f'?latitude={LAT}&longitude={LON}'
    '&current=temperature_2m,precipitation,weather_code,wind_speed_10m'
    '&hourly=precipitation_probability'
    '&daily=temperature_2m_min,sunset'
    '&timezone=Asia%2FTokyo&forecast_days=2'
)

# 雷予報（補助判定）
JMA_FORECAST = 'https://www.jma.go.jp/bosai/forecast/data/forecast/140000.json'
FORECAST_AREA_CODE = '140010'  # 神奈川県東部

# 対象地域の判定（警報XMLは市町村単位。名称に「横浜」を含むものを対象）
TARGET_AREA_NAMES = ('横浜',)
TARGET_AREA_CODES = ('1410000', '140010')

# 電文種別 → その電文が「担当」するフラグ（掲載が無い種別は前回値を維持）
CATEGORY_TYPES = {
    'VPWW61': ('thunder', 'dry'),                     # その他（雷・乾燥 等）
    'VPWW56': ('landslide', 'landslideAdvisory'),     # 土砂災害
    'VXWW50': ('landslide',),                         # 土砂災害警戒情報（警報級）
    'VPWW55': ('heavyRain',),                         # 大雨
    'VPWW58': ('storm',),                             # 暴風・強風
    # 移行期の予備（全要素を含む旧電文）
    'VPWW53': ('thunder', 'dry', 'landslide',
               'landslideAdvisory', 'heavyRain', 'storm'),
    'VPWW54': ('thunder', 'dry', 'landslide',
               'landslideAdvisory', 'heavyRain', 'storm'),
}

# app.js が参照するフラグ一式
DEFAULT_WARNINGS = {
    'dry': False,
    'thunder': False,          # 正式な雷注意報（XML由来・即時表示）
    'thunderForecast': False,  # 予報文由来の雷（補助・00〜10分表示）
    'heavyRain': False,
    'landslide': False,
    'landslideAdvisory': False,
    'storm': False,
}

# 「無効（採用しない）」とみなす status
INACTIVE_STATUS = (
    '解除', 'なし', '発表警報・注意報はなし', '警報・注意報はなし', ''
)

# データURLの抽出パターン（例: .../20260909012014_0_VPWW61_090000.xml）
DATA_URL_RE = re.compile(
    r'https://www\.data\.jma\.go\.jp/developer/xml/data/'
    r'(\d{14})_\d+_([A-Z0-9]+)_(\d{6})\.xml'
)


# ---------------------------------------------------------------------------
# 取得ユーティリティ
# ---------------------------------------------------------------------------
def _request(url):
    sep = '&' if '?' in url else '?'
    bust = f'{sep}_={int(datetime.now(timezone.utc).timestamp())}'
    req = urllib.request.Request(
        url + bust,
        headers={
            'User-Agent': 'tsurugamine-safety-signage/3.0',
            'Accept': 'application/xml, application/json, */*',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        },
    )
    with urllib.request.urlopen(req, timeout=25) as res:
        raw = res.read()
    # gzip マジックナンバーなら展開
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    return raw


def get_json(url):
    return json.loads(_request(url).decode('utf-8'))


def get_text(url):
    return _request(url).decode('utf-8', errors='replace')


def get_bytes(url):
    return _request(url)


# ---------------------------------------------------------------------------
# XML 解析（名前空間に依存しないローカル名で処理）
# ---------------------------------------------------------------------------
def _lname(el):
    return el.tag.rsplit('}', 1)[-1]


def _child(el, name):
    for c in el:
        if _lname(c) == name:
            return c
    return None


def _text(el, name):
    c = _child(el, name)
    if c is not None and c.text:
        return c.text.strip()
    return ''


def name_to_flags(name):
    """警報・注意報の日本語名 → フラグ集合（キーワード判定）。"""
    flags = set()
    if '土砂災害' in name:
        if '注意報' in name and '警報' not in name:
            flags.add('landslideAdvisory')
        else:
            flags.add('landslide')  # 警報／特別警報／危険警報／警戒情報
    if '大雨' in name and '警報' in name:      # 大雨警報／特別警報（注意報は除外）
        flags.add('heavyRain')
    if '暴風' in name and '警報' in name:      # 暴風警報／暴風雪警報（強風注意報は除外）
        flags.add('storm')
    if '雷' in name:                           # 雷注意報
        flags.add('thunder')
    if '乾燥' in name:                         # 乾燥注意報
        flags.add('dry')
    return flags


def _area_is_target(item):
    for a in item.iter():
        if _lname(a) != 'Area':
            continue
        aname = _text(a, 'Name')
        acode = _text(a, 'Code')
        if any(t in aname for t in TARGET_AREA_NAMES):
            return True
        if acode in TARGET_AREA_CODES:
            return True
    return False


def parse_warning_doc(xml_bytes, allowed_flags):
    """1つの警報XMLを解析し、対象地域で有効なフラグ集合とデバッグ名を返す。"""
    true_flags = set()
    active_names = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return true_flags, active_names, None

    report_dt = None
    for el in root.iter():
        if _lname(el) == 'ReportDateTime' and el.text:
            report_dt = el.text.strip()
            break

    for item in root.iter():
        if _lname(item) != 'Item':
            continue
        kind = _child(item, 'Kind')
        if kind is None:
            continue
        name = _text(kind, 'Name')
        status = _text(kind, 'Status')
        if not name or status in INACTIVE_STATUS:
            continue
        if not _area_is_target(item):
            continue
        flags = name_to_flags(name) & set(allowed_flags)
        if flags:
            true_flags |= flags
            active_names.append(name)

    return true_flags, active_names, report_dt


# ---------------------------------------------------------------------------
# フィードから対象電文（神奈川県）の最新URLを種別ごとに選ぶ
# ---------------------------------------------------------------------------
def select_latest_urls(feed_text):
    """{type: (timestamp, url)} を返す（PREF_CODE かつ CATEGORY_TYPES のもの）。"""
    latest = {}
    for ts, mtype, area in DATA_URL_RE.findall(feed_text):
        if area != PREF_CODE:
            continue
        if mtype not in CATEGORY_TYPES:
            continue
        url = (
            'https://www.data.jma.go.jp/developer/xml/data/'
            f'{ts}_0_{mtype}_{area}.xml'
        )
        cur = latest.get(mtype)
        if cur is None or ts > cur[0]:
            latest[mtype] = (ts, url)
    return latest


def select_aggregate_url(feed_text):
    """定時フィードから最新の VPWS50（集約通報, area=010000）URLを返す。"""
    best = None
    for ts, mtype, area in DATA_URL_RE.findall(feed_text):
        if mtype != AGGREGATE_TYPE or area != AGGREGATE_AREA:
            continue
        if best is None or ts > best[0]:
            best = (ts, (
                'https://www.data.jma.go.jp/developer/xml/data/'
                f'{ts}_0_{mtype}_{area}.xml'
            ))
    return best


def fetch_warnings_from_xml(previous_warnings):
    """気象庁XMLから警報・注意報を取得。VPWS50(集約)を主に、個別電文で補強。"""
    result = dict(DEFAULT_WARNINGS)
    for k in result:
        if k in previous_warnings:
            result[k] = bool(previous_warnings[k])

    debug = {'reports': [], 'error': None, 'aggregate': None}
    got_aggregate = False

    # --- (A) 主データ: VPWS50 集約通報（regular.xml）---
    try:
        reg_text = get_text(FEED_REGULAR)
        agg = select_aggregate_url(reg_text)
        if agg:
            ts, url = agg
            xml_bytes = get_bytes(url)
            flags, names, report_dt = parse_warning_doc(
                xml_bytes, AGGREGATE_FLAGS
            )
            for f in AGGREGATE_FLAGS:
                result[f] = False
            for f in flags:
                result[f] = True
            got_aggregate = True
            debug['aggregate'] = {
                'type': AGGREGATE_TYPE,
                'reportDateTime': report_dt,
                'activeNames': names,
            }
    except Exception as e:  # noqa: BLE001
        debug['aggregate'] = {'error': type(e).__name__}

    # --- (B) 補強: 神奈川県の個別電文（extra.xml）---
    try:
        feed_text = get_text(FEED_EXTRA)
        latest = select_latest_urls(feed_text)
    except Exception as e:  # noqa: BLE001
        latest = {}
        debug['error'] = type(e).__name__

    # 集約が取れなかった場合のみ、個別電文の担当フラグをリセット
    if not got_aggregate and latest:
        covered = set()
        for mtype in latest:
            covered |= set(CATEGORY_TYPES[mtype])
        for f in covered:
            result[f] = False

    for mtype, (ts, url) in sorted(latest.items()):
        allowed = CATEGORY_TYPES[mtype]
        try:
            xml_bytes = get_bytes(url)
        except Exception as e:  # noqa: BLE001
            debug['reports'].append(
                {'type': mtype, 'url': url, 'error': type(e).__name__}
            )
            continue
        flags, names, report_dt = parse_warning_doc(xml_bytes, allowed)
        # 個別電文はその種別の最新スナップショット。担当フラグを確定
        for f in allowed:
            result[f] = False
        for f in flags:
            result[f] = True
        debug['reports'].append({
            'type': mtype,
            'reportDateTime': report_dt,
            'activeNames': names,
        })

    if not got_aggregate and not latest:
        debug['note'] = 'no_data_available_keep_previous'
        # 取得不能時は前回値を維持
        for k in result:
            if k in previous_warnings:
                result[k] = bool(previous_warnings[k])

    return result, debug


# ---------------------------------------------------------------------------
# 雷予報（補助）
# ---------------------------------------------------------------------------
def detect_forecast_thunder(forecast_json):
    """府県予報の神奈川県東部(140010)の天気文に『雷』が含まれるか（補助判定）。"""
    try:
        for block in forecast_json:
            for ts in block.get('timeSeries', []):
                for area in ts.get('areas', []):
                    if area.get('area', {}).get('code') != FORECAST_AREA_CODE:
                        continue
                    for w in area.get('weathers', []) or []:
                        if '雷' in w:
                            return True
    except Exception:  # noqa: BLE001
        return False
    return False


# ---------------------------------------------------------------------------
# 既存 current.json の読み込み（前回の警報値を土台にする）
# ---------------------------------------------------------------------------
def load_previous_warnings():
    try:
        prev = json.loads(OUT.read_text(encoding='utf-8'))
        w = prev.get('warnings', {})
        if isinstance(w, dict):
            return w
    except Exception:  # noqa: BLE001
        pass
    return {}


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main():
    w = get_json(WEATHER)
    c = w['current']
    h = w['hourly']
    dy = w['daily']
    t = c['time']

    key = t[:13] + ':00'
    i = h['time'].index(key) if key in h['time'] else 0

    previous = load_previous_warnings()

    # 警報・注意報（気象庁XMLフィード）
    warnings = dict(DEFAULT_WARNINGS)
    warning_debug = {'reports': [], 'error': None}
    try:
        warnings, warning_debug = fetch_warnings_from_xml(previous)
    except Exception as e:  # noqa: BLE001
        warning_debug = {'reports': [], 'error': type(e).__name__}
        # 失敗時は前回値を維持
        for k in warnings:
            if k in previous:
                warnings[k] = bool(previous[k])
        print('JMA XML fetch failed:', type(e).__name__, e)

    # 雷予報（正式な雷注意報が無い時だけ補助表示）
    warnings.setdefault('thunderForecast', False)
    try:
        if not warnings.get('thunder'):
            fc = get_json(JMA_FORECAST)
            warnings['thunderForecast'] = detect_forecast_thunder(fc)
        else:
            warnings['thunderForecast'] = False
    except Exception as e:  # noqa: BLE001
        print('forecast fetch failed:', type(e).__name__, e)

    payload = {
        'schemaVersion': 1,
        'generatedAt': datetime.now(timezone.utc)
        .isoformat().replace('+00:00', 'Z'),
        'location': {'name': '鶴ヶ峰', 'latitude': LAT, 'longitude': LON},
        'weather': {
            'observedAt': t,
            'temperature': c['temperature_2m'],
            'precipitation': c['precipitation'],
            'weatherCode': c['weather_code'],
            'windSpeed': c['wind_speed_10m'],
            'rainProbability': h['precipitation_probability'][i] or 0,
            'minTemperature': dy['temperature_2m_min'][0],
            'sunset': dy['sunset'][0],
        },
        'warnings': warnings,
        'warningSource': 'jma-xml-feed',
        'warningDebug': warning_debug,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n',
        encoding='utf-8',
    )
    print('updated', OUT, '/ warnings:', warnings)


if __name__ == '__main__':
    main()

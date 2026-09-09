#!/usr/bin/env python3
"""
鶴ヶ峰（横浜市旭区）安全サイネージ用データ生成スクリプト

- 気象データ: Open-Meteo
- 警報・注意報: 気象庁 140000.json（神奈川県）

【この版のポイント】
1. 座標は横浜市旭区・鶴ヶ峰
2. 警報・注意報の対象エリアは横浜市(1410000)を使用
   ※ 1420100 は横須賀市のコードのため使用しない
3. 内陸の丘陵地のため landslide（土砂災害）を有効化
   - 土砂災害 警報以上(09/49/39) = landslide（即時・全画面）
   - 土砂災害 注意報(29)          = landslideAdvisory（00〜10分掲示）
   海沿い用の wave / stormSurge は対象外
4. 暴風 storm に対応

【判定方式】
気象庁の警報JSONは日本語名を持たず数字コードのみのため、
令和8年体系の公式コード表に基づきコード番号で判定する。
"""

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LAT = 35.474917
LON = 139.549250
OUT = Path(__file__).resolve().parents[1] / 'data' / 'current.json'

# 鶴ヶ峰（横浜市旭区）で参照する対象エリア。
# 気象庁の警報JSONは警報の種類ごとに格納される階層が異なるため、
# 市町村コードと二次細分コードの両方を対象にし、コードを合算する。
#   1410000：横浜市（土砂災害など市町村単位の情報）
#   140010 ：神奈川県東部（雷・強風など細分区域単位の情報）
# ※ 1420100 は横須賀市のため使用しない。
TARGET_AREA_CODES = ('1410000', '140010')

WEATHER = (
    'https://api.open-meteo.com/v1/forecast'
    f'?latitude={LAT}&longitude={LON}'
    '&current=temperature_2m,precipitation,weather_code,wind_speed_10m'
    '&hourly=precipitation_probability'
    '&daily=temperature_2m_min,sunset'
    '&timezone=Asia%2FTokyo&forecast_days=2'
)

# 警報・注意報JSONは「都道府県コード 140000（神奈川県）」を使う。
JMA = 'https://www.jma.go.jp/bosai/warning/data/warning/140000.json'

# 警報・注意報コード → サイネージのフラグ名
#
# 令和8年体系（2026-05-29〜）の公式コード表:
#   大雨 : 03=警報(L3) / 43=危険警報(L4) / 33=特別警報(L5) / 10=注意報(L2)
#   土砂 : 09=警報(L3) / 49=危険警報(L4) / 39=特別警報(L5) / 29=注意報(L2)
#   暴風 : 05=警報 / 35=特別警報 /（15=強風注意報）
#   雷   : 14=雷注意報
#   乾燥 : 21=乾燥注意報
CODE_TO_FLAG = {
    # 暴風
    '05': 'storm',                # 暴風警報
    '35': 'storm',                # 暴風特別警報
    # 大雨・浸水
    '03': 'heavyRain',            # 大雨警報(L3)
    '43': 'heavyRain',            # 大雨危険警報(L4)
    '33': 'heavyRain',            # 大雨特別警報(L5)
    # 土砂災害
    '09': 'landslide',            # 土砂災害警報(L3)
    '49': 'landslide',            # 土砂災害危険警報(L4)
    '39': 'landslide',            # 土砂災害特別警報(L5)
    '29': 'landslideAdvisory',    # 土砂災害注意報(L2)
    # 雷・乾燥
    '14': 'thunder',              # 雷注意報
    '21': 'dry',                  # 乾燥注意報
}

# app.js が参照するフラグ一式（鶴ヶ峰は内陸なので wave/stormSurge は持たない）
DEFAULT_WARNINGS = {
    'dry': False,
    'thunder': False,
    'heavyRain': False,
    'landslide': False,
    'landslideAdvisory': False,
    'storm': False,
}

# 「無効」とみなす status（この警報コードは採用しない）
INACTIVE_STATUS = ('解除', '発表警報・注意報はなし', '')


def get(url):
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'tsurugamine-safety-signage/2.0',
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)


def collect_active_codes(jma_json):
    """対象エリアすべての、解除されていない警報コードを合算して返す。

    気象庁の警報JSONは、雷・強風などが二次細分区域(140010)に、
    土砂災害などが市町村(1410000)に格納されるなど、警報の種類ごとに
    階層が異なる。そのため最初の一致で打ち切らず、対象エリア全ての
    コードを集合として合算する。
    """
    codes = set()
    for target in TARGET_AREA_CODES:
        for area_type in jma_json.get('areaTypes', []):
            for area in area_type.get('areas', []):
                if area.get('code') != target:
                    continue
                for w in area.get('warnings', []):
                    code = w.get('code')
                    status = w.get('status', '')
                    if code and status not in INACTIVE_STATUS:
                        codes.add(code)
    return codes


def parse_warnings(jma_json):
    warnings = dict(DEFAULT_WARNINGS)
    for code in collect_active_codes(jma_json):
        flag = CODE_TO_FLAG.get(code)
        if flag:
            warnings[flag] = True

    # 土砂災害警報以上が出ている場合、注意報フラグは下げる（重複表示防止）
    if warnings['landslide']:
        warnings['landslideAdvisory'] = False

    return warnings



def collect_debug(jma_json):
    """対象エリアに実際に入っている、解除されていない全コードを地域別に返す。"""
    result = {}
    for area_type in jma_json.get('areaTypes', []):
        for area in area_type.get('areas', []):
            acode = area.get('code')
            if acode not in TARGET_AREA_CODES:
                continue
            active = []
            for w in area.get('warnings', []):
                code = w.get('code')
                status = w.get('status', '')
                if code and status not in INACTIVE_STATUS:
                    active.append(code)
            if active:
                result[acode] = active
    return result


def main():
    w = get(WEATHER)
    c = w['current']
    h = w['hourly']
    dy = w['daily']
    t = c['time']

    key = t[:13] + ':00'
    i = h['time'].index(key) if key in h['time'] else 0

    warnings = dict(DEFAULT_WARNINGS)
    debug = {}
    err = None
    try:
        jma = get(JMA)
        warnings = parse_warnings(jma)
        debug = collect_debug(jma)
    except Exception as e:
        err = type(e).__name__
        print('JMA fetch failed:', err, e)

    payload = {
        'schemaVersion': 1,
        'generatedAt': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
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
        'warningFetchError': err,
        'activeCodesDebug': debug,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n',
        encoding='utf-8',
    )
    print('updated', OUT, '/ warnings:', warnings, '/ error:', err)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
鶴ヶ峰（横浜市旭区）安全サイネージ用データ生成スクリプト

- 気象データ: Open-Meteo
- 警報・注意報: 気象庁 140000.json（神奈川県）

【勝どき版からの主な違い】
1. 座標を横浜市旭区に、JMA URL を神奈川県(140000)に変更
2. 対象エリアを旭区(1420100)に限定
3. 内陸の丘陵地のため landslide（土砂災害）を有効化 ← 本命
   逆に wave / stormSurge（海沿い用）は対象外
4. 暴風 storm を追加

【重要な修正（勝どきと同じ）】
旧版は JMA JSON を「文字列に '土砂' が含まれるか」で判定していたが、
警報 JSON は数字コードしか持たず日本語名を含まないため、実際には
土砂災害を含む JMA 由来の警報が一切検知できていなかった。
本版は令和8年体系の公式コード表に基づきコード番号で判定する。
"""

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LAT = 35.474917
LON = 139.549250
OUT = Path(__file__).resolve().parents[1] / 'data' / 'current.json'

# 横浜市旭区の市区町村コード（見つからなければ 横浜市→神奈川東部 にフォールバック）
TARGET_AREA_CODES = ('1420100', '1410000', '140010')

WEATHER = (
    'https://api.open-meteo.com/v1/forecast'
    f'?latitude={LAT}&longitude={LON}'
    '&current=temperature_2m,precipitation,weather_code,wind_speed_10m'
    '&hourly=precipitation_probability'
    '&daily=temperature_2m_min,sunset'
    '&timezone=Asia%2FTokyo&forecast_days=2'
)
# 警報・注意報JSONは「都道府県コード 140000（神奈川県）」を使う。
# 140010（東部）や r8/140010 は天気予報用/存在しないコードで 404 になる。
JMA = 'https://www.jma.go.jp/bosai/warning/data/warning/140000.json'

# 警報・注意報コード → サイネージのフラグ名
#
# 令和8年体系（2026-05-29〜）の公式コード表（shiromatz r8 / 気象庁準拠）:
#   大雨   : 03=警報(L3) / 43=危険警報(L4) / 33=特別警報(L5) / 10=注意報(L2)
#   土砂   : 09=警報(L3) / 49=危険警報(L4) / 39=特別警報(L5) / 29=注意報(L2)
#   暴風   : 05=警報 / 35=特別警報 /（15=強風注意報）
#   雷     : 14=雷注意報
#   乾燥   : 21=乾燥注意報
#
# ※土砂災害(09/49/39)は大雨(03/43/33)とは別コード。
#   鶴ヶ峰は内陸の丘陵地のため土砂災害が本命。landslide として独立表示する。
#   土砂注意報(29, L2)は警報級ではないため既定では含めない。
#   注意報レベルから点灯したい場合は '29': 'landslide' を追加する。
CODE_TO_FLAG = {
    # 暴風
    '05': 'storm',        # 暴風警報
    '35': 'storm',        # 暴風特別警報
    # 大雨・浸水
    '03': 'heavyRain',    # 大雨警報(L3)
    '43': 'heavyRain',    # 大雨危険警報(L4)
    '33': 'heavyRain',    # 大雨特別警報(L5)
    # 土砂災害（鶴ヶ峰の本命）
    '09': 'landslide',    # 土砂災害警報(L3)
    '49': 'landslide',    # 土砂災害危険警報(L4)
    '39': 'landslide',    # 土砂災害特別警報(L5)
    # 雷・乾燥
    '14': 'thunder',      # 雷注意報
    '21': 'dry',          # 乾燥注意報
}

# app.js が参照するフラグ一式（鶴ヶ峰は内陸なので wave/stormSurge は持たない）
DEFAULT_WARNINGS = {
    'dry': False,
    'thunder': False,
    'heavyRain': False,
    'landslide': False,
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
    """対象エリアの、解除されていない警報コードの集合を返す。"""
    for target in TARGET_AREA_CODES:
        for area_type in jma_json.get('areaTypes', []):
            for area in area_type.get('areas', []):
                if area.get('code') != target:
                    continue
                codes = set()
                for w in area.get('warnings', []):
                    code = w.get('code')
                    status = w.get('status', '')
                    if code and status not in INACTIVE_STATUS:
                        codes.add(code)
                return codes  # 対象エリアが見つかった時点で確定
    return set()


def parse_warnings(jma_json):
    warnings = dict(DEFAULT_WARNINGS)
    for code in collect_active_codes(jma_json):
        flag = CODE_TO_FLAG.get(code)
        if flag:
            warnings[flag] = True
    return warnings


def main():
    w = get(WEATHER)
    c = w['current']
    h = w['hourly']
    dy = w['daily']
    t = c['time']

    key = t[:13] + ':00'
    i = h['time'].index(key) if key in h['time'] else 0

    warnings = dict(DEFAULT_WARNINGS)
    err = None
    try:
        warnings = parse_warnings(get(JMA))
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
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n',
        encoding='utf-8',
    )
    print('updated', OUT, '/ warnings:', warnings, '/ error:', err)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
VPWS50 地域構造診断版

一時的に scripts/update_data.py の代わりに実行し、既存の
 data/current.json を保持したまま、末尾に vpws50AreaDebug を追加する。

目的:
- VPWS50 内の「雷注意報」などが、どの地域名・地域コード・階層で
  格納されているかを確認する。
- 横浜市／神奈川県東部を正しく絞り込むための診断専用。

診断後は本番版 update_data.py に戻すこと。
"""

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parents[1] / 'data' / 'current.json'
REGULAR_FEED = 'https://www.data.jma.go.jp/developer/xml/feed/regular.xml'

DATA_URL_RE = re.compile(
    r'https://www\.data\.jma\.go\.jp/developer/xml/data/'
    r'(\d{14})_\d+_(VPWS50)_(010000)\.xml'
)

# 診断対象。必要な警報・注意報だけに絞り、JSON肥大化を防ぐ。
TARGET_WORDS = ('雷', '乾燥', '土砂災害', '大雨', '暴風', '強風')


def get_bytes(url):
    sep = '&' if '?' in url else '?'
    bust = f'{sep}_={int(datetime.now(timezone.utc).timestamp())}'
    req = urllib.request.Request(
        url + bust,
        headers={
            'User-Agent': 'tsurugamine-safety-signage-vpws50-debug/1.0',
            'Accept': 'application/xml, */*',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read()


def lname(element):
    return element.tag.rsplit('}', 1)[-1]


def direct_child(element, local_name):
    for child in element:
        if lname(child) == local_name:
            return child
    return None


def direct_text(element, local_name):
    child = direct_child(element, local_name)
    if child is not None and child.text:
        return child.text.strip()
    return ''


def latest_vpws50_url(feed_bytes):
    text = feed_bytes.decode('utf-8', errors='replace')
    matches = DATA_URL_RE.findall(text)
    if not matches:
        raise RuntimeError('regular.xml に VPWS50_010000 が見つかりません')
    timestamp = max(item[0] for item in matches)
    return (
        timestamp,
        'https://www.data.jma.go.jp/developer/xml/data/'
        f'{timestamp}_0_VPWS50_010000.xml',
    )


def collect_area_context(item):
    """Item 内の全 Area と、Area までの簡易パスを収集する。"""
    areas = []

    def walk(element, path):
        current = path + [lname(element)]
        if lname(element) == 'Area':
            name = direct_text(element, 'Name')
            code = direct_text(element, 'Code')
            areas.append({
                'name': name,
                'code': code,
                'path': '/'.join(current),
            })
        for child in element:
            walk(child, current)

    walk(item, [])
    return areas


def collect_kind_details(item):
    """Item 内の Kind 名・状態を、ネストを問わず収集する。"""
    kinds = []
    for element in item.iter():
        if lname(element) != 'Kind':
            continue
        kinds.append({
            'name': direct_text(element, 'Name'),
            'status': direct_text(element, 'Status'),
        })
    return kinds


def diagnose(xml_bytes):
    root = ET.fromstring(xml_bytes)
    report_datetime = ''
    for element in root.iter():
        if lname(element) == 'ReportDateTime' and element.text:
            report_datetime = element.text.strip()
            break

    matches = []
    for item_index, item in enumerate(
        (element for element in root.iter() if lname(element) == 'Item'),
        start=1,
    ):
        kinds = collect_kind_details(item)
        relevant = [
            kind for kind in kinds
            if any(word in kind['name'] for word in TARGET_WORDS)
        ]
        if not relevant:
            continue

        areas = collect_area_context(item)
        matches.append({
            'itemIndex': item_index,
            'kinds': relevant,
            'areas': areas,
            'containsYokohamaText': any(
                '横浜' in area['name'] for area in areas
            ),
            'containsKanagawaEastText': any(
                '神奈川県東部' in area['name'] or area['name'] == '東部'
                for area in areas
            ),
            'containsExpectedCode': any(
                area['code'] in ('1410000', '140010') for area in areas
            ),
        })

    return {
        'reportDateTime': report_datetime,
        'targetWords': list(TARGET_WORDS),
        'matchCount': len(matches),
        'matches': matches,
    }


def load_current():
    try:
        value = json.loads(OUT.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def main():
    payload = load_current()
    debug = {
        'generatedAt': datetime.now(timezone.utc)
        .isoformat().replace('+00:00', 'Z'),
        'error': None,
    }

    try:
        feed_bytes = get_bytes(REGULAR_FEED)
        feed_timestamp, xml_url = latest_vpws50_url(feed_bytes)
        detail = diagnose(get_bytes(xml_url))
        debug.update({
            'feedEntryTimestamp': feed_timestamp,
            'sourceFile': xml_url.rsplit('/', 1)[-1],
            **detail,
        })
    except Exception as error:
        debug['error'] = f'{type(error).__name__}: {error}'

    payload['vpws50AreaDebug'] = debug
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')) + '\n',
        encoding='utf-8',
    )

    print('VPWS50 diagnostic result:')
    print(json.dumps(debug, ensure_ascii=False, indent=2))
    print('updated', OUT)


if __name__ == '__main__':
    main()

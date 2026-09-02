#!/usr/bin/env python3
import json, urllib.request
from datetime import datetime, timezone
from pathlib import Path
LAT=35.474917; LON=139.549250
OUT=Path(__file__).resolve().parents[1]/'data'/'current.json'
WEATHER=('https://api.open-meteo.com/v1/forecast?latitude=35.474917&longitude=139.549250'
'&current=temperature_2m,precipitation,weather_code,wind_speed_10m'
'&hourly=precipitation_probability&daily=temperature_2m_min,sunset&timezone=Asia%2FTokyo&forecast_days=2')
JMA='https://www.jma.go.jp/bosai/warning/data/r8/140010.json'
def get_json(url):
    req=urllib.request.Request(url,headers={'User-Agent':'tsurugamine-safety-signage/1.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
def text_blob(obj):
    return json.dumps(obj,ensure_ascii=False)
def main():
    weather=get_json(WEATHER); current=weather['current']; hourly=weather['hourly']; daily=weather['daily']
    current_time=current['time']; nearest=hourly['time'].index(current_time[:13]+':00') if current_time[:13]+':00' in hourly['time'] else 0
    warnings={'dry':False,'thunder':False,'heavyRain':False,'landslide':False}
    warning_error=None
    try:
        jma=get_json(JMA); blob=text_blob(jma)
        active_words=('発表','継続','警報','注意報','危険')
        active=any(word in blob for word in active_words) and '解除' not in blob
        warnings={'dry':active and '乾燥' in blob,'thunder':active and '雷' in blob,'heavyRain':active and '大雨' in blob,'landslide':active and '土砂' in blob}
    except Exception as e: warning_error=type(e).__name__
    payload={'schemaVersion':1,'generatedAt':datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'location':{'name':'鶴ヶ峰','latitude':LAT,'longitude':LON},'weather':{'observedAt':current_time,'temperature':current['temperature_2m'],'precipitation':current['precipitation'],'weatherCode':current['weather_code'],'windSpeed':current['wind_speed_10m'],'rainProbability':hourly['precipitation_probability'][nearest] or 0,'minTemperature':daily['temperature_2m_min'][0],'sunset':daily['sunset'][0]},'warnings':warnings,'warningFetchError':warning_error}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf-8')
    print(f'updated {OUT}')
if __name__=='__main__':main()

'use strict';
(() => {
  const C = window.SIGNAGE_CONFIG;
  const $ = id => document.getElementById(id);
  const images = {
    dry: ['./images/dry-warning.png','乾燥注意報'], rainProbability: ['./images/rain-probability.png','降水確率が高い'],
    lowTemperature: ['./images/low-temperature.png','低温・凍結注意'], heavyRain: ['./images/heavy-rain.png','大雨・浸水注意'],
    landslide: ['./images/landslide.png','土砂災害警戒'], sunset: ['./images/sunset.png','日没注意'],
    strongWind: ['./images/strong-wind.png','強風注意'], thunder: ['./images/thunder.png','雷注意報']
  };
  let data = null, queue = [], queueIndex = 0, lastRotation = 0;
  const jstParts = d => Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(d).filter(x=>x.type!=='literal').map(x=>[x.type,x.value]));
  const formatDate = value => new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit',weekday:'short',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date(value));
  const weatherName = code => ({0:'快晴',1:'晴れ',2:'一部曇り',3:'曇り',45:'霧',48:'霧',51:'弱い霧雨',53:'霧雨',55:'強い霧雨',61:'弱い雨',63:'雨',65:'強い雨',71:'弱い雪',73:'雪',75:'強い雪',80:'にわか雨',81:'にわか雨',82:'激しいにわか雨',95:'雷雨',96:'雷雨',99:'激しい雷雨'})[code] || '気象情報';
  function activeRules() {
    if (!data) return [];
    const w=data.weather, warn=data.warnings||{};
    const immediate=[]; const scheduled=[];
    if (warn.landslide) immediate.push('landslide');
    if (warn.heavyRain || w.precipitation >= C.thresholds.heavyRainPerHour) immediate.push('heavyRain');
    if (warn.thunder) immediate.push('thunder');
    if (w.windSpeed >= C.thresholds.strongWind) scheduled.push('strongWind');
    if (w.minTemperature <= C.thresholds.lowTemperature) scheduled.push('lowTemperature');
    if (warn.dry) scheduled.push('dry');
    if (w.rainProbability >= C.thresholds.rainProbability) scheduled.push('rainProbability');
    const now = new Date();
const sunset = new Date(w.sunset);
const delta = sunset.getTime() - now.getTime();

/*
 * 日没30分前から日没10分後までは即時表示します。
 * 毎時00分から10分という制限は適用しません。
 */
if (
  Number.isFinite(delta) &&
  delta <= 30 * 60 * 1000 &&
  delta >= -10 * 60 * 1000
) {
  immediate.push('sunset');
}

const minute = Number(jstParts(now).minute);

if (immediate.length > 0) {
  return immediate;
}

if (
  minute >= C.scheduledStartMinute &&
  minute < C.scheduledEndMinute
) {
  return scheduled;
}

return [];
  function render() {
    if (!data) return;
    const w=data.weather;
    $('temperature').textContent=Number(w.temperature).toFixed(1);
    $('weatherLabel').textContent=weatherName(w.weatherCode);
    $('rainProbability').textContent=`${Math.round(w.rainProbability)}%`;
    $('precipitation').textContent=`${Number(w.precipitation).toFixed(1)} mm/h`;
    $('windSpeed').textContent=`${Number(w.windSpeed).toFixed(1)} m/s`;
    $('minTemperature').textContent=`${Number(w.minTemperature).toFixed(1)}℃`;
    $('sunsetTime').textContent=new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',hour:'2-digit',minute:'2-digit'}).format(new Date(w.sunset));
    $('generatedAt').textContent=formatDate(data.generatedAt);
    const all=activeRules();
    $('activeAlerts').textContent=all.length ? all.map(k=>images[k][1]).join(' ／ ') : '現在、サイネージ表示対象の注意情報はありません';
    const card=$('statusCard'); card.className='status-card '+(all.some(k=>['landslide','heavyRain','thunder'].includes(k))?'danger':all.length?'caution':'normal');
    $('statusText').textContent=all.length?'注意情報あり':'通常';
  }
  function renderOverlay(now=Date.now()) {
    queue=activeRules(); const overlay=$('alertOverlay');
    if (!queue.length){overlay.hidden=true;return;}
    if (now-lastRotation>=C.rotationMs){queueIndex=(queueIndex+1)%queue.length;lastRotation=now;}
    const key=queue[queueIndex%queue.length]; const [src,title]=images[key];
    if ($('alertImage').getAttribute('src')!==src) $('alertImage').src=src;
    $('alertImage').alt=title; $('overlayTitle').textContent=title;
    $('overlayCounter').textContent=queue.length>1?`${queueIndex%queue.length+1}/${queue.length}`:'';
    overlay.hidden=false;
  }
  async function refresh(){
    try{
      const response=await fetch(`${C.dataUrl}?t=${Date.now()}`,{cache:'no-store'}); if(!response.ok) throw new Error(`HTTP ${response.status}`);
      const next=await response.json(); const age=Date.now()-new Date(next.generatedAt).getTime(); if(!Number.isFinite(age)||age>C.staleMs) throw new Error('データが1時間以上更新されていません');
      data=next; $('network').textContent='● 通信正常'; $('network').className='ok'; $('stale').hidden=true; render();
    }catch(error){console.error(error);$('network').textContent='● 通信失敗';$('network').className='ng';$('stale').hidden=false;}
  }
  function tick(){ $('clock').textContent=formatDate(new Date()); render(); renderOverlay(); }
  function fit(){const el=$('signage'),s=Math.min(innerWidth/1920,innerHeight/1080);el.style.transform=`scale(${s})`;el.style.left=`${Math.max(0,(innerWidth-1920*s)/2)}px`;el.style.top=`${Math.max(0,(innerHeight-1080*s)/2)}px`;el.style.position='absolute';}
  addEventListener('resize',fit); fit(); refresh(); tick(); setInterval(tick,1000); setInterval(refresh,C.refreshMs);
})();

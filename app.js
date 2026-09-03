'use strict';

(() => {
  const C = window.SIGNAGE_CONFIG;
  const $ = (id) => document.getElementById(id);

  const IMAGES = {
    dry: ['./images/dry-warning.png', '乾燥注意報'],
    rainProbability: ['./images/rain-probability.png', '降水確率が高い'],
    lowTemperature: ['./images/low-temperature.png', '低温・凍結注意'],
    heavyRain: ['./images/heavy-rain.png', '大雨・浸水注意'],
    landslide: ['./images/landslide.png', '土砂災害警戒'],
    sunset: ['./images/sunset.png', '日没注意'],
    strongWind: ['./images/strong-wind.png', '強風注意'],
    thunder: ['./images/thunder.png', '雷注意報']
  };

  let data = null;
  let imageIndex = 0;
  let lastRotationTime = 0;

  const formatDateTime = (value) =>
    new Intl.DateTimeFormat('ja-JP', {
      timeZone: 'Asia/Tokyo',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    }).format(new Date(value));

  const getJapanTimeParts = (value) =>
    Object.fromEntries(
      new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Tokyo',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23'
      })
        .formatToParts(value)
        .filter((part) => part.type !== 'literal')
        .map((part) => [part.type, part.value])
    );

  const getWeatherName = (code) =>
    ({
      0: '快晴',
      1: '晴れ',
      2: '一部曇り',
      3: '曇り',
      45: '霧',
      61: '弱い雨',
      63: '雨',
      65: '強い雨',
      80: 'にわか雨',
      95: '雷雨',
      96: '雷雨',
      99: '激しい雷雨'
    })[code] || '気象情報';

  function getActiveRules() {
    if (!data) return [];

    const weather = data.weather;
    const warnings = data.warnings || {};
    const immediate = [];
    const scheduled = [];

    // 警報級は時刻に関係なく即時表示
    if (warnings.landslide) {
      immediate.push('landslide');
    }

    if (
      warnings.heavyRain ||
      weather.precipitation >= C.thresholds.heavyRainPerHour
    ) {
      immediate.push('heavyRain');
    }

    if (warnings.thunder) {
      immediate.push('thunder');
    }

    // 注意情報は毎時00分から10分間に表示
    if (weather.windSpeed >= C.thresholds.strongWind) {
      scheduled.push('strongWind');
    }

    if (weather.minTemperature <= C.thresholds.lowTemperature) {
      scheduled.push('lowTemperature');
    }

    if (warnings.dry) {
      scheduled.push('dry');
    }

    if (weather.rainProbability >= C.thresholds.rainProbability) {
      scheduled.push('rainProbability');
    }

    // 日没30分前から日没10分後は、毎時表示枠に関係なく即時表示
    const now = new Date();
    const sunset = new Date(weather.sunset);
    const millisecondsUntilSunset = sunset.getTime() - now.getTime();

    if (
      Number.isFinite(millisecondsUntilSunset) &&
      millisecondsUntilSunset <= 30 * 60 * 1000 &&
      millisecondsUntilSunset >= -10 * 60 * 1000
    ) {
      immediate.push('sunset');
    }

    if (immediate.length > 0) {
      return immediate;
    }

    const minute = Number(getJapanTimeParts(now).minute);

    if (
      minute >= C.scheduledStartMinute &&
      minute < C.scheduledEndMinute
    ) {
      return scheduled;
    }

    return [];
  }

  function render() {
    if (!data) return;

    const weather = data.weather;
    const activeRules = getActiveRules();

    $('temperature').textContent = Number(weather.temperature).toFixed(1);
    $('weatherLabel').textContent = getWeatherName(weather.weatherCode);
    $('rainProbability').textContent =
      `${Math.round(weather.rainProbability)}%`;
    $('precipitation').textContent =
      `${Number(weather.precipitation).toFixed(1)} mm/h`;
    $('windSpeed').textContent =
      `${Number(weather.windSpeed).toFixed(1)} m/s`;
    $('minTemperature').textContent =
      `${Number(weather.minTemperature).toFixed(1)}℃`;
    $('sunsetTime').textContent =
      new Intl.DateTimeFormat('ja-JP', {
        timeZone: 'Asia/Tokyo',
        hour: '2-digit',
        minute: '2-digit'
      }).format(new Date(weather.sunset));
    $('generatedAt').textContent = formatDateTime(data.generatedAt);

    $('activeAlerts').textContent = activeRules.length
      ? activeRules.map((key) => IMAGES[key][1]).join(' ／ ')
      : '現在、サイネージ表示対象の注意情報はありません';

    const hasDanger = activeRules.some((key) =>
      ['landslide', 'heavyRain', 'thunder'].includes(key)
    );

    $('statusCard').className = hasDanger
      ? 'danger'
      : activeRules.length
        ? 'caution'
        : '';

    $('statusText').textContent = activeRules.length
      ? '注意情報あり'
      : '通常';
  }

  function renderOverlay() {
    const queue = getActiveRules();
    const overlay = $('alertOverlay');

    if (queue.length === 0) {
      overlay.hidden = true;
      return;
    }

    if (Date.now() - lastRotationTime >= C.rotationMs) {
      imageIndex = (imageIndex + 1) % queue.length;
      lastRotationTime = Date.now();
    }

    const key = queue[imageIndex % queue.length];
    $('alertImage').src = IMAGES[key][0];
    $('alertImage').alt = IMAGES[key][1];
    $('overlayTitle').textContent = IMAGES[key][1];
    $('overlayCounter').textContent =
      queue.length > 1
        ? `${(imageIndex % queue.length) + 1}/${queue.length}`
        : '';

    overlay.hidden = false;
  }

  async function refreshData() {
    try {
      const response = await fetch(`${C.dataUrl}?t=${Date.now()}`, {
        cache: 'no-store'
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const nextData = await response.json();
      const dataAge =
        Date.now() - new Date(nextData.generatedAt).getTime();

      if (!Number.isFinite(dataAge) || dataAge > C.staleMs) {
        throw new Error('データが1時間以上更新されていません');
      }

      data = nextData;
      $('network').textContent = '● 通信正常';
      $('network').className = 'ok';
      $('stale').hidden = true;
      render();
    } catch (error) {
      console.error(error);
      $('network').textContent = '● 通信失敗';
      $('network').className = 'ng';
      $('stale').hidden = false;
    }
  }

  function fitToScreen() {
    const signage = $('signage');
    const scale = Math.min(
      window.innerWidth / 1920,
      window.innerHeight / 1080
    );

    signage.style.transform = `scale(${scale})`;
    signage.style.position = 'absolute';
    signage.style.left =
      `${Math.max(0, (window.innerWidth - 1920 * scale) / 2)}px`;
    signage.style.top =
      `${Math.max(0, (window.innerHeight - 1080 * scale) / 2)}px`;
  }

  function tick() {
    $('clock').textContent = formatDateTime(new Date());
    render();
    renderOverlay();
  }

  window.addEventListener('resize', fitToScreen);

  fitToScreen();
  refreshData();
  tick();

  window.setInterval(tick, 1000);
  window.setInterval(refreshData, C.refreshMs);
})();

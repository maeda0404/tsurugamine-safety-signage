'use strict';

(() => {
  const C = window.SIGNAGE_CONFIG;
  const $ = (id) => document.getElementById(id);

  // 鶴ヶ峰の直近データを端末内に保存
  const CACHE_KEY = 'tsurugamine_safety_last';

  function saveCache(d) {
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify(d));
    } catch (error) {
      console.warn('キャッシュ保存に失敗', error);
    }
  }

  function loadCache() {
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (error) {
      console.warn('キャッシュ読込に失敗', error);
      return null;
    }
  }

  const ONE_HOUR_MS = 60 * 60 * 1000;
  const THREE_HOURS_MS = 3 * 60 * 60 * 1000;

  const IMAGES = {
    dry: ['./images/dry-warning.png', '乾燥注意報'],
    rainProbability: ['./images/rain-probability.png', '降水確率が高い'],
    lowTemperature: ['./images/low-temperature.png', '低温・凍結注意'],
    heavyRain: ['./images/heavy-rain.png', '大雨・浸水注意'],
    landslide: ['./images/landslide.png', '土砂災害警戒'],
    landslideAdvisory: ['./images/landslide-advisory.png', '土砂災害注意報'],
    storm: ['./images/storm.png', '暴風警報'],
    sunset: ['./images/sunset.png', '日没注意'],
    strongWind: ['./images/strong-wind.png', '強風注意'],
    thunder: ['./images/thunder.png', '雷注意報'],
    // 予報文ベースの雷（正式な雷注意報ではない補助表示）
    thunderForecast: ['./images/thunder-forecast.png', '雷予報 発表中']
  };

  // 時刻に関係なく即時・全画面表示する警報級ルール。
  // thunderForecast は予報ベースの補助表示のため、ここには含めない。
  const IMMEDIATE_RULES = [
    'landslide',
    'heavyRain',
    'storm',
    'sunset'
  ];

  // 総合判定を「危険（赤）」にするルール。
  // thunderForecast は正式発表ではないため含めない。
  const DANGER_RULES = [
    'landslide',
    'heavyRain',
    'storm',
    'thunder'
  ];

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

  function resetNetworkStyle() {
    const network = $('network');
    network.style.color = '';
    network.style.backgroundColor = '';
    network.style.borderColor = '';
  }

  function showNetworkNormal() {
    const network = $('network');
    const stale = $('stale');

    resetNetworkStyle();
    network.textContent = '● 通信正常';
    network.className = 'ok';

    stale.hidden = true;
    stale.textContent = '';
    stale.style.backgroundColor = '';
    stale.style.color = '';
  }

  function showNetworkDelay(message) {
    const network = $('network');
    const stale = $('stale');

    network.textContent = '● 更新遅延';
    network.className = 'delay';
    network.style.color = '#8a5700';
    network.style.backgroundColor = '#fff3cd';
    network.style.borderColor = '#d39e00';

    stale.hidden = false;
    stale.textContent =
      message || 'データ更新が遅延しています。取得済みの気象情報を表示しています';
    stale.style.backgroundColor = '#b26a00';
    stale.style.color = '#ffffff';
  }

  function showNetworkFailure(message = '最新情報を取得できていません') {
    const network = $('network');
    const stale = $('stale');

    resetNetworkStyle();
    network.textContent = '● 通信失敗';
    network.className = 'ng';

    stale.hidden = false;
    stale.textContent = message;
    stale.style.backgroundColor = '';
    stale.style.color = '';
  }

  function clearWeatherDisplay() {
    data = null;

    $('temperature').textContent = '--';
    $('weatherLabel').textContent = '情報取得中';
    $('rainProbability').textContent = '--%';
    $('precipitation').textContent = '-- mm/h';
    $('windSpeed').textContent = '-- m/s';
    $('minTemperature').textContent = '--℃';
    $('sunsetTime').textContent = '--:--';
    $('generatedAt').textContent = '--';
    $('activeAlerts').textContent = '該当情報を確認しています';
    $('statusCard').className = 'status-card normal';
    $('statusText').textContent = '確認中';
    $('alertOverlay').hidden = true;

    imageIndex = 0;
    lastRotationTime = 0;
  }

  function getDataFreshness(generatedAt) {
    const generatedTime = new Date(generatedAt).getTime();

    if (!Number.isFinite(generatedTime)) {
      return {
        status: 'failure',
        ageMs: null,
        message: 'データの生成時刻を確認できません'
      };
    }

    const ageMs = Math.max(0, Date.now() - generatedTime);

    if (ageMs >= THREE_HOURS_MS) {
      return {
        status: 'failure',
        ageMs,
        message: 'データが3時間以上更新されていません'
      };
    }

    if (ageMs >= ONE_HOUR_MS) {
      return {
        status: 'delay',
        ageMs,
        message: 'データ更新が遅延しています。取得済みの気象情報を表示しています'
      };
    }

    return {
      status: 'normal',
      ageMs,
      message: ''
    };
  }

  function isUsableData(candidate) {
    return Boolean(
      candidate &&
      candidate.weather &&
      candidate.generatedAt &&
      Number.isFinite(new Date(candidate.generatedAt).getTime())
    );
  }

  function showStoredDataStatus(candidate, fetchFailed = false) {
    const freshness = getDataFreshness(candidate.generatedAt);
    const generatedText = formatDateTime(candidate.generatedAt);

    if (freshness.status === 'failure') {
      showNetworkFailure(
        `通信失敗：${generatedText}時点の情報を表示中`
      );
      return;
    }

    if (freshness.status === 'delay') {
      showNetworkDelay(
        `更新遅延：${generatedText}時点の情報を表示中`
      );
      return;
    }

    if (fetchFailed) {
      showNetworkFailure(
        `最新情報の取得に一時的に失敗しました。${generatedText}時点の情報を表示中`
      );
      return;
    }

    showNetworkNormal();
  }

  function getCurrentRules() {
    if (!data) return [];

    const weather = data.weather;
    const warnings = data.warnings || {};
    const rules = [];

    if (warnings.landslide) {
      rules.push('landslide');
    } else if (warnings.landslideAdvisory) {
      rules.push('landslideAdvisory');
    }

    if (
      warnings.heavyRain ||
      weather.precipitation >= C.thresholds.heavyRainPerHour
    ) {
      rules.push('heavyRain');
    }

    const stormActive =
      warnings.storm ||
      weather.windSpeed >= C.thresholds.stormWind;

    if (stormActive) {
      rules.push('storm');
    }

    if (warnings.thunder) {
      // 正式な雷注意報（即時・全画面）
      rules.push('thunder');
    } else if (warnings.thunderForecast) {
      // 正式な雷注意報が無いときのみ、予報ベースの雷を補助表示（00〜10分）
      rules.push('thunderForecast');
    }

    if (
      !stormActive &&
      weather.windSpeed >= C.thresholds.strongWind
    ) {
      rules.push('strongWind');
    }

    if (weather.minTemperature <= C.thresholds.lowTemperature) {
      rules.push('lowTemperature');
    }

    if (warnings.dry) {
      rules.push('dry');
    }

    if (weather.rainProbability >= C.thresholds.rainProbability) {
      rules.push('rainProbability');
    }

    const now = new Date();
    const sunset = new Date(weather.sunset);
    const millisecondsUntilSunset = sunset.getTime() - now.getTime();

    if (
      Number.isFinite(millisecondsUntilSunset) &&
      millisecondsUntilSunset <= 30 * 60 * 1000 &&
      millisecondsUntilSunset >= -10 * 60 * 1000
    ) {
      rules.push('sunset');
    }

    return rules;
  }

  function getActiveRules() {
    const currentRules = getCurrentRules();

    if (currentRules.length === 0) {
      return [];
    }

    const immediateRules = currentRules.filter((key) =>
      IMMEDIATE_RULES.includes(key)
    );

    if (immediateRules.length > 0) {
      return immediateRules;
    }

    const minute = Number(getJapanTimeParts(new Date()).minute);

    if (
      minute >= C.scheduledStartMinute &&
      minute < C.scheduledEndMinute
    ) {
      return currentRules;
    }

    return [];
  }

  function render() {
    if (!data) return;

    const weather = data.weather;
    const activeRules = getCurrentRules();

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
      DANGER_RULES.includes(key)
    );

    $('statusCard').className = hasDanger
      ? 'status-card danger'
      : activeRules.length
        ? 'status-card caution'
        : 'status-card normal';

    $('statusText').textContent = activeRules.length
      ? '注意情報あり'
      : '通常';
  }

  function renderOverlay() {
    const queue = getActiveRules();
    const overlay = $('alertOverlay');

    if (queue.length === 0) {
      overlay.hidden = true;
      imageIndex = 0;
      return;
    }

    imageIndex %= queue.length;

    if (Date.now() - lastRotationTime >= C.rotationMs) {
      imageIndex = (imageIndex + 1) % queue.length;
      lastRotationTime = Date.now();
    }

    const key = queue[imageIndex % queue.length];

    $('alertImage').src = IMAGES[key][0];
    $('alertImage').alt = IMAGES[key][1];
    $('overlayTitle').textContent = IMAGES[key][1];
    $('overlayCounter').textContent = queue.length > 1
      ? `${(imageIndex % queue.length) + 1}/${queue.length}`
      : '';

    overlay.hidden = false;
  }

  async function refreshData() {
    try {
      const response = await fetch(
        `${C.dataUrl}?t=${Date.now()}`,
        { cache: 'no-store' }
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const nextData = await response.json();

      if (!isUsableData(nextData)) {
        throw new Error('気象データの内容を確認できません');
      }

      data = nextData;
      saveCache(nextData);
      render();
      showStoredDataStatus(nextData, false);

      const freshness = getDataFreshness(nextData.generatedAt);
      if (freshness.status !== 'normal') {
        console.warn(freshness.message);
      }
    } catch (error) {
      console.error(error);

      const cachedData = isUsableData(data) ? data : loadCache();

      if (isUsableData(cachedData)) {
        data = cachedData;
        render();
        showStoredDataStatus(cachedData, true);
        return;
      }

      clearWeatherDisplay();
      showNetworkFailure('最新情報を取得できていません');
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

  const cachedData = loadCache();
  if (isUsableData(cachedData)) {
    data = cachedData;
    render();
    showStoredDataStatus(cachedData, false);
  }

  refreshData();
  tick();

  window.setInterval(tick, 1000);
  window.setInterval(refreshData, C.refreshMs);
})();

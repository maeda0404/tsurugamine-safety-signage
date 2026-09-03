'use strict';

(() => {
  const C = window.SIGNAGE_CONFIG;
  const $ = (id) => document.getElementById(id);

  /*
   * データ鮮度の判定時間
   *
   * 1時間未満：
   *   通信正常
   *
   * 1時間以上3時間未満：
   *   更新遅延
   *
   * 3時間以上：
   *   通信失敗
   */
  const ONE_HOUR_MS = 60 * 60 * 1000;
  const THREE_HOURS_MS = 3 * 60 * 60 * 1000;

  const IMAGES = {
    dry: ['./images/dry-warning.png', '乾燥注意報'],
    rainProbability: [
      './images/rain-probability.png',
      '降水確率が高い'
    ],
    lowTemperature: [
      './images/low-temperature.png',
      '低温・凍結注意'
    ],
    heavyRain: [
      './images/heavy-rain.png',
      '大雨・浸水注意'
    ],
    landslide: [
      './images/landslide.png',
      '土砂災害警戒'
    ],
    sunset: [
      './images/sunset.png',
      '日没注意'
    ],
    strongWind: [
      './images/strong-wind.png',
      '強風注意'
    ],
    thunder: [
      './images/thunder.png',
      '雷注意報'
    ]
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

  /**
   * app.jsから設定した通信表示用の色を解除する
   */
  function resetNetworkStyle() {
    const network = $('network');

    network.style.color = '';
    network.style.backgroundColor = '';
    network.style.borderColor = '';
  }

  /**
   * 1時間未満の正常表示
   */
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

  /**
   * 1時間以上3時間未満の更新遅延表示
   */
  function showNetworkDelay() {
    const network = $('network');
    const stale = $('stale');

    /*
     * style.cssを変更しなくても黄色表示になるように、
     * app.jsから色を設定しています。
     */
    network.textContent = '● 更新遅延';
    network.className = 'delay';
    network.style.color = '#8a5700';
    network.style.backgroundColor = '#fff3cd';
    network.style.borderColor = '#d39e00';

    stale.hidden = false;
    stale.textContent =
      'データ更新が遅延しています。取得済みの気象情報を表示しています';
    stale.style.backgroundColor = '#b26a00';
    stale.style.color = '#ffffff';
  }

  /**
   * 3時間以上またはデータ取得不能時の失敗表示
   */
  function showNetworkFailure(
    message = '最新情報を取得できていません'
  ) {
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

  /**
   * 3時間以上古くなった数値を非表示にする
   */
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

    $('activeAlerts').textContent =
      '該当情報を確認しています';

    $('statusCard').className =
      'status-card normal';

    $('statusText').textContent =
      '確認中';

    $('alertOverlay').hidden = true;

    imageIndex = 0;
    lastRotationTime = 0;
  }

  /**
   * データの生成時刻を確認する
   */
  function getDataFreshness(generatedAt) {
    const generatedTime =
      new Date(generatedAt).getTime();

    if (!Number.isFinite(generatedTime)) {
      return {
        status: 'failure',
        ageMs: null,
        message: 'データの生成時刻を確認できません'
      };
    }

    const calculatedAgeMs =
      Date.now() - generatedTime;

    /*
     * パソコン側とデータ側の時計が少しずれ、
     * 生成時刻がわずかに未来になった場合は、
     * データの古さを0分として扱います。
     */
    const ageMs =
      Math.max(0, calculatedAgeMs);

    if (ageMs >= THREE_HOURS_MS) {
      return {
        status: 'failure',
        ageMs,
        message:
          'データが3時間以上更新されていません'
      };
    }

    if (ageMs >= ONE_HOUR_MS) {
      return {
        status: 'delay',
        ageMs,
        message:
          'データ更新が遅延しています。取得済みの気象情報を表示しています'
      };
    }

    return {
      status: 'normal',
      ageMs,
      message: ''
    };
  }

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
      weather.precipitation >=
        C.thresholds.heavyRainPerHour
    ) {
      immediate.push('heavyRain');
    }

    if (warnings.thunder) {
      immediate.push('thunder');
    }

    // 注意情報は毎時00分から10分間に表示
    if (
      weather.windSpeed >=
      C.thresholds.strongWind
    ) {
      scheduled.push('strongWind');
    }

    if (
      weather.minTemperature <=
      C.thresholds.lowTemperature
    ) {
      scheduled.push('lowTemperature');
    }

    if (warnings.dry) {
      scheduled.push('dry');
    }

    if (
      weather.rainProbability >=
      C.thresholds.rainProbability
    ) {
      scheduled.push('rainProbability');
    }

    /*
     * 日没30分前から日没10分後は、
     * 毎時表示枠に関係なく即時表示
     */
    const now = new Date();
    const sunset = new Date(weather.sunset);

    const millisecondsUntilSunset =
      sunset.getTime() - now.getTime();

    if (
      Number.isFinite(millisecondsUntilSunset) &&
      millisecondsUntilSunset <=
        30 * 60 * 1000 &&
      millisecondsUntilSunset >=
        -10 * 60 * 1000
    ) {
      immediate.push('sunset');
    }

    if (immediate.length > 0) {
      return immediate;
    }

    const minute =
      Number(getJapanTimeParts(now).minute);

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

    $('temperature').textContent =
      Number(weather.temperature).toFixed(1);

    $('weatherLabel').textContent =
      getWeatherName(weather.weatherCode);

    $('rainProbability').textContent =
      `${Math.round(
        weather.rainProbability
      )}%`;

    $('precipitation').textContent =
      `${Number(
        weather.precipitation
      ).toFixed(1)} mm/h`;

    $('windSpeed').textContent =
      `${Number(
        weather.windSpeed
      ).toFixed(1)} m/s`;

    $('minTemperature').textContent =
      `${Number(
        weather.minTemperature
      ).toFixed(1)}℃`;

    $('sunsetTime').textContent =
      new Intl.DateTimeFormat('ja-JP', {
        timeZone: 'Asia/Tokyo',
        hour: '2-digit',
        minute: '2-digit'
      }).format(
        new Date(weather.sunset)
      );

    $('generatedAt').textContent =
      formatDateTime(data.generatedAt);

    $('activeAlerts').textContent =
      activeRules.length
        ? activeRules
            .map((key) => IMAGES[key][1])
            .join(' ／ ')
        : '現在、サイネージ表示対象の注意情報はありません';

    const hasDanger =
      activeRules.some((key) =>
        [
          'landslide',
          'heavyRain',
          'thunder'
        ].includes(key)
      );

    /*
     * 鶴ヶ峰版の既存クラス構成を維持
     */
    $('statusCard').className =
      hasDanger
        ? 'status-card danger'
        : activeRules.length
          ? 'status-card caution'
          : 'status-card normal';

    $('statusText').textContent =
      activeRules.length
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

    /*
     * 対象画像の数が変化した場合に、
     * imageIndexが範囲外にならないよう調整
     */
    imageIndex %= queue.length;

    if (
      Date.now() - lastRotationTime >=
      C.rotationMs
    ) {
      imageIndex =
        (imageIndex + 1) % queue.length;

      lastRotationTime = Date.now();
    }

    const key =
      queue[imageIndex % queue.length];

    $('alertImage').src =
      IMAGES[key][0];

    $('alertImage').alt =
      IMAGES[key][1];

    $('overlayTitle').textContent =
      IMAGES[key][1];

    $('overlayCounter').textContent =
      queue.length > 1
        ? `${
            (imageIndex % queue.length) + 1
          }/${queue.length}`
        : '';

    overlay.hidden = false;
  }

  async function refreshData() {
    try {
      const response = await fetch(
        `${C.dataUrl}?t=${Date.now()}`,
        {
          cache: 'no-store'
        }
      );

      if (!response.ok) {
        throw new Error(
          `HTTP ${response.status}`
        );
      }

      const nextData =
        await response.json();

      if (
        !nextData ||
        !nextData.weather
      ) {
        throw new Error(
          '気象データの内容を確認できません'
        );
      }

      const freshness =
        getDataFreshness(
          nextData.generatedAt
        );

      /*
       * 3時間以上更新されていない場合
       *
       * 古い数値は非表示にして、
       * 通信失敗へ切り替えます。
       */
      if (
        freshness.status === 'failure'
      ) {
        clearWeatherDisplay();

        showNetworkFailure(
          '最新情報を取得できていません'
        );

        console.error(
          new Error(freshness.message)
        );

        return;
      }

      /*
       * 3時間未満なら取得データを保持します。
       *
       * 1時間以上3時間未満でも、
       * 最後に取得できた数値を表示します。
       */
      data = nextData;
      render();

      if (
        freshness.status === 'delay'
      ) {
        showNetworkDelay();

        console.warn(
          freshness.message
        );

        return;
      }

      /*
       * 1時間未満
       */
      showNetworkNormal();
    } catch (error) {
      console.error(error);

      /*
       * HTTPエラーやJSONエラーが起きても、
       * すでに保持しているデータがある場合は、
       * そのデータの古さを確認します。
       */
      if (
        data &&
        data.generatedAt
      ) {
        const currentFreshness =
          getDataFreshness(
            data.generatedAt
          );

        /*
         * 保持データが1時間未満でも、
         * 最新ファイルの取得自体には失敗したため、
         * 通信失敗であることを表示します。
         *
         * 数値はすぐには消しません。
         */
        if (
          currentFreshness.status ===
          'normal'
        ) {
          showNetworkFailure(
            '最新情報の取得に一時的に失敗しました'
          );

          return;
        }

        /*
         * 保持データが1時間以上3時間未満なら、
         * 更新遅延として表示を継続します。
         */
        if (
          currentFreshness.status ===
          'delay'
        ) {
          showNetworkDelay();
          return;
        }
      }

      /*
       * 使用できるデータがない場合、
       * または保持データが3時間以上古い場合
       */
      clearWeatherDisplay();

      showNetworkFailure(
        '最新情報を取得できていません'
      );
    }
  }

  function fitToScreen() {
    const signage = $('signage');

    const scale = Math.min(
      window.innerWidth / 1920,
      window.innerHeight / 1080
    );

    signage.style.transform =
      `scale(${scale})`;

    signage.style.position =
      'absolute';

    signage.style.left =
      `${Math.max(
        0,
        (
          window.innerWidth -
          1920 * scale
        ) / 2
      )}px`;

    signage.style.top =
      `${Math.max(
        0,
        (
          window.innerHeight -
          1080 * scale
        ) / 2
      )}px`;
  }

  function tick() {
    $('clock').textContent =
      formatDateTime(new Date());

    render();
    renderOverlay();
  }

  window.addEventListener(
    'resize',
    fitToScreen
  );

  fitToScreen();
  refreshData();
  tick();

  window.setInterval(
    tick,
    1000
  );

  window.setInterval(
    refreshData,
    C.refreshMs
  );
})();

'use strict';
window.SIGNAGE_CONFIG = Object.freeze({
  locationName: '鶴ヶ峰',
  dataUrl: './data/current.json',
  refreshMs: 5 * 60 * 1000,
  staleMs: 60 * 60 * 1000,
  rotationMs: 20 * 1000,
  scheduledStartMinute: 0,
  scheduledEndMinute: 10,
  thresholds: Object.freeze({
    rainProbability: 60,
    lowTemperature: 5,
    strongWind: 10,
    heavyRainPerHour: 10
  })
});

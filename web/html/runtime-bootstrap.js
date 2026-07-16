(function () {
  'use strict';

  var origin = window.location.origin || 'http://127.0.0.1:8888';
  var wsScheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  var wsOrigin = wsScheme + '//' + window.location.host;
  var defaults = {
    video: {
      videoStreamUrl: wsOrigin + '/ws/camera1',
      hotStreamUrl: wsOrigin + '/ws/camera2'
    },
    mqtt: {
      brokerUrl: wsOrigin + '/mqtt',
      username: '',
      password: '',
      topics: { sensor: 'sensor/data', yolo: 'yolo/person_img' }
    },
    api: { baseURL: origin }
  };

  function camera(id, name, socketPath, topic) {
    return {
      id: id,
      name: name,
      wsUrl: wsOrigin + socketPath,
      rtspUrl: '',
      username: '',
      password: '',
      topic: topic
    };
  }

  if (!Array.isArray(window.LOONGINX_CAMERA_CONFIG)) {
    window.LOONGINX_CAMERA_CONFIG = [
      camera(1, 'Entrance', '/ws/camera1', 'sensor/data'),
      camera(2, 'Conveyor south', '/ws/camera1', 'sensor/data2'),
      camera(3, 'Conveyor north', '/ws/camera1', 'sensor/data3'),
      camera(4, 'Conveyor middle 1', '/ws/camera2', 'sensor/data4'),
      camera(5, 'Conveyor middle 2', '/ws/camera2', 'sensor/data4'),
      camera(6, 'Conveyor middle 3', '/ws/camera2', 'sensor/data4')
    ];
  }

  function parse(key) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (error) {
      return null;
    }
  }

  function isLegacy(value, ports) {
    if (!value) return true;
    try {
      var url = new URL(value, origin);
      var host = (url.hostname || '').toLowerCase();
      var port = url.port || (url.protocol === 'https:' || url.protocol === 'wss:' ? '443' : '80');
      return host === 'localhost' || host === '127.0.0.1' || host === '::1' ||
        host === '192.168.0.100' || host === '192.168.0.79' || ports.indexOf(port) >= 0;
    } catch (error) {
      return true;
    }
  }

  var config = parse('app_config') || defaults;
  config.video = config.video || defaults.video;
  config.mqtt = config.mqtt || defaults.mqtt;
  config.api = config.api || defaults.api;
  config.mqtt.topics = config.mqtt.topics || defaults.mqtt.topics;

  if (isLegacy(config.api.baseURL, ['8000'])) config.api.baseURL = defaults.api.baseURL;
  if (isLegacy(config.video.videoStreamUrl, ['8086', '8089'])) {
    config.video.videoStreamUrl = defaults.video.videoStreamUrl;
  }
  if (isLegacy(config.video.hotStreamUrl, ['8087', '8090'])) {
    config.video.hotStreamUrl = defaults.video.hotStreamUrl;
  }
  if (isLegacy(config.mqtt.brokerUrl, ['1884'])) config.mqtt.brokerUrl = defaults.mqtt.brokerUrl;

  window.localStorage.setItem('app_config', JSON.stringify(config));
  window.localStorage.setItem('video_config', JSON.stringify(config.video));
  window.localStorage.setItem('mqtt_config', JSON.stringify(config.mqtt));
  window.localStorage.setItem('api_config', JSON.stringify(config.api));
})();

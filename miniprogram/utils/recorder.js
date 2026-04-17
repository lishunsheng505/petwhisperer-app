/** wx.getRecorderManager 的 Promise 包装：开始/停止录音。 */
let _manager = null;
let _stopResolver = null;
let _stopRejecter = null;
let _isRecording = false;

function _ensureManager() {
  if (_manager) return _manager;
  _manager = wx.getRecorderManager();
  _manager.onStop((res) => {
    _isRecording = false;
    if (_stopResolver) {
      _stopResolver(res);
    }
    _stopResolver = null;
    _stopRejecter = null;
  });
  _manager.onError((err) => {
    _isRecording = false;
    if (_stopRejecter) {
      _stopRejecter(err);
    }
    _stopResolver = null;
    _stopRejecter = null;
  });
  return _manager;
}

function startRecording(opts = {}) {
  return new Promise((resolve, reject) => {
    const m = _ensureManager();
    if (_isRecording) {
      resolve();
      return;
    }
    const onStart = () => {
      _isRecording = true;
      m.offStart && m.offStart(onStart);
      resolve();
    };
    if (m.onStart) m.onStart(onStart);
    try {
      m.start({
        duration: opts.duration || 60000,
        sampleRate: opts.sampleRate || 44100,
        numberOfChannels: opts.numberOfChannels || 1,
        encodeBitRate: opts.encodeBitRate || 96000,
        format: opts.format || "aac",
      });
    } catch (e) {
      reject(e);
    }
  });
}

function stopRecording() {
  return new Promise((resolve, reject) => {
    const m = _ensureManager();
    if (!_isRecording) {
      resolve(null);
      return;
    }
    _stopResolver = resolve;
    _stopRejecter = reject;
    try {
      m.stop();
    } catch (e) {
      _stopResolver = null;
      _stopRejecter = null;
      reject(e);
    }
  });
}

function isRecording() {
  return _isRecording;
}

function ensureRecordAuth() {
  return new Promise((resolve, reject) => {
    wx.getSetting({
      success(res) {
        if (res.authSetting["scope.record"] === false) {
          wx.openSetting({
            success(s) {
              if (s.authSetting["scope.record"]) resolve();
              else reject(new Error("用户未授权录音"));
            },
            fail: reject,
          });
        } else if (res.authSetting["scope.record"] === undefined) {
          wx.authorize({
            scope: "scope.record",
            success: () => resolve(),
            fail: () => reject(new Error("授权录音失败")),
          });
        } else {
          resolve();
        }
      },
      fail: reject,
    });
  });
}

module.exports = {
  startRecording,
  stopRecording,
  isRecording,
  ensureRecordAuth,
};

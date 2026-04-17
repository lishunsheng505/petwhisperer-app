let _ctx = null;

function _ensureCtx() {
  if (_ctx) return _ctx;
  _ctx = wx.createInnerAudioContext();
  _ctx.obeyMuteSwitch = false;
  return _ctx;
}

function base64ToTempFile(base64, ext = "mp3") {
  return new Promise((resolve, reject) => {
    const fs = wx.getFileSystemManager();
    const tempPath = `${wx.env.USER_DATA_PATH}/audio_${Date.now()}.${ext}`;
    fs.writeFile({
      filePath: tempPath,
      data: base64,
      encoding: "base64",
      success: () => resolve(tempPath),
      fail: reject,
    });
  });
}

function playFile(filePath) {
  return new Promise((resolve) => {
    const ctx = _ensureCtx();
    try {
      ctx.stop();
    } catch (e) {}
    ctx.src = filePath;
    ctx.onEnded(() => resolve());
    ctx.onError(() => resolve());
    ctx.play();
  });
}

function stop() {
  if (_ctx) {
    try {
      _ctx.stop();
    } catch (e) {}
  }
}

async function playBase64(base64, ext = "mp3") {
  if (!base64) return;
  const path = await base64ToTempFile(base64, ext);
  await playFile(path);
}

module.exports = {
  base64ToTempFile,
  playFile,
  playBase64,
  stop,
};

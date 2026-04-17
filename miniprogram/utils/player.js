function _b64ToTempFile(base64, ext = "mp3") {
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

function _fmt(sec) {
  if (!sec || isNaN(sec) || sec === Infinity) return "00:00";
  sec = Math.max(0, Math.floor(sec));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

function createPlayer(onState) {
  const ctx = wx.createInnerAudioContext();
  ctx.obeyMuteSwitch = false;

  const state = {
    src: "",
    playing: false,
    currentTime: 0,
    duration: 0,
    progress: 0,
    currentTimeStr: "00:00",
    durationStr: "00:00",
    ready: false,
  };

  const emit = () => {
    state.progress = state.duration > 0 ? Math.min(100, (state.currentTime / state.duration) * 100) : 0;
    state.currentTimeStr = _fmt(state.currentTime);
    state.durationStr = _fmt(state.duration);
    onState && onState({ ...state });
  };

  ctx.onCanplay(() => {
    state.ready = true;
    if (ctx.duration && ctx.duration !== Infinity) {
      state.duration = ctx.duration;
    }
    emit();
  });
  ctx.onPlay(() => {
    state.playing = true;
    emit();
  });
  ctx.onPause(() => {
    state.playing = false;
    emit();
  });
  ctx.onStop(() => {
    state.playing = false;
    state.currentTime = 0;
    emit();
  });
  ctx.onEnded(() => {
    state.playing = false;
    state.currentTime = 0;
    emit();
  });
  ctx.onTimeUpdate(() => {
    state.currentTime = ctx.currentTime || 0;
    if ((!state.duration || state.duration === Infinity) && ctx.duration) {
      state.duration = ctx.duration;
    }
    emit();
  });
  ctx.onError(() => {
    state.playing = false;
    emit();
  });

  return {
    async loadBase64(base64, ext = "mp3", autoPlay = true) {
      if (!base64) return;
      try { ctx.stop(); } catch (e) {}
      const path = await _b64ToTempFile(base64, ext);
      state.src = path;
      state.currentTime = 0;
      state.duration = 0;
      state.ready = false;
      ctx.src = path;
      emit();
      if (autoPlay) ctx.play();
    },
    play() { ctx.play(); },
    pause() { ctx.pause(); },
    stop() { try { ctx.stop(); } catch (e) {} },
    toggle() {
      if (state.playing) ctx.pause();
      else ctx.play();
    },
    seek(sec) {
      if (sec >= 0) ctx.seek(sec);
    },
    seekRatio(ratio) {
      if (state.duration > 0) ctx.seek(state.duration * ratio);
    },
    destroy() {
      try { ctx.stop(); } catch (e) {}
      try { ctx.destroy(); } catch (e) {}
    },
    getState() { return { ...state }; },
  };
}

module.exports = { createPlayer };

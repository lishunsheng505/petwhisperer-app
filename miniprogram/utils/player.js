function _b64ToTempFile(base64, ext = "mp3") {
  return new Promise((resolve, reject) => {
    if (!base64 || typeof base64 !== "string") {
      reject(new Error("音频数据为空或非 base64 字符串"));
      return;
    }

    // 清掉空白/换行（极少数链路会加 \n），只保留 base64 合法字符 + 等号
    const cleaned = base64.replace(/[^A-Za-z0-9+/=]/g, "");

    const fs = wx.getFileSystemManager();
    const rand = Math.random().toString(36).slice(2, 8);
    const tempPath = `${wx.env.USER_DATA_PATH}/audio_${Date.now()}_${rand}.${ext}`;
    console.log("[player] 写音频文件", {
      base64_raw_len: base64.length,
      base64_clean_len: cleaned.length,
      path: tempPath,
    });
    base64 = cleaned;

    // === 方式 A: base64 字符串 + encoding 直写（主路径，最快） ===
    const tryBase64String = () => {
      fs.writeFile({
        filePath: tempPath,
        data: base64,
        encoding: "base64",
        success: () => resolve(tempPath),
        fail: (e) => {
          console.warn("[player] base64 string 写入失败，尝试 ArrayBuffer", e);
          tryArrayBuffer();
        },
      });
    };

    // === 方式 B: 转 ArrayBuffer 写（fallback，绕开 fs 对 base64 字符串的解析） ===
    const tryArrayBuffer = () => {
      let buf;
      try {
        buf = wx.base64ToArrayBuffer(base64);
      } catch (e) {
        console.error("[player] base64ToArrayBuffer 失败", e);
        reject(e);
        return;
      }
      fs.writeFile({
        filePath: tempPath,
        data: buf,
        // 不传 encoding，data 是 ArrayBuffer
        success: () => {
          console.log("[player] ArrayBuffer 路径写入成功");
          resolve(tempPath);
        },
        fail: (e2) => {
          console.error("[player] ArrayBuffer 路径写入也失败", e2);
          reject(e2);
        },
      });
    };

    tryBase64String();
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
    error: "",
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
  ctx.onError((res) => {
    state.playing = false;
    const errMsg = (res && (res.errMsg || res.errCode)) || "未知错误";
    state.error = `音频播放失败：${errMsg}`;
    console.error("[player] InnerAudioContext error", res);
    emit();
  });

  return {
    async loadBase64(base64, ext = "mp3", autoPlay = true) {
      if (!base64) {
        state.error = "服务端没返回音频数据";
        emit();
        return;
      }
      state.error = "";
      try { ctx.stop(); } catch (e) {}
      let path;
      try {
        path = await _b64ToTempFile(base64, ext);
      } catch (e) {
        state.error = `音频文件写入失败：${e && (e.errMsg || e.message) || e}`;
        console.error("[player] loadBase64 失败", e);
        emit();
        throw e;
      }
      state.src = path;
      state.currentTime = 0;
      state.duration = 0;
      state.ready = false;
      ctx.src = path;
      emit();
      if (autoPlay) {
        try { ctx.play(); } catch (e) {
          console.error("[player] ctx.play 抛错", e);
        }
      }
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

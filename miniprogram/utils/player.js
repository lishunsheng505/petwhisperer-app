function _b64ToTempFile(base64, ext = "mp3") {
  return new Promise((resolve, reject) => {
    if (!base64 || typeof base64 !== "string") {
      reject(new Error("音频数据为空或非 base64 字符串"));
      return;
    }

    const cleaned = base64.replace(/[^A-Za-z0-9+/=]/g, "");
    const fs = wx.getFileSystemManager();
    const rand = Math.random().toString(36).slice(2, 8);
    const userDir = wx.env.USER_DATA_PATH;

    console.log("[player] env", {
      USER_DATA_PATH: userDir,
      base64_raw_len: base64.length,
      base64_clean_len: cleaned.length,
    });

    const baseDir = userDir || "wxfile://usr";
    const tempPath = `${baseDir}/audio_${Date.now()}_${rand}.${ext}`;

    // 4 条路径依次尝试。每条失败就走下一条，最后全失败才 reject 给上层。
    // 把每一步的 errMsg 记下来，最终 reject 时一并暴露,前端 toast 能看到全貌。
    const errors = [];

    const finalReject = () => {
      reject(new Error(
        "all writeFile paths failed: " + errors.map(
          (e) => (e && (e.errMsg || e.message)) || String(e)
        ).join(" | ")
      ));
    };

    // 路径 1: writeFileSync + base64 string
    try {
      fs.writeFileSync(tempPath, cleaned, "base64");
      console.log("[player] 路径1 writeFileSync(base64) ok", tempPath);
      resolve(tempPath);
      return;
    } catch (e1) {
      console.warn("[player] 路径1 writeFileSync(base64) fail", e1);
      errors.push(e1);
    }

    // 路径 2: writeFileSync + ArrayBuffer
    try {
      const buf = wx.base64ToArrayBuffer(cleaned);
      fs.writeFileSync(tempPath, buf);
      console.log("[player] 路径2 writeFileSync(ArrayBuffer) ok", tempPath);
      resolve(tempPath);
      return;
    } catch (e2) {
      console.warn("[player] 路径2 writeFileSync(ArrayBuffer) fail", e2);
      errors.push(e2);
    }

    // 路径 3: writeFile 异步 + base64 string
    fs.writeFile({
      filePath: tempPath,
      data: cleaned,
      encoding: "base64",
      success: () => {
        console.log("[player] 路径3 writeFile(base64 异步) ok", tempPath);
        resolve(tempPath);
      },
      fail: (e3) => {
        console.warn("[player] 路径3 writeFile(base64 异步) fail", e3);
        errors.push(e3);

        // 路径 4: writeFile 异步 + ArrayBuffer
        let buf2;
        try {
          buf2 = wx.base64ToArrayBuffer(cleaned);
        } catch (e4a) {
          errors.push(e4a);
          finalReject();
          return;
        }
        fs.writeFile({
          filePath: tempPath,
          data: buf2,
          success: () => {
            console.log("[player] 路径4 writeFile(buf 异步) ok", tempPath);
            resolve(tempPath);
          },
          fail: (e4) => {
            console.error("[player] 路径4 fail, 4 条路径都挂了", e4);
            errors.push(e4);
            finalReject();
          },
        });
      },
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

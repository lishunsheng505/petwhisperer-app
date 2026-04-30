const {
  API_BASE,
  USE_CLOUD,
  CLOUD_ENV,
  CLOUD_SERVICE,
} = require("./config.js");

function parseJsonSafe(raw) {
  try {
    return typeof raw === "string" ? JSON.parse(raw) : raw;
  } catch (e) {
    return { ok: false, error: "JSON 解析失败", raw };
  }
}

function _basename(p) {
  if (!p) return "";
  const norm = p.replace(/\\/g, "/");
  const i = norm.lastIndexOf("/");
  return i < 0 ? norm : norm.slice(i + 1);
}

function _readFileBase64(filePath) {
  return new Promise((resolve, reject) => {
    wx.getFileSystemManager().readFile({
      filePath,
      encoding: "base64",
      success: (r) => resolve(r.data),
      fail: reject,
    });
  });
}

/** 调用云托管容器（HTTPS 自动 + 鉴权 + 不用配合法域名）。
 *  timeoutMs 可选，默认 60s；AI 重绘等长任务路径会用 120s。
 */
function _callContainer(path, { data, header, method, timeoutMs } = {}) {
  return new Promise((resolve, reject) => {
    if (!wx.cloud || !wx.cloud.callContainer) {
      reject(new Error("当前微信版本不支持 wx.cloud.callContainer"));
      return;
    }
    const m = (method || "POST").toUpperCase();
    const cfg = {
      config: { env: CLOUD_ENV },
      path,
      method: m,
      header: Object.assign(
        {
          "X-WX-SERVICE": CLOUD_SERVICE,
          "content-type": "application/json",
        },
        header || {}
      ),
      timeout: timeoutMs || 60000,
      success(res) {
        console.log("[callContainer ok]", path, res);
        if (res.statusCode >= 400) {
          const j = res.data || {};
          reject(j.detail || j.error || res.errMsg || "请求失败");
          return;
        }
        const body =
          typeof res.data === "object" ? res.data : parseJsonSafe(res.data);
        resolve(body);
      },
      fail(err) {
        console.error("[callContainer fail]", path, err);
        reject(err);
      },
    };
    if (m !== "GET") cfg.data = data || {};
    wx.cloud.callContainer(cfg);
  });
}

/** 自检接口（GET /health），用来快速定位 -606001 是不是 env/service/AppID 问题 */
function pingHealth() {
  return _callContainer("/health", { method: "GET" });
}

/** 查询当日 AI 重绘剩余次数 + 可选风格列表。 */
function getPhotoQuota() {
  if (USE_CLOUD) {
    return _callContainer("/photo/quota", { method: "GET" });
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}/photo/quota`,
      method: "GET",
      timeout: 10000,
      success(res) {
        const j = typeof res.data === "object" ? res.data : parseJsonSafe(res.data);
        if (res.statusCode >= 400) {
          reject(j.detail || j.error || res.data);
          return;
        }
        resolve(j);
      },
      fail: reject,
    });
  });
}

/** 分享后调用：兑换额外的 AI 重绘次数。 */
function claimShareBonus() {
  if (USE_CLOUD) {
    return _callContainer("/photo/quota/share-bonus", { data: {} });
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}/photo/quota/share-bonus`,
      method: "POST",
      timeout: 10000,
      success(res) {
        const j = typeof res.data === "object" ? res.data : parseJsonSafe(res.data);
        if (res.statusCode >= 400) {
          reject(j.detail || j.error || res.data);
          return;
        }
        resolve(j);
      },
      fail: reject,
    });
  });
}

// ========== /photo ==========

/** options: { redraw?: boolean, artStyle?: string } */
async function photoTranslate(filePath, options) {
  const opts = options || {};
  const redraw = !!opts.redraw;
  const artStyle = opts.artStyle || "ghibli";

  if (USE_CLOUD) {
    const file_b64 = await _readFileBase64(filePath);
    return _callContainer("/photo", {
      data: {
        file_b64,
        filename: _basename(filePath),
        redraw,
        art_style: artStyle,
      },
    });
  }
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${API_BASE}/photo`,
      filePath,
      name: "file",
      formData: {
        redraw: redraw ? "1" : "0",
        art_style: artStyle,
      },
      success(res) {
        const j = parseJsonSafe(res.data);
        if (res.statusCode >= 400) {
          reject(j.detail || j.error || res.data);
          return;
        }
        resolve(j);
      },
      fail(err) {
        reject(err);
      },
    });
  });
}

function _sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 轮询 AI 重绘异步任务结果。callContainer 客户端硬超时 15s，
 *  所以重绘必须走异步：start 立即返回 task_id，前端每 1s 轮询。
 *
 *  POLL_INTERVAL: 1000ms 一次（让完成后最快 1s 内回显，降低体感等待）
 *  MAX_WAIT:      60s（极速档多数 10-25s，超时就提示稍后重试）
 */
async function _pollRedrawResult(taskId, onProgress) {
  const POLL_INTERVAL = 1000;
  const MAX_WAIT_MS = 60000;
  const started = Date.now();

  while (Date.now() - started < MAX_WAIT_MS) {
    await _sleep(POLL_INTERVAL);
    const elapsed = Math.round((Date.now() - started) / 1000);

    if (typeof onProgress === "function") {
      try {
        onProgress({ phase: "redraw", elapsed, max: MAX_WAIT_MS / 1000 });
      } catch (e) {}
    }

    let r;
    try {
      r = await _callContainer("/photo/redraw/result", {
        data: { task_id: taskId },
      });
    } catch (e) {
      // 单次轮询失败（网络抖动）不致命，继续重试
      console.warn("[poll] 单次轮询失败，2.5s 后重试", e);
      continue;
    }

    if (!r || typeof r !== "object") continue;
    if (r.status === "done") return r;
    if (r.status === "error") {
      throw new Error(r.error || "AI 重绘失败");
    }
    // pending / running 继续轮询
  }
  throw new Error(`AI 重绘超时（${Math.round(MAX_WAIT_MS / 1000)}s 内未完成），请稍后再试`);
}

/**
 * 分片版照片翻译 —— 绕开 callContainer 1MB 请求体限制。
 *   onProgress 回调形态：
 *     - 上传阶段: { done, total }
 *     - 重绘阶段: { phase: "redraw", elapsed, max }
 *   options: { redraw?: boolean, artStyle?: string }
 *
 *  AI 重绘最后一片走异步：服务端立即返回 task_id，
 *  本函数自动轮询 /photo/redraw/result 直到完成。
 */
async function photoTranslateChunked(filePath, onProgress, options) {
  const opts = options || {};
  if (!USE_CLOUD) {
    return photoTranslate(filePath, opts);
  }
  const redraw = !!opts.redraw;
  const artStyle = opts.artStyle || "ghibli";

  const fullB64 = await _readFileBase64(filePath);
  const filename = _basename(filePath) || "upload.jpg";
  // callContainer 单次请求体限制约 1MB。200KB 可把请求次数降到原来的 1/4，
  // 同时保留足够余量，上传阶段更快。
  const CHUNK = 200 * 1024;
  const total = Math.max(1, Math.ceil(fullB64.length / CHUNK));
  const sessionId = `s_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;

  let lastResult = null;
  for (let i = 0; i < total; i++) {
    const slice = fullB64.slice(i * CHUNK, (i + 1) * CHUNK);
    const isLast = i === total - 1;
    const r = await _callContainer("/photo/chunk", {
      data: {
        session_id: sessionId,
        chunk_index: i,
        total_chunks: total,
        chunk_b64: slice,
        filename,
        is_last: isLast,
        redraw,
        art_style: artStyle,
      },
      // 每片必须 < 15s 硬超时，正常 < 2s 没问题
      timeoutMs: 14000,
    });
    if (typeof onProgress === "function") {
      try {
        onProgress({ done: i + 1, total });
      } catch (e) {}
    }
    if (isLast) lastResult = r;
  }

  // 如果是异步重绘任务 → 自动开始轮询
  if (lastResult && lastResult.async && lastResult.task_id) {
    return _pollRedrawResult(lastResult.task_id, onProgress);
  }
  return lastResult;
}

// ========== /cat /dog ==========

async function voiceTranslate(pet, opts) {
  const path = pet === "cat" ? "/cat" : "/dog";
  const { mode, lang, voiceGender, text, audioPath } = opts || {};

  if (USE_CLOUD) {
    const data = {
      mode,
      lang: lang || "zh",
      voice_gender: voiceGender || "female",
      text: text || "",
    };
    if (audioPath) {
      data.audio_b64 = await _readFileBase64(audioPath);
      data.audio_filename = _basename(audioPath) || "audio.aac";
    }
    return _callContainer(path, { data });
  }

  const url = `${API_BASE}${path}`;
  if (mode === "pet_to_human" && audioPath) {
    return new Promise((resolve, reject) => {
      wx.uploadFile({
        url,
        filePath: audioPath,
        name: "audio",
        formData: {
          mode,
          lang: lang || "zh",
          voice_gender: voiceGender || "female",
          text: text || "",
        },
        success(res) {
          const j = parseJsonSafe(res.data);
          if (res.statusCode >= 400) {
            reject(j.detail || j.error || res.data);
            return;
          }
          resolve(j);
        },
        fail(err) {
          reject(err);
        },
      });
    });
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method: "POST",
      header: { "content-type": "application/x-www-form-urlencoded" },
      data: {
        mode,
        lang: lang || "zh",
        voice_gender: voiceGender || "female",
        text: text || "",
      },
      timeout: 60000,
      success(res) {
        const j =
          typeof res.data === "object" ? res.data : parseJsonSafe(res.data);
        if (res.statusCode >= 400) {
          reject(j.detail || j.error || res.data);
          return;
        }
        resolve(j);
      },
      fail(err) {
        reject(err);
      },
    });
  });
}

/**
 * 分片版语音翻译 —— 绕开 callContainer 1MB 请求体限制。
 * pet: "cat" | "dog"
 * opts: { mode, lang, voiceGender, text, audioPath }
 * onProgress({ done, total }) 可选。
 *
 * 没有 audioPath（纯文本翻译）时直接走老的单次 voiceTranslate，
 * 因为请求体本来就很小不会触发 -606001。
 */
async function voiceTranslateChunked(pet, opts, onProgress) {
  const { mode, lang, voiceGender, text, audioPath } = opts || {};
  if (!USE_CLOUD || !audioPath) {
    return voiceTranslate(pet, opts);
  }

  const fullB64 = await _readFileBase64(audioPath);
  const filename = _basename(audioPath) || "audio.aac";
  const CHUNK = 50 * 1024;
  const total = Math.max(1, Math.ceil(fullB64.length / CHUNK));
  const sessionId = `v_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;

  let lastResult = null;
  for (let i = 0; i < total; i++) {
    const slice = fullB64.slice(i * CHUNK, (i + 1) * CHUNK);
    const isLast = i === total - 1;
    const r = await _callContainer("/voice/chunk", {
      data: {
        session_id: sessionId,
        chunk_index: i,
        total_chunks: total,
        chunk_b64: slice,
        filename,
        is_last: isLast,
        pet,
        mode,
        lang: lang || "zh",
        voice_gender: voiceGender || "female",
        text: text || "",
      },
    });
    if (typeof onProgress === "function") {
      try {
        onProgress({ done: i + 1, total });
      } catch (e) {}
    }
    if (isLast) lastResult = r;
  }
  return lastResult;
}

module.exports = {
  API_BASE,
  USE_CLOUD,
  photoTranslate,
  photoTranslateChunked,
  voiceTranslate,
  voiceTranslateChunked,
  pingHealth,
  getPhotoQuota,
  claimShareBonus,
};

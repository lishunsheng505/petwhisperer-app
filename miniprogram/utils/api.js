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

/** 调用云托管容器（HTTPS 自动 + 鉴权 + 不用配合法域名）。 */
function _callContainer(path, { data, header } = {}) {
  return new Promise((resolve, reject) => {
    if (!wx.cloud || !wx.cloud.callContainer) {
      reject(new Error("当前微信版本不支持 wx.cloud.callContainer"));
      return;
    }
    wx.cloud.callContainer({
      config: { env: CLOUD_ENV },
      path,
      method: "POST",
      header: Object.assign(
        {
          "X-WX-SERVICE": CLOUD_SERVICE,
          "content-type": "application/json",
        },
        header || {}
      ),
      data: data || {},
      timeout: 60000,
      success(res) {
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
        reject(err);
      },
    });
  });
}

// ========== /photo ==========

async function photoTranslate(filePath) {
  if (USE_CLOUD) {
    const file_b64 = await _readFileBase64(filePath);
    return _callContainer("/photo", {
      data: { file_b64, filename: _basename(filePath) },
    });
  }
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url: `${API_BASE}/photo`,
      filePath,
      name: "file",
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

module.exports = {
  API_BASE,
  USE_CLOUD,
  photoTranslate,
  voiceTranslate,
};

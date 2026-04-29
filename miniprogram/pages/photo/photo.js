const { photoTranslateChunked } = require("../../utils/api.js");
const history = require("../../utils/history.js");
const { toFriendly, withRetry, isFriendlyError } = require("../../utils/errors.js");

const ALLOWED_EXT = ["jpg", "jpeg", "png", "webp", "heic", "heif", "bmp", "gif"];
const PREVIEWABLE = ["jpg", "jpeg", "png", "webp", "bmp", "gif"];

function _ext(name) {
  if (!name) return "";
  const i = name.lastIndexOf(".");
  return i < 0 ? "" : name.slice(i + 1).toLowerCase();
}

function _basename(p) {
  if (!p) return "upload.jpg";
  const norm = p.replace(/\\/g, "/");
  const i = norm.lastIndexOf("/");
  return i < 0 ? norm : norm.slice(i + 1);
}

/** 分片上传后无需压缩，仅作"过大就先轻压一次"的安全网。 */
const MAX_UPLOAD_BYTES = 6 * 1024 * 1024;
const COMPRESSIBLE = ["jpg", "jpeg", "png"];

function _statSize(p) {
  return new Promise((resolve) => {
    wx.getFileSystemManager().stat({
      path: p,
      success: (r) => resolve((r.stats && r.stats.size) || 0),
      fail: () => resolve(0),
    });
  });
}

function _compressOnce(src, quality) {
  return new Promise((resolve, reject) => {
    wx.compressImage({
      src,
      quality,
      success: (r) => resolve(r.tempFilePath),
      fail: reject,
    });
  });
}

async function ensureUnderLimit(src, ext) {
  const e = (ext || "").toLowerCase();
  if (COMPRESSIBLE.indexOf(e) < 0) return src;
  let cur = src;
  let size = await _statSize(cur);
  if (!size || size <= MAX_UPLOAD_BYTES) return cur;
  for (const q of [80, 60, 40, 25, 15]) {
    try {
      const next = await _compressOnce(cur, q);
      const ns = await _statSize(next);
      cur = next;
      size = ns || size;
      if (size <= MAX_UPLOAD_BYTES) return cur;
    } catch (e) {
      break;
    }
  }
  return cur;
}

Page({
  data: {
    previewPath: "",
    fileName: "",
    loading: false,
    errorMsg: "",
    quote: "",
    persona: "",
    vibe: "",
    vibeLabel: "",
    palette: [],
    pets: [],
    posterImageSrc: "",

    historyOpen: false,
    historyList: [],
  },

  onShow() {
    this.setData({ historyList: this._buildHistory() });
  },

  _buildHistory() {
    const items = history.list("photo") || [];
    return items.map((it) => Object.assign({}, it, { _t: history.fmtTime(it.time) }));
  },

  openHistory() {
    this.setData({ historyOpen: true, historyList: this._buildHistory() });
  },
  closeHistory() {
    this.setData({ historyOpen: false });
  },
  clearHistory() {
    wx.showModal({
      title: "清空历史？",
      content: "本地保存的照片翻译记录将全部删除（不可恢复）",
      confirmColor: "#FF6B6B",
      success: (r) => {
        if (r.confirm) {
          history.clear("photo");
          this.setData({ historyList: [] });
        }
      },
    });
  },
  reopenHistoryItem(e) {
    const idx = e.currentTarget.dataset.idx;
    const it = (this.data.historyList || [])[idx];
    if (!it) return;
    this.setData({
      historyOpen: false,
      quote: it.quote || "",
      persona: it.persona || "",
      vibe: it.vibe || "",
      vibeLabel: it.vibeLabel || "",
      palette: it.palette || [],
      pets: it.pets || [],
      previewPath: it.previewPath || "",
      fileName: it.fileName || "（历史记录）",
      posterImageSrc: it.posterImageSrc || "",
      errorMsg: "",
    });
  },

  onShareAppMessage() {
    const txt = (this.data.quote || "").trim();
    return {
      title: txt
        ? "我家毛孩子的趣味文案：" + txt.slice(0, 24)
        : "喵汪心语 · 给毛孩子做一张趣味海报",
      path: "/pages/index/index",
      imageUrl: this.data.posterImageSrc || "/images/pet.png",
    };
  },
  onShareTimeline() {
    return {
      title: this.data.quote
        ? "我家毛孩子的趣味海报"
        : "喵汪心语 · 萌宠趣味海报",
      query: "",
      imageUrl: this.data.posterImageSrc || "/images/pet.png",
    };
  },

  chooseFromAlbum() {
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      sourceType: ["album", "camera"],
      sizeType: ["compressed"],
      success: (res) => {
        const f = res.tempFiles && res.tempFiles[0];
        if (!f) return;
        this._acceptFile(f.tempFilePath, _basename(f.tempFilePath));
      },
      fail: (e) => {
        if (e && e.errMsg && e.errMsg.indexOf("cancel") >= 0) return;
        this.setData({ errorMsg: "选择图片失败：" + (e.errMsg || e) });
      },
    });
  },

  chooseFromFile() {
    wx.chooseMessageFile({
      count: 1,
      type: "file",
      extension: ALLOWED_EXT,
      success: (res) => {
        const f = res.tempFiles && res.tempFiles[0];
        if (!f) return;
        const name = f.name || _basename(f.path);
        this._acceptFile(f.path, name);
      },
      fail: (e) => {
        if (e && e.errMsg && e.errMsg.indexOf("cancel") >= 0) return;
        this.setData({ errorMsg: "选择文件失败：" + (e.errMsg || e) });
      },
    });
  },

  _acceptFile(path, name) {
    const ext = _ext(name);
    if (ext && ALLOWED_EXT.indexOf(ext) < 0) {
      this.setData({
        errorMsg: `不支持的格式 .${ext}，仅支持 ${ALLOWED_EXT.join(" / ")}`,
      });
      return;
    }
    const isPreviewable = PREVIEWABLE.indexOf(ext) >= 0 || !ext;
    this.setData({
      previewPath: isPreviewable ? path : "",
      fileName: name || _basename(path),
      errorMsg: isPreviewable
        ? ""
        : "HEIC/HEIF 无法本地预览，将由后端解析",
      _selectedPath: path,
    });
  },

  clearFile() {
    this.setData({
      previewPath: "",
      fileName: "",
      _selectedPath: "",
      errorMsg: "",
      quote: "",
      persona: "",
      vibe: "",
      vibeLabel: "",
      palette: [],
      pets: [],
      posterImageSrc: "",
    });
    this._posterBase64 = "";
  },

  previewLocal() {
    if (this.data.previewPath) {
      wx.previewImage({ urls: [this.data.previewPath] });
    }
  },

  async startTranslate() {
    const path = this.data._selectedPath;
    if (!path) {
      this.setData({ errorMsg: "请先选择图片" });
      return;
    }
    const net = await _getNetType();
    if (net === "none") {
      this.setData({ errorMsg: "网络似乎断开了，请检查 Wi-Fi 或数据连接" });
      return;
    }

    this.setData({
      loading: true,
      errorMsg: "",
      quote: "",
      persona: "",
      vibe: "",
      vibeLabel: "",
      palette: [],
      pets: [],
      posterImageSrc: "",
    });
    this._posterBase64 = "";
    wx.showLoading({ title: "上传中 0%", mask: true });

    let coldHintTimer = setTimeout(() => {
      wx.showLoading({ title: "服务器正在唤醒…", mask: true });
    }, 8000);

    try {
      const ext = (this.data.fileName || "").split(".").pop().toLowerCase();
      const finalPath = await ensureUnderLimit(path, ext);
      const finalSize = await _statSize(finalPath);
      console.log("[upload size]", finalSize, "bytes");
      const r = await withRetry(() =>
        photoTranslateChunked(finalPath, ({ done, total }) => {
          const pct = Math.floor((done / total) * 100);
          wx.showLoading({
            title: pct < 100 ? `上传中 ${pct}%` : "生成中…",
            mask: true,
          });
        })
      );
      clearTimeout(coldHintTimer);

      const a = (r && r.analysis) || {};
      const posterB64 = (r && r.poster_image_base64) || "";
      this._posterBase64 = posterB64;

      let posterPath = "";
      if (posterB64) {
        posterPath = await new Promise((resolve) => {
          const fs = wx.getFileSystemManager();
          const p = `${wx.env.USER_DATA_PATH}/poster_view_${Date.now()}.png`;
          fs.writeFile({
            filePath: p,
            data: posterB64,
            encoding: "base64",
            success: () => resolve(p),
            fail: () => resolve(""),
          });
        });
      }

      wx.hideLoading();
      const next = {
        loading: false,
        quote: a.quote_cn || "",
        persona: a.persona || "",
        vibe: a.vibe || "",
        vibeLabel: a.vibe_label_cn || "",
        palette: a.palette || [],
        pets: a.pets || [],
        posterImageSrc: posterPath,
      };
      this.setData(next);

      history.add("photo", {
        quote: next.quote,
        persona: next.persona,
        vibe: next.vibe,
        vibeLabel: next.vibeLabel,
        palette: next.palette,
        pets: next.pets,
        previewPath: this.data.previewPath,
        fileName: this.data.fileName,
        posterImageSrc: next.posterImageSrc,
      });
      this.setData({ historyList: this._buildHistory() });
    } catch (e) {
      clearTimeout(coldHintTimer);
      wx.hideLoading();
      const msg = toFriendly(e, "photo/translate");
      this.setData({
        loading: false,
        errorMsg: isFriendlyError(e) ? msg : "翻译失败：" + msg,
      });
    }
  },

  savePoster() {
    const b64 = this._posterBase64;
    if (!b64) return;
    const fs = wx.getFileSystemManager();
    const tempPath = `${wx.env.USER_DATA_PATH}/poster_${Date.now()}.png`;
    fs.writeFile({
      filePath: tempPath,
      data: b64,
      encoding: "base64",
      success: () => {
        wx.saveImageToPhotosAlbum({
          filePath: tempPath,
          success: () => wx.showToast({ title: "已保存到相册", icon: "success" }),
          fail: (e) => {
            if (e.errMsg && e.errMsg.indexOf("auth") >= 0) {
              wx.showModal({
                title: "需要相册权限",
                content: "请到设置里允许保存到相册",
                showCancel: false,
              });
            } else {
              wx.showToast({ title: "保存失败", icon: "none" });
            }
          },
        });
      },
      fail: () => wx.showToast({ title: "处理图片失败", icon: "none" }),
    });
  },

  previewPoster() {
    if (!this.data.posterImageSrc) return;
    wx.previewImage({ urls: [this.data.posterImageSrc] });
  },

  noop() {},
});

function _getNetType() {
  return new Promise((resolve) => {
    wx.getNetworkType({
      success: (r) => resolve(r.networkType || "unknown"),
      fail: () => resolve("unknown"),
    });
  });
}

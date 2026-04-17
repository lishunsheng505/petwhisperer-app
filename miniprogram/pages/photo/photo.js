const { photoTranslate } = require("../../utils/api.js");

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
  },

  chooseFromAlbum() {
    wx.chooseMedia({
      count: 1,
      mediaType: ["image"],
      sourceType: ["album", "camera"],
      sizeType: ["original", "compressed"],
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
    wx.showLoading({ title: "AI 分析中…", mask: true });
    try {
      const r = await photoTranslate(path);
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
      this.setData({
        loading: false,
        quote: a.quote_cn || "",
        persona: a.persona || "",
        vibe: a.vibe || "",
        vibeLabel: a.vibe_label_cn || "",
        palette: a.palette || [],
        pets: a.pets || [],
        posterImageSrc: posterPath,
      });
    } catch (e) {
      wx.hideLoading();
      this.setData({
        loading: false,
        errorMsg:
          "翻译失败：" +
          (typeof e === "string"
            ? e
            : e.errMsg || e.message || JSON.stringify(e)),
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
});

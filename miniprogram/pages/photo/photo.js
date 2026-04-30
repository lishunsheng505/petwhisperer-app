const {
  photoTranslateChunked,
  getPhotoQuota,
  claimShareBonus,
  registerRedrawNotify,
  pollRedrawOnce,
} = require("../../utils/api.js");
const history = require("../../utils/history.js");
const pending = require("../../utils/redraw_pending.js");
const { toFriendly, withRetry, isFriendlyError } = require("../../utils/errors.js");

// 订阅消息模板 ID。请到 mp.weixin.qq.com → 订阅消息 → 我的模板里申请，
// 然后在小程序后台 / 这个常量里填入你的 tmplId。
// 模板字段建议：thing1(标题) thing2(结果) time3(时间) thing4(备注)
// 见 backend/wx_subscribe.py 顶部注释。
// 如果留空，前端会跳过订阅授权步骤，回退到原来的"全程等待"流程。
const REDRAW_NOTIFY_TMPL_ID = "";  // ← TODO: 填模板 ID 后启用推送

const ART_STYLE_OPTIONS = [
  { key: "ghibli", label: "吉卜力", emoji: "🌿" },
  { key: "oil", label: "古典油画", emoji: "🖼️" },
  { key: "ink", label: "中国水墨", emoji: "🖌️" },
  { key: "pixel", label: "像素风", emoji: "👾" },
  { key: "lego", label: "乐高积木", emoji: "🧱" },
  { key: "watercolor", label: "水彩淡彩", emoji: "🎨" },
  { key: "crayon", label: "蜡笔童画", emoji: "🖍️" },
  { key: "cyberpunk", label: "霓虹朋克", emoji: "🌃" },
  { key: "ukiyo", label: "浮世绘", emoji: "🌊" },
  { key: "vapor", label: "蒸汽波80s", emoji: "🌅" },
];

// 等待 AI 绘制时的简短文案。微信 showLoading 标题只能放 6-7 个汉字，
// 后面还要拼倒计时 "Xs"，所以每条限制 ≤ 5 个汉字，避免被截断。
const REDRAW_WAITING_TIPS = [
  "AI 调色中",
  "勾线中",
  "上色中",
  "润色中",
  "马上好啦",
  "最后一笔",
];

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

    mode: "origin",
    artStyle: "ghibli",
    artStyleOptions: ART_STYLE_OPTIONS,
    redrawRemaining: 5,
    redrawLimit: 5,
    redrawBonus: 0,

    historyOpen: false,
    historyList: [],
  },

  onShow() {
    this.setData({ historyList: this._buildHistory() });
    this._refreshQuota();
    // 用户从订阅消息 / 后台回到小程序时，自动检查待领取的 AI 重绘任务
    this._checkPendingRedraws();
  },

  onLoad(query) {
    // 订阅消息推送进来会带 ?task_id=xxx，记一下，等 onShow 处理
    if (query && query.task_id) {
      this._notifiedTaskId = String(query.task_id);
    }
  },

  /** 扫描本地待领取队列，逐个拉一次结果。
   *  done   → 直接展示在页面上（覆盖当前预览）
   *  error  → toast + 移除
   *  其他   → 留着下次再拉
   *  特殊：若推送带过来的 task_id 在队列里，优先它
   */
  async _checkPendingRedraws() {
    let list = pending.listPending();
    if (!list.length) return;

    if (this._notifiedTaskId) {
      const tid = this._notifiedTaskId;
      this._notifiedTaskId = "";
      list = [
        ...list.filter((x) => x.task_id === tid),
        ...list.filter((x) => x.task_id !== tid),
      ];
    }

    for (const item of list) {
      try {
        const r = await pollRedrawOnce(item.task_id);
        if (!r) continue;

        if (r.status === "done") {
          pending.remove(item.task_id);
          await this._renderRedrawResult(r, item);
          // 一次只展示一个，避免多个完成时刷得太快
          return;
        }
        if (r.status === "error") {
          pending.remove(item.task_id);
          wx.showToast({
            title: `${item.style_label || "AI 重绘"} 任务失败`,
            icon: "none",
            duration: 2200,
          });
          continue;
        }
        // pending / running，继续等
      } catch (e) {
        console.warn("[pending] 拉取失败", item.task_id, e);
      }
    }
  },

  /** 把后端结果渲染到当前页面（与同步流程的展示逻辑保持一致）。 */
  async _renderRedrawResult(r, item) {
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
          fail: (e) => {
            console.warn("[poster/async] writeFile 失败，使用 data URL", e);
            resolve("");
          },
        });
      });
      if (!posterPath) {
        posterPath = `data:image/png;base64,${posterB64}`;
      }
    }

    const petsRaw = Array.isArray(a.pets) ? a.pets : [];
    const petsDisplay = petsRaw
      .map((p) => {
        if (!p) return "";
        if (typeof p === "string") return p;
        if (typeof p === "object") {
          return p.individual_quote || p.type || "";
        }
        return String(p);
      })
      .filter(Boolean);

    const next = {
      loading: false,
      errorMsg: "",
      quote: a.quote_cn || a.quote || "",
      persona: a.persona || "",
      vibe: a.vibe || "",
      vibeLabel: a.vibe_label_cn || a.vibe_label || "",
      palette: a.palette || [],
      pets: petsDisplay,
      posterImageSrc: posterPath || this.data.previewPath || "",
    };
    if (typeof r.redraw_remaining === "number") next.redrawRemaining = r.redraw_remaining;
    if (typeof r.redraw_limit === "number") next.redrawLimit = r.redraw_limit;
    this.setData(next);

    history.add("photo", {
      time: Date.now(),
      previewPath: this.data.previewPath,
      fileName: this.data.fileName || `${item.style_label || "AI 重绘"}.png`,
      quote: next.quote,
      persona: next.persona,
      vibe: next.vibe,
      vibeLabel: next.vibeLabel,
      palette: next.palette,
      pets: next.pets,
      posterImageSrc: posterPath,
    });

    wx.showToast({
      title: `${item.style_label || "AI 重绘"} 海报已生成`,
      icon: "success",
      duration: 2200,
    });
  },

  /** 弹"是否后台运行"决策框。返回 'wait' / 'background'。
   *  默认按钮 = 继续等待（用户多数还在页面上等）。
   */
  _askDeferOrWait(estimatedSec) {
    const sec = estimatedSec || 30;
    return new Promise((resolve) => {
      wx.hideLoading();
      wx.showModal({
        title: "AI 已开始绘制 🎨",
        content: `预计还要 ${sec} 秒左右。\n您可以继续等，也可以先去逛别的，做完会通过微信通知您。`,
        confirmText: "继续等待",
        cancelText: "稍后通知我",
        confirmColor: "#FF6B6B",
        success(r) {
          resolve(r.confirm ? "wait" : "background");
        },
        fail() {
          resolve("wait");
        },
      });
    });
  },

  /** 触发订阅消息授权。返回 true=用户接受，false=拒绝/未配置/失败。
   *  必须在用户点击事件回调内同步调用 wx.requestSubscribeMessage。
   */
  _requestSubscribePush() {
    return new Promise((resolve) => {
      const tplId = REDRAW_NOTIFY_TMPL_ID;
      if (!tplId) {
        // 没配模板 ID → 直接 false，回退到全程等待流程
        resolve(false);
        return;
      }
      wx.requestSubscribeMessage({
        tmplIds: [tplId],
        success(res) {
          const ok = res && res[tplId] === "accept";
          resolve(!!ok);
        },
        fail() {
          resolve(false);
        },
      });
    });
  },

  _refreshQuota() {
    getPhotoQuota()
      .then((r) => {
        if (!r) return;
        const next = {};
        if (typeof r.redraw_remaining === "number") {
          next.redrawRemaining = r.redraw_remaining;
        }
        if (typeof r.redraw_limit === "number") {
          next.redrawLimit = r.redraw_limit;
        }
        if (typeof r.redraw_bonus === "number") {
          next.redrawBonus = r.redraw_bonus;
        }
        this.setData(next);
      })
      .catch((e) => {
        console.warn("[quota] 获取剩余次数失败", e);
      });
  },

  switchMode(e) {
    const m = e.currentTarget.dataset.mode || "origin";
    if (m === this.data.mode) return;
    if (m === "redraw") {
      wx.vibrateShort({ type: "light" });
    }
    this.setData({ mode: m, errorMsg: "" });
  },

  switchStyle(e) {
    const k = e.currentTarget.dataset.key;
    if (!k || k === this.data.artStyle) return;
    this.setData({ artStyle: k });
  },

  _maybePromptShareBonus() {
    const _self = this;
    wx.showModal({
      title: "想再画几张？",
      content: "把海报分享给好友 / 朋友圈\n每分享一次 +2 次 AI 重绘机会（每天最高 20 次）",
      confirmText: "去分享",
      cancelText: "下次",
      confirmColor: "#FF6B6B",
      success(r) {
        if (r.confirm) {
          // 调起分享菜单（用户必须主动点系统分享按钮才能真分享）
          wx.showShareMenu({ withShareTicket: false });
          wx.showToast({
            title: "请点击右上角 ··· 选择分享",
            icon: "none",
            duration: 2200,
          });
          // 标记一下，等用户从分享回来时领取奖励
          _self._pendingShareBonus = true;
        }
      },
    });
  },

  onShareAppMessage() {
    // 复用现有 onShareAppMessage（在下方），分享触发时尝试领奖
    if (this._pendingShareBonus) {
      this._pendingShareBonus = false;
      claimShareBonus()
        .then((r) => {
          if (r && r.ok) {
            this.setData({
              redrawRemaining: r.redraw_remaining,
              redrawLimit: r.redraw_limit,
              redrawBonus: r.redraw_bonus || 0,
            });
            wx.showToast({
              title: `已 +${r.added} 次重绘机会！`,
              icon: "success",
              duration: 2200,
            });
          }
        })
        .catch((e) => {
          console.warn("[bonus] 领取失败", e);
        });
    }
    const txt = (this.data.quote || "").trim();
    return {
      title: txt
        ? "我家毛孩子的趣味文案：" + txt.slice(0, 24)
        : "喵汪心语 · 给毛孩子做一张趣味海报",
      path: "/pages/index/index",
      imageUrl: this.data.posterImageSrc || "/images/pet.png",
    };
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

  onShareTimeline() {
    if (this._pendingShareBonus) {
      this._pendingShareBonus = false;
      claimShareBonus()
        .then((r) => {
          if (r && r.ok) {
            this.setData({
              redrawRemaining: r.redraw_remaining,
              redrawLimit: r.redraw_limit,
              redrawBonus: r.redraw_bonus || 0,
            });
            wx.showToast({
              title: `已 +${r.added} 次重绘机会！`,
              icon: "success",
              duration: 2200,
            });
          }
        })
        .catch((e) => console.warn("[bonus] 领取失败", e));
    }
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

    const isRedraw = this.data.mode === "redraw";
    const artStyle = this.data.artStyle;
    const styleOption =
      (this.data.artStyleOptions || []).find((s) => s.key === artStyle) || {};
    const styleLabel = styleOption.label || "AI 风格";

    // ---- AI 重绘模式：必须在"用户点击事件"的同步上下文里请求订阅授权 ----
    // 如果模板 ID 没配置，_requestSubscribePush 直接返回 false，回退到全程等待。
    let subscribeAccepted = false;
    if (isRedraw) {
      subscribeAccepted = await this._requestSubscribePush();
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
      // 不限制用户上传图片；这里只在微信请求体过大时做透明压缩安全网。
      // AI 重绘提速在后端完成（缩到 640 长边 + 8 步），不靠限制用户原图。
      const finalPath = await ensureUnderLimit(path, ext);
      const finalSize = await _statSize(finalPath);
      console.log("[upload size]", finalSize, "bytes");

      const apiOpts = {
        redraw: isRedraw,
        artStyle,
        // 异步任务被创建时回调：注册推送 + 询问是否后台跑
        onTaskCreated: async (taskId) => {
          // 1. 用户同意了订阅 → 把 task_id 绑定到 openid 上，做完会推送
          if (subscribeAccepted) {
            try {
              await registerRedrawNotify(taskId);
            } catch (e) {
              console.warn("[subscribe] notify_consent 注册失败", e);
            }
          }
          // 2. 弹"是否后台跑"决策框
          const decision = await this._askDeferOrWait(30);
          if (decision === "background") {
            // 把任务存本地，下次 onShow 自动拉
            pending.add({
              task_id: taskId,
              art_style: artStyle,
              style_label: styleLabel,
              created_at: Date.now(),
            });
            wx.showToast({
              title: subscribeAccepted
                ? "AI 在后台绘制，做完微信通知您"
                : "AI 在后台绘制，下次回来自动展示",
              icon: "none",
              duration: 2400,
            });
          } else {
            // 选了"继续等待"，重新挂上 loading 让轮询有 UI
            wx.showLoading({ title: "AI 调色中 0s", mask: true });
          }
          return decision;
        },
      };

      const r = await withRetry(() =>
        photoTranslateChunked(
          finalPath,
          (p) => {
            let title;
            if (p && p.phase === "redraw") {
              const tipIdx = Math.floor(p.elapsed / 3) % REDRAW_WAITING_TIPS.length;
              title = `${REDRAW_WAITING_TIPS[tipIdx]} ${p.elapsed}s`;
            } else if (p && typeof p.done === "number") {
              const pct = Math.floor((p.done / p.total) * 100);
              if (pct < 100) {
                title = `上传中 ${pct}%`;
              } else if (isRedraw) {
                title = "AI 启动中";
              } else {
                title = "生成中";
              }
            } else {
              title = "处理中…";
            }
            wx.showLoading({ title, mask: true });
          },
          apiOpts
        )
      );
      clearTimeout(coldHintTimer);

      // 用户选了"稍后通知我" → 任务在后台跑，本次直接结束
      if (r && r.deferred) {
        wx.hideLoading();
        this.setData({ loading: false });
        return;
      }

      const a = (r && r.analysis) || {};
      const posterB64 = (r && r.poster_image_base64) || "";
      this._posterBase64 = posterB64;

      const quotaPatch = {};
      if (typeof r.redraw_remaining === "number") {
        quotaPatch.redrawRemaining = r.redraw_remaining;
      }
      if (typeof r.redraw_limit === "number") {
        quotaPatch.redrawLimit = r.redraw_limit;
      }
      if (typeof r.redraw_bonus === "number") {
        quotaPatch.redrawBonus = r.redraw_bonus;
      }

      if (a.redraw_error) {
        wx.showToast({
          title: "AI 重绘失败，已回退原图渲染",
          icon: "none",
          duration: 2200,
        });
      }

      // 海报图片来源：优先写本地文件（saveImageToPhotosAlbum 才能用文件路径），
      // 写文件失败时 fallback 到 data URL（image 组件兼容，至少不会"图没了"）。
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
            fail: (e) => {
              console.warn("[poster] writeFile 失败，使用 data URL", e);
              resolve("");
            },
          });
        });
        if (!posterPath) {
          posterPath = `data:image/png;base64,${posterB64}`;
        }
      }

      // pets 字段是对象数组（后端返回 {type, head_x, head_y, individual_quote}），
      // 渲染前压平成可显示的字符串数组，避免 wxml {{item}} 出现 [object Object]
      const petsRaw = Array.isArray(a.pets) ? a.pets : [];
      const petsDisplay = petsRaw
        .map((p) => {
          if (!p) return "";
          if (typeof p === "string") return p;
          if (typeof p === "object") {
            return p.individual_quote || p.type || "";
          }
          return String(p);
        })
        .filter(Boolean);

      wx.hideLoading();
      const next = Object.assign(
        {
          loading: false,
          quote: a.quote_cn || "",
          persona: a.persona || "",
          vibe: a.vibe || "",
          vibeLabel: a.vibe_label_cn || "",
          palette: a.palette || [],
          pets: petsDisplay,
          posterImageSrc: posterPath,
        },
        quotaPatch
      );
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

      // 用了重绘 + 配额所剩不多时，温柔引导分享解锁
      if (a.redraw_used && next.redrawRemaining <= 2) {
        this._maybePromptShareBonus();
      }
    } catch (e) {
      clearTimeout(coldHintTimer);
      wx.hideLoading();
      const msg = toFriendly(e, "photo/translate");
      const raw = (typeof e === "string" ? e : (e && (e.detail || e.message))) || "";
      const isQuota = /次数已用完|每日|429/.test(String(raw));
      this.setData({
        loading: false,
        errorMsg: isFriendlyError(e) ? msg : "翻译失败：" + msg,
      });
      if (isQuota) {
        this._refreshQuota();
      }
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

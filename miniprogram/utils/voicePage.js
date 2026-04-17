const { voiceTranslate } = require("./api.js");
const recorder = require("./recorder.js");
const { createPlayer } = require("./player.js");

const LANG_LIST = [
  { value: "zh", label: "中文" },
  { value: "en", label: "English" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
];

const VOICE_LIST = [
  { value: "female", label: "女声" },
  { value: "male", label: "男声" },
];

function _errMsg(e) {
  if (!e) return "未知错误";
  if (typeof e === "string") return e;
  if (e.errMsg) return e.errMsg;
  if (e.message) return e.message;
  try { return JSON.stringify(e); } catch (_) { return "" + e; }
}

const STEP_HEADS = ["🗣️", "🤲", "🎯", "💡"];

function parseGuide(text) {
  if (!text) return [];
  const out = [];
  const re = /(🗣️|🤲|🎯|💡)([^\n]*)\n?([\s\S]*?)(?=(?:🗣️|🤲|🎯|💡|$))/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const icon = m[1];
    const headRest = (m[2] || "").trim();
    const body = (m[3] || "").trim();
    out.push({ icon, head: headRest || _defaultHead(icon), body });
  }
  return out;
}

function _stripBase64(obj) {
  if (!obj || typeof obj !== "object") return obj;
  const out = {};
  for (const k in obj) {
    if (k === "tts_audio_base64" || k === "animal_audio_base64") continue;
    out[k] = obj[k];
  }
  return out;
}

function _defaultHead(icon) {
  switch (icon) {
    case "🗣️": return "说这句话";
    case "🤲": return "做这个动作";
    case "🎯": return "给这个奖励";
    case "💡": return "小贴士";
    default: return "";
  }
}

function createVoicePage(pet) {
  const isCat = pet === "cat";
  return {
    data: {
      pet,
      petName: isCat ? "猫" : "狗",
      petEmoji: isCat ? "🐱" : "🐶",
      petLang: isCat ? "喵" : "汪",
      petColor: isCat ? "#FF6B6B" : "#FF8C42",

      langList: LANG_LIST,
      voiceList: VOICE_LIST,
      langIndex: 0,
      lang: "zh",
      voiceIndex: 0,
      voiceGender: "female",

      topMode: "to_human",
      toHumanSubMode: "record",
      toPetSubMode: "guide",

      text: "",
      audioPath: "",
      audioFileName: "",
      recording: false,
      recordingDuration: 0,

      loading: false,
      result: null,
      hasTts: false,
      hasAnimal: false,
      guideSteps: [],
      errorMsg: "",

      player: {
        playing: false,
        currentTime: 0,
        duration: 0,
        progress: 0,
        currentTimeStr: "00:00",
        durationStr: "00:00",
        ready: false,
      },
    },

    onLoad() {
      this._player = createPlayer((s) => this.setData({ player: s }));
    },

    onUnload() {
      this._stopTimer();
      if (this._player) this._player.destroy();
    },
    onHide() {
      this._stopTimer();
      if (this._player) this._player.pause();
    },

    setTopMode(e) {
      const topMode = e.currentTarget.dataset.value;
      if (!topMode || topMode === this.data.topMode) return;
      this._player && this._player.stop();
      this.setData({
        topMode,
        result: null,
        hasTts: false,
        hasAnimal: false,
        guideSteps: [],
        errorMsg: "",
        text: "",
        audioPath: "",
        audioFileName: "",
      });
    },
    setToHumanSubMode(e) {
      const v = e.currentTarget.dataset.value;
      if (!v || v === this.data.toHumanSubMode) return;
      this.setData({
        toHumanSubMode: v,
        errorMsg: "",
      });
    },
    setToPetSubMode(e) {
      const v = e.currentTarget.dataset.value;
      if (!v || v === this.data.toPetSubMode) return;
      this._player && this._player.stop();
      this.setData({
        toPetSubMode: v,
        result: null,
        hasTts: false,
        hasAnimal: false,
        guideSteps: [],
        errorMsg: "",
      });
    },

    setLang(e) {
      const idx = Number(e.detail.value) || 0;
      this.setData({ langIndex: idx, lang: LANG_LIST[idx].value });
    },
    setVoice(e) {
      const idx = Number(e.detail.value) || 0;
      this.setData({ voiceIndex: idx, voiceGender: VOICE_LIST[idx].value });
    },
    onTextInput(e) {
      this.setData({ text: e.detail.value });
    },

    _startTimer() {
      this._timerStart = Date.now();
      this._timer = setInterval(() => {
        const sec = ((Date.now() - this._timerStart) / 1000).toFixed(1);
        this.setData({ recordingDuration: Number(sec) });
      }, 100);
    },
    _stopTimer() {
      if (this._timer) {
        clearInterval(this._timer);
        this._timer = null;
      }
      this.setData({ recordingDuration: 0 });
    },

    async onRecordStart() {
      if (this.data.loading || this.data.recording) return;
      try {
        await recorder.ensureRecordAuth();
        await recorder.startRecording({ format: "aac", duration: 60000 });
        this.setData({
          recording: true,
          errorMsg: "",
          result: null,
          hasTts: false,
          hasAnimal: false,
          guideSteps: [],
        });
        this._startTimer();
        wx.vibrateShort && wx.vibrateShort({ type: "light" });
      } catch (e) {
        this._stopTimer();
        this.setData({ recording: false, errorMsg: "录音启动失败：" + _errMsg(e) });
      }
    },

    async onRecordEnd() {
      if (!recorder.isRecording()) {
        this._stopTimer();
        this.setData({ recording: false });
        return;
      }
      try {
        const res = await recorder.stopRecording();
        this._stopTimer();
        this.setData({ recording: false });
        const path = res && res.tempFilePath;
        const dur = (res && res.duration) || 0;
        if (!path) return;
        if (dur < 500) {
          this.setData({ errorMsg: "录音太短，请按住按钮说话（≥0.5秒）" });
          return;
        }
        await this._submitAudio(path, "录音");
      } catch (e) {
        this._stopTimer();
        this.setData({ recording: false, errorMsg: "录音失败：" + _errMsg(e) });
      }
    },

    chooseExistingAudio() {
      wx.chooseMessageFile({
        count: 1,
        type: "file",
        extension: ["mp3", "wav", "m4a", "aac", "ogg", "flac"],
        success: (res) => {
          const file = res.tempFiles && res.tempFiles[0];
          if (file && file.path) {
            this._submitAudio(file.path, file.name || "音频");
          }
        },
        fail: (e) => {
          if (e && e.errMsg && e.errMsg.indexOf("cancel") >= 0) return;
          this.setData({ errorMsg: "选择文件失败：" + _errMsg(e) });
        },
      });
    },

    async _submitAudio(audioPath, fileName) {
      this._player && this._player.stop();
      this.setData({
        loading: true,
        audioPath,
        audioFileName: fileName || "",
        errorMsg: "",
        result: null,
        hasTts: false,
        hasAnimal: false,
        guideSteps: [],
      });
      wx.showLoading({ title: "翻译中…", mask: true });
      try {
        const r = await voiceTranslate(this.data.pet, {
          mode: "pet_to_human",
          lang: this.data.lang,
          voiceGender: this.data.voiceGender,
          audioPath,
        });
        wx.hideLoading();
        const tts = r && r.tts_audio_base64;
        const animal = r && r.animal_audio_base64;
        const lite = _stripBase64(r);
        this.setData({
          result: lite,
          hasTts: !!tts,
          hasAnimal: !!animal,
          loading: false,
        });
        if (tts) this._player.loadBase64(tts, "mp3", true);
      } catch (e) {
        wx.hideLoading();
        this.setData({ loading: false, errorMsg: "翻译失败：" + _errMsg(e) });
      }
    },

    async sendText() {
      const text = (this.data.text || "").trim();
      if (!text) {
        this.setData({ errorMsg: "请先输入要翻译的内容" });
        return;
      }
      const apiMode =
        this.data.toPetSubMode === "guide"
          ? "human_to_pet_guide"
          : "human_to_pet_fun";

      this._player && this._player.stop();
      this.setData({
        loading: true,
        errorMsg: "",
        result: null,
        hasTts: false,
        hasAnimal: false,
        guideSteps: [],
      });
      wx.showLoading({ title: "翻译中…", mask: true });
      try {
        const r = await voiceTranslate(this.data.pet, {
          mode: apiMode,
          lang: this.data.lang,
          voiceGender: this.data.voiceGender,
          text,
        });
        wx.hideLoading();
        const tts = r && r.tts_audio_base64;
        const animal = r && r.animal_audio_base64;
        const lite = _stripBase64(r);
        const guideSteps =
          apiMode === "human_to_pet_guide"
            ? parseGuide((r && r.translation) || "")
            : [];
        this.setData({
          result: lite,
          hasTts: !!tts,
          hasAnimal: !!animal,
          guideSteps,
          loading: false,
        });
        const b = animal || tts;
        if (b) this._player.loadBase64(b, "mp3", true);
      } catch (e) {
        wx.hideLoading();
        this.setData({ loading: false, errorMsg: "翻译失败：" + _errMsg(e) });
      }
    },

    togglePlay() {
      this._player && this._player.toggle();
    },

    seekProgress(e) {
      const ratio = Number(e.detail.value) / 100;
      this._player && this._player.seekRatio(ratio);
    },
  };
}

module.exports = { createVoicePage };

/* eslint-disable */
// 隐私协议弹窗（符合 2023.9 后微信新规）
// 1. app.json 必须设置 "__usePrivacyCheck__": true
// 2. 微信公众平台后台必须配置「用户隐私保护指引」
// 3. 这个组件在 app.json 中以全局组件方式注入，
//    每个页面 wxml 顶部放 <privacy-popup /> 即可
Component({
  data: {
    show: false,
    desc: "",
    urlText: "《用户隐私保护指引》",
  },
  lifetimes: {
    attached() {
      // 监听微信发出的「需要授权」事件
      if (wx.onNeedPrivacyAuthorization) {
        wx.onNeedPrivacyAuthorization((resolve) => {
          this._resolve = resolve;
          this._fetchAndShow();
        });
      }
    },
  },
  methods: {
    _fetchAndShow() {
      const done = (info) => {
        const desc = (info && info.privacyContractName) || "用户隐私保护指引";
        this.setData({
          show: true,
          desc:
            "我们非常重视你的个人信息保护，使用前请仔细阅读" +
            "并同意" +
            desc +
            "。",
          urlText: "《" + desc + "》",
        });
      };
      if (wx.getPrivacySetting) {
        wx.getPrivacySetting({
          success: done,
          fail: () => done({}),
        });
      } else {
        done({});
      }
    },
    onAgree() {
      this.setData({ show: false });
      if (this._resolve) {
        this._resolve({ event: "agree", buttonId: "agree-btn" });
        this._resolve = null;
      }
    },
    onDisagree() {
      this.setData({ show: false });
      if (this._resolve) {
        this._resolve({ event: "disagree" });
        this._resolve = null;
      }
      wx.showToast({ title: "未同意将无法使用相关功能", icon: "none" });
    },
    openContract() {
      if (wx.openPrivacyContract) {
        wx.openPrivacyContract({
          fail: () =>
            wx.showToast({ title: "打开协议失败，请稍后重试", icon: "none" }),
        });
      }
    },
    noop() {},
  },
});

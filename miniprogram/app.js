const { USE_CLOUD, CLOUD_ENV } = require("./utils/config.js");
const { cleanupByPrefix } = require("./utils/storage_cleanup.js");

App({
  onLaunch() {
    try {
      const sys = wx.getSystemInfoSync();
      this.globalData.statusBarHeight = sys.statusBarHeight || 20;
    } catch (e) {}

    // 启动时清理累积的临时文件, 避免 USER_DATA_PATH 10MB 配额满.
    // 老用户从历次会话残留的文件可能已经占了大半. 海报保留最新 1 张
    // (用户可能正回到 photo 页继续看), 音频全清.
    try {
      cleanupByPrefix("audio_", 0);
      cleanupByPrefix("poster_view_", 1);
    } catch (e) {
      console.warn("[app] 启动清理失败", e);
    }

    if (USE_CLOUD) {
      if (!wx.cloud) {
        console.error("当前微信版本过低，无法使用云能力，请升级微信。");
      } else if (!CLOUD_ENV || CLOUD_ENV === "your-cloud-env-id") {
        console.error(
          "请在 utils/config.js 里填好 CLOUD_ENV，再开启 USE_CLOUD=true。"
        );
      } else {
        wx.cloud.init({
          env: CLOUD_ENV,
          traceUser: true,
        });
      }
    }
  },
  globalData: {
    statusBarHeight: 20,
  },
});

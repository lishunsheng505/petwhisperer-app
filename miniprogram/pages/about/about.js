const VERSION = "1.0.0";

Page({
  data: {
    version: VERSION,
  },
  copyContact(e) {
    const v = e.currentTarget.dataset.value;
    if (!v) return;
    wx.setClipboardData({
      data: v,
      success: () => wx.showToast({ title: "已复制", icon: "success" }),
    });
  },
  onShareAppMessage() {
    return {
      title: "喵汪心语 · 和毛孩子的趣味日常",
      path: "/pages/index/index",
      imageUrl: "/images/pet.png",
    };
  },
});

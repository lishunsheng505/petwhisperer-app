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
  openPrivacy() {
    if (wx.openPrivacyContract) {
      wx.openPrivacyContract({
        fail: () =>
          wx.showToast({ title: "请稍后再试", icon: "none" }),
      });
    }
  },
  onShareAppMessage() {
    return {
      title: "PetWhisperer · 读懂毛孩子的每一句话",
      path: "/pages/index/index",
      imageUrl: "/images/pet.png",
    };
  },
});

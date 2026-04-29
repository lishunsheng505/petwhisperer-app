Page({
  goPhoto() { wx.switchTab({ url: "/pages/photo/photo" }); },
  goCat() { wx.switchTab({ url: "/pages/cat/cat" }); },
  goDog() { wx.switchTab({ url: "/pages/dog/dog" }); },
  goAbout() { wx.navigateTo({ url: "/pages/about/about" }); },

  onShareAppMessage() {
    return {
      title: "喵汪心语 · 和毛孩子的趣味日常",
      path: "/pages/index/index",
      imageUrl: "/images/pet.png",
    };
  },
  onShareTimeline() {
    return {
      title: "喵汪心语 · 和毛孩子的趣味日常",
      query: "",
      imageUrl: "/images/pet.png",
    };
  },
});

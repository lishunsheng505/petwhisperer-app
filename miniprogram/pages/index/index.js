Page({
  goPhoto() { wx.switchTab({ url: "/pages/photo/photo" }); },
  goCat() { wx.switchTab({ url: "/pages/cat/cat" }); },
  goDog() { wx.switchTab({ url: "/pages/dog/dog" }); },
  goAbout() { wx.navigateTo({ url: "/pages/about/about" }); },

  onShareAppMessage() {
    return {
      title: "PetWhisperer · 读懂毛孩子的每一句话",
      path: "/pages/index/index",
      imageUrl: "/images/pet.png",
    };
  },
  onShareTimeline() {
    return {
      title: "PetWhisperer · 读懂毛孩子的每一句话",
      query: "",
      imageUrl: "/images/pet.png",
    };
  },
});

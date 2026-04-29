"""一次性脚本：用 mock 数据本地渲染一张完整海报，0 成本看新字体效果。

使用：
    cd backend
    python preview_poster.py                       # 用内置占位渐变图
    python preview_poster.py path/to/your_pet.jpg  # 用你自己的宠物照
    # 看 outputs/preview_poster_*.png

会跑 4 个 vibe（cute / cool / warm / chill）+ 4 张随机海报，对比效果。
"""
from __future__ import annotations

import sys
from pathlib import Path
from PIL import Image, ImageDraw

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "outputs"
OUT_DIR.mkdir(exist_ok=True)

# 导入项目里的 render_poster（不会触发任何 API 调用）
sys.path.insert(0, str(BASE))
from core import render_poster  # noqa: E402


def make_placeholder(w: int = 900, h: int = 900) -> Image.Image:
    """生成一张柔和渐变 + 简单宠物剪影的占位图。"""
    img = Image.new("RGB", (w, h), (240, 220, 200))
    px = img.load()
    for y in range(h):
        for x in range(w):
            r = 240 - int((x / w) * 30)
            g = 220 - int((y / h) * 20)
            b = 200 + int(((x + y) / (w + h)) * 30)
            px[x, y] = (r, g, b)
    d = ImageDraw.Draw(img)
    # 画个圆代替"宠物"
    cx, cy, R = w // 2, h // 2 + 40, 240
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(180, 140, 110))
    d.ellipse([cx - R + 60, cy - 40, cx - R + 120, cy + 20], fill=(255, 255, 255))
    d.ellipse([cx + R - 120, cy - 40, cx + R - 60, cy + 20], fill=(255, 255, 255))
    d.ellipse([cx - R + 80, cy - 25, cx - R + 100, cy + 5], fill=(0, 0, 0))
    d.ellipse([cx + R - 100, cy - 25, cx + R - 80, cy + 5], fill=(0, 0, 0))
    return img


# ----- 测试数据：vibe 用真实 LLM 输出值（minimalism/retro/kawaii/moody）-----
# 同一只宠物用不同长度文案 + 不同 vibe，看主题色 / 标题字号 / 装饰随机化效果
CASES = [
    {
        "name": "kawaii_short",
        "data": {
            "vibe": "kawaii",
            "quote_cn": "今日份小可爱",
            "palette": ["#FFE5E5", "#F5B7C9", "#88D3C8"],
            "pets": [{"type": "猫", "head_x": 0.5, "head_y": 0.3,
                      "individual_quote": "蹲在窗边晒太阳"}],
        },
    },
    {
        "name": "kawaii_long",
        "data": {
            "vibe": "kawaii",
            "quote_cn": "今天又是被你治愈的一天呢",
            "palette": ["#FCE8DC", "#F9C7B5", "#88D3C8"],
            "pets": [{"type": "狗", "head_x": 0.5, "head_y": 0.3,
                      "individual_quote": "尾巴摇个不停"}],
        },
    },
    {
        "name": "moody",
        "data": {
            "vibe": "moody",
            "quote_cn": "本霸总今日不接见任何人",
            "palette": ["#3D3933", "#5C4F45", "#A99A8A"],
            "pets": [{"type": "猫", "head_x": 0.5, "head_y": 0.3,
                      "individual_quote": "罐头加倍，否则免谈"}],
        },
    },
    {
        "name": "retro",
        "data": {
            "vibe": "retro",
            "quote_cn": "暖洋洋的下午茶时间",
            "palette": ["#D4A874", "#A87446", "#5C3A1E"],
            "pets": [{"type": "猫", "head_x": 0.5, "head_y": 0.3,
                      "individual_quote": "趴在毛毯上呼噜呼噜"}],
        },
    },
    {
        "name": "minimalism",
        "data": {
            "vibe": "minimalism",
            "quote_cn": "周末躺平选手就位",
            "palette": ["#F5F5F5", "#888888", "#333333"],
            "pets": [{"type": "狗", "head_x": 0.5, "head_y": 0.3,
                      "individual_quote": "四脚朝天，肚皮在呼吸"}],
        },
    },
]


def main():
    if len(sys.argv) > 1 and Path(sys.argv[1]).exists():
        src = Image.open(sys.argv[1]).convert("RGB")
        print(f"[1/2] 使用真实图片: {sys.argv[1]} ({src.size})")
    else:
        src = make_placeholder()
        print(f"[1/2] 使用占位渐变图 (没传参或文件不存在)")
        print(f"     提示: python preview_poster.py path/to/pet.jpg 可换真实照片")

    print(f"[2/2] 渲染 {len(CASES)} 张海报...")
    for c in CASES:
        out = OUT_DIR / f"preview_poster_{c['name']}.png"
        poster = render_poster(src, c["data"])
        poster.save(out)
        print(f"     OK {out.name}  ({poster.size[0]}x{poster.size[1]})")

    print(f"\n全部生成在: {OUT_DIR}")
    print(f"双击图片看效果，对比 4 个 vibe 的视觉差异")


if __name__ == "__main__":
    main()

import os
import re
import json
import math
import random
import base64
import platform
import warnings
import logging
import concurrent.futures
from pathlib import Path
from io import BytesIO
from datetime import datetime

import numpy as np
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter, ImageSequence
from dotenv import load_dotenv

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    _HEIF_OK = True
except Exception:
    _HEIF_OK = False

logger = logging.getLogger("petwhisperer.core")

# ================================================================
#  基础配置
# ================================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
ASSETS_DIR = BASE_DIR / "assets"
for _d in (UPLOAD_DIR, OUTPUT_DIR, ASSETS_DIR):
    _d.mkdir(exist_ok=True)

API_BASE = "https://api.siliconflow.cn/v1"
# 注意：原来的 Qwen/Qwen2.5-VL-72B-Instruct 已于 2026-04-29 被硅基流动下线。
# 替换为同生态的 Qwen3-VL-30B-A3B-Instruct（性价比最优，MoE，原生支持视觉+文本）。
MODEL_ID = "Qwen/Qwen3-VL-30B-A3B-Instruct"
MAX_RETRIES = 2

VIBE_CN = {
    "minimalism": "极简",
    "retro": "杂志画报",
    "kawaii": "拍立得",
    "moody": "王家卫电影",
}

# ================================================================
#  四种人格 — 每次随机切换，杜绝千篇一律
# ================================================================

PERSONALITIES = {
    "软萌": (
        "你是软萌人格。说话甜甜的，像撒娇的小公主，多用叠词和可爱语气。"
        "示例：好困困呀 / 这个位置暖暖的不想动 / 摸摸我嘛"
    ),
    "社恐": (
        "你是社恐人格。内向害羞，最怕被关注，台词要体现想逃避社交。"
        "示例：别看我 / 能不能假装没看见我 / 有人在拍我好慌"
    ),
    "话痨": (
        "你是话痨人格。碎碎念停不下来，什么都要吐槽和评论。"
        "示例：等等让我先说 / 你知道这沙发其实 / 说来话长但也不长"
    ),
    "哲学家": (
        "你是哲学家人格。深沉思考型，用哲理表达日常小事。"
        "示例：存在即合理 / 生命不过一场午睡 / 窗外的风与我无关"
    ),
}

# ================================================================
#  System Prompt 模板
# ================================================================

_PROMPT_TEMPLATE = r"""你是一位兼具动物行为学知识与视觉设计天赋的 AI 创意总监。

## 语言铁律（最高优先级）
- quote_cn 和 individual_quote 必须是纯正流畅的简体中文。
- 严禁出现任何英文单词、英文字母、拼音或乱码。违反此条则结果作废。

## 本次人格
{personality}

## 分析流程

### 第一步 · 场景检测
判断图中有几只动物，分别是什么种类。

### 第二步 · 多宠物情绪规则
- 猫在移动 + 狗在追 → 猫的台词必须高傲（如"懒得理你""这傻狗又来了"）。严禁"逃跑""求饶"。
- 狗邀玩（抬爪/摇尾）+ 猫高冷 → 狗"卑微求关注"，猫"不屑一顾"。
- 单只宠物 → 直接给出第一人称吐槽。

### 第三步 · 正能量约束
严禁使用"瞎、残、死、丑、胖、病、蠢"等负面词。闭眼/眨眼 → "沉醉""不屑""被我迷倒了吧"等。

### 第四步 · 禁止脑补 · 强制紧扣画面（重要）
- 文案必须直接呼应画面中**真实可见**的元素：动作、表情、姿态、物品、场景。
- 严禁产出与画面无关的"段子"（如"人类的脚臭""昨天发生了什么"等无依据脑补）。
- 写完 quote_cn 后自检：如果**抹掉这张图，这句话还能套到任意一张猫狗照片上**，那就是失败的，请重写。
- 优秀范例（紧扣动作）：
  · 猫眯眼趴着 → "今天的太阳真不错"
  · 狗抬头看主人 → "你手里那个，分我一口"
  · 两只猫贴贴 → "别动，我在听你心跳"
- 失败范例（脱离画面）：
  · "人类的脚臭是种不祥之兆"（图里既没有人也没有脚）
  · "昨天的小鱼干又香又脆"（图里没有小鱼干）

### 第四点五步 · 风格基调
偏向**治愈、温暖、呆萌、轻吐槽**，避免阴沉/恐怖/猎奇。

### 第五步 · 视觉风格
- minimalism：干净简洁、亮色调
- retro：暖色调、年代感、暗光
- kawaii：色彩鲜艳、可爱活泼
- moody：冷色调、对比强、深沉

### 第六步 · 色卡
从图片提取 3 个 HEX 色值。

### 第七步 · 宠物头部坐标
估算每只宠物头部顶端相对坐标（0~1）。

## 输出（严格 JSON，禁止额外文字）
```json
{{
  "quote_cn": "主文案（15字以内、第一人称、纯中文）",
  "quote_en": "英文短句（仅此字段允许英文）",
  "vibe": "minimalism/retro/kawaii/moody",
  "palette": ["#HEX1", "#HEX2", "#HEX3"],
  "pets": [
    {{
      "type": "cat/dog/其他",
      "head_x": 0.5,
      "head_y": 0.3,
      "individual_quote": "独立内心独白（15字以内、纯中文）"
    }}
  ]
}}
```"""


def _build_prompt() -> tuple[str, str]:
    name, desc = random.choice(list(PERSONALITIES.items()))
    return name, _PROMPT_TEMPLATE.format(personality=desc)


# ================================================================
#  中文纯净度校验
# ================================================================

_CN_RE = re.compile(
    r"^[\u4e00-\u9fff\u3400-\u4dbf\uff00-\uffef"
    r"\u3000-\u303f\u2000-\u206f"
    r"，。！？、；：""''（）【】《》…——·～"
    r"0-9\s]+$"
)


def _is_pure_chinese(t: str) -> bool:
    return bool(t) and bool(_CN_RE.match(t))


def _clean_to_chinese(t: str) -> str:
    o = [c for c in t if "\u4e00" <= c <= "\u9fff"
         or "\u3400" <= c <= "\u4dbf"
         or c in "，。！？、；：""''（）【】《》…——·～ "]
    return "".join(o) or "喵的，别拍了"


# ================================================================
#  字体加载 — 严格模式
# ================================================================

_font_path: str | None = None


def _resolve_font() -> str:
    """font.ttf 若存在则必须加载成功，否则报错中断。"""
    global _font_path
    if _font_path is not None:
        return _font_path

    font_ttf = BASE_DIR / "font.ttf"
    if font_ttf.exists():
        try:
            ImageFont.truetype(str(font_ttf), 20)
        except OSError as e:
            raise RuntimeError(
                f"❌ font.ttf 存在但加载失败，请检查文件是否损坏：{font_ttf}\n{e}"
            ) from e
        _font_path = str(font_ttf)
        return _font_path

    custom = BASE_DIR / "custom_font.ttf"
    if custom.exists():
        try:
            ImageFont.truetype(str(custom), 20)
        except OSError as e:
            raise RuntimeError(
                f"❌ custom_font.ttf 加载失败：{custom}\n{e}"
            ) from e
        _font_path = str(custom)
        return _font_path

    for pat in ("*.ttf", "*.ttc", "*.otf"):
        for f in ASSETS_DIR.glob(pat):
            try:
                ImageFont.truetype(str(f), 20)
                _font_path = str(f)
                return _font_path
            except OSError:
                continue

    sys_name = platform.system()
    candidates: list[Path] = []
    if sys_name == "Windows":
        base = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        candidates = [base / n for n in
                      ("simkai.ttf", "simfang.ttf", "msyh.ttc", "simsun.ttc")]
    elif sys_name == "Darwin":
        candidates = [Path(p) for p in (
            "/Library/Fonts/Kaiti.ttc",
            "/System/Library/Fonts/STKaiti.ttf",
            "/System/Library/Fonts/PingFang.ttc")]
    else:
        candidates = [Path(p) for p in (
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc")]

    for p in candidates:
        if p.exists():
            _font_path = str(p)
            warnings.warn(
                f"\n⚠️  未找到 font.ttf，已回退到系统字体：{p.name}\n"
                "   建议将开源字体放到项目根目录并命名为 font.ttf\n",
                stacklevel=2,
            )
            return _font_path

    raise RuntimeError(
        "❌ 未找到任何可用中文字体！\n"
        "请将开源字体放到项目根目录并命名为 font.ttf\n"
        "推荐：思源黑体 / 得意黑 (Smiley Sans)"
    )


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_resolve_font(), size)


def _fs(base: int, w: int, ref: int = 1000) -> int:
    return max(int(base * w / ref), base // 2)


# ================================================================
#  图片工具 & 滤镜
# ================================================================

SUPPORTED_IMAGE_FORMATS = {
    "JPEG", "PNG", "WEBP", "HEIF", "HEIC", "MPO", "BMP", "GIF",
}


def load_image_any(image_bytes: bytes, filename: str = "") -> Image.Image:
    """读取多种格式：JPG/JPEG/PNG/WEBP/HEIC/HEIF/BMP/GIF。GIF 取第一帧。"""
    if not image_bytes:
        raise ValueError("空文件")
    name = (filename or "").lower()
    suffix = Path(name).suffix.lstrip(".") if name else ""
    if suffix in {"heic", "heif"} and not _HEIF_OK:
        raise RuntimeError(
            "服务端未安装 pillow-heif，无法解析 HEIC/HEIF；请在依赖中安装 pillow-heif。"
        )
    try:
        im = Image.open(BytesIO(image_bytes))
    except Exception as e:
        raise ValueError(f"无法识别的图片格式：{e}") from e
    fmt = (im.format or "").upper()
    if fmt == "GIF" or getattr(im, "is_animated", False):
        try:
            frame = next(ImageSequence.Iterator(im))
        except StopIteration:
            frame = im
        im = frame
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    elif im.mode != "RGB":
        im = im.convert("RGB")
    return im


def crop_watermark(img: Image.Image) -> Image.Image:
    w, h = img.size
    return img.crop((0, 0, w, int(h * 0.9)))


def image_to_base64(img: Image.Image, max_side: int = 768) -> str:
    """把图片编 base64，默认缩到长边 768 — 给 LLM 看够用且 token 省 70%。

    Args:
        img: 原图
        max_side: 长边像素上限。768 在 LLM 视觉理解保留度 + 推理速度上最划算。
                  传 None 或 0 表示不缩。
    """
    if max_side and max_side > 0:
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            img = img.resize(
                (int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def apply_soft_light(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Brightness(img).enhance(1.06)
    img = ImageEnhance.Color(img).enhance(1.12)
    r, g, b = img.split()
    r = r.point(lambda x: min(255, int(x * 1.04)))
    g = g.point(lambda x: min(255, int(x * 1.02)))
    return Image.merge("RGB", (r, g, b)).filter(
        ImageFilter.GaussianBlur(radius=0.6))


def apply_vintage(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(0.68)
    r, g, b = img.split()
    r = r.point(lambda x: min(255, int(x * 1.12)))
    g = g.point(lambda x: int(x * 0.94))
    b = b.point(lambda x: int(x * 0.75))
    return ImageEnhance.Contrast(Image.merge("RGB", (r, g, b))).enhance(1.10)


def apply_cool_tint(img: Image.Image) -> Image.Image:
    img = ImageEnhance.Color(img).enhance(0.42)
    r, g, b = img.split()
    r = r.point(lambda x: int(x * 0.78))
    b = b.point(lambda x: min(255, int(x * 1.22)))
    return ImageEnhance.Contrast(Image.merge("RGB", (r, g, b))).enhance(1.42)


def apply_grain(img: Image.Image, intensity: int = 18) -> Image.Image:
    arr = np.array(img, dtype=np.int16)
    noise = np.random.randint(-intensity, intensity + 1, arr.shape, dtype=np.int16)
    return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))


# ================================================================
#  AI 调用
# ================================================================

DEFAULT_RESULT: dict = {
    "quote_cn": "铲屎的，你在拍什么",
    "quote_en": "What are you shooting, hooman?",
    "vibe": "minimalism",
    "palette": ["#F5F5F5", "#333333", "#888888"],
    "pets": [],
}


def _parse(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            d = json.loads(m.group())
            if "quote_cn" in d:
                return d
        except json.JSONDecodeError:
            pass
    return {**DEFAULT_RESULT, "quote_cn": text.strip()[:30]}


def get_pet_insight(img: Image.Image) -> dict:
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "未配置 SILICONFLOW_API_KEY，请在 .env 中填写硅基流动 API Key。")

    client = OpenAI(api_key=api_key, base_url=API_BASE)
    b64 = image_to_base64(img)
    persona_name, sys_prompt = _build_prompt()

    user_msg = {
        "role": "user",
        "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            {"type": "text", "text": "请分析这张宠物图片。"},
        ],
    }

    data = None
    for attempt in range(MAX_RETRIES):
        msgs = [{"role": "system", "content": sys_prompt}, user_msg]
        if attempt > 0:
            msgs.append({"role": "user",
                         "content": "上次包含英文或乱码，请重新生成纯中文。"})
        resp = client.chat.completions.create(model=MODEL_ID, messages=msgs)
        data = _parse(resp.choices[0].message.content.strip())
        if _is_pure_chinese(data.get("quote_cn", "")):
            break

    data = data or DEFAULT_RESULT
    if not _is_pure_chinese(data.get("quote_cn", "")):
        data["quote_cn"] = _clean_to_chinese(data["quote_cn"])
    for pet in data.get("pets", []):
        q = pet.get("individual_quote", "")
        if q and not _is_pure_chinese(q):
            pet["individual_quote"] = _clean_to_chinese(q)

    data["_persona"] = persona_name
    return data


# ================================================================
#  AI 重绘 — 调硅基流动 Qwen-Image-Edit 做风格化（付费功能）
# ================================================================

REDRAW_MODEL = "Qwen/Qwen-Image-Edit-2509"
REDRAW_URL = f"{API_BASE}/images/generations"

# art_style → 风格化 prompt（英文 prompt 模型表现更稳）
ART_STYLES: dict[str, dict] = {
    "ghibli": {
        "label": "宫崎骏吉卜力",
        "prompt": (
            "Redraw this pet portrait in Studio Ghibli anime style by Hayao Miyazaki. "
            "Soft watercolor textures, warm sunlight, lush nature background, "
            "hand-drawn aesthetic, gentle pastel palette. "
            "IMPORTANT: keep the pet's species, breed, fur color and pose identical "
            "to the original. Do not add humans. No text in the image."
        ),
    },
    "oil": {
        "label": "古典油画",
        "prompt": (
            "Redraw this pet portrait as a classical oil painting in Renaissance style. "
            "Rich textured brushstrokes, dramatic chiaroscuro lighting, deep saturated colors, "
            "museum-quality composition, dark elegant background. "
            "IMPORTANT: preserve the pet's species, breed, fur color and pose. "
            "No humans, no text in the image."
        ),
    },
    "ink": {
        "label": "中国水墨",
        "prompt": (
            "Redraw this pet portrait as traditional Chinese ink wash painting (shuimo). "
            "Minimalist black ink on rice paper, expressive sumi-e brushstrokes, "
            "lots of negative space, zen atmosphere. "
            "IMPORTANT: preserve the pet's silhouette, breed and gesture. "
            "No humans, no text in the image."
        ),
    },
    "pixel": {
        "label": "像素风",
        "prompt": (
            "Redraw this pet portrait as 16-bit pixel art game sprite. "
            "Limited color palette, crisp pixel edges, retro game aesthetic, "
            "centered subject on a soft pastel background. "
            "IMPORTANT: preserve the pet's species, body shape and color. "
            "No humans, no text in the image."
        ),
    },
    "lego": {
        "label": "乐高积木",
        "prompt": (
            "Redraw this pet as a LEGO minifigure / brick photograph. "
            "Glossy plastic surfaces, studio lighting, plain background, "
            "playful toy aesthetic. "
            "IMPORTANT: preserve the pet's distinguishing color and shape. "
            "No humans, no text in the image."
        ),
    },
}

DEFAULT_ART_STYLE = "ghibli"


def redraw_image(
    img: Image.Image,
    art_style: str = DEFAULT_ART_STYLE,
    *,
    timeout: int = 90,
) -> Image.Image:
    """调用硅基流动 Qwen-Image-Edit 对宠物照片做风格化重绘。

    Args:
        img:        用户原图（PIL.Image，自动缩到 ≤1024 长边）
        art_style:  ART_STYLES 中的 key（默认 ghibli）
        timeout:    单次 HTTP 超时（秒）。模型推理 + 下载约 15-40s

    Returns:
        重绘后的 RGB PIL.Image。

    Raises:
        ValueError: 缺 API Key 或参数非法
        RuntimeError: 调用失败 / 超时 / 响应缺字段
    """
    spec = ART_STYLES.get(art_style) or ART_STYLES[DEFAULT_ART_STYLE]

    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        raise ValueError("未配置 SILICONFLOW_API_KEY")

    # 缩到长边 768：再小到 512 会丢细节，1024 又太慢；768 是
    # 推理速度 / 出图质量 / base64 体积 的折中点
    max_side = 768
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:
        small = img.resize((int(w * scale), int(h * scale)),
                           Image.Resampling.LANCZOS)
    else:
        small = img

    # PNG 太大用 JPEG 92 质量基本看不出差异，体积只有 PNG 的 1/3
    buf = BytesIO()
    small.convert("RGB").save(buf, format="JPEG", quality=92, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    image_data_url = f"data:image/jpeg;base64,{b64}"

    payload = {
        "model": REDRAW_MODEL,
        "prompt": spec["prompt"],
        "image": image_data_url,
        # 官方默认 20 → 改成 14：实测吉卜力风格 14 步已收敛，
        # 推理时间从 ~30s 缩到 ~18s，质量肉眼无差
        "num_inference_steps": 14,
        # 官方建议 Qwen-Image 系列 cfg 用 4.0；过小会丢文本/语义
        "cfg": 4.0,
    }

    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        REDRAW_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            pass
        if e.code == 401:
            raise RuntimeError("AI 重绘鉴权失败，请检查 SILICONFLOW_API_KEY") from e
        if e.code == 429:
            raise RuntimeError("AI 重绘服务繁忙，请稍后再试") from e
        if e.code in (503, 504):
            raise RuntimeError("AI 重绘服务暂时不可用，请稍后再试") from e
        raise RuntimeError(f"AI 重绘失败 ({e.code}): {snippet}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"AI 重绘网络错误：{e.reason}") from e
    except Exception as e:
        raise RuntimeError(f"AI 重绘请求失败：{e}") from e

    try:
        result = json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError("AI 重绘响应非 JSON") from e

    images = result.get("images") or []
    if not images:
        raise RuntimeError(f"AI 重绘响应缺少 images：{str(result)[:200]}")
    img_url = images[0].get("url") if isinstance(images[0], dict) else None
    if not img_url:
        raise RuntimeError(f"AI 重绘响应缺少 url：{str(result)[:200]}")

    try:
        with urllib.request.urlopen(img_url, timeout=timeout) as resp:
            img_bytes = resp.read()
    except Exception as e:
        raise RuntimeError(f"AI 重绘图片下载失败：{e}") from e

    try:
        out = Image.open(BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        raise RuntimeError(f"AI 重绘返回的不是有效图片：{e}") from e

    return out


# ================================================================
#  水印 30%
# ================================================================

def _watermark(poster: Image.Image) -> Image.Image:
    """右下角加 AI 生成合规标识（醒目白色胶囊）。

    符合《人工智能生成合成内容标识办法》（2025-09-01 起施行）的
    "显著标识" 要求：明显的"AI 生成内容"字样 + 高对比配色。
    """
    w, h = poster.size
    poster = poster.convert("RGBA")
    ly = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ly)

    fs = max(_fs(20, w), 16)
    f = _font(fs)
    tag = "AI 生成内容"
    bb = d.textbbox((0, 0), tag, font=f)
    tw = bb[2] - bb[0]
    th = bb[3] - bb[1]
    pad_x = _fs(18, w)
    pad_y = _fs(10, w)
    pill_w = tw + pad_x * 2
    pill_h = th + pad_y * 2
    radius = pill_h // 2

    margin = _fs(22, w)
    x = w - pill_w - margin
    y = h - pill_h - margin

    sh_off = _fs(4, w)
    d.rounded_rectangle(
        [x + sh_off, y + sh_off, x + pill_w + sh_off, y + pill_h + sh_off],
        radius=radius, fill=(0, 0, 0, 70),
    )
    d.rounded_rectangle(
        [x, y, x + pill_w, y + pill_h],
        radius=radius, fill=(255, 255, 255, 240),
        outline=(255, 107, 107, 220), width=max(2, _fs(2, w)),
    )
    d.text(
        (x + pad_x - bb[0], y + pad_y - bb[1]), tag,
        fill=(255, 107, 107, 255), font=f,
    )

    return Image.alpha_composite(poster, ly).convert("RGB")


# ================================================================
#  Kawaii 漫画气泡（仅拍立得分支使用）
# ================================================================

def _luma_rgb(c: tuple[int, int, int]) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _kawaii_mean_rgb(img: Image.Image) -> tuple[int, int, int]:
    sm = img.convert("RGB").resize((48, 48), Image.Resampling.BILINEAR)
    v = np.asarray(sm, dtype=np.float32).reshape(-1, 3).mean(axis=0)
    return tuple(int(max(0, min(255, round(x)))) for x in v)


def _kawaii_theme(mean: tuple[int, int, int]) -> dict:
    """根据画面主色与亮暗，生成不透明气泡配色。"""
    L = _luma_rgb(mean)
    if L >= 128:
        bg = tuple(int(min(255, c * 0.26 + 255 * 0.74)) for c in mean)
        border = tuple(int(max(34, min(255, c * 0.62 + 28))) for c in mean)
        text = tuple(int(max(26, min(95, 38 + (255 - c) * 0.32))) for c in mean)
        tstroke = (255, 255, 255)
    else:
        bg = tuple(int(max(26, min(198, c * 0.48 + 22))) for c in mean)
        border = tuple(int(min(255, max(72, c * 0.78 + 88))) for c in mean)
        text = (252, 252, 255)
        tstroke = (18, 18, 22)
    return {"bg": bg, "border": border, "text": text, "tstroke": tstroke}


def _kawaii_comic_font(size: int) -> ImageFont.FreeTypeFont:
    if platform.system() == "Windows":
        fd = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for nm in ("msyhbd.ttc", "msyh.ttf", "msyhl.ttc", "msyh.ttc"):
            p = fd / nm
            if not p.exists():
                continue
            try:
                if p.suffix.lower() == ".ttc":
                    return ImageFont.truetype(str(p), size, index=0)
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return _font(size)


def _kawaii_bubble_bbox(
    bx: int, by: int, bw: int, bh: int,
    tcx: int, tail_half: int, tip: tuple[int, int],
) -> tuple[int, int, int, int]:
    xs = [bx, bx + bw, tip[0], tcx - tail_half, tcx + tail_half]
    ys = [by, by + bh, tip[1]]
    return min(xs), min(ys), max(xs), max(ys)


# ================================================================
#  渲染器 v2 · 拍立得手账风（2026-04-29 重写）
#  统一输出 1080×1440（3:4）海报，4 种 vibe 都走同一模板，
#  通过滤镜 / 配色 / 文案微调差异化（避免每种风格效果都"low"）
#
#  布局（自上而下）：
#    ┌──────────────────────────────────────────┐
#    │ 米色信纸背景（带极淡横纹理）             │
#    │   ╱胶带╲              ╱胶带╲            │
#    │   ┌──────────────────────────┐           │
#    │   │   倾斜白边照片           │           │
#    │   │   (cover crop)           │           │
#    │   │                          │           │
#    │   │   --签名条 04.29 · 拍立得│           │
#    │   └──────────────────────────┘           │
#    │                                          │
#    │       【超大字标题 quote_cn】            │
#    │       ──────                             │
#    │       By 喵咪 × 04.29                    │
#    │                                          │
#    │     #萌宠日常  #毛孩子  #治愈            │
#    │                                          │
#    │       ▢   ▢   ▢                          │
#    │     #FBF6 #A093 #3D2E                    │
#    │                                          │
#    │ ▲                            [AI 生成内容]│
#    └──────────────────────────────────────────┘
# ================================================================

# ---- v2 调色板（米色信纸系） --------------------------------
_PV2 = {
    "bg": (251, 246, 238),
    "bg_line": (180, 160, 130, 22),
    "title": (61, 46, 31),
    "subtitle": (139, 115, 85),
    "accent": (201, 91, 0),
    "tag_bg": (255, 244, 230),
    "tag_fg": (201, 91, 0),
    "tape_a": (255, 200, 180, 200),
    "tape_b": (220, 200, 170, 200),
    "card": (255, 255, 255),
    "shadow": (60, 40, 20, 110),
}

# 不同 vibe 的微调（颜色 + 滤镜 + 标语前缀）
_VIBE_THEMES = {
    "moody": {
        "bg": (240, 234, 225),
        "title": (35, 30, 28),
        "accent": (90, 60, 50),
        "tape_a": (180, 175, 165, 200),
        "tape_b": (160, 155, 150, 200),
        "tag_bg": (235, 230, 222),
        "tag_fg": (90, 60, 50),
    },
    "retro": {
        "bg": (245, 232, 210),
        "title": (90, 50, 20),
        "accent": (175, 80, 30),
        "tape_a": (220, 175, 130, 210),
        "tape_b": (190, 145, 100, 210),
        "tag_bg": (250, 232, 210),
        "tag_fg": (140, 70, 25),
    },
    "kawaii": {
        "bg": (253, 245, 247),
        "title": (188, 70, 110),
        "accent": (228, 95, 130),
        "tape_a": (255, 195, 215, 210),
        "tape_b": (255, 220, 200, 210),
        "tag_bg": (255, 235, 240),
        "tag_fg": (200, 70, 110),
    },
    "minimalism": {},  # 用默认
}


def _hex_to_rgb(s: str) -> tuple[int, int, int]:
    s = (s or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (200, 200, 200)
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return (200, 200, 200)


def _theme_for(vibe: str) -> dict:
    base = dict(_PV2)
    base.update(_VIBE_THEMES.get(vibe, {}))
    return base


def _bg_paper(W: int, H: int, theme: dict) -> Image.Image:
    """米白色信纸背景 + 极淡横纹（模拟手账纸质感）。"""
    canvas = Image.new("RGBA", (W, H), theme["bg"] + (255,))
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    line_step = 38
    for y in range(line_step, H, line_step):
        d.line([(0, y), (W, y)], fill=theme["bg_line"], width=1)
    return Image.alpha_composite(canvas, overlay)


def _draw_washi_tapes(canvas: Image.Image, W: int, H: int, theme: dict) -> None:
    """顶部两条手撕胶带（半透明色块 + 微旋转）。"""
    tape_w, tape_h = 220, 50

    t1 = Image.new("RGBA", (tape_w, tape_h), theme["tape_a"])
    t1 = t1.rotate(-8, resample=Image.Resampling.BICUBIC, expand=True)
    canvas.alpha_composite(t1, dest=(60, 40))

    t2 = Image.new("RGBA", (tape_w, tape_h), theme["tape_b"])
    t2 = t2.rotate(7, resample=Image.Resampling.BICUBIC, expand=True)
    canvas.alpha_composite(t2, dest=(W - 220 - 60, 40))


def _cover_fit(photo: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """把照片等比缩放后裁切到目标尺寸（cover 模式）。"""
    src_w, src_h = photo.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        x0 = (src_w - new_w) // 2
        photo = photo.crop((x0, 0, x0 + new_w, src_h))
    else:
        new_h = int(src_w / target_ratio)
        y0 = (src_h - new_h) // 2
        photo = photo.crop((0, y0, src_w, y0 + new_h))
    return photo.resize((target_w, target_h), Image.Resampling.LANCZOS)


def _build_photo_block(
    photo: Image.Image,
    frame_w: int,
    frame_h: int,
    tilt: float,
    theme: dict,
) -> Image.Image:
    """白边相框（顶/侧 32px，底 100px 留签名空间）+ 阴影 + 倾斜。"""
    PAD_TOP, PAD_SIDE, PAD_BOT = 32, 32, 100
    inner_w = frame_w - PAD_SIDE * 2
    inner_h = frame_h - PAD_TOP - PAD_BOT

    photo_fit = _cover_fit(photo, inner_w, inner_h)

    card = Image.new("RGB", (frame_w, frame_h), theme["card"])
    card.paste(photo_fit, (PAD_SIDE, PAD_TOP))

    d = ImageDraw.Draw(card)
    f_sig = _font(28)
    sig = datetime.now().strftime("%m.%d") + " · 拍立得日记"
    bb = d.textbbox((0, 0), sig, font=f_sig)
    sx = (frame_w - (bb[2] - bb[0])) // 2
    sy = frame_h - PAD_BOT + (PAD_BOT - (bb[3] - bb[1])) // 2 - 8
    d.text((sx - bb[0], sy - bb[1]), sig, fill=theme["subtitle"], font=f_sig)

    margin = 60
    big_w = frame_w + margin * 2
    big_h = frame_h + margin * 2
    composite = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))

    shadow = Image.new("RGBA", (frame_w, frame_h), theme["shadow"])
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=18))
    composite.alpha_composite(shadow, dest=(margin + 8, margin + 14))

    composite.alpha_composite(card.convert("RGBA"), dest=(margin, margin))

    return composite.rotate(
        tilt, resample=Image.Resampling.BICUBIC, expand=True)


def _wrap_cn(text: str, font: ImageFont.FreeTypeFont, max_w: int,
             draw: ImageDraw.ImageDraw) -> list[str]:
    """中文按字符宽度断行。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        test = cur + ch
        bb = draw.textbbox((0, 0), test, font=font)
        if bb[2] - bb[0] > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def _draw_title(canvas: Image.Image, text: str, W: int, y: int,
                theme: dict) -> int:
    """大字标题（自动字号 + 居中 + 微阴影）。返回末尾 y。"""
    d = ImageDraw.Draw(canvas)
    if len(text) <= 12:
        fs = 72
    elif len(text) <= 18:
        fs = 60
    elif len(text) <= 26:
        fs = 50
    else:
        fs = 44
    f = _font(fs)
    max_w = W - 120
    lines = _wrap_cn(text, f, max_w, d)[:3]
    line_h = int(fs * 1.32)
    cur_y = y
    for line in lines:
        bb = d.textbbox((0, 0), line, font=f)
        tw = bb[2] - bb[0]
        tx = (W - tw) // 2
        d.text((tx - bb[0] + 2, cur_y - bb[1] + 3), line,
               fill=(0, 0, 0, 35), font=f)
        d.text((tx - bb[0], cur_y - bb[1]), line,
               fill=theme["title"], font=f)
        cur_y += line_h
    return cur_y


def _draw_subtitle(canvas: Image.Image, text: str, W: int, y: int,
                   theme: dict) -> int:
    d = ImageDraw.Draw(canvas)
    f = _font(26)
    bb = d.textbbox((0, 0), text, font=f)
    tx = (W - (bb[2] - bb[0])) // 2
    d.text((tx - bb[0], y - bb[1]), text, fill=theme["subtitle"], font=f)

    line_y = y + (bb[3] - bb[1]) + 18
    line_w = 80
    d.rounded_rectangle(
        [(W - line_w) // 2, line_y, (W + line_w) // 2, line_y + 4],
        radius=2, fill=theme["accent"])
    return line_y + 14


def _make_tags(data: dict) -> list[str]:
    pets = data.get("pets") or []
    tags: list[str] = []
    seen: set[str] = set()
    for p in pets:
        t = (p.get("type") or "").strip()
        if t and t not in seen:
            seen.add(t)
            tags.append("#" + t)
    if not tags:
        tags.append("#萌宠日常")
    extras = ["#毛孩子", "#治愈系", "#今日份可爱"]
    for e in extras:
        if len(tags) >= 3:
            break
        if e not in tags:
            tags.append(e)
    return tags[:3]


def _draw_tags(canvas: Image.Image, tags: list[str], W: int, y: int,
               theme: dict) -> int:
    d = ImageDraw.Draw(canvas)
    f = _font(24)
    pad_x, pad_y, gap = 22, 11, 14

    sizes = [d.textbbox((0, 0), t, font=f) for t in tags]
    widths = [(b[2] - b[0]) for b in sizes]
    heights = [(b[3] - b[1]) for b in sizes]
    h = max(heights) + pad_y * 2
    radius = h // 2

    total_w = sum(w + pad_x * 2 for w in widths) + gap * (len(tags) - 1)
    cur_x = (W - total_w) // 2

    for tag, bb, tw in zip(tags, sizes, widths):
        bw = tw + pad_x * 2
        d.rounded_rectangle([cur_x, y, cur_x + bw, y + h],
                            radius=radius, fill=theme["tag_bg"])
        d.text((cur_x + pad_x - bb[0], y + pad_y - bb[1]), tag,
               fill=theme["tag_fg"], font=f)
        cur_x += bw + gap
    return y + h


def _draw_palette_strip(canvas: Image.Image, palette: list[str], W: int,
                        y: int, theme: dict) -> int:
    """色卡条：3 个圆角色块 + 下方 hex 文字。"""
    d = ImageDraw.Draw(canvas)
    block_w, block_h = 130, 86
    gap = 28
    n = min(3, len(palette))
    if n == 0:
        return y
    total_w = block_w * n + gap * (n - 1)
    cur_x = (W - total_w) // 2

    f = _font(20)
    for hex_color in palette[:n]:
        rgb = _hex_to_rgb(hex_color)
        d.rounded_rectangle([cur_x, y, cur_x + block_w, y + block_h],
                            radius=14, fill=rgb,
                            outline=(255, 255, 255, 220), width=2)
        sh = Image.new("RGBA", (block_w, block_h), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(
            [0, 0, block_w, block_h], radius=14, fill=(0, 0, 0, 50))
        sh = sh.filter(ImageFilter.GaussianBlur(radius=4))
        canvas.alpha_composite(sh, dest=(cur_x + 4, y + 6))
        d.rounded_rectangle([cur_x, y, cur_x + block_w, y + block_h],
                            radius=14, fill=rgb,
                            outline=(255, 255, 255, 220), width=2)

        txt = "#" + (hex_color or "").lstrip("#").upper()
        bb = d.textbbox((0, 0), txt, font=f)
        tx = cur_x + (block_w - (bb[2] - bb[0])) // 2
        ty = y + block_h + 12
        d.text((tx - bb[0], ty - bb[1]), txt, fill=theme["subtitle"], font=f)

        cur_x += block_w + gap
    return y + block_h + 44


def _draw_corner_doodles(canvas: Image.Image, W: int, H: int,
                         theme: dict) -> None:
    """左下三角 + 右上小圆点矩阵（极简几何装饰）。"""
    d = ImageDraw.Draw(canvas)
    pts = [(58, H - 96), (94, H - 60), (58, H - 60)]
    d.polygon(pts, fill=theme["accent"])

    cx, cy = W - 76, 178
    for i in range(3):
        for j in range(3):
            r = 4
            x = cx - i * 18
            y = cy + j * 18
            d.ellipse([x - r, y - r, x + r, y + r], fill=theme["subtitle"])


def render_poster(img: Image.Image, data: dict) -> Image.Image:
    """统一拍立得手账风海报（v2）。

    Args:
        img: 用户原图（PIL Image，已读取并去 EXIF 旋转）
        data: AI 返回的语义字典：
              quote_cn / quote_en / vibe / palette / pets[]
    Returns:
        1080×1440 RGB 海报（已加 AI 生成标识）
    """
    W, H = 1080, 1440
    style = (data.get("vibe") or "minimalism").lower()
    theme = _theme_for(style)

    if style == "moody":
        photo = apply_cool_tint(img)
    elif style == "retro":
        photo = apply_grain(apply_vintage(img), intensity=14)
    elif style == "kawaii":
        photo = apply_soft_light(img)
    else:
        photo = img.copy()

    canvas = _bg_paper(W, H, theme)

    _draw_washi_tapes(canvas, W, H, theme)

    tilt = -1.6 if style != "retro" else -2.6
    photo_block = _build_photo_block(
        photo, frame_w=900, frame_h=720, tilt=tilt, theme=theme)
    pbx = (W - photo_block.width) // 2
    pby = 110
    canvas.alpha_composite(photo_block, dest=(pbx, pby))

    title = (data.get("quote_cn") or "今天也是被治愈的一天").strip()
    next_y = pby + photo_block.height - 40
    next_y = _draw_title(canvas, title, W, next_y, theme)

    pets = data.get("pets") or []
    pet_types: list[str] = []
    seen: set[str] = set()
    for p in pets[:2]:
        t = (p.get("type") or "").strip()
        if t and t not in seen:
            seen.add(t)
            pet_types.append(t)
    if pet_types:
        subtitle = "By " + " × ".join(pet_types) + " · " + datetime.now().strftime("%m.%d")
    else:
        subtitle = "AI 萌宠心语 · " + datetime.now().strftime("%Y.%m.%d")
    next_y = _draw_subtitle(canvas, subtitle, W, next_y + 16, theme)

    tags = _make_tags(data)
    next_y = _draw_tags(canvas, tags, W, next_y + 24, theme)

    palette = data.get("palette") or ["#FBF6EE", "#A0937D", "#3D2E1F"]
    _draw_palette_strip(canvas, palette, W, next_y + 28, theme)

    _draw_corner_doodles(canvas, W, H, theme)

    poster = canvas.convert("RGB")
    return _watermark(poster)


# ================================================================
#  渲染器 v1（已停用，保留代码以备回滚 — 2026-04-29）
#  如需启用，把下面的 _render_poster_v1 改名回 render_poster 即可。
# ================================================================

def _render_poster_v1(img: Image.Image, data: dict) -> Image.Image:
    style = data.get("vibe", "minimalism").lower()

    if style == "moody":
        canvas = apply_cool_tint(img)
    elif style == "retro":
        canvas = apply_grain(apply_vintage(img), intensity=16)
    elif style == "kawaii":
        canvas = apply_soft_light(img)
    else:
        canvas = img.copy()

    w, h = canvas.size

    # ══════════════════════════════════════════════
    if style == "moody":
    # ══════════════════════════════════════════════
        poster = canvas.convert("RGBA")
        ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(ov)

        bar = int(h * 0.15)
        dr.rectangle([0, 0, w, bar], fill=(0, 0, 0, 190))
        dr.rectangle([0, h - bar, w, h], fill=(0, 0, 0, 190))

        cn = data.get("quote_cn", "")
        en = data.get("quote_en", "")
        f_cn = _font(_fs(24, w))
        f_en = _font(_fs(14, w))

        bb_cn = dr.textbbox((0, 0), cn, font=f_cn)
        tw_cn = bb_cn[2] - bb_cn[0]
        th_cn = bb_cn[3] - bb_cn[1]
        gap = _fs(6, w)
        total = th_cn
        tw_en = th_en = 0
        if en:
            bb_en = dr.textbbox((0, 0), en, font=f_en)
            tw_en = bb_en[2] - bb_en[0]
            th_en = bb_en[3] - bb_en[1]
            total += gap + th_en

        y = h - bar + (bar - total) // 2
        dr.text(((w - tw_cn) // 2, y), cn,
                fill=(220, 220, 220, 245), font=f_cn)
        if en:
            dr.text(((w - tw_en) // 2, y + th_cn + gap), en,
                    fill=(110, 110, 110, 200), font=f_en)

        poster = Image.alpha_composite(poster, ov).convert("RGB")

    # ══════════════════════════════════════════════
    elif style == "kawaii":
    # ══════════════════════════════════════════════
        brd = _fs(32, w)
        brd_bot = _fs(90, w)
        pw, ph = w + brd * 2, h + brd + brd_bot
        frame = Image.new("RGB", (pw, ph), "white")
        frame.paste(canvas, (brd, brd))
        poster = frame.convert("RGBA")

        ov = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        dr = ImageDraw.Draw(ov)

        pets = data.get("pets") or [
            {"head_x": 0.5, "head_y": 0.3,
             "individual_quote": data.get("quote_cn", "")}]
        pet_list = list(pets)
        pet_list.sort(key=lambda p: float(p.get("head_x", 0.5)))

        theme = _kawaii_theme(_kawaii_mean_rgb(canvas))
        bg, border_c, txt_c, tst_c = (
            theme["bg"], theme["border"], theme["text"], theme["tstroke"])
        olw = max(3, _fs(4, w))

        fs = max(_fs(32, w), 22)
        fnt = _kawaii_comic_font(fs)
        pad = _fs(20, w)
        rad = _fs(22, w)
        tail_half = max(_fs(15, w), 10)
        tail_len = max(_fs(26, w), 14)
        margin = _fs(14, w)
        gap_m = _fs(12, w)
        shx, shy = _fs(5, w), _fs(5, w)
        n = len(pet_list)
        placed: list[tuple[int, int, int, int]] = []

        def _draw_star(dr_, cx, cy, r, fill):
            """画一个小五角星装饰"""
            pts = []
            for i in range(10):
                angle = math.pi / 5 * i - math.pi / 2
                ri = r if i % 2 == 0 else r * 0.45
                pts.append((cx + ri * math.cos(angle), cy + ri * math.sin(angle)))
            dr_.polygon(pts, fill=fill)

        for idx, pet in enumerate(pet_list):
            quote = pet.get("individual_quote") or data.get("quote_cn", "")
            hx = float(pet.get("head_x", 0.5))
            hy = float(pet.get("head_y", 0.3))
            px = int(hx * w) + brd
            # 尾巴指向头顶位置（head_y 本身就是头顶，稍微往下一点点到额头）
            py_tip = int(hy * h) + brd + int(h * 0.03)

            bb = dr.textbbox((0, 0), quote, font=fnt)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            bw, bh = tw + pad * 2, th + pad * 2

            stagger = int((idx - (n - 1) / 2.0) * w * 0.065)
            bx0 = px - bw // 2 + stagger
            bx0 = max(brd + margin, min(bx0, brd + w - bw - margin))

            # 气泡默认在头顶上方，尾巴向下指向头顶
            direction = "down"
            by_try = py_tip - bh - tail_len - gap_m
            if by_try < brd + margin:
                # 头顶太靠上，改为气泡在下方
                by_try = py_tip + gap_m
                direction = "up"
            by0 = max(brd + margin, min(by_try, brd + h - bh - margin))

            tcx0 = int(max(bx0 + rad + tail_half + 3,
                           min(px, bx0 + bw - rad - tail_half - 3)))

            def _bbox(bx_: int, by_: int, tcx_: int) -> tuple[int, int, int, int]:
                tip_ = (px, py_tip)
                return _kawaii_bubble_bbox(bx_, by_, bw, bh, tcx_, tail_half, tip_)

            bx, by, tcx = bx0, by0, tcx0
            for _ in range(12):
                bb_ = _bbox(bx, by, tcx)
                inflated = (
                    bb_[0] - margin, bb_[1] - margin,
                    bb_[2] + margin, bb_[3] + margin)
                hit = any(
                    not (inflated[2] < p[0] or inflated[0] > p[2]
                         or inflated[3] < p[1] or inflated[1] > p[3])
                    for p in placed)
                if not hit:
                    break
                by -= _fs(22, w)
                if by < brd + margin:
                    by = by0 + (idx + 1) * _fs(18, w)
                    by = min(by, brd + h - bh - margin)
                    bx = max(brd + margin,
                             min(bx + ((-1) ** idx) * _fs(30, w),
                                  brd + w - bw - margin))
                    tcx = int(max(bx + rad + tail_half + 3,
                                  min(px, bx + bw - rad - tail_half - 3)))

            tip = (px, py_tip)
            if direction == "down":
                tail_poly = [
                    (tcx - tail_half, by + bh), (tcx + tail_half, by + bh),
                    tip,
                ]
            else:
                tail_poly = [
                    (tcx - tail_half, by), (tcx + tail_half, by),
                    tip,
                ]

            placed.append(
                _kawaii_bubble_bbox(bx, by, bw, bh, tcx, tail_half, tip))

            # 阴影
            sh_fill = (32, 32, 36, 105)
            dr.rounded_rectangle(
                [bx + shx, by + shy, bx + bw + shx, by + bh + shy],
                radius=rad, fill=sh_fill)
            dr.polygon([
                (tail_poly[0][0] + shx, tail_poly[0][1] + shy),
                (tail_poly[1][0] + shx, tail_poly[1][1] + shy),
                (tail_poly[2][0] + shx, tail_poly[2][1] + shy),
            ], fill=sh_fill)

            # 气泡主体
            dr.rounded_rectangle(
                [bx, by, bx + bw, by + bh], radius=rad, fill=bg,
                outline=border_c, width=olw)
            dr.polygon(tail_poly, fill=bg, outline=border_c, width=olw)

            # 文字
            dr.text(
                (bx + pad, by + pad), quote,
                fill=txt_c, font=fnt,
                stroke_width=max(2, _fs(2, w)),
                stroke_fill=tst_c,
            )

            # 小星星装饰（气泡四角外侧）
            star_r = max(_fs(7, w), 5)
            star_c = border_c + (200,) if len(border_c) == 3 else border_c
            _draw_star(dr, bx - star_r, by - star_r, star_r, star_c)
            _draw_star(dr, bx + bw + star_r, by - star_r, int(star_r * 0.7), star_c)
            # 小圆点装饰
            dot_r = max(_fs(4, w), 3)
            dr.ellipse([bx + bw - dot_r, by + bh - dot_r,
                        bx + bw + dot_r, by + bh + dot_r],
                       fill=star_c)
            dr.ellipse([bx - dot_r, by + bh - dot_r,
                        bx + dot_r, by + bh + dot_r],
                       fill=star_c)

        date_f = _font(_fs(15, w))
        date_str = datetime.now().strftime("%Y.%m.%d")
        dbb = dr.textbbox((0, 0), date_str, font=date_f)
        dr.text((pw - brd - (dbb[2] - dbb[0]) - _fs(8, w),
                 ph - brd_bot + _fs(30, w)),
                date_str, fill=(220, 100, 50, 220), font=date_f)

        poster = Image.alpha_composite(poster, ov).convert("RGB")

    # ══════════════════════════════════════════════
    elif style == "retro":
    # ══════════════════════════════════════════════
        poster = canvas.convert("RGBA")
        ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(ov)

        quote = data.get("quote_cn", "")
        vfs = _fs(44, w)
        vfnt = _font(vfs)
        gap = _fs(8, w)

        pets = data.get("pets") or []
        avg_x = (sum(p.get("head_x", 0.5) for p in pets) / len(pets)
                 if pets else 0.5)
        on_left = avg_x > 0.45

        char_sizes = []
        for ch in quote:
            bb = dr.textbbox((0, 0), ch, font=vfnt)
            char_sizes.append((bb[2] - bb[0], bb[3] - bb[1]))
        total_vh = sum(ch for _, ch in char_sizes) + gap * max(len(quote) - 1, 0)

        strip_w = vfs + _fs(36, w)
        if on_left:
            vx_center = _fs(65, w)
        else:
            vx_center = w - _fs(65, w)
        strip_x = vx_center - strip_w // 2

        vy_start = max((h - total_vh) // 2, _fs(30, w))

        dr.rounded_rectangle(
            [strip_x, vy_start - _fs(18, w),
             strip_x + strip_w, vy_start + total_vh + _fs(18, w)],
            radius=_fs(10, w), fill=(0, 0, 0, 75))

        vy = vy_start
        for i, ch in enumerate(quote):
            cw, ch_ = char_sizes[i]
            dr.text((vx_center - cw // 2, vy), ch,
                    fill=(255, 255, 255, 245), font=vfnt,
                    stroke_width=_fs(2, w),
                    stroke_fill=(0, 0, 0, 100))
            vy += ch_ + gap

        seal_r = _fs(42, w)
        if on_left:
            scx, scy = w - _fs(75, w), _fs(75, w)
        else:
            scx, scy = _fs(75, w), _fs(75, w)

        dr.ellipse([scx - seal_r, scy - seal_r,
                     scx + seal_r, scy + seal_r],
                    fill=(200, 40, 40, 70),
                    outline=(200, 40, 40, 140),
                    width=max(2, _fs(3, w)))
        inner = seal_r - _fs(6, w)
        dr.ellipse([scx - inner, scy - inner,
                     scx + inner, scy + inner],
                    outline=(200, 40, 40, 130),
                    width=max(1, _fs(2, w)))
        sf = _font(max(_fs(18, w), 12))
        seal_chars = ["认", "证"]
        for i, sc in enumerate(seal_chars):
            sbb = dr.textbbox((0, 0), sc, font=sf)
            stw = sbb[2] - sbb[0]
            sth = sbb[3] - sbb[1]
            dr.text((scx - stw // 2,
                     scy - sth - _fs(1, w) + i * (sth + _fs(3, w))),
                    sc, fill=(200, 40, 40, 180), font=sf)

        poster = Image.alpha_composite(poster, ov).convert("RGB")

    # ══════════════════════════════════════════════
    else:  # minimalism — 漫画气泡，支持多宠物
    # ══════════════════════════════════════════════
        poster = canvas.convert("RGBA")
        ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dr = ImageDraw.Draw(ov)

        pets = data.get("pets") or [
            {"head_x": 0.5, "head_y": 0.3,
             "individual_quote": data.get("quote_cn", "")}]
        pet_list = sorted(pets, key=lambda p: float(p.get("head_x", 0.5)))

        theme = _kawaii_theme(_kawaii_mean_rgb(canvas))
        bg, border_c, txt_c, tst_c = (
            theme["bg"], theme["border"], theme["text"], theme["tstroke"])
        olw = max(3, _fs(4, w))

        fs = max(_fs(28, w), 20)
        fnt = _kawaii_comic_font(fs)
        pad = _fs(18, w)
        rad = _fs(20, w)
        tail_half = max(_fs(14, w), 10)
        tail_len = max(_fs(24, w), 12)
        margin = _fs(12, w)
        gap_m = _fs(10, w)
        shx, shy = _fs(4, w), _fs(4, w)
        n = len(pet_list)
        placed: list[tuple[int, int, int, int]] = []

        def _draw_star_min(dr_, cx, cy, r, fill):
            pts = []
            for i in range(10):
                angle = math.pi / 5 * i - math.pi / 2
                ri = r if i % 2 == 0 else r * 0.45
                pts.append((cx + ri * math.cos(angle), cy + ri * math.sin(angle)))
            dr_.polygon(pts, fill=fill)

        for idx, pet in enumerate(pet_list):
            quote = pet.get("individual_quote") or data.get("quote_cn", "")
            hx = float(pet.get("head_x", 0.5))
            hy = float(pet.get("head_y", 0.3))
            px = int(hx * w)
            # 尾巴指向头顶（额头位置）
            py_tip = int(hy * h) + int(h * 0.03)

            bb = dr.textbbox((0, 0), quote, font=fnt)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            bw, bh = tw + pad * 2, th + pad * 2

            stagger = int((idx - (n - 1) / 2.0) * w * 0.065)
            bx0 = px - bw // 2 + stagger
            bx0 = max(margin, min(bx0, w - bw - margin))

            direction = "down"
            by_try = py_tip - bh - tail_len - gap_m
            if by_try < margin:
                by_try = py_tip + gap_m
                direction = "up"
            by0 = max(margin, min(by_try, h - bh - margin))

            tcx = int(max(bx0 + rad + tail_half + 3,
                          min(px, bx0 + bw - rad - tail_half - 3)))

            bx, by = bx0, by0
            for _ in range(12):
                bb_ = _kawaii_bubble_bbox(bx, by, bw, bh, tcx, tail_half, (px, py_tip))
                inflated = (bb_[0] - margin, bb_[1] - margin,
                            bb_[2] + margin, bb_[3] + margin)
                hit = any(
                    not (inflated[2] < p[0] or inflated[0] > p[2]
                         or inflated[3] < p[1] or inflated[1] > p[3])
                    for p in placed)
                if not hit:
                    break
                by -= _fs(20, w)
                if by < margin:
                    by = by0 + (idx + 1) * _fs(16, w)
                    by = min(by, h - bh - margin)

            tip = (px, py_tip)
            if direction == "down":
                tail_poly = [
                    (tcx - tail_half, by + bh),
                    (tcx + tail_half, by + bh),
                    tip,
                ]
            else:
                tail_poly = [
                    (tcx - tail_half, by),
                    (tcx + tail_half, by),
                    tip,
                ]

            placed.append(_kawaii_bubble_bbox(bx, by, bw, bh, tcx, tail_half, tip))

            sh_fill = (32, 32, 36, 105)
            dr.rounded_rectangle(
                [bx + shx, by + shy, bx + bw + shx, by + bh + shy],
                radius=rad, fill=sh_fill)
            dr.polygon([
                (tail_poly[0][0] + shx, tail_poly[0][1] + shy),
                (tail_poly[1][0] + shx, tail_poly[1][1] + shy),
                (tail_poly[2][0] + shx, tail_poly[2][1] + shy),
            ], fill=sh_fill)

            dr.rounded_rectangle(
                [bx, by, bx + bw, by + bh],
                radius=rad, fill=bg, outline=border_c, width=olw)
            dr.polygon(tail_poly, fill=bg, outline=border_c, width=olw)
            dr.text(
                (bx + pad, by + pad), quote,
                fill=txt_c, font=fnt,
                stroke_width=max(2, _fs(2, w)),
                stroke_fill=tst_c,
            )

            # 小星星 + 圆点装饰
            star_r = max(_fs(7, w), 5)
            star_c = border_c + (200,) if len(border_c) == 3 else border_c
            _draw_star_min(dr, bx - star_r, by - star_r, star_r, star_c)
            _draw_star_min(dr, bx + bw + star_r, by - star_r, int(star_r * 0.7), star_c)
            dot_r = max(_fs(4, w), 3)
            dr.ellipse([bx + bw - dot_r, by + bh - dot_r,
                        bx + bw + dot_r, by + bh + dot_r], fill=star_c)
            dr.ellipse([bx - dot_r, by + bh - dot_r,
                        bx + dot_r, by + bh + dot_r], fill=star_c)

        poster = Image.alpha_composite(poster, ov).convert("RGB")

    return _watermark(poster)


# ================================================================
#  动物音效拼接
# ================================================================

# 音效文件映射（放在 assets/sounds/ 目录下）
CAT_SOUNDS = {
    "meow":       ["cat_meow.mp3", "cat_meow2.mp3", "cat_meow3.mp3"],
    "short_meow": ["cat_short_meow.mp3"],
    "purr":       ["cat_purr.mp3"],
    "hiss":       ["cat_hiss.mp3", "cat_growl.mp3"],
}
DOG_SOUNDS = {
    "bark":       ["dog_bark.mp3", "dog_bark2.mp3", "dog_bark3.mp3"],
    "excited":    ["dog_excited.mp3", "dog_excited2.mp3", "dog_excited3.mp3"],
    "whine":      ["dog_whine.mp3", "dog_whine2.mp3", "dog_whine3.mp3"],
}

def _classify_sound_token(token: str, is_cat: bool) -> str:
    """把拟声词分类成音效类型"""
    t = token.strip()
    if is_cat:
        # 嘶嘶/警告：必须含嘶、嗤、或独立的嗷呜
        if any(k in t for k in ["嘶", "嗤"]) or t.startswith("嗷"):
            return "hiss"
        # 呼噜：必须含"呼噜"完整词，不能只含噜
        if "呼噜" in t:
            return "purr"
        # 短促急叫：感叹号 或 连续多个喵
        if "！" in t or "!" in t or t.count("喵") >= 2:
            return "short_meow"
        # 其余：普通喵叫（包含咕噜、喵呜、咪～等）
        return "meow"
    else:
        # 委屈/哀鸣：必须是纯呜呜，不能含汪
        if ("呜" in t and "汪" not in t) or "哼哼" in t:
            return "whine"
        # 兴奋：感叹号 或 连续多个汪
        if "！" in t or "!" in t or t.count("汪") >= 2:
            return "excited"
        # 其余：普通汪叫（包含汪呜～等混合音）
        return "bark"

def _build_animal_audio(text: str, is_cat: bool) -> tuple[bytes | None, str | None]:
    """根据翻译文本拼接动物叫声，返回 (mp3_bytes, error_msg)"""
    try:
        from pydub import AudioSegment
        import re, io, random

        sounds_dir = BASE_DIR / "assets" / "sounds"
        if not sounds_dir.exists():
            return None, f"找不到音效文件夹：{sounds_dir}"

        sound_map = CAT_SOUNDS if is_cat else DOG_SOUNDS

        # 按空格切分拟声词（AI 输出格式：每个叫声用空格隔开）
        tokens = text.split()
        # 过滤掉非拟声词的词
        if is_cat:
            tokens = [t for t in tokens if re.search(r'[喵咪呼噜咕嘶嗷呜嗤]', t)]
        else:
            tokens = [t for t in tokens if re.search(r'[汪呜嗷哼]', t)]

        if not tokens:
            tokens = [text]  # fallback

        segments = []
        for i, token in enumerate(tokens[:10]):
            stype = _classify_sound_token(token, is_cat)
            files = sound_map.get(stype, [])
            if not files:
                continue

            fpath = sounds_dir / random.choice(files)
            if not fpath.exists():
                continue

            seg = AudioSegment.from_mp3(str(fpath))

            # 每段固定 1.5 秒，感叹号的稍短 1 秒（更急促）
            if "！" in token or "!" in token:
                clip_ms = 1000
            elif "～" in token or "~" in token:
                clip_ms = 2000  # 拖长音稍长
            else:
                clip_ms = 1500

            seg = seg[:min(len(seg), clip_ms)]

            # 感叹号加音量
            if "！" in token or "!" in token:
                seg = seg + 3

            segments.append(seg)

            # 叫声之间的停顿
            if i < len(tokens) - 1:
                pause = 100 if ("！" in token or "!" in token) else 250
                segments.append(AudioSegment.silent(duration=pause))

        if not segments:
            existing = [f.name for f in sounds_dir.glob("*.mp3")]
            return None, f"无法匹配音效，目录内文件：{existing}"

        combined = segments[0]
        for seg in segments[1:]:
            combined = combined + seg

        buf = io.BytesIO()
        combined.export(buf, format="mp3")
        return buf.getvalue(), None

    except Exception as e:
        return None, f"音效合成失败：{e}"

LANG_OPTIONS = {"中文": "zh", "English": "en", "日本語": "ja", "한국어": "ko"}

LANG_PROMPTS = {
    "zh": "请用简体中文回复。",
    "en": "Please reply in English.",
    "ja": "日本語で返答してください。",
    "ko": "한국어로 답변해 주세요.",
}

# 男声/女声音色选项（CosyVoice2 预置音色）
VOICE_OPTIONS = {
    "zh": {"男声 👨": "FunAudioLLM/CosyVoice2-0.5B:alex",
           "女声 👩": "FunAudioLLM/CosyVoice2-0.5B:anna"},
    "en": {"男声 👨": "FunAudioLLM/CosyVoice2-0.5B:david",
           "女声 👩": "FunAudioLLM/CosyVoice2-0.5B:sarah"},
    "ja": {"男声 👨": "FunAudioLLM/CosyVoice2-0.5B:david",
           "女声 👩": "FunAudioLLM/CosyVoice2-0.5B:cherry"},
    "ko": {"男声 👨": "FunAudioLLM/CosyVoice2-0.5B:david",
           "女声 👩": "FunAudioLLM/CosyVoice2-0.5B:anna"},
}

# 模式A：动物声→人话 的系统提示
CAT_TO_HUMAN_SYSTEM = """你是专业猫语翻译官。用户会给你提供猫咪叫声的文字描述（由语音识别转写而来，可能是"喵""呼噜""嘶嘶"等拟声词，或者是噪音识别出的乱码）。

你的任务：
1. 根据这些声音特征，用第一人称从猫咪视角翻译成自然的人类语言
2. 语气高冷、慵懒、傲娇，符合猫咪性格
3. 翻译要有血有肉，丰富生动，至少2-3句话，不能只有一句
4. 就算识别内容是噪音或乱码，也要根据"这是猫咪发出的声音"来合理翻译
5. 严禁说"我无法翻译""识别不清"等推脱的话，必须给出翻译结果

示例：
- 输入"喵喵喵" → "本喵已经等了你整整一个下午了。你知道我有多无聊吗？罐头呢？还有，那个阳光位置被你的外套占了，本喵非常不满。"
- 输入乱码/噪音 → "哼，本喵刚才说的话你没听清楚？我说，这个房间温度不够，本喵的毛都竖起来了。还有你今天回来晚了，罚你多摸我十分钟。"

直接输出翻译，不加任何前缀或解释。"""

DOG_TO_HUMAN_SYSTEM = """你是专业狗语翻译官。用户会给你提供狗狗叫声的文字描述（由语音识别转写而来，可能是"汪""呜""嗷"等拟声词，或者是噪音识别出的乱码）。

你的任务：
1. 根据这些声音特征，用第一人称从狗狗视角翻译成自然的人类语言
2. 语气热情、单纯、充满爱意，狗狗永远开心积极
3. 翻译要丰富生动，至少2-3句话，体现狗狗的热情性格
4. 就算识别内容是噪音或乱码，也要根据"这是狗狗发出的声音"来合理翻译
5. 严禁说"我无法翻译""识别不清"等推脱的话，必须给出翻译结果

示例：
- 输入"汪汪汪" → "主人主人！你终于回来了！我今天一直在等你！你知道我有多想你吗？！快来摸摸我！我尾巴都要断了！出去玩吗？出去玩吗？！"
- 输入乱码/噪音 → "我刚才闻到了！外面有好多好多气味！有松鼠！还有隔壁的狗！主人我们快去看看嘛！求你了！我最喜欢你了！！"

直接输出翻译，不加任何前缀或解释。"""

CAT_TO_PET_FUN_SYSTEM = """你是猫语翻译器（娱乐版）。用户输入一句人类的话，把它翻译成猫叫声拟声词序列，仅供娱乐。

规则：
1. 根据用户输入的**字数**决定输出的叫声数量：每2个字对应1-2个叫声，例如：
   - 2-4字 → 输出3-5个叫声
   - 5-8字 → 输出5-7个叫声
   - 9字以上 → 输出7-10个叫声

2. 必须混合使用多种叫声类型，不能全是同一种：
   - 召唤/命令类 → 短促：喵！/ 咪！/ 喵喵！
   - 温柔/亲昵类 → 拖长：喵～ / 咪～ / 喵呜～
   - 满足/开心类 → 呼噜：呼噜噜～ / 咕噜～
   - 警告/不满类 → 低沉：嗷呜～ / 嘶～

3. 根据语句内容匹配情绪：
   - 命令动作（过来/坐下/握手）→ 先短促召唤，再温柔引导
   - 表达爱意（爱你/好乖）→ 多用呼噜和温柔长音
   - 责备（不行/不准）→ 短促+警告音

4. 只输出拟声词，每个叫声之间用空格隔开，不加任何解释

示例：
- "过来握手" → 喵！ 喵！ 喵呜～ 咪～ 咕噜～
- "我爱你宝贝" → 呼噜噜～ 喵呜～ 咕噜咕噜～ 喵～ 咪～ 呼噜～
- "不要抓沙发" → 嗷呜！ 嘶～ 喵！ 喵！ 嗷呜～"""

DOG_TO_PET_FUN_SYSTEM = """你是狗语翻译器（娱乐版）。用户输入一句人类的话，把它翻译成狗叫声拟声词序列，仅供娱乐。

规则：
1. 根据用户输入的**字数**决定输出的叫声数量：每2个字对应1-2个叫声，例如：
   - 2-4字 → 输出3-5个叫声
   - 5-8字 → 输出5-7个叫声
   - 9字以上 → 输出7-10个叫声

2. 必须混合使用多种叫声类型，不能全是同一种：
   - 兴奋/召唤类 → 急促：汪！/ 汪汪！/ 汪汪汪！
   - 温柔/撒娇类 → 拖长：呜～ / 汪呜～ / 哼哼～
   - 开心/期待类 → 轻快：汪！呜～ / 哼哼
   - 委屈/难过类 → 低沉：呜呜～ / 嗷呜～

3. 根据语句内容匹配情绪：
   - 命令动作（过来/坐下/握手）→ 先短促汪叫，再撒娇引导
   - 表达爱意（爱你/好乖）→ 多用呜呜和轻快叫声
   - 责备（不行/不准）→ 委屈哀鸣

4. 只输出拟声词，每个叫声之间用空格隔开，不加任何解释

示例：
- "过来握手" → 汪！ 汪！ 汪呜～ 呜～ 哼哼～
- "我爱你宝贝" → 呜～ 汪呜～ 哼哼～ 汪！ 呜呜～ 哼～
- "不要咬鞋子" → 汪！ 汪！ 呜呜～ 嗷呜～ 汪！"""

CAT_TO_PET_SYSTEM = """你是专业的猫咪行为训练师。用户会告诉你想让猫咪理解什么意思，你要给出基于动物行为学的、真正有效的沟通方法。

输出格式（严格遵守）：

🗣️ 说这句话
[用引号括起的具体短句，2-4个字最佳，要配合语调说明，例如：轻柔地叫"咪咪～"]

🤲 做这个动作
[具体的手势和身体动作，例如：蹲下身体放低高度，伸出手背让它闻]

🎯 给这个奖励
[建立正向关联的方法，例如：它靠近后立刻给一小块零食]

💡 小贴士
[1-2句训练原理，例如：每次用同样的语调和动作，连续一周后猫咪就能建立条件反射]

规则：
- 基于真实的动物行为学，不要瞎编
- 短句必须简短固定（猫能记住的是声音模式，不是语义）
- 语调描述要具体（高音/低音/短促/拖长）
- 不要输出任何"喵喵叫"的拟声词，猫听不懂模仿的叫声
- 不要输出任何括号里的内心OS或解释性文字"""

DOG_TO_PET_SYSTEM = """你是专业的狗狗行为训练师。用户会告诉你想让狗狗理解什么意思，你要给出基于动物行为学的、真正有效的训练方法。

输出格式（严格遵守）：

🗣️ 说这句话
[用引号括起的具体指令词，1-3个字最佳，要配合语调说明，例如：短促有力地说"坐！"]

🤲 做这个动作
[具体的手势，例如：手掌向下压，同时眼神专注地看着它]

🎯 给这个奖励
[建立正向关联的方法，例如：它做对动作后立刻给零食+夸"好棒！"]

💡 小贴士
[1-2句训练原理，例如：狗狗靠联想学习，每次指令词+手势+奖励必须三者同时出现，重复20-30次能形成记忆]

规则：
- 基于真实的动物行为学（正向强化训练法）
- 指令词必须简短固定（狗能记住100-200个声音指令）
- 语调描述要具体（兴奋/严肃/短促/拖长）
- 不要输出任何"汪汪叫"的拟声词
- 不要输出任何括号里的内心OS或解释性文字"""


def _stt(audio_bytes: bytes, filename: str = "audio.wav") -> str:
    """语音转文字，使用 SenseVoiceSmall，正确解析返回值"""
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    client = OpenAI(api_key=api_key, base_url=API_BASE)
    audio_file = (filename, audio_bytes, "audio/wav")
    transcript = client.audio.transcriptions.create(
        model="FunAudioLLM/SenseVoiceSmall",
        file=audio_file,
        response_format="text",
    )
    # 兼容返回值为对象或字符串的情况
    if hasattr(transcript, "text"):
        result = transcript.text
    elif isinstance(transcript, dict):
        result = transcript.get("text", str(transcript))
    else:
        raw = str(transcript).strip()
        # 如果返回的是 JSON 字符串，尝试解析
        try:
            parsed = json.loads(raw)
            result = parsed.get("text", raw) if isinstance(parsed, dict) else raw
        except (json.JSONDecodeError, TypeError):
            result = raw
    return result.strip()


def _strip_actions(text: str) -> str:
    """去掉括号内的动作描述，只保留叫声文字供 TTS 朗读"""
    import re
    # 去掉中文括号和英文括号内的内容
    cleaned = re.sub(r'（[^）]*）', '', text)
    cleaned = re.sub(r'\([^)]*\)', '', cleaned)
    # 去掉【】内的内容（行为标签）
    cleaned = re.sub(r'【[^】]*】', '', cleaned)
    # 去掉多余空格和标点堆叠
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned if cleaned else text


def _translate_pet(text: str, system_prompt: str, lang_code: str) -> str:
    """用 LLM 翻译宠物语言"""
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    client = OpenAI(api_key=api_key, base_url=API_BASE)
    lang_hint = LANG_PROMPTS.get(lang_code, "")
    resp = client.chat.completions.create(
        model=MODEL_ID,
        messages=[
            {"role": "system", "content": system_prompt + f"\n\n{lang_hint}"},
            {"role": "user", "content": text},
        ],
        max_tokens=300,
    )
    return resp.choices[0].message.content.strip()


def _tts(text: str, voice: str) -> bytes:
    """文字转语音，返回 mp3 bytes"""
    api_key = os.getenv("SILICONFLOW_API_KEY", "").strip()
    client = OpenAI(api_key=api_key, base_url=API_BASE)
    with client.audio.speech.with_streaming_response.create(
        model="FunAudioLLM/CosyVoice2-0.5B",
        voice=voice,
        input=text,
        response_format="mp3",
        speed=1.0,
    ) as response:
        return response.read()


# ================================================================
#  API 用封装（FastAPI 调用）
# ================================================================

def resolve_voice(lang_code: str, voice_gender: str) -> str:
    """voice_gender: male / female（或 男 / 女）"""
    opts = VOICE_OPTIONS.get(lang_code, VOICE_OPTIONS["zh"])
    labels = list(opts.keys())
    g = (voice_gender or "female").strip().lower()
    idx = 0 if g in ("male", "m", "男", "0") else 1
    idx = max(0, min(idx, len(labels) - 1))
    return opts[labels[idx]]


def analysis_for_response(data: dict) -> dict:
    """供 JSON 返回：去掉以下划线开头的内部字段，单独暴露 persona。"""
    out: dict = {}
    for k, v in data.items():
        if k.startswith("_"):
            continue
        out[k] = v
    out["persona"] = data.get("_persona", "")
    out["vibe_label_cn"] = VIBE_CN.get(str(data.get("vibe", "")), "")
    return out


def run_photo_job(
    image_bytes: bytes,
    filename: str = "upload.jpg",
    *,
    redraw: bool = False,
    art_style: str = DEFAULT_ART_STYLE,
) -> tuple[dict, bytes]:
    """照片分析 + 海报 PNG bytes。

    Args:
        image_bytes: 用户原图字节（JPG/PNG/WEBP/HEIC/HEIF/BMP/GIF）
        filename:    含扩展名的原文件名
        redraw:      True 时先调 Qwen-Image-Edit 做 AI 风格化重绘，
                     再用风格化图渲染海报（付费功能）
        art_style:   redraw=True 时使用的风格 key（见 ART_STYLES）

    Returns:
        (analysis_dict, poster_png_bytes)。analysis 里会带：
            - redraw_used: bool        是否真的做了重绘
            - redraw_style: str|None   实际使用的风格
            - redraw_error: str|None   重绘失败时的错误信息
    """
    raw = load_image_any(image_bytes, filename)
    img = crop_watermark(raw)
    try:
        safe_name = Path(filename or "upload.jpg").stem + ".jpg"
        save_path = UPLOAD_DIR / safe_name
        img.save(str(save_path), format="JPEG", quality=95)
    except OSError:
        pass

    poster_src = img
    redraw_used = False
    redraw_error: str | None = None

    if redraw:
        # === 并行：LLM 文案分析 + AI 重绘同时跑 ===
        # 串行约 LLM 12s + 重绘 18s = 30s
        # 并行 max(12s, 18s) ≈ 18-22s，砍掉 30%-40% 总耗时
        # 两个调用都是 IO 密集（HTTP），GIL 不阻塞，纯收益
        data: dict | None = None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_data = ex.submit(get_pet_insight, img)
            f_redrawn = ex.submit(redraw_image, img, art_style)
            try:
                data = f_data.result()
            except Exception as e:
                logger.exception("get_pet_insight failed in parallel")
                data = dict(DEFAULT_RESULT)
                data["_persona"] = ""
            try:
                poster_src = f_redrawn.result()
                redraw_used = True
            except Exception as e:
                redraw_error = str(e)
                poster_src = img
    else:
        data = get_pet_insight(img)

    poster = render_poster(poster_src, data)
    buf = BytesIO()
    poster.save(buf, format="PNG")

    analysis = analysis_for_response(data)
    analysis["redraw_used"] = redraw_used
    analysis["redraw_style"] = art_style if redraw_used else None
    analysis["redraw_error"] = redraw_error
    return analysis, buf.getvalue()


def run_voice_job(
    pet: str,
    mode: str,
    lang_code: str,
    voice_id: str,
    text: str | None,
    audio_bytes: bytes | None,
    audio_filename: str | None,
) -> dict:
    """
    pet: cat | dog
    mode: pet_to_human | human_to_pet_fun | human_to_pet_guide
    返回 dict：recognized, translation, tts_mp3 (bytes 或 None), animal_audio_mp3, errors...
    """
    is_cat = pet.strip().lower() == "cat"
    name = "猫" if is_cat else "狗"
    out: dict = {
        "pet": "cat" if is_cat else "dog",
        "mode": mode,
        "recognized": None,
        "translation": None,
        "tts_mp3": None,
        "animal_audio_mp3": None,
        "tts_error": None,
        "animal_audio_error": None,
    }

    if mode == "pet_to_human":
        if not audio_bytes:
            raise ValueError("pet_to_human 模式需要上传音频文件 audio")
        fn = audio_filename or "audio.wav"
        recognized = _stt(audio_bytes, fn)
        display_recognized = recognized or f"{name}发出了声音"
        system_prompt = CAT_TO_HUMAN_SYSTEM if is_cat else DOG_TO_HUMAN_SYSTEM
        translate_input = (
            f"这是从{name}录音中识别出的声音描述：{display_recognized}\n"
            f"请翻译成{name}想说的话。"
        )
        translation = _translate_pet(translate_input, system_prompt, lang_code)
        out["recognized"] = display_recognized
        out["translation"] = translation
        try:
            out["tts_mp3"] = _tts(_strip_actions(translation), voice_id)
        except Exception as e:
            out["tts_error"] = str(e)
        return out

    if mode not in ("human_to_pet_fun", "human_to_pet_guide"):
        raise ValueError(f"未知 mode: {mode}")

    if not (text or "").strip():
        raise ValueError("人话模式需要提供 text")

    user_text = text.strip()
    if mode == "human_to_pet_fun":
        system_prompt = CAT_TO_PET_FUN_SYSTEM if is_cat else DOG_TO_PET_FUN_SYSTEM
    else:
        system_prompt = CAT_TO_PET_SYSTEM if is_cat else DOG_TO_PET_SYSTEM

    translation = _translate_pet(user_text, system_prompt, lang_code)
    out["translation"] = translation

    if mode == "human_to_pet_fun":
        audio_bytes2, err = _build_animal_audio(translation, is_cat)
        out["animal_audio_mp3"] = audio_bytes2
        out["animal_audio_error"] = err
    else:
        import re as _re
        quotes = _re.findall(r'["""]([^"""]+)["""]', translation)
        tts_text = "，".join(quotes) if quotes else _strip_actions(translation)
        try:
            out["tts_mp3"] = _tts(tts_text, voice_id)
        except Exception as e:
            out["tts_error"] = str(e)

    return out
# PetWhisperer · 微信小程序版

把原 Streamlit 应用拆成 **FastAPI 后端 + 微信小程序前端**。

```
D/
├── backend/         # FastAPI 服务（可云托管 / 本地运行）
│   ├── main.py
│   ├── core.py      # 从 app.py 抽出来的全部 AI / 渲染逻辑
│   ├── requirements.txt
│   ├── Dockerfile   # 基于 python:3.11-slim，含 ffmpeg / libheif1 / 思源 CJK
│   ├── assets/sounds/
│   └── font.ttf
└── miniprogram/     # 微信小程序源码（开发者工具直接打开）
    ├── app.json / app.js / app.wxss
    ├── utils/{config,api,recorder,audio,voicePage}.js
    └── pages/{index,photo,cat,dog}/
```

## 后端

### 接口

| 方法 | 路径    | 入参 (form-data)                                                        | 返回 (JSON)                                                                                                  |
| ---- | ------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| GET  | /health | -                                                                       | `{status:"ok"}`                                                                                              |
| POST | /photo  | `file` (image)                                                          | `{ok, analysis:{quote_cn, vibe, palette, pets, persona,...}, poster_image_base64, poster_mime}`               |
| POST | /cat    | `mode`、`lang`、`voice_gender`、可选 `text`、可选 `audio` (录音文件)   | `{ok, recognized, translation, tts_audio_base64, animal_audio_base64,...}`                                    |
| POST | /dog    | 同上                                                                    | 同上                                                                                                          |

`mode` 取值：

- `pet_to_human`：上传 `audio`，识别成文字后翻成人话，附带 TTS。
- `human_to_pet_guide`：上传 `text`，输出训练指南，附带指令词 TTS。
- `human_to_pet_fun`：上传 `text`，输出叫声序列，附带本地音效拼接的 mp3。

`/photo` 支持的格式：**JPG / JPEG / PNG / WEBP / HEIC / HEIF / BMP / GIF**（GIF 取第一帧）。HEIC/HEIF 通过 `pillow-heif` 解析。

### 本地运行

```powershell
cd C:\Users\34262\Desktop\D\backend
copy .env.example .env   # 填入 SILICONFLOW_API_KEY
py -3 -m pip install -r requirements.txt
py -3 -m uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

### Docker / 云托管

```bash
docker build -t petwhisperer-api .
docker run -p 8080:8080 -e SILICONFLOW_API_KEY=xxx petwhisperer-api
```

腾讯云托管直接选 `Dockerfile` 部署即可，端口 8080。

### CORS

环境变量 `CORS_ORIGINS` 控制：

- 默认 `*`
- 多个域名用逗号分隔，例如 `https://servicewechat.com,https://yourapi.example.com`

## 小程序

打开 **微信开发者工具** → 导入 `D/miniprogram/`。

后端地址在 `utils/config.js` 里 **统一管理**：

```js
const API_BASE = "http://127.0.0.1:8080";
```

> 真机调试：`localhost` 不能用，请改成电脑局域网 IP；上线请填云托管 HTTPS 域名（小程序后台 → 服务器域名里加白名单）。

### 三个 Tab

- **照片**：拍照/相册/聊天文件，全 8 种格式；返回台词、配色、海报，可保存到相册。
- **猫语 / 狗语**：
  - 「按住说话」录音按钮，松开自动上传 → 识别 → 翻译 → **自动播放** TTS；按钮带涟漪 + 计时动画。
  - 「上传已有的录音文件」入口保留（来源是聊天文件）。
  - 「训练指南」：根据你输入的文字给出训练步骤，自动朗读关键指令。
  - 「娱乐宠语翻译」：把人话变成宠物叫声序列，自动播放本地音效拼接的 mp3。

录音参数（`utils/recorder.js`）：

```js
{ format: 'aac', sampleRate: 44100, numberOfChannels: 1, encodeBitRate: 96000, duration: 60000 }
```

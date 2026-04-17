"""PetWhisperer FastAPI 入口（云托管 / 本地）。

每个业务接口同时支持两种入参：
  - multipart/form-data （普通 HTTP / 本地 wx.uploadFile）
  - application/json + base64 字段（微信云托管 wx.cloud.callContainer）
"""
from __future__ import annotations

import base64
import logging
import os
import traceback
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from core import resolve_voice, run_photo_job, run_voice_job

logger = logging.getLogger("petwhisperer")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="PetWhisperer API",
    description="宠物翻译官 · 照片 / 猫语 / 狗语",
    version="1.0.0",
)

_origins = os.getenv("CORS_ORIGINS", "*").strip()
_allow = ["*"] if _origins == "*" else [o.strip() for o in _origins.split(",") if o.strip()]

_cred = False if _allow == ["*"] else True
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow,
    allow_credentials=_cred,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# ============================================================
# 兼容入参辅助：JSON(base64) 或 multipart/form-data 都能接
# ============================================================

def _is_json_request(request: Request) -> bool:
    ct = (request.headers.get("content-type") or "").lower()
    return "application/json" in ct


async def _read_image_input(request: Request) -> tuple[bytes, str]:
    """
    从请求里读出图片字节 + 文件名。
      - JSON: { "file_b64": "<base64>", "filename": "x.heic" }
              （兼容字段名：image_b64 / file / image / data）
      - multipart: 字段名 file
    """
    if _is_json_request(request):
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"无法解析 JSON: {e}") from e
        b64 = (
            body.get("file_b64")
            or body.get("image_b64")
            or body.get("file")
            or body.get("image")
            or body.get("data")
        )
        if not b64:
            raise HTTPException(status_code=400, detail="JSON 缺少 file_b64 字段")
        try:
            raw = base64.b64decode(b64)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"base64 解码失败: {e}") from e
        if not raw:
            raise HTTPException(status_code=400, detail="空文件")
        filename = (body.get("filename") or "upload.jpg").strip() or "upload.jpg"
        return raw, filename

    form = await request.form()
    f = form.get("file")
    if f is None:
        raise HTTPException(status_code=400, detail="缺少文件字段 'file'")
    if not getattr(f, "filename", None):
        raise HTTPException(status_code=400, detail="缺少文件名")
    raw = await f.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    return raw, f.filename


async def _read_voice_input(
    request: Request,
) -> tuple[str, str, str, str | None, bytes | None, str | None]:
    """
    返回 (mode, lang, voice_gender, text, audio_bytes, audio_filename)
    """
    if _is_json_request(request):
        try:
            body = await request.json()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"无法解析 JSON: {e}") from e
        mode = (body.get("mode") or "").strip()
        lang = (body.get("lang") or "zh").strip()
        voice_gender = (body.get("voice_gender") or "female").strip()
        text = body.get("text")
        if isinstance(text, str):
            text = text.strip() or None
        else:
            text = None
        ab64 = body.get("audio_b64") or body.get("audio")
        audio_bytes: bytes | None = None
        audio_name: str | None = None
        if ab64:
            try:
                audio_bytes = base64.b64decode(ab64)
            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"audio base64 解码失败: {e}"
                ) from e
            if not audio_bytes:
                audio_bytes = None
            else:
                audio_name = (body.get("audio_filename") or "audio.aac").strip()
        return mode, lang, voice_gender, text, audio_bytes, audio_name

    form = await request.form()
    mode = str(form.get("mode") or "").strip()
    lang = str(form.get("lang") or "zh").strip()
    voice_gender = str(form.get("voice_gender") or "female").strip()
    text_raw = form.get("text")
    text = (str(text_raw).strip() or None) if text_raw is not None else None
    audio = form.get("audio")
    audio_bytes = None
    audio_name = None
    if audio is not None and getattr(audio, "filename", None):
        data = await audio.read()
        if data:
            audio_bytes = data
            audio_name = audio.filename
    return mode, lang, voice_gender, text, audio_bytes, audio_name


# ============================================================
# 通用页面与健康检查
# ============================================================

@app.get("/", include_in_schema=False)
def root() -> HTMLResponse:
    return HTMLResponse(
        """
<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>PetWhisperer API</title>
<style>
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
       background:#FFFAF5;color:#1F1F1F;margin:0;padding:48px 32px;}
  .card{max-width:640px;margin:0 auto;background:#fff;border:2px solid #F1E6DD;
        border-radius:20px;padding:32px;box-shadow:0 8px 24px rgba(255,107,107,.08);}
  h1{margin:0 0 8px;font-size:28px;background:linear-gradient(135deg,#FF6B6B,#FF8C42);
     -webkit-background-clip:text;-webkit-text-fill-color:transparent;}
  p{color:#8B8580;margin:6px 0 24px;}
  ul{padding-left:20px;line-height:2;}
  code{background:#FFEFE3;color:#FF6B6B;padding:2px 8px;border-radius:6px;
       font-family:ui-monospace,Consolas,monospace;}
  a{color:#FF6B6B;text-decoration:none;font-weight:600;}
  a:hover{text-decoration:underline;}
</style></head><body>
<div class="card">
  <h1>🐾 PetWhisperer API</h1>
  <p>FastAPI 后端正在运行 · 同时兼容微信云托管 callContainer</p>
  <ul>
    <li>健康检查：<a href="/health">/health</a></li>
    <li>交互式接口文档：<a href="/docs">/docs</a> (Swagger)</li>
    <li>另一种文档：<a href="/redoc">/redoc</a></li>
    <li>POST <code>/photo</code> · POST <code>/cat</code> · POST <code>/dog</code></li>
  </ul>
  <p>这些接口是给微信小程序调用的，不是给浏览器直接打开的。</p>
</div>
</body></html>
"""
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ============================================================
# /photo
# ============================================================

@app.post("/photo")
async def photo_translate(request: Request) -> JSONResponse:
    """上传宠物照片，返回 AI 分析 JSON + 海报 PNG（base64）。

    入参（任选其一）：
      - multipart/form-data: file=<图片二进制>
      - application/json:    {"file_b64": "<base64>", "filename": "x.heic"}
    """
    try:
        raw, filename = await _read_image_input(request)
        analysis, poster_png = run_photo_job(raw, filename)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("photo_translate failed")
        msg = traceback.format_exc() if os.getenv("DEBUG") == "1" else str(e)
        raise HTTPException(status_code=500, detail=msg) from e

    b64 = base64.b64encode(poster_png).decode("ascii")
    return JSONResponse(
        {
            "ok": True,
            "analysis": analysis,
            "poster_image_base64": b64,
            "poster_mime": "image/png",
        }
    )


# ============================================================
# /cat /dog
# ============================================================

def _voice_json_from_result(result: dict) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "pet": result.get("pet"),
        "mode": result.get("mode"),
        "recognized": result.get("recognized"),
        "translation": result.get("translation"),
        "tts_error": result.get("tts_error"),
        "animal_audio_error": result.get("animal_audio_error"),
        "tts_audio_base64": None,
        "animal_audio_base64": None,
    }
    tts = result.get("tts_mp3")
    if isinstance(tts, (bytes, bytearray)) and tts:
        out["tts_audio_base64"] = base64.b64encode(tts).decode("ascii")
        out["tts_mime"] = "audio/mpeg"
    an = result.get("animal_audio_mp3")
    if isinstance(an, (bytes, bytearray)) and an:
        out["animal_audio_base64"] = base64.b64encode(an).decode("ascii")
        out["animal_audio_mime"] = "audio/mpeg"
    return out


@app.post("/cat")
async def cat_translate(request: Request) -> JSONResponse:
    return await _voice_endpoint("cat", request)


@app.post("/dog")
async def dog_translate(request: Request) -> JSONResponse:
    return await _voice_endpoint("dog", request)


async def _voice_endpoint(pet: str, request: Request) -> JSONResponse:
    mode, lang, voice_gender, text, audio_bytes, audio_name = await _read_voice_input(
        request
    )
    allowed_modes = ("pet_to_human", "human_to_pet_fun", "human_to_pet_guide")
    if mode not in allowed_modes:
        raise HTTPException(
            status_code=400,
            detail=f"mode 必须是 {allowed_modes} 之一（实际收到 {mode!r}）",
        )
    voice_id = resolve_voice(lang, voice_gender)
    try:
        result = run_voice_job(
            pet=pet,
            mode=mode,
            lang_code=lang,
            voice_id=voice_id,
            text=text,
            audio_bytes=audio_bytes,
            audio_filename=audio_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("voice_translate failed pet=%s", pet)
        msg = traceback.format_exc() if os.getenv("DEBUG") == "1" else str(e)
        raise HTTPException(status_code=500, detail=msg) from e
    return JSONResponse(_voice_json_from_result(result))

"""PetWhisperer FastAPI 入口（云托管 / 本地）。

每个业务接口同时支持两种入参：
  - multipart/form-data （普通 HTTP / 本地 wx.uploadFile）
  - application/json + base64 字段（微信云托管 wx.cloud.callContainer）
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
import traceback
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from core import (
    ART_STYLES,
    DEFAULT_ART_STYLE,
    resolve_voice,
    run_photo_job,
    run_voice_job,
)
from wx_security import ContentUnsafeError, check_image_safe, check_text_safe
from wx_subscribe import build_redraw_done_data, send_subscribe_message

logger = logging.getLogger("petwhisperer")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="PetWhisperer API",
    description="宠物翻译官 · 照片 / 猫语 / 狗语",
    version="1.0.0",
)

# ============================================================
# AI 重绘日配额（每个 openid 每自然日 N 次）
# ------------------------------------------------------------
# 单实例内存计数器：云托管务必把 photo 服务的"最小=最大实例数"
# 都设为 1，否则不同实例会各自计数导致超额。
# 上线后接入数据库（云开发集合）即可分布式准确计数。
# ============================================================
# 阶段 1 (保命模式): 等接广告之前, 全部数字按"最坏也只烧 ~¥100/天"算
#   - 单用户每天免费 3 次   (够大部分用户尝鲜)
#   - 分享 +1 次, 单用户最多 10 次/天
#   - 全平台每天 1000 次硬上限 (¥0.10/次 × 1000 = ¥100/天 是天花板)
# 阶段 2 (上线 1-2 周, 广告接入后): 调宽 + 关全平台上限
#   - 推荐通过云托管环境变量调, 不用改代码
REDRAW_DAILY_LIMIT = int(os.getenv("REDRAW_DAILY_LIMIT", "3"))
REDRAW_DAILY_CAP = int(os.getenv("REDRAW_DAILY_CAP", "10"))
REDRAW_BONUS_PER_SHARE = int(os.getenv("REDRAW_BONUS_PER_SHARE", "1"))
# 全平台日总配额: 所有用户加起来的硬上限. 0 = 不限 (阶段 2 设 0).
REDRAW_GLOBAL_DAILY_CAP = int(os.getenv("REDRAW_GLOBAL_DAILY_CAP", "1000"))

_default_bonus_times = max(
    0,
    (max(0, REDRAW_DAILY_CAP - REDRAW_DAILY_LIMIT) + REDRAW_BONUS_PER_SHARE - 1)
    // REDRAW_BONUS_PER_SHARE,
)
REDRAW_BONUS_DAILY_MAX = int(
    os.getenv("REDRAW_BONUS_DAILY_MAX", str(_default_bonus_times))
)
_REDRAW_QUOTA: dict[str, int] = {}
_REDRAW_BONUS: dict[str, int] = {}        # openid:date → 已获得的奖励额度
_REDRAW_BONUS_TIMES: dict[str, int] = {}  # openid:date → 今日触发奖励的次数
_REDRAW_GLOBAL_USED: dict[str, int] = {}  # date → 今日全平台已用的总次数


def _wx_openid(request: Request) -> str:
    """微信云托管会自动注入 X-WX-OPENID；本地/开发工具调试时为空。"""
    return (request.headers.get("X-WX-OPENID") or "").strip()


def _quota_key(openid: str) -> str:
    today = time.strftime("%Y-%m-%d")
    return f"{openid or 'anon'}:{today}"


def _redraw_remaining(openid: str) -> int:
    k = _quota_key(openid)
    used = _REDRAW_QUOTA.get(k, 0)
    bonus = _REDRAW_BONUS.get(k, 0)
    total_limit = min(REDRAW_DAILY_CAP, REDRAW_DAILY_LIMIT + bonus)
    return max(0, total_limit - used)


def _redraw_total_limit(openid: str) -> int:
    """当前用户今天已解锁的总额度（基础 + 分享奖励，封顶 REDRAW_DAILY_CAP）。"""
    k = _quota_key(openid)
    bonus = _REDRAW_BONUS.get(k, 0)
    return min(REDRAW_DAILY_CAP, REDRAW_DAILY_LIMIT + bonus)


def _global_quota_key() -> str:
    return time.strftime("%Y-%m-%d")


def _redraw_global_used() -> int:
    """今日全平台已经用掉的总次数。"""
    return _REDRAW_GLOBAL_USED.get(_global_quota_key(), 0)


def _redraw_global_remaining() -> int:
    """今日全平台还能再用多少次。0 表示已用完, -1 表示无全平台上限。"""
    if REDRAW_GLOBAL_DAILY_CAP <= 0:
        return -1
    return max(0, REDRAW_GLOBAL_DAILY_CAP - _redraw_global_used())


def _redraw_consume(openid: str, art_style: str = "") -> int:
    """成功使用一次后调用：+1 已用 (单用户 + 全平台双计数), 返回新剩余。

    同时打一行结构化日志方便云托管「运行日志」搜索 `[REDRAW]` 监控消耗。
    """
    k = _quota_key(openid)
    _REDRAW_QUOTA[k] = _REDRAW_QUOTA.get(k, 0) + 1
    gk = _global_quota_key()
    _REDRAW_GLOBAL_USED[gk] = _REDRAW_GLOBAL_USED.get(gk, 0) + 1
    used = _REDRAW_QUOTA[k]
    total_limit = _redraw_total_limit(openid)
    remaining = max(0, total_limit - used)
    short = (openid or "anon")[:8] + "***" if openid else "anon"
    logger.info(
        "[REDRAW] openid=%s style=%s used=%d/%d remaining=%d "
        "global=%d/%d cost=¥0.10",
        short, art_style or "?", used, total_limit, remaining,
        _REDRAW_GLOBAL_USED[gk],
        REDRAW_GLOBAL_DAILY_CAP if REDRAW_GLOBAL_DAILY_CAP > 0 else -1,
    )
    return remaining


# ============================================================
# AI 重绘异步任务队列
# ------------------------------------------------------------
# 微信 callContainer 客户端硬超时 15s（无法调整），AI 重绘要 20-30s
# → 必须用异步：上传完立即返回 task_id，前端轮询拉结果
# ============================================================
_REDRAW_TASKS: dict[str, dict[str, Any]] = {}
_REDRAW_TASK_TTL = 1800  # 任务结果保留 30 分钟，过期 GC


def _gc_redraw_tasks() -> None:
    now = time.time()
    expired = [
        tid for tid, t in _REDRAW_TASKS.items()
        if now - t.get("created_at", 0) > _REDRAW_TASK_TTL
    ]
    for tid in expired:
        _REDRAW_TASKS.pop(tid, None)


# ============================================================
# 语音翻译异步任务队列（同 redraw 思路，规避 callContainer 15s 超时）
# 猫/狗语翻译串行做 STT + LLM + TTS, 总耗时常 8-15s, 触发上限就 102002
# ============================================================
_VOICE_TASKS: dict[str, dict[str, Any]] = {}
_VOICE_TASK_TTL = 1800


def _gc_voice_tasks() -> None:
    now = time.time()
    expired = [
        tid for tid, t in _VOICE_TASKS.items()
        if now - t.get("created_at", 0) > _VOICE_TASK_TTL
    ]
    for tid in expired:
        _VOICE_TASKS.pop(tid, None)


def _run_voice_task(
    task_id: str,
    *,
    pet: str,
    mode: str,
    lang_code: str,
    voice_id: str,
    text: str | None,
    audio_bytes: bytes | None,
    audio_filename: str | None,
) -> None:
    """后台 thread 跑 run_voice_job，结果写回 _VOICE_TASKS。"""
    task = _VOICE_TASKS.get(task_id)
    if task is None:
        return
    task["status"] = "running"
    task["started_at"] = time.time()
    try:
        result = run_voice_job(
            pet=pet, mode=mode, lang_code=lang_code, voice_id=voice_id,
            text=text, audio_bytes=audio_bytes, audio_filename=audio_filename,
        )
        task["status"] = "done"
        task["result"] = _voice_json_from_result(result)
    except ValueError as e:
        task["status"] = "error"
        task["error"] = str(e)
    except Exception as e:
        logger.exception("[voice_task %s] failed", task_id)
        task["status"] = "error"
        task["error"] = str(e)
    finally:
        task["finished_at"] = time.time()


def _run_redraw_task(
    task_id: str,
    image_bytes: bytes,
    filename: str,
    art_style: str,
    openid: str,
) -> None:
    """后台 thread 跑 run_photo_job(redraw=True)，结果写回 _REDRAW_TASKS。
    完成后若 task['notify_consent'] 为 True，则通过订阅消息推送给用户。
    """
    task = _REDRAW_TASKS.get(task_id)
    if task is None:
        return
    task["status"] = "running"
    task["started_at"] = time.time()
    try:
        analysis, poster_png = run_photo_job(
            image_bytes, filename, redraw=True, art_style=art_style)
        if analysis.get("redraw_used"):
            _redraw_consume(openid, art_style)
        task["status"] = "done"
        task["result"] = {
            "ok": True,
            "analysis": analysis,
            "poster_image_base64": base64.b64encode(poster_png).decode("ascii"),
            "poster_mime": "image/png",
            "redraw_remaining": _redraw_remaining(openid),
            "redraw_limit": _redraw_total_limit(openid),
            "redraw_base_limit": REDRAW_DAILY_LIMIT,
            "redraw_cap": REDRAW_DAILY_CAP,
        }
    except Exception as e:
        logger.exception("[redraw_task %s] failed", task_id)
        task["status"] = "error"
        task["error"] = str(e)
    finally:
        task["finished_at"] = time.time()
        # 任务结束后触发订阅消息推送（仅"做完"才推）。
        # 失败任务不推，避免浪费用户的一次订阅授权。
        if task.get("status") == "done" and task.get("notify_consent") and openid:
            try:
                style_label = ART_STYLES.get(art_style, {}).get("label", "AI 风格")
                # 从 analysis 里抓宠物昵称（后备 "毛孩子"）
                analysis = (task.get("result") or {}).get("analysis") or {}
                pets = analysis.get("pets") or []
                pet_name = "毛孩子"
                if pets:
                    first = pets[0]
                    pet_name = (first.get("name") or first.get("species")
                                or pet_name)
                send_subscribe_message(
                    openid,
                    page=f"pages/photo/photo?task_id={task_id}",
                    data=build_redraw_done_data(
                        pet_name=pet_name,
                        style_label=style_label,
                        finished_at=task["finished_at"],
                    ),
                )
            except Exception:
                # 推送失败绝不能影响任务结果
                logger.exception("[redraw_task %s] subscribe push failed", task_id)


def _parse_photo_options(body_or_form: Any) -> tuple[bool, str]:
    """从 JSON dict 或 form 里读 redraw + art_style。

    redraw:    "1"/"true"/True 都视为 True
    art_style: 必须是 ART_STYLES 的 key，否则回退默认
    """
    if hasattr(body_or_form, "get"):
        raw_redraw = body_or_form.get("redraw")
        raw_style = body_or_form.get("art_style")
    else:
        raw_redraw = None
        raw_style = None

    redraw = False
    if isinstance(raw_redraw, bool):
        redraw = raw_redraw
    elif isinstance(raw_redraw, (int, float)):
        redraw = bool(raw_redraw)
    elif isinstance(raw_redraw, str):
        redraw = raw_redraw.strip().lower() in ("1", "true", "yes", "y", "on")

    art_style = (str(raw_style).strip() if raw_style else "") or DEFAULT_ART_STYLE
    if art_style not in ART_STYLES:
        art_style = DEFAULT_ART_STYLE
    return redraw, art_style


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


async def _read_image_input(
    request: Request,
) -> tuple[bytes, str, dict[str, Any]]:
    """
    从请求里读出图片字节 + 文件名 + 业务 options。
      - JSON: { "file_b64": "<base64>", "filename": "x.heic",
                "redraw": bool, "art_style": "ghibli" ... }
              （兼容字段名：image_b64 / file / image / data）
      - multipart: 字段名 file，其它字段直接放 options（form 字段都是字符串）
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
        return raw, filename, body if isinstance(body, dict) else {}

    form = await request.form()
    f = form.get("file")
    if f is None:
        raise HTTPException(status_code=400, detail="缺少文件字段 'file'")
    if not getattr(f, "filename", None):
        raise HTTPException(status_code=400, detail="缺少文件名")
    raw = await f.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    opts: dict[str, Any] = {
        k: form.get(k) for k in ("redraw", "art_style") if form.get(k) is not None
    }
    return raw, f.filename, opts


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


@app.get("/photo/quota")
def photo_quota(request: Request) -> JSONResponse:
    """查询当前 openid 当日 AI 重绘剩余次数 + 已解锁奖励额度。"""
    openid = _wx_openid(request)
    k = _quota_key(openid)
    return JSONResponse({
        "ok": True,
        "redraw_remaining": _redraw_remaining(openid),
        "redraw_limit": _redraw_total_limit(openid),
        "redraw_base_limit": REDRAW_DAILY_LIMIT,
        "redraw_cap": REDRAW_DAILY_CAP,
        "redraw_bonus": _REDRAW_BONUS.get(k, 0),
        "bonus_times_used": _REDRAW_BONUS_TIMES.get(k, 0),
        "bonus_times_max": REDRAW_BONUS_DAILY_MAX,
        "bonus_per_share": REDRAW_BONUS_PER_SHARE,
        "global_remaining": _redraw_global_remaining(),
        "global_cap": REDRAW_GLOBAL_DAILY_CAP,
        "art_styles": [
            {"key": k, "label": v["label"]} for k, v in ART_STYLES.items()
        ],
    })


@app.post("/photo/quota/share-bonus")
async def claim_share_bonus(request: Request) -> JSONResponse:
    """分享后由前端调用领取额外重绘次数。

    每次分享 +REDRAW_BONUS_PER_SHARE 次，每日总额度最多 REDRAW_DAILY_CAP 次。
    无法 100% 防作弊（前端自报），但额度有封顶，ROI 不高。
    """
    openid = _wx_openid(request)
    k = _quota_key(openid)
    times_used = _REDRAW_BONUS_TIMES.get(k, 0)
    current_bonus = _REDRAW_BONUS.get(k, 0)
    bonus_room = max(0, REDRAW_DAILY_CAP - REDRAW_DAILY_LIMIT - current_bonus)
    if times_used >= REDRAW_BONUS_DAILY_MAX or bonus_room <= 0:
        return JSONResponse({
            "ok": False,
            "error": f"今日 AI 重绘总次数已达上限（{REDRAW_DAILY_CAP} 次）",
            "redraw_remaining": _redraw_remaining(openid),
            "redraw_limit": _redraw_total_limit(openid),
            "redraw_cap": REDRAW_DAILY_CAP,
        }, status_code=429)

    added = min(REDRAW_BONUS_PER_SHARE, bonus_room)
    _REDRAW_BONUS_TIMES[k] = times_used + 1
    _REDRAW_BONUS[k] = current_bonus + added

    short = (openid or "anon")[:8] + "***" if openid else "anon"
    logger.info(
        "[SHARE_BONUS] openid=%s +%d times=%d/%d new_remaining=%d",
        short, added,
        _REDRAW_BONUS_TIMES[k], REDRAW_BONUS_DAILY_MAX,
        _redraw_remaining(openid),
    )
    return JSONResponse({
        "ok": True,
        "added": added,
        "redraw_remaining": _redraw_remaining(openid),
        "redraw_limit": _redraw_total_limit(openid),
        "redraw_base_limit": REDRAW_DAILY_LIMIT,
        "redraw_cap": REDRAW_DAILY_CAP,
        "redraw_bonus": _REDRAW_BONUS[k],
        "bonus_times_used": _REDRAW_BONUS_TIMES[k],
        "bonus_times_max": REDRAW_BONUS_DAILY_MAX,
    })


# ============================================================
# /photo/chunk —— 分片上传，绕开 callContainer 1MB 请求体限制
# ============================================================
# 内存中暂存上传中的分片。云托管要把实例最小=最大=1，
# 否则不同 chunk 命中不同实例会丢失。
_UPLOAD_SESSIONS: dict[str, dict[str, Any]] = {}
_UPLOAD_TTL = 300  # 5 分钟


def _gc_sessions() -> None:
    now = time.time()
    expired = [k for k, v in _UPLOAD_SESSIONS.items() if now - v["t0"] > _UPLOAD_TTL]
    for k in expired:
        _UPLOAD_SESSIONS.pop(k, None)


@app.post("/photo/chunk")
async def photo_chunk(request: Request) -> JSONResponse:
    """分片上传图片：
      入参 JSON: {
        session_id: str,         # 客户端生成，整次上传共用
        chunk_index: int,        # 从 0 开始
        total_chunks: int,
        chunk_b64: str,          # 这一片的 base64
        filename: str,           # 原文件名（带扩展名）
        is_last: bool            # 是否最后一片
      }
      非最后一片：返回 {received, total}
      最后一片 + 收齐：返回完整 AI 分析结果 + 海报 base64
    """
    _gc_sessions()
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法解析 JSON: {e}") from e

    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    try:
        chunk_index = int(body.get("chunk_index", -1))
        total_chunks = int(body.get("total_chunks", 0))
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"chunk_index/total_chunks 非整数: {e}") from e
    if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
        raise HTTPException(status_code=400, detail="chunk_index/total_chunks 无效")

    chunk_b64 = body.get("chunk_b64") or ""
    if not chunk_b64:
        raise HTTPException(status_code=400, detail="chunk_b64 为空")
    try:
        chunk_bytes = base64.b64decode(chunk_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"chunk base64 解码失败: {e}") from e

    filename = (body.get("filename") or "upload.jpg").strip() or "upload.jpg"
    is_last = bool(body.get("is_last"))

    sess = _UPLOAD_SESSIONS.get(session_id)
    if sess is None:
        sess = {
            "chunks": {},
            "filename": filename,
            "total": total_chunks,
            "t0": time.time(),
        }
        _UPLOAD_SESSIONS[session_id] = sess

    sess["chunks"][chunk_index] = chunk_bytes
    sess["filename"] = filename
    sess["total"] = total_chunks

    received = len(sess["chunks"])

    if not is_last or received < total_chunks:
        return JSONResponse({
            "ok": True,
            "received": received,
            "total": total_chunks,
            "done": False,
        })

    try:
        full = b"".join(sess["chunks"][i] for i in range(total_chunks))
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"分片缺失: index={e}") from e
    finally:
        _UPLOAD_SESSIONS.pop(session_id, None)

    if not full:
        raise HTTPException(status_code=400, detail="组装后文件为空")

    redraw, art_style = _parse_photo_options(body)
    openid = _wx_openid(request)

    if redraw:
        if _redraw_remaining(openid) <= 0:
            raise HTTPException(
                status_code=429,
                detail=f"今日 AI 重绘次数已用完（基础 {REDRAW_DAILY_LIMIT} 次，分享后最高 {REDRAW_DAILY_CAP} 次），明天再来吧～",
            )
        # 全平台日上限（保命用，阶段 2 接广告后可设 0 = 不限）
        if _redraw_global_remaining() == 0:
            logger.warning(
                "[REDRAW] global cap hit %d, openid=%s blocked",
                REDRAW_GLOBAL_DAILY_CAP, (openid or "anon")[:8] + "***",
            )
            raise HTTPException(
                status_code=429,
                detail="今日 AI 重绘已被抢光啦 🔥 大家热情太高，明早 0 点重置～",
            )

    try:
        check_image_safe(full, filename=filename)
    except ContentUnsafeError as e:
        raise HTTPException(status_code=400, detail=e.message) from e

    # === 关键分支：AI 重绘走异步（callContainer 硬超时 15s 必死）===
    if redraw:
        _gc_redraw_tasks()
        task_id = "rd_" + uuid.uuid4().hex[:16]
        _REDRAW_TASKS[task_id] = {
            "status": "pending",
            "created_at": time.time(),
            "openid": openid,
            "art_style": art_style,
        }
        t = threading.Thread(
            target=_run_redraw_task,
            args=(task_id, full, filename, art_style, openid),
            daemon=True,
        )
        t.start()
        logger.info(
            "[REDRAW] task created task_id=%s style=%s openid=%s",
            task_id, art_style, (openid or "anon")[:8] + "***",
        )
        return JSONResponse({
            "ok": True,
            "done": False,
            "async": True,
            "task_id": task_id,
            "status": "pending",
            "estimated_seconds": 30,
        })

    # === 原图模式：保持同步（5-10s 不会触碰 15s 网关） ===
    try:
        analysis, poster_png = run_photo_job(
            full, filename, redraw=False, art_style=art_style)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("photo_chunk job failed")
        raise HTTPException(status_code=500, detail=f"分析失败：{e}") from e

    return JSONResponse({
        "ok": True,
        "done": True,
        "analysis": analysis,
        "poster_image_base64": base64.b64encode(poster_png).decode("ascii"),
        "redraw_remaining": _redraw_remaining(openid),
        "redraw_limit": _redraw_total_limit(openid),
        "redraw_base_limit": REDRAW_DAILY_LIMIT,
        "redraw_cap": REDRAW_DAILY_CAP,
    })


@app.post("/photo/redraw/result")
async def redraw_result(request: Request) -> JSONResponse:
    """轮询 AI 重绘任务结果（前端每 2-3s 调一次直到 done/error）。

    入参 JSON: { "task_id": "rd_xxxxx" }
    返回：{
        ok, task_id, status: "pending|running|done|error",
        # status=done 时附加：analysis, poster_image_base64, redraw_remaining, ...
        # status=error 时附加：error
    }
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法解析 JSON: {e}") from e
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="缺少 task_id")

    task = _REDRAW_TASKS.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail="任务不存在或已过期（30 分钟），请重新发起",
        )

    out: dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "status": task["status"],
    }
    elapsed = time.time() - task.get("started_at", task.get("created_at", time.time()))
    out["elapsed_seconds"] = round(elapsed, 1)

    if task["status"] == "done":
        out.update(task.get("result") or {})
    elif task["status"] == "error":
        out["error"] = task.get("error", "AI 重绘失败")

    return JSONResponse(out)


@app.post("/photo/redraw/notify_consent")
async def redraw_notify_consent(request: Request) -> JSONResponse:
    """前端 wx.requestSubscribeMessage 用户同意后调用本接口，
    标记"任务做完时给我推一条订阅消息"。

    入参 JSON: { "task_id": "rd_xxxxx" }
    必须由微信小程序通过云托管发起（X-WX-OPENID 自动注入），
    且这个 openid 必须就是当初创建 task 的人，否则拒绝。

    返回: { ok: true, task_id, notify: true }
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法解析 JSON: {e}") from e

    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="缺少 task_id")

    task = _REDRAW_TASKS.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail="任务不存在或已过期，请重新发起 AI 重绘",
        )

    openid = _wx_openid(request)
    # 校验 openid 一致：防止别人拿到你的 task_id 给自己注册推送
    if not openid or openid != (task.get("openid") or ""):
        raise HTTPException(status_code=403, detail="无权操作该任务")

    task["notify_consent"] = True
    logger.info(
        "[REDRAW] notify_consent registered task_id=%s openid=%s",
        task_id, openid[:8] + "***",
    )

    # 兼容"任务已经完成才注册"的边界：直接补推一次
    if task.get("status") == "done":
        try:
            style_label = ART_STYLES.get(
                task.get("art_style", ""), {}
            ).get("label", "AI 风格")
            analysis = (task.get("result") or {}).get("analysis") or {}
            pets = analysis.get("pets") or []
            pet_name = "毛孩子"
            if pets:
                first = pets[0]
                pet_name = (first.get("name") or first.get("species")
                            or pet_name)
            send_subscribe_message(
                openid,
                page=f"pages/photo/photo?task_id={task_id}",
                data=build_redraw_done_data(
                    pet_name=pet_name,
                    style_label=style_label,
                    finished_at=task.get("finished_at", time.time()),
                ),
            )
        except Exception:
            logger.exception("[REDRAW] late-consent push failed task_id=%s", task_id)

    return JSONResponse({"ok": True, "task_id": task_id, "notify": True})


# ============================================================
# /voice/chunk —— 音频分片上传，绕开 callContainer 1MB 请求体限制
# ============================================================

@app.post("/voice/chunk")
async def voice_chunk(request: Request) -> JSONResponse:
    """音频分片上传：
      入参 JSON: {
        session_id: str,
        chunk_index: int,        # 从 0 开始
        total_chunks: int,
        chunk_b64: str,          # 这一片的 base64
        filename: str,           # 音频文件名（带扩展名）
        is_last: bool,
        # 业务字段（每片都带也行，最后一片必须带）:
        pet: "cat" | "dog",
        mode: "pet_to_human" | "human_to_pet_fun" | "human_to_pet_guide",
        lang: str,
        voice_gender: str,
        text: str | None
      }
      非最后一片：返回 {received, total, done:false}
      最后一片 + 收齐：返回与 /cat /dog 完全相同的翻译结果
    """
    _gc_sessions()
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法解析 JSON: {e}") from e

    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    try:
        chunk_index = int(body.get("chunk_index", -1))
        total_chunks = int(body.get("total_chunks", 0))
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"chunk_index/total_chunks 非整数: {e}") from e
    if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
        raise HTTPException(status_code=400, detail="chunk_index/total_chunks 无效")

    chunk_b64 = body.get("chunk_b64") or ""
    if not chunk_b64:
        raise HTTPException(status_code=400, detail="chunk_b64 为空")
    try:
        chunk_bytes = base64.b64decode(chunk_b64)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"chunk base64 解码失败: {e}") from e

    filename = (body.get("filename") or "audio.aac").strip() or "audio.aac"
    is_last = bool(body.get("is_last"))

    sess_key = f"voice:{session_id}"
    sess = _UPLOAD_SESSIONS.get(sess_key)
    if sess is None:
        sess = {
            "chunks": {},
            "filename": filename,
            "total": total_chunks,
            "t0": time.time(),
        }
        _UPLOAD_SESSIONS[sess_key] = sess

    sess["chunks"][chunk_index] = chunk_bytes
    sess["filename"] = filename
    sess["total"] = total_chunks

    received = len(sess["chunks"])

    if not is_last or received < total_chunks:
        return JSONResponse({
            "ok": True,
            "received": received,
            "total": total_chunks,
            "done": False,
        })

    try:
        full = b"".join(sess["chunks"][i] for i in range(total_chunks))
    except KeyError as e:
        raise HTTPException(status_code=400, detail=f"分片缺失: index={e}") from e
    finally:
        _UPLOAD_SESSIONS.pop(sess_key, None)

    if not full:
        raise HTTPException(status_code=400, detail="组装后音频为空")

    pet = (body.get("pet") or "").strip()
    if pet not in ("cat", "dog"):
        raise HTTPException(
            status_code=400, detail=f"pet 必须是 cat 或 dog（实际 {pet!r}）"
        )
    mode = (body.get("mode") or "").strip()
    allowed_modes = ("pet_to_human", "human_to_pet_fun", "human_to_pet_guide")
    if mode not in allowed_modes:
        raise HTTPException(
            status_code=400,
            detail=f"mode 必须是 {allowed_modes} 之一（实际 {mode!r}）",
        )
    lang = (body.get("lang") or "zh").strip()
    voice_gender = (body.get("voice_gender") or "female").strip()
    text = body.get("text")
    if isinstance(text, str):
        text = text.strip() or None
    else:
        text = None

    # 微信内容安全：用户主动输入的文字（人话→宠物语）必须过滤
    if text and mode in ("human_to_pet_fun", "human_to_pet_guide"):
        try:
            openid = request.headers.get("X-WX-OPENID", "")
            check_text_safe(text, openid=openid)
        except ContentUnsafeError as e:
            raise HTTPException(status_code=400, detail=e.message) from e

    voice_id = resolve_voice(lang, voice_gender)

    # === 改异步：voice_chunk 最后一片必须立即返回, 不能等 STT+LLM+TTS ===
    # callContainer 客户端硬超时 15s, 串行做 STT/LLM/TTS 经常 8-17s,
    # 直接同步死路一条. 所以收齐分片后立刻起后台 task, 前端轮询 /voice/result.
    _gc_voice_tasks()
    task_id = "vc_" + uuid.uuid4().hex[:16]
    _VOICE_TASKS[task_id] = {
        "status": "pending",
        "created_at": time.time(),
        "pet": pet,
        "mode": mode,
    }
    t = threading.Thread(
        target=_run_voice_task,
        args=(task_id,),
        kwargs=dict(
            pet=pet, mode=mode, lang_code=lang, voice_id=voice_id,
            text=text, audio_bytes=full, audio_filename=filename,
        ),
        daemon=True,
    )
    t.start()
    logger.info(
        "[VOICE] task created task_id=%s pet=%s mode=%s",
        task_id, pet, mode,
    )
    return JSONResponse({
        "ok": True,
        "done": False,
        "async": True,
        "task_id": task_id,
        "status": "pending",
        "estimated_seconds": 12,
    })


@app.post("/voice/result")
async def voice_result(request: Request) -> JSONResponse:
    """轮询语音翻译异步任务结果（前端每 1-2s 调一次直到 done/error）。

    入参 JSON: { "task_id": "vc_xxxxx" }
    返回：{
        ok, task_id, status: "pending|running|done|error",
        # status=done 时带上 _voice_json_from_result 全套字段
        # status=error 时带 error
    }
    """
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无法解析 JSON: {e}") from e
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(status_code=400, detail="缺少 task_id")

    task = _VOICE_TASKS.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail="语音任务不存在或已过期（30 分钟），请重新发起",
        )

    out: dict[str, Any] = {
        "ok": True,
        "task_id": task_id,
        "status": task["status"],
    }
    elapsed = time.time() - task.get("started_at", task.get("created_at", time.time()))
    out["elapsed_seconds"] = round(elapsed, 1)

    if task["status"] == "done":
        out.update(task.get("result") or {})
    elif task["status"] == "error":
        out["error"] = task.get("error", "翻译失败")

    return JSONResponse(out)


# ============================================================
# /photo
# ============================================================

@app.post("/photo")
async def photo_translate(request: Request) -> JSONResponse:
    """上传宠物照片，返回 AI 分析 JSON + 海报 PNG（base64）。

    入参（任选其一）：
      - multipart/form-data: file=<图片二进制>, redraw="1", art_style="ghibli"
      - application/json:    {
            "file_b64": "<base64>", "filename": "x.heic",
            "redraw": false,                       # 是否使用 AI 重绘（付费）
            "art_style": "ghibli|oil|ink|pixel|lego"
        }

    返回：{
      ok, analysis, poster_image_base64, poster_mime,
      redraw_remaining,    # 当日 AI 重绘剩余次数
      redraw_limit         # 当日总额度
    }
    """
    openid = _wx_openid(request)
    try:
        raw, filename, opts = await _read_image_input(request)
        redraw, art_style = _parse_photo_options(opts)

        if redraw:
            if _redraw_remaining(openid) <= 0:
                raise HTTPException(
                    status_code=429,
                    detail=f"今日 AI 重绘次数已用完（基础 {REDRAW_DAILY_LIMIT} 次，分享后最高 {REDRAW_DAILY_CAP} 次），明天再来吧～",
                )
            if _redraw_global_remaining() == 0:
                logger.warning(
                    "[REDRAW] global cap hit %d, openid=%s blocked (chunk)",
                    REDRAW_GLOBAL_DAILY_CAP, (openid or "anon")[:8] + "***",
                )
                raise HTTPException(
                    status_code=429,
                    detail="今日 AI 重绘已被抢光啦 🔥 大家热情太高，明早 0 点重置～",
                )

        # 微信内容安全：图片同步检测（仅 < 1MB 的图，超出依赖 LLM prompt 兜底）
        try:
            check_image_safe(raw, filename=filename)
        except ContentUnsafeError as e:
            raise HTTPException(status_code=400, detail=e.message) from e

        analysis, poster_png = run_photo_job(
            raw, filename, redraw=redraw, art_style=art_style)

        if analysis.get("redraw_used"):
            _redraw_consume(openid, art_style)
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
            "redraw_remaining": _redraw_remaining(openid),
            "redraw_limit": _redraw_total_limit(openid),
            "redraw_base_limit": REDRAW_DAILY_LIMIT,
            "redraw_cap": REDRAW_DAILY_CAP,
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

    # 微信内容安全：用户主动输入的文字（人话→宠物语）必须过滤
    if text and mode in ("human_to_pet_fun", "human_to_pet_guide"):
        try:
            openid = request.headers.get("X-WX-OPENID", "")
            check_text_safe(text, openid=openid)
        except ContentUnsafeError as e:
            raise HTTPException(status_code=400, detail=e.message) from e

    voice_id = resolve_voice(lang, voice_gender)

    # === 同样改异步：/cat /dog 文本翻译 LLM + TTS 串行也常 8-15s ===
    # 立即返回 task_id, 前端轮询 /voice/result, 与 voice/chunk 共享后端任务表
    _gc_voice_tasks()
    task_id = "vc_" + uuid.uuid4().hex[:16]
    _VOICE_TASKS[task_id] = {
        "status": "pending",
        "created_at": time.time(),
        "pet": pet,
        "mode": mode,
    }
    t = threading.Thread(
        target=_run_voice_task,
        args=(task_id,),
        kwargs=dict(
            pet=pet, mode=mode, lang_code=lang, voice_id=voice_id,
            text=text, audio_bytes=audio_bytes, audio_filename=audio_name,
        ),
        daemon=True,
    )
    t.start()
    logger.info(
        "[VOICE] task created task_id=%s pet=%s mode=%s",
        task_id, pet, mode,
    )
    return JSONResponse({
        "ok": True,
        "done": False,
        "async": True,
        "task_id": task_id,
        "status": "pending",
        "estimated_seconds": 12,
    })

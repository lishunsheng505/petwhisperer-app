"""微信小程序内容安全 API 封装。

为什么需要：
  - 微信审核员会用敏感词测试用户输入框，没拦截 → 直接驳回
  - 上线后用户输入违法违规内容被举报 → 小程序封号
  - 法规依据：《互联网信息服务管理办法》、《微信小程序运营规范》

实现的检测：
  - 文本：security.msgSecCheck (V2)，同步、免费、最高 2500 字符
  - 图片：security.imgSecCheck（同步），用于 photo 接口

环境变量（必填，否则跳过检测但打印警告）：
  - WX_APPID     微信小程序 AppID（如 wxa51288cfc6bce986）
  - WX_APPSECRET 微信小程序 AppSecret（mp.weixin.qq.com → 开发管理 → 开发设置）

容错策略：
  - 网络/微信服务异常 → 放行（避免误伤正常用户）
  - 未配置环境变量 → 放行 + 日志警告（开发环境友好）
  - 命中违规 → 抛 ContentUnsafeError，由调用方转 HTTP 400
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger("petwhisperer.wx_security")

WX_API_BASE = "https://api.weixin.qq.com"

_token_cache: dict = {"token": "", "expires_at": 0.0}
_token_lock = threading.Lock()


class ContentUnsafeError(Exception):
    """内容命中微信安全检测违规规则。"""

    def __init__(self, message: str = "内容含有违法违规信息，请修改后重试"):
        super().__init__(message)
        self.message = message


def _http_get_json(url: str, timeout: int = 8) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.warning("wx api GET failed: %s", e)
        return None


def _http_post_json(url: str, body: dict, timeout: int = 8) -> Optional[dict]:
    try:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.warning("wx api POST failed: %s", e)
        return None


def _http_post_binary(
    url: str, file_bytes: bytes, filename: str, timeout: int = 15
) -> Optional[dict]:
    """以 multipart/form-data 上传二进制（图片检测用）。"""
    boundary = "----petwhispererSecCheck" + str(int(time.time() * 1000))
    crlf = "\r\n"
    body_parts: list[bytes] = []
    body_parts.append(f"--{boundary}{crlf}".encode("utf-8"))
    body_parts.append(
        f'Content-Disposition: form-data; name="media"; filename="{filename}"{crlf}'
        .encode("utf-8")
    )
    body_parts.append(f"Content-Type: application/octet-stream{crlf}{crlf}".encode("utf-8"))
    body_parts.append(file_bytes)
    body_parts.append(f"{crlf}--{boundary}--{crlf}".encode("utf-8"))
    body = b"".join(body_parts)
    try:
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.warning("wx api upload failed: %s", e)
        return None


def _get_access_token() -> Optional[str]:
    """获取微信小程序 access_token，缓存 7000 秒，线程安全。"""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    with _token_lock:
        # double-check after lock
        now = time.time()
        if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
            return _token_cache["token"]

        appid = os.getenv("WX_APPID", "").strip()
        secret = os.getenv("WX_APPSECRET", "").strip()
        if not appid or not secret:
            logger.warning(
                "WX_APPID / WX_APPSECRET 未配置，内容安全检测将跳过。"
                "上线前必须在云托管环境变量里配上，否则审核会驳回。"
            )
            return None

        params = urllib.parse.urlencode(
            {"grant_type": "client_credential", "appid": appid, "secret": secret}
        )
        data = _http_get_json(f"{WX_API_BASE}/cgi-bin/token?{params}")
        if not data:
            return None
        token = data.get("access_token")
        if not token:
            logger.error(
                "微信 access_token 获取失败：%s",
                data.get("errmsg") or json.dumps(data, ensure_ascii=False),
            )
            return None

        _token_cache["token"] = token
        _token_cache["expires_at"] = now + (int(data.get("expires_in", 7200)) - 200)
        return token


def check_text_safe(text: str, openid: str = "") -> None:
    """对一段用户输入文本做微信内容安全检查。

    安全 → 静默返回 None
    违规 → 抛 ContentUnsafeError
    服务异常 / 未配置 → 放行（不抛异常）
    """
    if not text or not text.strip():
        return

    token = _get_access_token()
    if not token:
        return  # 未配置或网络异常，跳过

    body = {
        "version": 2,
        "scene": 2,  # 评论场景（用户输入想对宠物说什么 ≈ 评论）
        "openid": openid or "",
        "content": text[:2500],
        "title": "",
        "nickname": "",
        "signature": "",
    }

    url = f"{WX_API_BASE}/wxa/msg_sec_check?access_token={token}"
    data = _http_post_json(url, body)
    if not data:
        return  # 服务异常放行

    errcode = data.get("errcode", 0)
    if errcode == 0:
        # 进一步看 result.suggest
        result = data.get("result") or {}
        suggest = (result.get("suggest") or "").lower()
        if suggest in ("risky", "block"):
            raise ContentUnsafeError("文字内容含有敏感信息，请换种说法再试 🙏")
        # pass / review 都放行（review 由微信人工复核）
        return

    if errcode in (87014,):  # 旧版直接命中违规码
        raise ContentUnsafeError("文字内容含有敏感信息，请换种说法再试 🙏")

    # 其他错误（access_token 过期、参数错等）放行避免误伤
    logger.warning(
        "msg_sec_check 异常 errcode=%s errmsg=%s, fallthrough",
        errcode,
        data.get("errmsg"),
    )


def check_image_safe(file_bytes: bytes, filename: str = "upload.jpg") -> None:
    """对上传图片做微信内容安全检查（同步版 imgSecCheck）。

    限制：图片必须 < 1MB，且尺寸 <= 750×1334，否则微信会报错（此时放行）。
    安全 → 返回 None
    违规 → 抛 ContentUnsafeError
    """
    if not file_bytes:
        return
    if len(file_bytes) > 1024 * 1024:
        # 超过 1MB 微信同步接口不支持，跳过（依赖大模型自身的内容安全 prompt）
        return

    token = _get_access_token()
    if not token:
        return

    url = f"{WX_API_BASE}/wxa/img_sec_check?access_token={token}"
    data = _http_post_binary(url, file_bytes, filename)
    if not data:
        return

    errcode = data.get("errcode", 0)
    if errcode == 0:
        return
    if errcode in (87014,):
        raise ContentUnsafeError("图片内容不符合规范，请换一张再试 🙏")
    logger.warning(
        "img_sec_check 异常 errcode=%s errmsg=%s, fallthrough",
        errcode,
        data.get("errmsg"),
    )

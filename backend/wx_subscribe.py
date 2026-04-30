"""微信小程序订阅消息发送封装。

为什么需要：
  - AI 重绘客观要 30-60s，留住用户最好的方式是"做完推给你"
  - 用户授权一次（wx.requestSubscribeMessage），后端就能 send 一次
  - 用户点推送回到小程序的指定页面 → 自动展示已完成的图

使用流程：
  1. 用户在小程序里点"AI 重绘" → 前端 wx.requestSubscribeMessage
  2. 用户同意后，前端把 (openid, task_id) 提交后端做绑定
  3. 后端任务完成时调 send_subscribe_message() 发模板消息

环境变量：
  - WX_APPID                  小程序 AppID（共享 wx_security 的）
  - WX_APPSECRET              小程序 AppSecret（共享 wx_security 的）
  - WX_REDRAW_TEMPLATE_ID     订阅消息模板 ID（在 mp.weixin.qq.com 申请）
                              没配置 → send 静默跳过 + 警告日志，不影响主流程

模板字段（建议在小程序后台申请时这样配）：
  名称：「AI 重绘完成通知」
  字段：
    - thing1（标题）         例: "猫主子"
    - thing2（生成结果）     例: "您的水彩童画风海报已生成"
    - time3（完成时间）      例: "2026年4月30日 18:30"
    - thing4（备注）         例: "点击查看，海报保留 30 分钟"
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from wx_security import _get_access_token, _http_post_json, WX_API_BASE

logger = logging.getLogger("petwhisperer.wx_subscribe")

DEFAULT_PAGE = "pages/photo/photo"


def send_subscribe_message(
    openid: str,
    *,
    template_id: Optional[str] = None,
    page: str = DEFAULT_PAGE,
    data: Optional[dict] = None,
    miniprogram_state: str = "formal",
) -> bool:
    """发送一条订阅消息。

    Args:
        openid:           用户 openid（来自 X-WX-OPENID header）
        template_id:      订阅消息模板 ID。None → 读 WX_REDRAW_TEMPLATE_ID
        page:             点击通知后跳转的小程序页面，默认 photo 主页
        data:             模板字段值，形如:
                          {"thing1": {"value": "猫主子"}, ...}
        miniprogram_state: "developer"=开发版 / "trial"=体验版 / "formal"=正式版

    Returns:
        True  发送成功
        False 发送失败（缺配置 / 网络异常 / 用户没订阅 等），已记录日志，
              主流程应继续（不能因为推送失败把任务标记 failed）
    """
    if not openid:
        logger.warning("[subscribe] openid 为空，跳过推送")
        return False

    tpl = (template_id or os.getenv("WX_REDRAW_TEMPLATE_ID", "")).strip()
    if not tpl:
        logger.warning(
            "[subscribe] WX_REDRAW_TEMPLATE_ID 未配置，跳过推送。"
            "申请步骤见 wx_subscribe.py 顶部注释"
        )
        return False

    token = _get_access_token()
    if not token:
        logger.warning("[subscribe] access_token 拿不到，跳过推送")
        return False

    body = {
        "touser": openid,
        "template_id": tpl,
        "page": page,
        "miniprogram_state": miniprogram_state,
        "lang": "zh_CN",
        "data": data or {},
    }

    url = f"{WX_API_BASE}/cgi-bin/message/subscribe/send?access_token={token}"
    resp = _http_post_json(url, body, timeout=8)
    if not resp:
        logger.warning("[subscribe] 微信接口无响应 openid=%s", openid[:8] + "***")
        return False

    errcode = resp.get("errcode", -1)
    if errcode == 0:
        logger.info(
            "[subscribe] sent ok openid=%s tpl=%s",
            openid[:8] + "***", tpl[:8] + "***",
        )
        return True

    # 常见错误码：
    #   43101 用户拒绝/取消订阅 → 正常情况，不告警
    #   40003 openid 不合法
    #   47003 模板字段不匹配
    if errcode == 43101:
        logger.info(
            "[subscribe] user did not subscribe (43101) openid=%s",
            openid[:8] + "***",
        )
    else:
        logger.warning(
            "[subscribe] send failed errcode=%s errmsg=%s openid=%s",
            errcode, resp.get("errmsg"), openid[:8] + "***",
        )
    return False


def build_redraw_done_data(
    *,
    pet_name: str = "毛孩子",
    style_label: str = "AI 风格",
    finished_at: Optional[float] = None,
    note: str = "点击查看，海报保留 30 分钟",
) -> dict:
    """构造"AI 重绘完成"模板的字段数据。

    根据 WX_REDRAW_TEMPLATE_ID 模板配置，字段顺序约定：
      thing1 = pet_name
      thing2 = "{style_label} 海报已生成"
      time3  = 完成时间
      thing4 = note

    各字段长度限制：thing 最多 20 字符，time 标准时间格式
    """
    if finished_at is None:
        finished_at = time.time()
    when = time.strftime("%Y年%m月%d日 %H:%M", time.localtime(finished_at))

    def _truncate(s: str, n: int = 20) -> str:
        s = (s or "").strip()
        return s if len(s) <= n else s[: n - 1] + "…"

    return {
        "thing1": {"value": _truncate(pet_name, 20)},
        "thing2": {"value": _truncate(f"{style_label} 海报已生成", 20)},
        "time3": {"value": when},
        "thing4": {"value": _truncate(note, 20)},
    }

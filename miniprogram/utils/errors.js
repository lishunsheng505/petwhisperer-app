/**
 * 把任意错误对象转成对用户友好的中文提示。
 * 同时把原始 errMsg 打印到 console，便于调试。
 */
function toFriendly(e, ctx) {
  const raw = _rawMsg(e);
  console.warn("[friendly]", ctx || "", raw, e);

  const m = (raw || "").toLowerCase();

  if (!raw) return "服务暂时不可用，请稍后重试";

  // 网络层
  if (
    m.indexOf("request:fail") >= 0 ||
    m.indexOf("network") >= 0 ||
    m.indexOf("err_network") >= 0 ||
    m.indexOf("disconnected") >= 0
  ) {
    return "网络似乎断开了，请检查 Wi-Fi 或数据连接后重试";
  }
  if (m.indexOf("timeout") >= 0 || m.indexOf("time out") >= 0) {
    return "服务器响应较慢，请稍后再试一次";
  }
  if (m.indexOf("ssl") >= 0 || m.indexOf("certificate") >= 0) {
    return "网络证书异常，请检查系统时间或换个网络";
  }

  // 微信云托管常见错误码
  if (raw.indexOf("-606001") >= 0 || m.indexOf("body too large") >= 0) {
    return "上传内容过大，请缩短录音或换张较小的图片";
  }
  if (raw.indexOf("INVALID_HOST") >= 0) {
    return "服务暂未配置好，请稍后重试";
  }
  if (raw.indexOf("CONTAINER_INSTANCE_ZERO") >= 0) {
    return "服务器正在启动，请稍等 10 秒后重试";
  }

  // HTTP 状态
  if (m.indexOf("statuscode") >= 0) {
    if (m.indexOf("502") >= 0 || m.indexOf("503") >= 0 || m.indexOf("504") >= 0) {
      return "服务器繁忙，请稍后再试";
    }
    if (m.indexOf("401") >= 0 || m.indexOf("403") >= 0) {
      return "没有访问权限，请稍后重试";
    }
    if (m.indexOf("429") >= 0) {
      return "请求太频繁，请稍后再试";
    }
  }

  // 后端业务报错（FastAPI detail 串）：太长就截断
  if (raw.length > 80) return raw.slice(0, 80) + "…";
  return raw;
}

function _rawMsg(e) {
  if (!e) return "";
  if (typeof e === "string") return e;
  if (e.errMsg) return e.errMsg;
  if (e.message) return e.message;
  if (e.detail) return e.detail;
  try {
    return JSON.stringify(e);
  } catch (_) {
    return "" + e;
  }
}

/** 通用：执行一次 promise，失败时延迟 ms 自动重试一次。 */
async function withRetry(fn, ms = 1500) {
  try {
    return await fn();
  } catch (e1) {
    const raw = _rawMsg(e1);
    // 仅对网络/超时/启动类错误重试一次，业务报错不重试
    const m = (raw || "").toLowerCase();
    const retriable =
      m.indexOf("timeout") >= 0 ||
      m.indexOf("network") >= 0 ||
      m.indexOf("request:fail") >= 0 ||
      raw.indexOf("CONTAINER_INSTANCE_ZERO") >= 0 ||
      m.indexOf("502") >= 0 ||
      m.indexOf("503") >= 0 ||
      m.indexOf("504") >= 0;
    if (!retriable) throw e1;
    await new Promise((r) => setTimeout(r, ms));
    return await fn();
  }
}

module.exports = { toFriendly, withRetry };

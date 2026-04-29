/**
 * 后端调用配置：本地开发 vs 微信云托管
 * ─────────────────────────────────────────
 * 切换方式：把下面 USE_CLOUD 改成 true / false 即可。
 *
 *   USE_CLOUD = false → 走 HTTP，调用 API_BASE（本地或自建服务器）
 *   USE_CLOUD = true  → 走 wx.cloud.callContainer 调用微信云托管，无需配域名
 */

/** 本地 / 自建 HTTPS 后端根地址（不要末尾斜杠）。仅 USE_CLOUD = false 时生效。 */
// cloudflared 临时隧道，朋友手机能直连。重启 cloudflared 后这个 URL 会变，需要重新填。
const API_BASE = "https://strengths-deutsch-bowling-lived.trycloudflare.com";

/** 是否使用微信云托管。生产环境必须 true（cloudflared 临时隧道仅用于本地开发/朋友测试）。 */
const USE_CLOUD = true;

/** 云开发 / 云托管 环境 ID。
 *  ⚠️ 切换主体（注册新小程序）后，需要在新 AppID 下重新开通云托管，
 *     再把这里换成新的环境 ID。 */
const CLOUD_ENV = "prod-d2gsvju9f35d6f154";

/** 云托管中的服务名（创建服务时自己取的）。
 *  新小程序部署时建议改成 "miaotuantuan-api"，并在云托管控制台用相同名字建服务。 */
const CLOUD_SERVICE = "petwhisperer-api";

module.exports = {
  API_BASE,
  USE_CLOUD,
  CLOUD_ENV,
  CLOUD_SERVICE,
};

# 部署到「微信云托管」（Step-by-step，10 分钟搞定）

最终目标：`miniprogram` 在真实手机上能跑，背后调的就是云托管里的 FastAPI。

> 整个过程**不用你买服务器、不用你配域名、不用你装 Docker**。

---

## 0. 准备账号

- 一个**已认证**的小程序（个人主体或企业主体都可以；个人主体只能开通基础版云开发）
- 微信支付能扣 ¥9.9 / 月（云开发基础包）—— 不开通也能体验，但流量受限

---

## 1. 开通云开发环境（拿到 `CLOUD_ENV`）

1. 打开 **微信公众平台** → 你的小程序 → **开发管理 → 云开发** → 「立即使用」
2. 选择套餐（个人开发者 ¥9.9/月「基础版」即可）
3. **环境名**：随便填，例如 `petwhisperer-prod`
4. 创建完成后看到「环境 ID」，例如 `petwhisperer-prod-3xxxxxxx`，**复制下来**

---

## 2. 在云开发里开「云托管」服务

1. 上面控制台左侧 → **云托管 → 我的服务**
2. 点「**新建服务**」
   - **服务名**：`petwhisperer-api`（这个名字会作为 `CLOUD_SERVICE`，**记下来**）
   - 公网访问：可以**不开**（mini-program 用 `callContainer` 走内网即可，省钱）
3. 进入这个服务 → **新建版本**
4. **代码来源** 选一个：
   - **A. 上传代码包（最简单）**
     - 把 `D/backend/` 整个文件夹打成 zip 上传
     - **不要包含 `.env`、`uploads/`、`outputs/`、`__pycache__/`**（已经在 `.gitignore` 里）
   - **B. 关联代码仓库（推荐，长期）**
     - 把 `D/backend/` 推到 GitHub（或 Gitee），授权云托管拉取
     - 之后每次 `git push` 自动构建 + 部署
5. **构建配置**：
   - 流水线类型：**Dockerfile**（云托管会自动识别根目录的 `Dockerfile`）
   - 端口：**8080**（已在 `Dockerfile` `EXPOSE 8080`）
   - 其他默认
6. **环境变量**（关键 ⚠️）：
   ```
   SILICONFLOW_API_KEY = sk-xxxxxxxxxxxx     ← 你自己的 SiliconFlow Key
   CORS_ORIGINS        = *                   ← callContainer 不走 CORS，可不填
   PYTHONUNBUFFERED    = 1
   ```
7. 点「**开始上传**」/「**部署**」，等待 3~8 分钟构建完成
8. 状态变 ✅ **运行中** 即成功。可以在「日志」里看到 `Uvicorn running on http://0.0.0.0:8080`

---

## 3. 把环境 ID 写进小程序

打开 `miniprogram/utils/config.js`，改这三行：

```js
const USE_CLOUD = true;                      // 切到云托管模式
const CLOUD_ENV = "petwhisperer-prod-3xxxxxxx";   // 第 1 步拿到的环境 ID
const CLOUD_SERVICE = "petwhisperer-api";    // 第 2 步起的服务名
```

`API_BASE` 那一行**不用改**，它只在 `USE_CLOUD = false` 时生效。

---

## 4. 在小程序里关联云开发环境

微信开发者工具 → 顶部菜单栏 → 「**云开发**」按钮  
→ 选择刚才那个环境 → 确认  
（这一步是把当前小程序和云开发环境绑定，否则 `wx.cloud.callContainer` 会拒绝）

---

## 5. 验证

1. 微信开发者工具点「编译」
2. 进入「猫语翻译」→ 输入 `你今天好乖呀` → 点击「翻译为猫语」
3. 应该能看到：
   - 翻译结果气泡（喵喵～）
   - 自动播放音频
4. 「真机预览」扫码 → 同上验证

如果出错，看 Console 里的错误：

| 错误 | 原因 | 解决 |
|---|---|---|
| `cloud.init` 报错 | 没绑环境 | 重做第 4 步 |
| `INVALID_SERVICE` | `CLOUD_SERVICE` 写错 | 检查云托管控制台里的服务名 |
| 502 / 504 | 容器还在冷启动 | 等 30 秒再试，或在控制台调高「最小副本数」到 1（保活但更费钱） |
| `SILICONFLOW_API_KEY` 报错 | 环境变量没填 | 第 2 步第 6 项 |

---

## 6. 后续维护

- 改代码 → `git push` → 云托管自动构建（关联仓库时）  
  或者手动「新建版本 → 上传代码包」
- 看日志：云托管控制台 → 服务 → 日志
- 调整规格：服务详情 → 规格设置（默认 0.5 核 1G，跑这个项目够用）
- 省钱：把「最小副本数」调到 0，没人用时不计费；冷启动 ≈ 5 秒

---

## 本地开发依然可用

把 `USE_CLOUD = false`，`API_BASE = http://127.0.0.1:8080`，
然后 `cd backend && py -3 -m uvicorn main:app --port 8080`，跟以前完全一样。

> 同一份后端代码同时支持：
> - **multipart/form-data**（本地 `wx.uploadFile`）
> - **application/json + base64**（云托管 `wx.cloud.callContainer`）
>
> 切换只改 `USE_CLOUD` 一行。

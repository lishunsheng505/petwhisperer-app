/**
 * 后台 AI 重绘任务的本地待领取队列。
 *
 * 用户在 AI 重绘弹窗里点"稍后通知我"后：
 *   1. 任务在后端继续跑
 *   2. 我们把 task_id + 元信息存到本地 storage
 *   3. 用户回到小程序（onShow）时，逐个调 /photo/redraw/result 拉一次
 *      - done   → 在 UI 上展示 + 从队列移除
 *      - error  → 提示失败 + 移除
 *      - pending/running → 留在队列下次再试
 *
 * 同时设置 24 小时 TTL：超过 30 分钟后端任务结果就被 GC 了，本地也跟着清。
 */

const STORAGE_KEY = "redraw_pending_v1";
const TTL_MS = 30 * 60 * 1000;  // 30 分钟，跟后端 GC 对齐

function _now() {
  return Date.now();
}

function _read() {
  try {
    const v = wx.getStorageSync(STORAGE_KEY);
    return Array.isArray(v) ? v : [];
  } catch (e) {
    return [];
  }
}

function _write(arr) {
  try {
    wx.setStorageSync(STORAGE_KEY, arr || []);
  } catch (e) {
    console.warn("[redraw_pending] 写本地失败", e);
  }
}

/** 把任务加入待领取队列。
 *  meta: { task_id, art_style, style_label, created_at, preview_path? }
 */
function add(meta) {
  if (!meta || !meta.task_id) return;
  const list = _read();
  if (list.some((x) => x.task_id === meta.task_id)) return;
  list.push(Object.assign({ created_at: _now() }, meta));
  _write(list);
}

/** 移除指定 task_id 的任务（已领取或失败）。 */
function remove(taskId) {
  const list = _read().filter((x) => x.task_id !== taskId);
  _write(list);
}

/** 拿出所有当前还在等的任务（自动剔除 TTL 过期）。 */
function listPending() {
  const now = _now();
  const list = _read();
  const alive = list.filter((x) => now - (x.created_at || 0) < TTL_MS);
  if (alive.length !== list.length) _write(alive);
  return alive;
}

/** 是否有任何待领取任务（用于在 UI 角标提示）。 */
function hasPending() {
  return listPending().length > 0;
}

module.exports = { add, remove, listPending, hasPending };

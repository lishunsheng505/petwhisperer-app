/**
 * 本地翻译历史（最近 N 条，仅本地，不上传）
 * key 区分类型：'photo' | 'cat' | 'dog'
 */
const MAX_PER_KEY = 20;
const STORE_KEY = "pw_history_v1";

function _loadAll() {
  try {
    return wx.getStorageSync(STORE_KEY) || {};
  } catch (e) {
    return {};
  }
}

function _saveAll(all) {
  try {
    wx.setStorageSync(STORE_KEY, all);
  } catch (e) {}
}

/**
 * 加一条历史。
 * @param {string} key 'photo' | 'cat' | 'dog'
 * @param {object} item 任意可序列化对象（不要塞 base64 大字段！）
 */
function add(key, item) {
  if (!key || !item) return;
  const all = _loadAll();
  const list = Array.isArray(all[key]) ? all[key] : [];
  const entry = Object.assign({ time: Date.now() }, item);
  list.unshift(entry);
  while (list.length > MAX_PER_KEY) list.pop();
  all[key] = list;
  _saveAll(all);
}

function list(key) {
  const all = _loadAll();
  return Array.isArray(all[key]) ? all[key] : [];
}

function clear(key) {
  const all = _loadAll();
  if (key) {
    delete all[key];
  } else {
    Object.keys(all).forEach((k) => delete all[k]);
  }
  _saveAll(all);
}

/** 把时间戳格式化成 "今天 14:23" / "昨天 09:01" / "10-12 14:23" */
function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts);
  const now = new Date();
  const pad = (n) => (n < 10 ? "0" + n : "" + n);
  const hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return "今天 " + hm;
  const y = new Date(now);
  y.setDate(y.getDate() - 1);
  if (
    d.getFullYear() === y.getFullYear() &&
    d.getMonth() === y.getMonth() &&
    d.getDate() === y.getDate()
  ) {
    return "昨天 " + hm;
  }
  return pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " + hm;
}

module.exports = { add, list, clear, fmtTime };

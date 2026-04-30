/**
 * 小程序本地文件存储清理工具。
 *
 * 微信小程序 USER_DATA_PATH 给每个小程序的配额是 10MB,
 * 一旦写满, 后续 fs.writeFile 会全失败:
 *   "writeFileSync:fail the maximum size of the file storage limit is exceeded"
 *
 * 用法: 每次写入新临时文件前, 先调一次 cleanupByPrefix() 清掉同类型旧文件.
 *
 *   cleanupByPrefix("audio_", 0)        // 写音频前, 不保留任何旧音频
 *   cleanupByPrefix("poster_view_", 1)  // 写海报前, 保留最新 1 张 (避免在看的时候被清掉)
 */

/** 删掉 USER_DATA_PATH 下所有以 prefix 开头的文件, 仅保留最近 keepCount 个.
 *  按文件名字典序倒排来近似"最近"判断 (我们的命名都带 timestamp).
 *  返回 { deleted, kept, freed_bytes }
 */
function cleanupByPrefix(prefix, keepCount = 0) {
  const stats = { deleted: 0, kept: 0, freed_bytes: 0 };
  if (!prefix) return stats;
  try {
    const fs = wx.getFileSystemManager();
    const dir = wx.env.USER_DATA_PATH;
    if (!dir) return stats;

    let files;
    try {
      files = fs.readdirSync(dir);
    } catch (e) {
      return stats;
    }
    if (!Array.isArray(files)) return stats;

    const matched = files
      .filter((f) => typeof f === "string" && f.indexOf(prefix) === 0)
      .sort()
      .reverse();  // 名字带 timestamp, 倒排即"最新在前"

    const toKeep = matched.slice(0, keepCount);
    const toDelete = matched.slice(keepCount);
    stats.kept = toKeep.length;

    for (const f of toDelete) {
      const p = `${dir}/${f}`;
      try {
        // 取一下文件大小, 方便观察清出来多少空间
        try {
          const st = fs.statSync(p);
          stats.freed_bytes += (st && st.size) || 0;
        } catch (e) {}
        fs.unlinkSync(p);
        stats.deleted += 1;
      } catch (e) {
        console.warn("[storage_cleanup] unlink fail", p, e);
      }
    }

    if (stats.deleted > 0) {
      console.log(
        `[storage_cleanup] prefix=${prefix} 清掉 ${stats.deleted} 个旧文件,`,
        `释放 ${(stats.freed_bytes / 1024).toFixed(1)} KB,`,
        `保留 ${stats.kept} 个`
      );
    }
  } catch (e) {
    console.warn("[storage_cleanup] failed", e);
  }
  return stats;
}

/** 紧急清理: 全部 audio_/poster_view_ 都干掉 (写文件 fail 时调一次).
 *  返回是否清出了空间.
 */
function emergencyClear() {
  const a = cleanupByPrefix("audio_", 0);
  const p = cleanupByPrefix("poster_view_", 0);
  return a.deleted + p.deleted > 0;
}

module.exports = { cleanupByPrefix, emergencyClear };

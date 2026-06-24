/* ─────────────── 前端数据缓存 ─────────────── *
 * 解决切换菜单时组件重新挂载导致重复请求的问题 *
 * 缓存以 key 标识，带 TTL 自动过期            *
 * ──────────────────────────────────────────── */

interface CacheEntry<T = unknown> {
  data: T;
  timestamp: number;
}

const store: Record<string, CacheEntry> = {};

/** 默认缓存有效期 30 秒 */
const DEFAULT_TTL = 30_000;

/** 写入缓存 */
export function setCacheData<T>(key: string, data: T): void {
  store[key] = { data, timestamp: Date.now() };
}

/** 读取缓存，过期返回 null */
export function getCacheData<T = unknown>(key: string, ttlMs: number = DEFAULT_TTL): T | null {
  const entry = store[key];
  if (!entry) return null;
  if (Date.now() - entry.timestamp > ttlMs) {
    delete store[key];
    return null;
  }
  return entry.data as T;
}

/** 删除指定缓存 */
export function invalidateCache(key: string): void {
  delete store[key];
}

/** 按前缀批量删除缓存（如切换场景后清除该场景所有缓存） */
export function invalidateCacheByPrefix(prefix: string): void {
  Object.keys(store).forEach((k) => {
    if (k.startsWith(prefix)) delete store[k];
  });
}

/** 清空全部缓存 */
export function clearCache(): void {
  Object.keys(store).forEach((k) => delete store[k]);
}

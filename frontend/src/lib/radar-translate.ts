// 资讯雷达非中文标题翻译 —— 前端懒加载 + localStorage 缓存（按标题哈希）。
//
// 设计：AI 配置是纯客户端（localStorage，后端从不持久化），所以翻译也在前端做，
// 复用 chatStream 把一整批非中文标题喂给用户自己的模型，返回 {index: 译文} JSON。
// 译文按标题哈希缓存进 localStorage（独立于后端 radar.json —— fetch_radar 每次原子覆盖
// radar.json，但永远碰不到这里的缓存），所以同一条标题跨刷新不重复花 token。
// 未配置 AI / 翻译失败 / JSON 解析失败 都静默降级：标题照显原文，不报错不阻塞。

import { chatStream, hasLlm } from "./llm";

const CACHE_KEY = "vr-radar-zh";
const MAX_CACHE = 2000; // 标题短、几千条远低于 localStorage 5MB 上限

// ---- CJK 检测：标题含任何一个汉字即视为「已是中文」，跳过翻译 ----
const HAN_RE = /[一-鿿㐀-䶿豈-﫿]/;
export function isChinese(s: string): boolean {
  return HAN_RE.test(s || "");
}

// 标题哈希：稳定、短、无依赖。标题短、量级几百条，碰撞风险可忽略
// （即便碰撞最坏只是一条显示别的译文，下次缓存未命中会自纠）。
export function titleHash(title: string): string {
  const t = (title || "").trim();
  let h = 5381;
  for (let i = 0; i < t.length; i++) h = ((h * 33) ^ t.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

type ZhCache = Record<string, string>; // hash -> 译文
let _mem: ZhCache | null = null; // 内存镜像，避免每次读 localStorage

function loadCache(): ZhCache {
  if (_mem) return _mem;
  try {
    _mem = JSON.parse(localStorage.getItem(CACHE_KEY) || "{}") as ZhCache;
  } catch {
    _mem = {};
  }
  return _mem;
}

function saveCache(c: ZhCache): void {
  _mem = c;
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(c));
  } catch {
    /* 超额或隐私模式：内存镜像仍生效，本轮可用 */
  }
}

export function getCachedZh(title: string): string | undefined {
  return loadCache()[titleHash(title)];
}

// 写入新译文（带 LRU 上限：超限按插入顺序丢最旧的——简单可靠，标题时效性强，老条目无价值）。
export function putZh(titles: { title: string; zh: string }[]): void {
  if (!titles.length) return;
  const c = loadCache();
  let changed = false;
  for (const { title, zh } of titles) {
    if (!zh || zh === title) continue;
    c[titleHash(title)] = zh;
    changed = true;
  }
  if (!changed) return;
  const keys = Object.keys(c);
  if (keys.length > MAX_CACHE) {
    // 超限：丢弃最早写入的（JS 对象键保序，前 N 个是最旧的）
    const drop = keys.length - MAX_CACHE;
    for (let i = 0; i < drop; i++) delete c[keys[i]];
  }
  saveCache(c);
}

// 把一批非中文标题喂给模型，返回 index->译文。
// signal 让调用方在切换赛道时中止，省 token。
async function translateBatch(titles: string[], signal?: AbortSignal): Promise<Record<number, string>> {
  const lines = titles.map((t, i) => `${i}\t${t}`).join("\n");
  const prompt =
    "你是财经新闻标题翻译。把下面每条英文/外文新闻标题译成简洁中文（不超过原文长度，不加任何解释、不加引号）。" +
    "只输出一个 JSON 对象，键是行号、值是中文译文，例如 {\"0\":\"...\",\"1\":\"...\"}。若某条已是中文则原样返回。\n\n" + lines;
  let acc = "";
  await chatStream(
    [{ role: "user", content: prompt }],
    "资讯标题翻译",
    { onDelta: (t) => { acc += t; } },
    signal,
  );
  return parseIndexJson(acc);
}

// 解析模型返回的 {"0":"...","1":"..."}：去 markdown 围栏、取第一个 {...}、容错。
function parseIndexJson(raw: string): Record<number, string> {
  let s = (raw || "").trim();
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/);
  if (fence) s = fence[1].trim();
  const start = s.indexOf("{");
  const end = s.lastIndexOf("}");
  if (start === -1 || end === -1 || end <= start) return {};
  try {
    const obj = JSON.parse(s.slice(start, end + 1)) as Record<string, string>;
    const out: Record<number, string> = {};
    for (const k of Object.keys(obj)) {
      const idx = Number(k);
      if (Number.isInteger(idx) && typeof obj[k] === "string") out[idx] = obj[k];
    }
    return out;
  } catch {
    return {};
  }
}

// 翻译一个赛道的非中文标题：先过滤已中文 + 缓存命中，剩下的批量调一次模型。
// 返回 {hash: zh}（含缓存命中 + 新翻译）；未配置 AI 返回空对象；调用方中止抛 AbortError。
export async function translateSector(
  items: { title: string }[],
  signal?: AbortSignal,
): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  const todo: { idx: number; title: string }[] = [];
  for (let i = 0; i < items.length; i++) {
    const title = items[i].title;
    if (!title || isChinese(title)) continue;
    const hit = getCachedZh(title);
    if (hit) { out[titleHash(title)] = hit; continue; }
    todo.push({ idx: i, title });
  }
  if (!todo.length) return out; // 全中文或全命中
  if (!hasLlm()) return out; // 未配置 AI：静默，标题照显原文

  // 批量上限 30，超出分块（单赛道一般 10-30 条，几乎不分块）
  for (let i = 0; i < todo.length; i += 30) {
    if (signal?.aborted) break;
    const chunk = todo.slice(i, i + 30);
    const titles = chunk.map((c) => c.title);
    const mapped: Record<number, string> = {};
    try {
      const res = await translateBatch(titles, signal);
      Object.assign(mapped, res);
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") throw e;
      // 网络/鉴权/配额失败：静默，本块不写入，下次再试
      continue;
    }
    const newOnes: { title: string; zh: string }[] = [];
    for (let j = 0; j < chunk.length; j++) {
      const zh = mapped[j];
      const title = chunk[j].title;
      if (zh) {
        out[titleHash(title)] = zh;
        newOnes.push({ title, zh });
      }
    }
    if (newOnes.length) putZh(newOnes);
  }
  return out;
}

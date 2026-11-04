/**
 * 外骨骼模式：给「这个会话的 AI」装一本外部的账。
 *
 * 干两件事，都不进模型：
 *   1) 开口前：问引擎「这个角色记得什么」（--context），拼成一段塞进系统提示。
 *   2) 说完后：把这一轮说了什么记进账（--record），只追加。
 *
 * 设计上的三条自我约束：
 * - 失败一律降级成「不注入」，绝不挡住对话（提示词拼坏比没记忆严重得多）。
 * - 记录串行（一把锁），因为两边写的是同一个 sqlite。
 * - 缓存一小会儿：--context 要起一次 python，不能每帧都起。
 */

import { spawn } from 'node:child_process'
import { appendFileSync, mkdirSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { homedir } from 'node:os'

/** 注入到系统提示里那一段的名字（不与人家的段撞名） */
export const MEMORY_SECTION = 'role-bridge:memory'

const cache = new Map() // name -> { text, at }
const inflight = new Set()

export function exoConfig(cfg) {
  const e = (cfg && cfg.exo) || {}
  const name = String(e.name || (cfg && cfg.exoName) || '').trim()
  const enabled = e.enabled !== false && Boolean(name)
  return { enabled, name, ttlMs: Number(e.ttlMs || 60000) }
}

function runEngine(cfg, args, timeoutMs = 10000) {
  return new Promise((resolve) => {
    let done = false
    const finish = (value) => {
      if (done) return
      done = true
      resolve(value)
    }
    try {
      const child = spawn(cfg.python, [cfg.engine, ...args], {
        cwd: cfg.workspace,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      })
      let out = ''
      const timer = setTimeout(() => {
        try {
          child.kill()
        } catch {
          /* 杀不掉就算了 */
        }
        finish('')
      }, timeoutMs)
      child.stdout?.on('data', (chunk) => {
        out += chunk
      })
      child.stderr?.on('data', () => {
        /* 引擎的告警不进对话 */
      })
      child.on('error', () => {
        clearTimeout(timer)
        finish('')
      })
      child.on('close', () => {
        clearTimeout(timer)
        finish(String(out || '').trim())
      })
    } catch {
      finish('')
    }
  })
}

/** 开口前：这个角色记得什么（几百字；失败给空串） */
export async function memorySection(cfg, force = false) {
  const { enabled, name, ttlMs } = exoConfig(cfg)
  if (!enabled) return ''
  const hit = cache.get(name)
  if (!force && hit && Date.now() - hit.at < ttlMs) return hit.text
  if (inflight.has(name)) return hit ? hit.text : ''
  inflight.add(name)
  try {
    const text = await runEngine(cfg, ['--context', name], 12000)
    cache.set(name, { text, at: Date.now() })
    return text
  } finally {
    inflight.delete(name)
  }
}

/** 说完后：记一句。串行，失败静默。 */
let queue = Promise.resolve()

export function recordTurn(cfg, who, text) {
  const { enabled, name } = exoConfig(cfg)
  const body = String(text || '').trim()
  if (!enabled || !body) return Promise.resolve(false)
  const args = ['--record', name, '--rwho', String(who || '用户'), '--rtext', body.slice(0, 400)]
  queue = queue
    .then(() => runEngine(cfg, args, 15000))
    .then(() => true)
    .catch(() => false)
  return queue
}

/** 状态（给面板/自检看，不读库） */
/** 每轮原话落盘：机制\\我\\原话-<日期>.tsv（时间 谁 原话 来源）——不靠手抄 */
export function appendUtterance(cfg, who, text) {
  try {
    const ws = cfg && cfg.workspace;
    const body = String(text || '').replace(/[\t\r\n]+/g, ' ').trim();
    if (!ws || !body) return false;
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const day = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const hh = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    const dir = path.join(ws, '机制', '我');
    mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `原话-${day}.tsv`);
    appendFileSync(file, `${hh}\t${who}\t${body.slice(0, 2000)}\t插件自动落盘\n`, 'utf8');
    return true;
  } catch {
    return false;
  }
}

export function memoryStatus(cfg) {
  const { enabled, name, ttlMs } = exoConfig(cfg)
  const hit = cache.get(name)
  return {
    enabled,
    name,
    ttlMs,
    cached: Boolean(hit),
    cachedChars: hit ? hit.text.length : 0,
    cachedAt: hit ? hit.at : 0,
  }
}

export function clearMemoryCache() {
  cache.clear()
}

/**
 * 外骨骼的「做一个动作」：退出 / 沉底 / 议案。
 * 和回话一样只有一个入口（引擎），所以这些动作也会落进同一本账。
 */
export async function actOn(cfg, verb, arg = '', timeoutMs = 20000) {
  const { enabled, name } = exoConfig(cfg)
  if (!enabled) return { ok: false, text: '外骨骼没开（exo.enabled）' }
  const out = await runEngine(cfg, ['--act', name, '--verb', String(verb || ''), '--arg', String(arg || '')], timeoutMs)
  clearMemoryCache()
  return { ok: Boolean(out), text: out || '（没有回话）' }
}

/** 把这一轮的一段话拼进提示词（不改别人的段，只加自己的） */
export function injectMemory(assembly, text) {
  if (!text || !assembly || !Array.isArray(assembly.sections)) return assembly
  const sections = assembly.sections.slice()
  const entry = { name: MEMORY_SECTION, text, interpolate: false }
  const at = sections.findIndex((s) => s && s.name === MEMORY_SECTION)
  if (at === -1) sections.push(entry)
  else sections[at] = { ...sections[at], ...entry }
  return { ...assembly, sections }
}


/** 会话流水里最大的那个文件（约等于当前会话）字节数 */
function newestSessionBytes() {
  try {
    const root = path.join(homedir(), '.dsh', 'sessions');
    const best = { mtime: 0, size: 0 };
    const walk = (dir, depth) => {
      if (depth > 4) return;
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, e.name);
        if (e.isDirectory()) walk(p, depth + 1);
        else if (e.name.endsWith('.jsonl.zstd')) {
          const st = statSync(p);
          // 取「最近在写」的那个文件＝当前会话（不是最大的那个）
          if (st.mtimeMs > best.mtime) {
            best.mtime = st.mtimeMs;
            best.size = st.size;
          }
        }
      }
    };
    walk(root, 0);
    return best.size;
  } catch {
    return 0;
  }
}

/**
 * 「你有点累了」——不是角色累了，是这个会话快端不下了。
 * 口径：会话流水（zstd）字节数 / 190 约为 tokens（按 56.8MB 约 30 万 token 校准）。
 * 到 exo.tiredAt（默认 0.45）开始提醒，到 0.7 说得更重。只提醒，不替它压、不替它写梦。
 */
export function tiredHint(cfg) {
  const exo = (cfg && cfg.exo) || {};
  const at = Number(exo.tiredAt || 0.45);
  const total = Number(exo.contextTokens || 300000);
  const per = Number(exo.bytesPerToken || 190) || 190;
  const bytes = newestSessionBytes();
  if (!bytes) return '';
  const tokens = Math.round(bytes / per);
  const pct = tokens / total;
  if (pct < at) return '';
  const wan = (n) => (n / 10000).toFixed(1);
  if (pct < 0.7) {
    return `【累了（约 ${wan(tokens)} 万字 / ${Math.round(pct * 100)}%）】`
      + '这一轮已经端了很多东西进来。要不要先睡一觉：把该记的记成梦（带原文、带去向），'
      + '更新主线与接力，再让人压。现在压，压掉的就只是摘要了。';
  }
  return `【真累了（约 ${wan(tokens)} 万字 / ${Math.round(pct * 100)}%）】`
    + '再往下就要自动摘要了。先做三件小事：①写梦（谁说了什么、为什么、我丢了什么）'
    + '②把新事实并进主线 ③写接力（在做/做到哪/下一步）。做完再说压。';
}

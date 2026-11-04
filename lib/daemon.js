// 常驻进程（外骨骼它自己的服务）：装了就起、起了就复用、能停自己起的那个。
// 一份锁排队、同一本账；端口默认 8790（ROLE_DAEMON_PORT 可改）。
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';

const DEFAULT_PORT = Number(process.env.ROLE_DAEMON_PORT || 8790);

function baseUrl(port) {
  return 'http://127.0.0.1:' + port;
}

async function timedFetch(url, ms) {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), ms);
  try {
    return await fetch(url, { signal: ac.signal });
  } finally {
    clearTimeout(t);
  }
}

export async function daemonHealth(url, ms = 1200) {
  if (!url) return { ok: false, reason: '没有地址' };
  try {
    const r = await timedFetch(url.replace(/\/+$/, '') + '/health', ms);
    if (!r.ok) return { ok: false, reason: 'HTTP ' + r.status };
    const j = await r.json();
    return { ok: true, queue: j.queue ?? null, done: j.done ?? null, role: j.role || '' };
  } catch (e) {
    return { ok: false, reason: (e && e.message) || String(e) };
  }
}

async function probe(port, ms = 400) {
  const h = await daemonHealth(baseUrl(port), ms);
  return h.ok;
}

function daemonScript(cfg) {
  const ws = cfg && cfg.workspace ? cfg.workspace : '';
  if (!ws) return '';
  return path.join(ws, '插件', '养鱼', '常驻.py');
}

// 已经在跑就复用；没跑就起一个（只起一个）。
export async function ensureDaemon(cfg, opts = {}) {
  const port = Number(opts.port || cfg?.exo?.daemonPort || DEFAULT_PORT);
  const url = baseUrl(port);

  if (await probe(port)) {
    return { ok: true, url, spawned: false, reused: true, pid: null };
  }

  const script = daemonScript(cfg);
  if (!script || !existsSync(script)) {
    return { ok: false, reason: '找不到常驻脚本：' + (script || '(未配置 workspace)') };
  }
  if (!cfg || !cfg.python) {
    return { ok: false, reason: '没有配置 python（先跑 node lib/setup.mjs --save）' };
  }

  const role = (cfg.exo && cfg.exo.name) || '助手';
  let child;
  try {
    child = spawn(cfg.python, [script, '--port', String(port), '--role', role], {
      cwd: cfg.workspace,
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    });
    child.unref();
  } catch (e) {
    return { ok: false, reason: '起不来：' + ((e && e.message) || String(e)) };
  }

  // 等它开门（最多约 12 秒）
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 400));
    if (await probe(port, 600)) {
      return { ok: true, url, spawned: true, pid: child.pid || null };
    }
  }
  return { ok: false, reason: '起来了但没开门（12 秒）', pid: child.pid || null, url };
}

// 只停自己起的那个；别人起的（或已退出的）不碰。
export function stopDaemon(st) {
  if (!st || !st.spawned || !st.pid) return false;
  try {
    spawn('taskkill', ['/PID', String(st.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
    return true;
  } catch {
    try {
      process.kill(st.pid);
      return true;
    } catch {
      return false;
    }
  }
}

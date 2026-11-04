// dsh-role-bridge —— 引擎外壳（纯函数，可单独用 node 跑通测试）
//
// 铁律：这里**只调用** role_core.py 的命令行，不复制、不重写任何引擎逻辑，
// 也不拼提示词。引擎说什么就发什么；引擎失败就只回固定那句。

import { spawn } from 'node:child_process';
import { promises as fsp } from 'node:fs';
import path from 'node:path';
import { resolveConfig } from './config.js';

export const FALLBACK_REPLY = '（她那边暂时没有回话）';

// 注意：**这里不再写死任何盘符**。真正的解析在 lib/config.js：
// 配置 > ~/.dsh/role-bridge.json > 环境变量 > 从 DSH 运行时/工作区推断。
// 最后两个是兜底值（拿不到就由 resolveConfig 报 missing + hint）。
export const DEFAULTS = {
  workspace: '',
  python: '',
  engine: '',
  // 一次回话一般 8–20 秒；给足余量，超时就放弃并回固定那句
  timeoutMs: Number(process.env.ROLE_TIMEOUT_MS || 120000),
  doctorTimeoutMs: Number(process.env.ROLE_DOCTOR_TIMEOUT_MS || 180000),
};

/** 异步解析后的运行时（插件 apply 里 await 一次） */
export async function createRuntimeAsync(overrides = {}) {
  const cfg = { ...(await resolveConfig(overrides)) };
  return createRuntime(cfg, true);
}

export function createRuntime(overrides = {}, resolved = false) {
  const cfg = { ...DEFAULTS, ...(overrides || {}) };
  return {
    cfg,
    resolved,
    // 同角色串行队列：角色名 -> Promise（保证不会并发写同一个 memory.sqlite）
    queue: new Map(),
    // 影子对账用的内存环形缓冲（只读观察，不参与发送）
    shadow: [],
  };
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

export function stamp(date = new Date()) {
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
  );
}

function oneLine(s, max = 400) {
  const t = String(s ?? '').replace(/\r\n|\r|\n/g, ' ⏎ ').trim();
  return t.length > max ? `${t.slice(0, max)}…` : t;
}

// stderr 里最有信息量的那一行（Traceback 取最后一行）
function diagLine(stderrText) {
  const lines = String(stderrText || '')
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (lines.length === 0) return '';
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    if (/^(Traceback|[A-Za-z_.]*Error|[A-Za-z_.]*Exception)/.test(lines[i])) return oneLine(lines[i], 240);
  }
  const warning = lines.find((l) => /⚠|警告|不可用|warning/i.test(l));
  return oneLine(warning || lines[lines.length - 1], 240);
}


// ── 常驻模式（ROLE_DAEMON=http://127.0.0.1:8790）──────────────────────────
// 设了就交给常驻进程：一把锁排队、同一本账、同一副脾气；不设就照旧自己 spawn。
async function askDaemon(cfg, args, timeoutMs) {
  const base = process.env.ROLE_DAEMON.replace(/\/+$/, '');
  const i = args.indexOf('--reply');
  if (i < 0) return { code: -1, out: '', err: '常驻模式只支持 --reply', timedOut: false };
  const text = args[i + 2] || '';
  const s = args.indexOf('--speaker');
  const who = s >= 0 ? args[s + 1] || 'X' : 'X';
  const via = args.indexOf('--via');
  const source = via >= 0 ? args[via + 1] || '面板' : '面板';
  try {
    const r = await fetch(base + '/say', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ who, text, source }),
    });
    const j = await r.json();
    if (!j || !j.ok) return { code: -1, out: '', err: '常驻拒收：' + JSON.stringify(j), timedOut: false };
    const deadline = Date.now() + (timeoutMs || 180000);
    while (Date.now() < deadline) {
      await new Promise((res) => setTimeout(res, 1500));
      const q = await fetch(base + '/reply?id=' + j.id);
      const st = await q.json();
      if (st.state === '好') return { code: 0, out: st.text || '', err: '', timedOut: false };
      if (st.state === '错') return { code: -1, out: st.text || '', err: st.err || '引擎报错', timedOut: false };
    }
    return { code: -1, out: '', err: '常驻超时', timedOut: true };
  } catch (e) {
    return { code: -1, out: '', err: '连不上常驻：' + (e && e.message ? e.message : String(e)), timedOut: false };
  }
}

export function spawnEngine(cfg, args, timeoutMs) {
  if (process.env.ROLE_DAEMON) return askDaemon(cfg, args, timeoutMs);
  // 配置没齐：不要 spawn（会抛 ENOENT / 空路径），直接给一句能照着做的话
  if (!cfg.python || !cfg.engine) {
    const miss = [!cfg.python && 'python', !cfg.engine && 'engine'].filter(Boolean).join('、');
    return Promise.resolve({
      ok: false,
      code: 'NOT_CONFIGURED',
      out: '',
      err:
        `还没配置好（缺 ${miss}）。跑一次 \`node lib/setup.mjs\` 写配置，` +
        `或在插件设置里填 python / engine / workspace。`,
    });
  }
  return new Promise((resolve) => {
    let settled = false;
    let out = '';
    let err = '';
    const done = (result) => {
      if (settled) return;
      settled = true;
      resolve(result);
    };

    let child;
    try {
      child = spawn(cfg.python, [cfg.engine, ...args], {
        cwd: cfg.workspace,
        env: {
          ...process.env,
          ROLE_WS: cfg.workspace,
          PYTHONIOENCODING: 'utf-8',
          PYTHONUTF8: '1',
          PYTHONUNBUFFERED: '1',
        },
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
      });
    } catch (e) {
      done({ code: -1, out: '', err: `启动失败：${e && e.message ? e.message : String(e)}`, timedOut: false });
      return;
    }

    const timer = setTimeout(() => {
      try {
        child.kill('SIGKILL');
      } catch {
        /* 忽略 */
      }
      done({ code: -1, out, err, timedOut: true });
    }, timeoutMs);

    if (child.stdout) {
      child.stdout.setEncoding('utf8');
      child.stdout.on('data', (d) => {
        out += d;
      });
    }
    if (child.stderr) {
      child.stderr.setEncoding('utf8');
      child.stderr.on('data', (d) => {
        err += d;
      });
    }
    child.on('error', (e) => {
      clearTimeout(timer);
      done({ code: -1, out, err: `${err}\n${e && e.message ? e.message : String(e)}`, timedOut: false });
    });
    child.on('close', (code) => {
      clearTimeout(timer);
      done({ code: code == null ? -1 : code, out, err, timedOut: false });
    });
  });
}

export async function logRound(cfg, role, entry) {
  try {
    const dir = path.join(cfg.workspace, '角色', role);
    await fsp.mkdir(dir, { recursive: true });
    const line =
      `[${stamp()}] 收（${oneLine(entry.speaker || '我', 40)}）：${oneLine(entry.text, 600)}` +
      ` ｜ 发：${oneLine(entry.reply, 800)}` +
      ` ｜ 耗时 ${(entry.ms / 1000).toFixed(1)}s` +
      (entry.diag ? ` ｜ 诊断：${oneLine(entry.diag, 240)}` : '') +
      '\n';
    await fsp.appendFile(path.join(dir, '轮次.log'), line, 'utf8');
    return '';
  } catch (e) {
    return e && e.message ? e.message : String(e);
  }
}

async function doAsk(rt, { role, text, speaker }) {
  const started = Date.now();
  if (!role) {
    return { ok: false, reply: FALLBACK_REPLY, ms: 0, degraded: true, diag: '没有指定角色' };
  }
  const args = ['--reply', role, String(text ?? ''), '--speaker', String(speaker ?? '')];
  const r = await spawnEngine(rt.cfg, args, rt.cfg.timeoutMs);
  const ms = Date.now() - started;
  const stdout = typeof r.out === 'string' ? r.out.trim() : '';
  const failed = r.timedOut || r.code !== 0 || !stdout || stdout.startsWith('Traceback');
  const diag = r.timedOut
    ? `超时 ${(ms / 1000).toFixed(0)}s 未回话`
    : diagLine(r.err) || (failed && r.code !== 0 ? `退出码 ${r.code}` : '');
  const reply = failed ? FALLBACK_REPLY : stdout;
  const logError = await logRound(rt.cfg, role, { speaker, text, reply, ms, diag: failed ? diag : '' });
  return {
    ok: !failed,
    reply,
    ms,
    degraded: failed,
    diag: logError ? `${diag}${diag ? '；' : ''}日志写失败：${logError}` : diag,
  };
}

// 同角色排队：前一条没说完，后一条等着，绝不并发
export function askRole(rt, params) {
  const role = String((params && params.role) || '').trim();
  const prev = rt.queue.get(role) || Promise.resolve();
  const task = prev.then(
    () => doAsk(rt, { ...params, role }),
    () => doAsk(rt, { ...params, role }),
  );
  rt.queue.set(
    role,
    task.then(
      () => undefined,
      () => undefined,
    ),
  );
  return task;
}

export function queueDepth(rt, role) {
  return rt.queue.has(role) ? 1 : 0;
}

export async function runDoctor(rt, role) {
  const started = Date.now();
  const args = ['--doctor', String(role || '')];
  const r = await spawnEngine(rt.cfg, args, rt.cfg.doctorTimeoutMs);
  const ms = Date.now() - started;
  const text = [String(r.out || '').trim(), String(r.err || '').trim()].filter(Boolean).join('\n');
  return {
    ok: !r.timedOut && r.code === 0 && !/Traceback/.test(String(r.out || '').slice(0, 40)),
    code: r.code,
    ms,
    text: text || '（没有任何输出）',
  };
}

export async function readRoles(cfg) {
  const dir = path.join(cfg.workspace, '角色');
  try {
    const entries = await fsp.readdir(dir, { withFileTypes: true });
    return entries
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
      .filter((n) => n && !n.startsWith('_') && !n.startsWith('.'))
      .sort((a, b) => a.localeCompare(b, 'zh'));
  } catch {
    return [];
  }
}

export async function readBindings(cfg) {
  const file = path.join(cfg.workspace, '机制', '群角色对照.tsv');
  try {
    const raw = await fsp.readFile(file, 'utf8');
    return raw
      .split(/\r?\n/)
      .map((l) => l.replace(/^\uFEFF/, ''))
      .filter((l) => l.trim() && !l.trimStart().startsWith('#'))
      .map((l) => {
        const cols = l.split('\t');
        return {
          chatId: (cols[0] || '').trim(),
          role: (cols[1] || '').trim(),
          note: (cols[2] || '').trim(),
        };
      })
      .filter((row) => row.chatId);
  } catch {
    return [];
  }
}

export async function readRoundLog(cfg, role, lines = 80) {
  const file = path.join(cfg.workspace, '角色', String(role || ''), '轮次.log');
  try {
    const raw = await fsp.readFile(file, 'utf8');
    const all = raw.split(/\r?\n/).filter(Boolean);
    return { file, text: all.slice(-Math.max(1, Math.min(500, lines))).join('\n') };
  } catch {
    return { file, text: '' };
  }
}

export async function probePaths(cfg) {
  const check = async (p) => {
    try {
      await fsp.access(p);
      return true;
    } catch {
      return false;
    }
  };
  return {
    workspace: { path: cfg.workspace, ok: await check(cfg.workspace) },
    python: { path: cfg.python, ok: await check(cfg.python) },
    engine: { path: cfg.engine, ok: await check(cfg.engine) },
    rolesDir: { path: path.join(cfg.workspace, '角色'), ok: await check(path.join(cfg.workspace, '角色')) },
  };
}

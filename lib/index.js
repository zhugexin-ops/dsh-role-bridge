// dsh-role-bridge —— 宿主半边
//
// 只做三件事：
//   1. 开一条本地路由 /role-bridge/api，让面板能问「有哪些角色」「帮我问一句」「体检」「看日志」
//   2. 把请求排队后交给 role_core.py（命令行），原样带回她的话
//   3. 只读观察 IM 会话（影子对账），不发送、不拦截、不改任何东西
//
// 边界（交接单）：不重写引擎逻辑、不拼提示词、不碰 角色\ 与 机制\ 下的文件。

import { appendFileSync } from 'node:fs';
import path from 'node:path';
import { homedir } from 'node:os';

import {
  createRuntime,
  createRuntimeAsync,
  askRole,
  runDoctor,
  readRoles,
  readBindings,
  readRoundLog,
  probePaths,
  FALLBACK_REPLY,
  stamp,
} from './engine.js';
import { attachExoskeleton, exoStatus, exoToggle, exoStream, shouldTakeOver, detectTrigger } from './exo.js';
import { ensureDaemon, stopDaemon, daemonHealth } from './daemon.js';
import { memorySection, recordTurn, injectMemory, memoryStatus, MEMORY_SECTION , appendUtterance } from './memory.js';
import { checkLedger } from './guard.js';

export const name = 'role-bridge';
export const inject = ['webServer', 'llm', 'systemPrompt', 'tools'];

const API_PREFIX = '/role-bridge/api';
const GUARD_HEADER = 'x-role-bridge';
const MAX_TEXT = 4000;

function sendJson(res, status, obj) {
  const body = JSON.stringify(obj);
  res.statusCode = status;
  res.setHeader('content-type', 'application/json; charset=utf-8');
  res.setHeader('cache-control', 'no-store');
  res.end(body);
}

function sendText(res, status, text) {
  res.statusCode = status;
  res.setHeader('content-type', 'text/plain; charset=utf-8');
  res.end(text);
}

function readJsonBody(req, limit = 512 * 1024) {
  return new Promise((resolve) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > limit) {
        chunks.length = 0;
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      try {
        const raw = Buffer.concat(chunks).toString('utf8');
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
    req.on('error', () => resolve({}));
  });
}

function pushShadow(rt, entry) {
  rt.shadow.push({ t: stamp(), ...entry });
  if (rt.shadow.length > 600) rt.shadow.splice(0, rt.shadow.length - 600);
}

function messageText(message) {
  if (!message) return '';
  if (typeof message === 'string') return message;
  if (typeof message.text === 'string') return message.text;
  const content = message.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === 'string') return block;
        if (block && typeof block.text === 'string') return block.text;
        return '';
      })
      .join('');
  }
  if (message.message) return messageText(message.message);
  return '';
}

function splitSpeaker(raw) {
  const text = String(raw || '').trim();
  const match = text.match(/^([^：:\n]{1,40})[：:]\s*([\s\S]*)$/);
  if (!match) return { speaker: '', text };
  return { speaker: match[1].trim(), text: match.slice(2).join('').trim() };
}

// 影子观察：只读。IM 会话 id 形如 im:<channel>:<kind>:<stamp>:<chatId>
function attachShadow(ctx, rt) {
  const buffers = new Map();

  ctx.on('agent/inbox/inserted', (payload) => {
    try {
      const sessionId = payload && payload.agent ? payload.agent.id : undefined;
      if (!sessionId || !String(sessionId).startsWith('im:')) return;
      const raw = messageText(payload.message);
      if (!raw) return;
      const { speaker, text } = splitSpeaker(raw);
      pushShadow(rt, { kind: '收', session: String(sessionId), speaker, text });
    } catch {
      /* 观察失败绝不影响主流程 */
    }
  });

  ctx.on('agent/assistant-stream', (payload) => {
    try {
      const sessionId = payload && payload.agent ? payload.agent.id : undefined;
      if (!sessionId || !String(sessionId).startsWith('im:')) return;
      const frame = payload.frame;
      if (!frame || !frame.type) return;
      const key = String(sessionId);
      if (frame.type === 'start') {
        buffers.set(key, { attemptId: frame.attemptId, text: '' });
        return;
      }
      if (frame.type === 'chunk') {
        const buf = buffers.get(key);
        if (!buf || buf.attemptId !== frame.attemptId) return;
        const chunk = frame.chunk;
        if (chunk && chunk.type === 'text-delta' && typeof chunk.text === 'string') buf.text += chunk.text;
        return;
      }
      if (frame.type === 'end') {
        const buf = buffers.get(key);
        buffers.delete(key);
        if (!buf || buf.attemptId !== frame.attemptId) return;
        const text = buf.text.trim();
        if (!text) return;
        pushShadow(rt, { kind: '发', session: key, speaker: '现链路', text });
      }
    } catch {
      /* 同上 */
    }
  });
}

export function apply(ctx, config = {}) {
  const overrides = {};
  for (const key of ['workspace', 'python', 'engine', 'timeoutMs', 'doctorTimeoutMs']) {
    if (config && config[key] !== undefined) overrides[key] = config[key];
  }
  if (config && config.roleWs && !overrides.workspace) overrides.workspace = config.roleWs;
  const rt = createRuntime(overrides);
  // 路径解析是异步的（配置 > ~/.dsh/role-bridge.json > 环境变量 > 推断）。
  // 为了让 apply 保持同步（不同 DSH 版本对 async apply 支持不一致），
  // 这里把解析挂成 rt.ready，处理器进来先 await 它。
  rt.ready = createRuntimeAsync(overrides)
    .then((r) => {
      Object.assign(rt.cfg, r.cfg, { missing: r.cfg.missing, hint: r.cfg.hint });
      rt.resolved = true;
      if (r.cfg.missing && r.cfg.missing.length) {
        ctx?.logger?.warn?.('[role-bridge] ' + (r.cfg.hint || '运行条件没配齐'));
      }
      return rt.cfg;
    })
    .catch((e) => {
      ctx?.logger?.warn?.('[role-bridge] 配置解析失败：' + (e && e.message));
      return rt.cfg;
    });

  // 「打开就运行」：配置一解析完就把常驻拉起来（已经在跑就复用），
  // 之后所有回话都走这一个进程——一把锁、同一本账。
  rt.ready
    .then(async () => {
      const st = await ensureDaemon(rt.cfg);
      rt.daemon = st;
      if (st.ok && st.url) {
        rt.cfg.daemonUrl = st.url;
        ctx?.logger?.info?.(`[role-bridge] 常驻已就绪 ${st.url}（${st.reason}）`);
      } else {
        ctx?.logger?.warn?.('[role-bridge] 常驻没起来：' + (st.reason || '未知原因') + '（回话会退回每次单起）');
      }
      return st;
    })
    .catch((e) => {
      ctx?.logger?.warn?.('[role-bridge] 拉起常驻失败：' + (e && e.message));
      return null;
    });

  ctx.effect(() => () => stopDaemon(rt.daemon), 'dsh-role-bridge: daemon');

  // ===== 账本护栏：只追加这件事要能成立，就得有一条物理的线 =====
  //
  // 她/它的账（`角色\<名>\memory.sqlite` 与同目录 .tsv）只由引擎写。
  // 会话里的 AI 调工具时只要指向这些文件，就在这里被拦下来。
  // 不拦别的文件——那是拿笼子关人。
  ctx.effect(
    () =>
      ctx.tools.guard((exec) => {
        try {
          const cfg = rt.cfg;
          if (!cfg) return undefined;
          const reason = checkLedger(exec, cfg);
          if (reason) {
            ctx?.logger?.warn?.(`[role-bridge] ${reason}`);
            return reason;
          }
          return undefined;
        } catch {
          return undefined;
        }
      }),
    'dsh-role-bridge: ledger-guard'
  );


  // ===== 外骨骼模式：给「这个会话的 AI」装一本外部的账 =====
  //
  // 开口前把「它记得什么」塞进系统提示；说完把这一轮记进账。
  // 两个钩子都只降级、不阻断：拼提示失败就当没记忆，记账失败就算了。
  // 默认关着（cfg.exo.name 为空），开了才动手。

  ctx.effect(
    () =>
      ctx.on('system-prompt/assemble', async (assembly, _context, next) => {
        const settled = await next();
        try {
          await rt.ready;
          const text = await memorySection(rt.cfg);
          if (!text) return settled;
          return injectMemory(settled, text);
        } catch (e) {
          ctx?.logger?.warn?.('[role-bridge] 记忆注入失败（按没有记忆继续）：' + (e && e.message));
          return settled;
        }
      }),
    'dsh-role-bridge: memory-inject'
  );

  const answers = new Map(); // agentId -> 这一轮助手说出来的一整段

  // 调试用：事件形状不确定，先记账再谈提取（只写本地文件，不打扰对话）
  const dbg = (ev, extra) => {
    if (!memoryStatus(rt.cfg).enabled) return;
    try {
      const line = JSON.stringify({ t: stamp(), ev, ...extra }) + '\n';
      appendFileSync(path.join(homedir(), '.dsh', 'plugins', 'role-bridge', 'memory-debug.log'), line);
    } catch {
      /* 调试失败不影响任何事 */
    }
  };

  /** 从各种可能的形状里挖出一段话（挖不到给空串） */
  const anyText = (obj, depth = 0) => {
    if (obj == null || depth > 3) return '';
    if (typeof obj === 'string') return obj;
    if (Array.isArray(obj)) {
      return obj.map((x) => anyText(x, depth + 1)).filter(Boolean).join('\n');
    }
    if (typeof obj !== 'object') return '';
    for (const key of ['text', 'content', 'body', 'message', 'value', 'delta', 'output']) {
      const got = anyText(obj[key], depth + 1);
      if (got) return got;
    }
    return '';
  };

  ctx.on('agent/assistant-stream', (payload) => {
    try {
      const agent = payload && payload.agent;
      if (!agent || !agent.id) return;
      const frame = payload.frame || {};
      const type = String(frame.type || '');
      const piece = anyText(frame);
      dbg('stream', { type, chars: piece.length, keys: Object.keys(frame).slice(0, 6) });
      if (!piece) return;
      if (type === 'start') {
        answers.set(agent.id, piece);
      } else {
        answers.set(agent.id, (answers.get(agent.id) || '') + piece);
      }
      if (answers.size > 200) {
        const first = answers.keys().next().value;
        answers.delete(first);
      }
    } catch {
      /* 观察失败不影响对话 */
    }
  });

  ctx.on('agent/inbox/inserted', (payload) => {
    try {
      const agent = payload && payload.agent;
      if (!agent || !agent.id) return;
      const said = answers.get(agent.id);
      if (said) {
        answers.delete(agent.id);
        recordTurn(rt.cfg, '助手', said).catch(() => {});
      }
      const text = anyText(payload.message) || messageText(payload.message);
      dbg('inbox', { chars: text.length, head: text.slice(0, 30) });
      if (text) recordTurn(rt.cfg, '用户', text).catch(() => {});
      if (text) appendUtterance(rt.cfg, '用户', text);
      if (said) appendUtterance(rt.cfg, '助手', said);
    } catch {
      /* 同上 */
    }
  });


  const handler = async (req, res) => {
    try {
      // 等路径解析完成（只算一次；失败也只是 cfg 里少几个字段）
      await rt.ready;
      // 同源闸门：跨源页面无法自带这个头（插件路由不应答 CORS 预检）
      if (!req.headers[GUARD_HEADER]) {
        sendText(res, 403, 'forbidden');
        return;
      }
      if (req.method !== 'POST') {
        sendText(res, 405, 'method not allowed');
        return;
      }
      const pathname = new URL(req.url || '/', 'http://dsh.internal').pathname;
      const action = pathname.slice(API_PREFIX.length).replace(/^\/+/, '').replace(/\/+$/, '');
      const body = await readJsonBody(req);
      const role = String(body.role || '').trim();

      switch (action) {
        case 'status': {
          sendJson(res, 200, {
            ok: true,
            paths: await probePaths(rt.cfg),
            roles: await readRoles(rt.cfg),
            bindings: await readBindings(rt.cfg),
            shadow: rt.shadow.slice(-200),
            daemon: {
              ...(rt.daemon || { ok: false, reason: '还没起来' }),
              health: await daemonHealth(rt.cfg.daemonUrl),
            },
          });
          return;
        }
        case 'ask': {
          const text = String(body.text || '').slice(0, MAX_TEXT);
          if (!role || !text.trim()) {
            sendJson(res, 200, { ok: false, reply: FALLBACK_REPLY, degraded: true, diag: '角色或内容为空' });
            return;
          }
          const result = await askRole(rt, {
            role,
            text,
            speaker: String(body.speaker || '我').slice(0, 40),
          });
          sendJson(res, 200, result);
          return;
        }
        case 'doctor': {
          if (!role) {
            sendJson(res, 200, { ok: false, code: -1, ms: 0, text: '没有指定角色' });
            return;
          }
          sendJson(res, 200, await runDoctor(rt, role));
          return;
        }
        case 'log': {
          const lines = Number(body.lines || 80);
          sendJson(res, 200, { ok: true, ...(await readRoundLog(rt.cfg, role || '大肥鱼', lines)) });
          return;
        }
        case 'shadow': {
          sendJson(res, 200, { ok: true, entries: rt.shadow.slice(-300) });
          return;
        }
        case 'exo': {
          sendJson(res, 200, exoStatus(rt));
          return;
        }
        case 'exo-toggle': {
          const enabled = typeof body.enabled === 'boolean' ? body.enabled : undefined;
          sendJson(res, 200, exoToggle(rt, enabled, body.patch));
          return;
        }
        case 'exo-detect': {
          // 只验「这句话会不会触发她」，不跑引擎、不花钱
          const text = String(body.text || '');
          const hit = detectTrigger(text, rt.exo, rt.exoRoles);
          sendJson(res, 200, { ok: true, text, hit });
          return;
        }
        case 'exo-dry': {
          // 走一遍完整的流（真跑引擎），把宿主会收到的那几块原样返回
          const dryText = String(body.text || '').trim() || '在吗';
          if (!role) {
            sendJson(res, 200, { ok: false, chunks: [], error: '没有指定角色' });
            return;
          }
          const chunks = [];
          for await (const chunk of exoStream(rt, { role, text: dryText }, { signal: null })) chunks.push(chunk);
          sendJson(res, 200, { ok: true, role, text: dryText, chunks });
          return;
        }
        default:
          sendText(res, 404, 'not found');
      }
    } catch (error) {
      try {
        sendJson(res, 500, { ok: false, error: error && error.message ? error.message : String(error) });
      } catch {
        /* 响应已发或已断开，忽略 */
      }
    }
  };

  ctx.effect(
    () => ctx.webServer.register({ kind: 'prefix', path: API_PREFIX, handler }),
    'dsh-role-bridge: /role-bridge/api',
  );

  attachShadow(ctx, rt);
  attachExoskeleton(ctx, rt);
}

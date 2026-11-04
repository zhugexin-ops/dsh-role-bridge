// dsh-role-bridge —— 宿主半边
//
// 只做三件事：
//   1. 开一条本地路由 /role-bridge/api，让面板能问「有哪些角色」「帮我问一句」「体检」「看日志」
//   2. 把请求排队后交给 role_core.py（命令行），原样带回她的话
//   3. 只读观察 IM 会话（影子对账），不发送、不拦截、不改任何东西
//
// 边界（交接单）：不重写引擎逻辑、不拼提示词、不碰 角色\ 与 机制\ 下的文件。

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

export const name = 'role-bridge';
export const inject = ['webServer', 'llm'];

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

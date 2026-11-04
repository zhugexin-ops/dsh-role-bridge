// dsh-role-bridge —— 外骨骼（宿主半边）
//
// 干一件很窄的事：挂上宿主的 `llm/stream` 瀑布。当一轮请求的「用户原话」以
// `@角色名` 开头时，这一轮的回答不由大模型写，而由 role_core.py 写。
//
// 边界（交接单 + 作者批准）：
//   · 不重写引擎逻辑，只 spawn role_core.py
//   · 不拼提示词（不碰 systemPrompt.section）
//   · 不改任何 角色\、机制\ 下的文件（引擎自己会写它的账）
//   · 默认只在 @角色名 时触发；没触发就原样 next()
//
// 为什么是 @ 触发而不是「绑定某个会话」：宿主只给 llm/stream 一个 sessionId，
// 面板拿不到「当前会话」也没有会话标题，绑定会话对作者是负担；@角色名 是他
// 本来就会打的字，任何会话里都能用，也不会误伤普通对话。

import { appendFileSync, existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { askRole, readRoles, stamp } from './engine.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_CONFIG_PATH = join(HERE, '..', 'exo.json');

const DEFAULT_CONFIG = {
  enabled: false,
  speaker: 'X',
  trigger: '@',
  maxMs: 90000,
  debug: false,
};

// 配置路径：自测时可以用 ROLE_BRIDGE_EXO_CONFIG 指到临时文件，
// 免得测试把真配置改掉（踩过一次：跑完测试后续检测开关变回 false）。
export function configPath() {
  const override = process.env.ROLE_BRIDGE_EXO_CONFIG;
  if (override && override.trim()) return override.trim();
  return DEFAULT_CONFIG_PATH;
}

export function loadConfig() {
  const base = { ...DEFAULT_CONFIG };
  try {
    const file = configPath();
    if (!existsSync(file)) return base;
    const raw = JSON.parse(readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
    if (raw && typeof raw === 'object') {
      if (typeof raw.enabled === 'boolean') base.enabled = raw.enabled;
      if (typeof raw.speaker === 'string' && raw.speaker.trim()) base.speaker = raw.speaker.trim().slice(0, 40);
      if (typeof raw.trigger === 'string' && raw.trigger.trim()) base.trigger = raw.trigger.trim().slice(0, 4);
      if (Number.isFinite(raw.maxMs) && raw.maxMs >= 1000) base.maxMs = Math.min(raw.maxMs, 180000);
      if (typeof raw.debug === 'boolean') base.debug = raw.debug;
    }
  } catch {
    /* 配置坏了就用默认值，不让它把插件带崩 */
  }
  return base;
}

export function saveConfig(config) {
  const next = { ...DEFAULT_CONFIG, ...(config || {}) };
  try {
    writeFileSync(configPath(), JSON.stringify(next, null, 2), 'utf8');
  } catch {
    /* 磁盘问题不该让本轮失败 */
  }
  return next;
}

// 排查用：把每一次路过 llm/stream 的请求写到旁边的 exo-debug.log
// （只在 exo.json 里 "debug": true 时才写，默认不写）
const DEBUG_PATH = join(HERE, '..', 'exo-debug.log');

export function debugPath() {
  return DEBUG_PATH;
}

function traceArrival(rt, options) {
  try {
    const text = lastUserText(options);
    const index = pickUserIndex(options);
    const picked = index >= 0 ? firstTextBlock(options.messages[index].content) : '';
    const messages = options && Array.isArray(options.messages) ? options.messages : [];
    const line = JSON.stringify({
      t: stamp(),
      session: options && options.sessionId ? String(options.sessionId) : null,
      purpose: (options && options.purpose) || null,
      model: (options && options.model) || null,
      msgs: messages.length,
      picked: picked.slice(0, 60),
      head: text.slice(0, 60),
      tail: messages.slice(-3).map((m) => [m && m.role, blocksToText(m && m.content).slice(0, 24)]),
    });
    appendFileSync(DEBUG_PATH, line + '\n', 'utf8');
  } catch {
    /* 日志写不进去也不能影响对话 */
  }
}

// 取「这一轮用户最后说的那句话」的纯文本（不改 options，它是深冻结的）
export function lastUserText(options) {
  const messages = options && Array.isArray(options.messages) ? options.messages : [];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message || message.role !== 'user') continue;
    return blocksToText(message.content);
  }
  return '';
}

function blocksToText(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  const parts = [];
  for (const block of content) {
    if (!block) continue;
    if (typeof block === 'string') parts.push(block);
    else if (typeof block.text === 'string') parts.push(block.text);
  }
  return parts.join('');
}

// 只取第一块文字：子会话的 user content 是数组，第二块常是宿主追加的
// 「Your parent agent id is … send_message() …」这类指令，不能当成她该听的话。
function firstTextBlock(content) {
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return '';
  for (const block of content) {
    if (!block) continue;
    if (typeof block === 'string') return block;
    if (typeof block.text === 'string') return block.text;
  }
  return '';
}

// 宿主/插件每轮往消息流里塞的「运行时上下文」，不是人说的话
const INJECTED_PREFIXES = [
  'Time sampled while preparing',
  'Current runtime context',
  'Browser time zone for this request',
  'Elapsed since the preceding',
  'Current working directory',
  'Current DSH file policy',
  'Approval prompts are disabled in this session',
  'You are the Team Lead named',
  'MNEMON RUNTIME MEMORY SNAPSHOT',
  '[MNEMON]',
  '<system-reminder>',
  '[Compressed conversation section]',
];

export function isInjectedText(text) {
  const raw = String(text || '').trimStart();
  if (!raw) return true;
  for (const prefix of INJECTED_PREFIXES) {
    if (raw.startsWith(prefix)) return true;
  }
  return false;
}

// 找「人真正说的最后一句」的下标：跳过注入的运行时上下文；
// 中途遇到 assistant 说明这一轮已经在往下走了，不再接管。
export function pickUserIndex(options) {
  const messages = options && Array.isArray(options.messages) ? options.messages : [];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message) continue;
    if (message.role === 'assistant') return -1;
    if (message.role !== 'user') continue;
    if (isInjectedText(blocksToText(message.content))) continue;
    return i;
  }
  return -1;
}

// 识别 `@角色名 正文`。返回 null 表示这一轮不归她。
export function detectTrigger(text, config, roles) {
  const raw = String(text || '').trim();
  if (!raw) return null;
  const trigger = (config && config.trigger) || '@';
  if (!raw.startsWith(trigger)) return null;
  const rest = raw.slice(trigger.length).trimStart();
  const match = rest.match(/^([^\s@，,：:]{1,20})[\s：:，,]*([\s\S]*)$/);
  if (!match) return null;
  const role = match[1].trim();
  if (!role || !Array.isArray(roles) || roles.indexOf(role) < 0) return null;
  const said = match[2].trim();
  return { role, text: said || '在吗' };
}

// 只读判断：这一轮要不要接管
export function shouldTakeOver(options, config, roles) {
  if (!shouldConsider(options, config)) return null;
  const index = pickUserIndex(options);
  if (index < 0) return null;
  return detectTrigger(firstTextBlock(options.messages[index].content), config, roles);
}

// 只看「该不该考虑这一轮」（开关、内部请求、有没有会话），不看触发词
export function shouldConsider(options, config) {
  if (!config || config.enabled !== true) return false;
  if (!options || typeof options !== 'object') return false;
  if (options.purpose) return false; // 压缩、起标题这类内部请求绝不碰
  if (!options.sessionId) return false;
  return true;
}

function textChunks(text) {
  return [
    { type: 'block-start', index: 0, blockType: 'text' },
    { type: 'text-delta', index: 0, text },
    { type: 'block-end', index: 0, block: { type: 'text', text } },
    { type: 'finish', reason: { kind: 'stop' } },
  ];
}

// 把引擎这一轮的话变成宿主认识的流。宁可只出一句固定话，也不抛异常——
// 这里抛出去会打断整个 agent 回合，比「没有回话」严重得多。
export async function* exoStream(rt, hit, options) {
  const started = Date.now();
  const signal = options && options.signal;
  const role = hit.role;
  try {
    if (signal && signal.aborted) {
      yield { type: 'finish', reason: { kind: 'stop' } };
      return;
    }
    const result = await askRole(rt, {
      role,
      text: hit.text,
      speaker: rt.exo.speaker,
    });
    const text = result && typeof result.reply === 'string' && result.reply ? result.reply : '（她那边暂时没有回话）';
    for (const chunk of textChunks(text)) yield chunk;
    rt.exoLog.push({ t: stamp(), role, text: hit.text, reply: text, ms: Date.now() - started, ok: !!(result && result.ok) });
  } catch (error) {
    const text = '（她那边暂时没有回话）';
    for (const chunk of textChunks(text)) yield chunk;
    rt.exoLog.push({ t: stamp(), role, text: hit.text, reply: text, ms: Date.now() - started, ok: false, diag: String((error && error.message) || error) });
  }
  if (rt.exoLog.length > 200) rt.exoLog.splice(0, rt.exoLog.length - 200);
}

// 挂到宿主上：一条 waterfall 监听 + 状态
export function attachExoskeleton(ctx, rt) {
  rt.exo = loadConfig();
  rt.exoLog = [];
  // null = 角色表还没读回来。**没读回来时不接管**（直接放行给模型），
  // 以前这里走的是「先交一个延迟流、等表回来再定」的分支——宿主如果不消费
  // 这个流，普通提问就会被挂住（test/fake-host 报的「没放行（不对）」就是它）。
  rt.exoRoles = null;
  rt.exoDebug = rt.exo.debug === true;

  try {
    appendFileSync(
      DEBUG_PATH,
      JSON.stringify({ t: stamp(), ev: 'attach', enabled: rt.exo.enabled === true, debug: rt.exoDebug, trigger: rt.exo.trigger }) + '\n',
      'utf8',
    );
  } catch {
    /* 排查日志写不进去不影响功能 */
  }

  // 先同步读一次角色表：启动后的头几轮 LLM 请求不用等异步 IO，
  // 也就不会出现「开着外骨骼却没接管」的那一小段空窗。
  const readRolesSyncNow = () => {
    try {
      const dir = rt.cfg && rt.cfg.workspace ? join(rt.cfg.workspace, '角色') : '';
      if (!dir) return false;
      rt.exoRoles = readdirSync(dir, { withFileTypes: true })
        .filter((e) => e.isDirectory())
        .map((e) => e.name)
        .filter((n) => n && !n.startsWith('_') && !n.startsWith('.'))
        .sort((a, b) => a.localeCompare(b, 'zh'));
      return true;
    } catch {
      return false;
    }
  };
  readRolesSyncNow();

  const refreshRoles = () => {
    if (readRolesSyncNow()) return; // 同步就能读到，不必再走异步
    readRoles(rt.cfg)
      .then((roles) => {
        rt.exoRoles = Array.isArray(roles) ? roles : [];
      })
      .catch(() => {
        // 读不到（引擎没配好 / 盘慢）就先保持 null＝不接管；隔 5 秒再试，最多 5 次
        if ((rt.exoRoleTries || 0) < 5) {
          rt.exoRoleTries = (rt.exoRoleTries || 0) + 1;
          const t = setTimeout(refreshRoles, 5000);
          if (typeof t.unref === 'function') t.unref();
        }
      });
  };
  refreshRoles();
  // 配置是异步解析的（lib/config.js）：等它回来再读一次角色表
  if (rt.ready && typeof rt.ready.then === 'function') {
    rt.ready.then(refreshRoles).catch(() => {});
  }
  // 角色目录会变（新加人、改名字）：每分钟刷一次，让 @ 认得出新角色
  const roleTimer = setInterval(refreshRoles, 60000);
  if (typeof roleTimer.unref === 'function') roleTimer.unref();

  // 用 { global: true } 注册：宿主里的事件是按 ctx 过滤的（EventsService.dispatch
  // 只把 global 监听器、或通过 thisArg[Context.filter] 的监听器交出去）。我们和
  // LlmRuntime 是兄弟 ctx，不带 global 就收不到。
  ctx.on(
    'llm/stream',
    (options, next) => {
      try {
        if (rt.exoDebug) traceArrival(rt, options);
        if (!shouldConsider(options, rt.exo)) return next();
        // 角色表还没读回来：这一轮不接管，原样放行（宁可不接管，也不能把对话卡住）
        if (!rt.exoRoles) return next();
        const hit = shouldTakeOver(options, rt.exo, rt.exoRoles);
        if (!hit) return next();
        return exoStream(rt, hit, options);
      } catch {
        // 判断本身出错也必须放行，不能把正常对话卡死
        return next();
      }
    },
    { global: true },
  );

  return refreshRoles;
}

export function exoStatus(rt) {
  return {
    ok: true,
    enabled: rt.exo.enabled === true,
    speaker: rt.exo.speaker,
    trigger: rt.exo.trigger,
    debug: rt.exoDebug === true,
    roles: rt.exoRoles.slice(),
    configPath: configPath(),
    debugPath: DEBUG_PATH,
    recent: rt.exoLog.slice(-20),
  };
}

export function exoToggle(rt, enabled, patch) {
  const next = { ...rt.exo };
  if (typeof enabled === 'boolean') next.enabled = enabled;
  if (patch && typeof patch === 'object') {
    if (typeof patch.speaker === 'string' && patch.speaker.trim()) next.speaker = patch.speaker.trim().slice(0, 40);
    if (typeof patch.trigger === 'string' && patch.trigger.trim()) next.trigger = patch.trigger.trim().slice(0, 4);
    if (Number.isFinite(patch.maxMs)) next.maxMs = patch.maxMs;
    if (typeof patch.debug === 'boolean') next.debug = patch.debug;
  }
  rt.exo = saveConfig(next);
  rt.exoDebug = rt.exo.debug === true;
  return exoStatus(rt);
}


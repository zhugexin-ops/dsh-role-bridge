// 假宿主自检：不启动 DSH，也能验证 apply() 有没有把路由挂上、闸门对不对、影子观察会不会崩
//   node test/fake-host.mjs

import { EventEmitter } from 'node:events';
import { tmpdir } from 'node:os';

// 自测用临时配置，别把插件目录里真正的 exo.json 改掉
process.env.ROLE_BRIDGE_EXO_CONFIG = tmpdir() + '\\role-bridge-exo-test-' + process.pid + '.json';

import { apply, inject, name } from '../lib/index.js';

const routes = [];
const effects = [];
const listeners = new Map();

const ctx = {
  webServer: {
    register(route) {
      routes.push(route);
      return () => {};
    },
  },
  effect(fn, label) {
    effects.push({ label, dispose: fn() });
  },
  on(event, fn) {
    if (!listeners.has(event)) listeners.set(event, []);
    listeners.get(event).push(fn);
    return () => {};
  },
};

console.log('name =', name, '| inject =', JSON.stringify(inject));
apply(ctx, {});
console.log('路由注册数 =', routes.length, '|', routes.map((r) => `${r.kind} ${r.path}`).join(', '));
console.log('effect 数 =', effects.length, effects.map((e) => e.label).join(' | '));
console.log('事件监听 =', [...listeners.keys()].join(', '));

const route = routes[0];

function call(pathname, { body = {}, headers = { 'x-role-bridge': '1' }, method = 'POST' } = {}) {
  return new Promise((resolve) => {
    const req = new EventEmitter();
    req.method = method;
    req.url = pathname;
    req.headers = headers;
    const chunks = [];
    const res = {
      statusCode: 0,
      headers: {},
      setHeader(k, v) {
        this.headers[k] = v;
      },
      end(buf) {
        chunks.push(Buffer.from(buf == null ? '' : String(buf), 'utf8'));
        resolve({ status: this.statusCode, headers: this.headers, body: Buffer.concat(chunks).toString('utf8') });
      },
    };
    route.handler(req, res).catch((e) => resolve({ status: -1, body: String(e) }));
    setImmediate(() => {
      req.emit('data', Buffer.from(JSON.stringify(body), 'utf8'));
      req.emit('end');
    });
  });
}

const denied = await call('/role-bridge/api/status', { headers: {} });
console.log('无闸门头 →', denied.status, denied.body);

const status = await call('/role-bridge/api/status');
const statusJson = JSON.parse(status.body);
console.log('status →', status.status, '| roles =', statusJson.roles.join(' / '));
console.log('  paths ok =', Object.entries(statusJson.paths).map(([k, v]) => `${k}:${v.ok}`).join(' '));

const unknown = await call('/role-bridge/api/nope');
console.log('未知动作 →', unknown.status, unknown.body);

// 影子观察：喂一条假的 IM 入站 + 一段流式回话，看会不会崩、有没有记下来
const inbox = listeners.get('agent/inbox/inserted') || [];
const stream = listeners.get('agent/assistant-stream') || [];
for (const fn of inbox) fn({ agent: { id: 'im:qq:group:1:g:TEST' }, message: { content: [{ type: 'text', text: '淼：你在吗' }] } });
for (const fn of stream) {
  fn({ agent: { id: 'im:qq:group:1:g:TEST' }, frame: { type: 'start', attemptId: 'a1', revision: 1, turn: 1, step: 1 } });
  fn({ agent: { id: 'im:qq:group:1:g:TEST' }, frame: { type: 'chunk', attemptId: 'a1', revision: 1, index: 0, time: 1, chunk: { type: 'text-delta', index: 0, text: '在的' } } });
  fn({ agent: { id: 'im:qq:group:1:g:TEST' }, frame: { type: 'end', attemptId: 'a1', revision: 1, index: 0, outcome: { kind: 'committed', eventType: 'assistant/message', seq: 9 } } });
}
// 非 IM 会话应当被忽略
for (const fn of inbox) fn({ agent: { id: 'ses_abc' }, message: { content: [{ type: 'text', text: '这不是 IM' }] } });

const shadow = await call('/role-bridge/api/shadow');
const shadowJson = JSON.parse(shadow.body);
console.log('影子对账 →', shadow.status, JSON.stringify(shadowJson.entries, null, 2));

// 外骨骼：路由 + 触发判定 + 瀑布放行（这里不真跑引擎，只验闸门和 next 的走向）
const exo = JSON.parse((await call('/role-bridge/api/exo')).body);
console.log('exo 状态 →', 'enabled =', exo.enabled, '| trigger =', exo.trigger, '| roles =', (exo.roles || []).length);
const detectHit = JSON.parse((await call('/role-bridge/api/exo-detect', { body: { text: '@大肥鱼 在吗' } })).body);
console.log('exo-detect 「@大肥鱼 在吗」→', JSON.stringify(detectHit.hit));
const detectMiss = JSON.parse((await call('/role-bridge/api/exo-detect', { body: { text: '帮我看个文件' } })).body);
console.log('exo-detect 「帮我看个文件」→', JSON.stringify(detectMiss.hit));

const falls = listeners.get('llm/stream') || [];
const options = {
  provider: 'deepseek',
  model: 'x',
  sessionId: 'ses_test',
  messages: [{ role: 'user', content: [{ type: 'text', text: '@大肥鱼 在吗' }] }],
};
const passthrough = falls[0] ? falls[0](options, () => 'NEXT') : 'NO-LISTENER';
console.log('外骨骼关着时 →', passthrough === 'NEXT' ? '放行给模型（next 被调用）' : passthrough);

const turnedOn = JSON.parse((await call('/role-bridge/api/exo-toggle', { body: { enabled: true } })).body);
console.log('打开外骨骼 →', 'enabled =', turnedOn.enabled);
const taken = falls[0] ? falls[0](options, () => 'NEXT') : 'NO-LISTENER';
console.log('外骨骼开着 + @命中 →', taken !== 'NEXT' && typeof taken[Symbol.asyncIterator] === 'function' ? '接管（交回一个流）' : '没接管（不对）');
const plain = falls[0] ? falls[0]({ ...options, messages: [{ role: 'user', content: [{ type: 'text', text: '普通问题' }] }] }, () => 'NEXT') : 'NO-LISTENER';
console.log('外骨骼开着 + 没 @ →', plain === 'NEXT' ? '放行给模型' : '没放行（不对）');
const back = JSON.parse((await call('/role-bridge/api/exo-toggle', { body: { enabled: false } })).body);
console.log('关回外骨骼（保持默认关）→', 'enabled =', back.enabled);

for (const item of effects) {
  try {
    item.dispose();
  } catch (e) {
    console.log('dispose 出问题：', item.label, String(e));
  }
}
console.log('全部跑完，没有抛异常。');

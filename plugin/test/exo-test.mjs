// 外骨骼的离线自测：不碰真引擎、不碰网络、不真跑模型。
//
//   node test/exo-test.mjs
//
// 验三件事：
//   1. 触发词识别（@角色名 / 不触发 / 角色不存在 / 只有 @角色名 没正文）
//   2. 该不该接管（开关、purpose、sessionId 三道闸）
//   3. 交出去的流长什么样（宿主认识的四种 chunk，最后是 finish/stop）

import { detectTrigger, shouldTakeOver, lastUserText, exoStream, loadConfig, isInjectedText, pickUserIndex } from '../lib/exo.js';
import { createRuntime } from '../lib/engine.js';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const failures = [];

function check(name, ok, detail) {
  if (!ok) failures.push(name + (detail ? ' → ' + detail : ''));
  console.log((ok ? '✓ ' : '✗ ') + name + (detail && !ok ? ' → ' + detail : ''));
}

const roles = ['大肥鱼', '爱莉希雅'];
const on = { enabled: true, speaker: 'X', trigger: '@', maxMs: 90000 };
const off = { ...on, enabled: false };

// 1. 触发词
check('@大肥鱼 在吗 → 命中', JSON.stringify(detectTrigger('@大肥鱼 在吗', on, roles)) === JSON.stringify({ role: '大肥鱼', text: '在吗' }));
check('@大肥鱼：在吗 → 命中并去掉冒号', JSON.stringify(detectTrigger('@大肥鱼：在吗', on, roles)) === JSON.stringify({ role: '大肥鱼', text: '在吗' }));
check('@大肥鱼（只有名字）→ 默认「在吗」', JSON.stringify(detectTrigger('@大肥鱼', on, roles)) === JSON.stringify({ role: '大肥鱼', text: '在吗' }));
check('普通对话不触发', detectTrigger('你好，帮我看一下这个文件', on, roles) === null);
check('@不存在的角色 不触发', detectTrigger('@苏格拉底 在吗', on, roles) === null);
check('@ 后面空着不触发', detectTrigger('@ 在吗', on, roles) === null);

// 2. 该不该接管
const mkOptions = (text, extra) => ({
  provider: 'deepseek',
  model: 'x',
  sessionId: 'ses_test',
  messages: [{ role: 'user', content: [{ type: 'text', text }] }],
  ...(extra || {}),
});
check('开关关着 → 不接管', shouldTakeOver(mkOptions('@大肥鱼 在吗'), off, roles) === null);
check('开关开着 + 命中 → 接管', (shouldTakeOver(mkOptions('@大肥鱼 在吗'), on, roles) || {}).role === '大肥鱼');
check('压缩请求（purpose）→ 不接管', shouldTakeOver(mkOptions('@大肥鱼 在吗', { purpose: 'compaction' }), on, roles) === null);
check('没有 sessionId → 不接管', shouldTakeOver(mkOptions('@大肥鱼 在吗', { sessionId: undefined }), on, roles) === null);
check('取最后一句话的文本', lastUserText(mkOptions('@大肥鱼 在吗')) === '@大肥鱼 在吗');
check('多轮时只看最后一条 user', lastUserText({
  sessionId: 's',
  messages: [
    { role: 'user', content: [{ type: 'text', text: '旧话' }] },
    { role: 'assistant', content: [{ type: 'text', text: '旧回答' }] },
    { role: 'user', content: [{ type: 'text', text: '@大肥鱼 新话' }] },
  ],
}) === '@大肥鱼 新话');

// 3. 流的样子（引擎换成假脚本，1.5 秒出话，不花钱）
const rt = createRuntime({
  workspace: join(HERE, '..', 'test'),
  engine: join(HERE, 'slow-engine.py'),
  timeoutMs: 20000,
});
rt.exo = { ...on };
rt.exoLog = [];

const chunks = [];
for await (const chunk of exoStream(rt, { role: '大肥鱼', text: '在吗' }, { signal: null })) chunks.push(chunk);

const types = chunks.map((c) => c.type);
check('流以 block-start/text 开头', chunks[0] && chunks[0].type === 'block-start' && chunks[0].blockType === 'text');
const delta = chunks.find((c) => c.type === 'text-delta');
check('有 text-delta 且不是空的', !!(delta && typeof delta.text === 'string' && delta.text.length > 0), delta && delta.text);
const end = chunks.find((c) => c.type === 'block-end');
check('block-end 带完整文本块', !!(end && end.block && end.block.type === 'text' && end.block.text === (delta && delta.text)));
const finish = chunks[chunks.length - 1];
check('最后一块是 finish/stop', finish && finish.type === 'finish' && finish.reason && finish.reason.kind === 'stop');
check('流里不含 usage/tool-call', !types.includes('usage') && !types.includes('tool-call-delta'));
check('记了一条外骨骼日志', rt.exoLog.length === 1 && rt.exoLog[0].role === '大肥鱼');

// 4. 配置文件默认值是「关」
const cfg = loadConfig();
check('配置文件默认读取成功（enabled 为布尔）', typeof cfg.enabled === 'boolean', JSON.stringify(cfg));

// 5. 宿主每轮会往消息流里塞运行时上下文，不能把它当成人说的话
const withInjected = {
  sessionId: 'ses_x',
  messages: [
    { role: 'user', content: '@大肥鱼 在吗' },
    { role: 'user', content: 'Time sampled while preparing turn 1, step 1: 2026-11-02T16:42:00+08:00' },
  ],
};
check('注入的运行时上下文被认出来', isInjectedText('Current runtime context. This snapshot supersedes') === true);
check('跳过注入后取到人说的话', pickUserIndex(withInjected) === 0);
check('注入尾巴不影响触发', JSON.stringify(shouldTakeOver(withInjected, on, roles)) === JSON.stringify({ role: '大肥鱼', text: '在吗' }));

// 6. 一轮已经往下走了（user 之后出现了 assistant），就不再接管
const midTurn = {
  sessionId: 'ses_x',
  messages: [
    { role: 'user', content: '@大肥鱼 在吗' },
    { role: 'assistant', content: '（她的话）' },
    { role: 'tool', content: '{"ok":true}' },
  ],
};
check('user 之后已有 assistant → 不接管', shouldTakeOver(midTurn, on, roles) === null);

// 7. 子会话的 user content 是数组，第二块是父指令，只能取第一块
const subagentLike = {
  sessionId: 'ses_x',
  messages: [
    {
      role: 'user',
      content: [
        { type: 'text', text: '@大肥鱼 在吗' },
        { type: 'text', text: 'Your parent agent id is "session-1". Before you finish, send your result…' },
      ],
    },
  ],
};
check('子会话数组只取第一块', JSON.stringify(shouldTakeOver(subagentLike, on, roles)) === JSON.stringify({ role: '大肥鱼', text: '在吗' }));

if (failures.length) {
  console.log('\n没过的：');
  for (const item of failures) console.log(' - ' + item);
  process.exitCode = 1;
} else {
  console.log('\n全过。外骨骼的触发、闸门、流形状都对。');
}

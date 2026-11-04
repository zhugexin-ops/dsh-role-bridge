#!/usr/bin/env node
// dsh-role-bridge —— 首次配置向导（不联网、不装东西，只探测 + 写一份 json）
//
// 用法：
//   node lib/setup.mjs                 # 探测并说明缺什么
//   node lib/setup.mjs --save          # 探测到就写 ~/.dsh/role-bridge.json
//   node lib/setup.mjs --workspace <路径> --python <路径> --engine <路径> --save
//
// 设计原则：**不猜盘符**。找不到就明确告诉你该填哪一项，而不是抛 ENOENT。

import { existsSync } from 'node:fs';
import path from 'node:path';
import { resolveConfig, saveConfig, CONFIG_FILE, pythonCandidates, engineCandidates } from './config.js';

function arg(name) {
  const i = process.argv.indexOf('--' + name);
  return i > -1 ? process.argv[i + 1] : undefined;
}

function line(s = '') {
  process.stdout.write(s + '\n');
}

const overrides = {
  workspace: arg('workspace'),
  python: arg('python'),
  engine: arg('engine'),
};

const cfg = await resolveConfig(overrides);

line('dsh-role-bridge · 运行环境检查');
line('--------------------------------');
line(`工作区   ${cfg.workspace || '（没找到）'}`);
line(`Python   ${cfg.python || '（没找到）'}`);
line(`引擎     ${cfg.engine || '（没找到）'}`);
line(`配置文件 ${CONFIG_FILE}`);
line('');

if (cfg.missing.length) {
  line('❌ 还缺：' + cfg.missing.join('、'));
  line('');
  line('可以填的候选：');
  if (cfg.missing.includes('python')) {
    for (const p of pythonCandidates()) line('  python  → ' + p + (existsSync(p) ? '  ✅' : ''));
  }
  if (cfg.missing.includes('engine')) {
    for (const p of engineCandidates(cfg.workspace)) line('  engine  → ' + p + (existsSync(p) ? '  ✅' : ''));
  }
  line('');
  line('例子：');
  line('  node lib/setup.mjs --workspace "<角色目录所在的工作区>" --python "<python 可执行文件>" --engine "<role_core.py>" --save');
  line('');
  line(cfg.hint);
  process.exit(2);
}

if (!existsSync(cfg.engine)) {
  line('❌ 引擎文件不存在：' + cfg.engine);
  process.exit(2);
}

line('✅ 条件齐了。' + (process.argv.includes('--save') ? '' : '（加 --save 写进配置文件）'));

if (process.argv.includes('--save')) {
  const saved = await saveConfig({
    workspace: cfg.workspace,
    python: cfg.python,
    engine: cfg.engine,
    timeoutMs: cfg.timeoutMs,
    doctorTimeoutMs: cfg.doctorTimeoutMs,
  });
  line('已写入：' + CONFIG_FILE);
  line('内容：' + JSON.stringify(saved));
  line('');
  line('下一步：在 DSH 里启用「叫她」插件（role-bridge），或设环境变量 ROLE_DAEMON 走常驻服务。');
  line('自检：node lib/selftest.mjs（若存在）或在插件面板里点「自检」。');
}

// 顺便提醒引擎本体在哪：本仓库**不带**引擎（见 README「引擎不在包里」一节）
line('');
line('提醒：本插件只带外壳，不带引擎本体；引擎（role_core.py + role_base.py + 角色目录）要单独准备。');
line('参考：' + path.join(cfg.workspace || '', '机制', '引擎-核心-五样.md'));

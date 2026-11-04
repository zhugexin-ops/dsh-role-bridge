// 命令行自检：不需要启动 DSH，就能验证「外壳 → 引擎」这条链路
//   node test/selftest.mjs status
//   node test/selftest.mjs doctor 大肥鱼
//   node test/selftest.mjs ask 大肥鱼 "在吗" me      （会真的调用引擎，约 ¥0.015）
//   node test/selftest.mjs fail                    （故意用坏路径，验证兜底那句话）
//   node test/selftest.mjs noconf                  （清空配置，验证“没配齐”那句话）

import { DEFAULTS, createRuntimeAsync, askRole, runDoctor, readRoles, readBindings, probePaths } from '../lib/engine.js';

const [, , cmd, a, b, c] = process.argv;

const rtFor = (over = {}) => createRuntimeAsync(over);

if (cmd === 'status') {
  const rt = await rtFor();
  if (rt.cfg.missing && rt.cfg.missing.length) {
    console.log('❌ 还缺：' + rt.cfg.missing.join('、'));
    console.log(rt.cfg.hint);
    process.exit(2);
  }
  const probe = await probePaths(rt.cfg);
  console.log(JSON.stringify(probe, null, 2));
  console.log('roles:', (await readRoles(rt.cfg)).join(' / '));
  console.log('bindings:', JSON.stringify(await readBindings(rt.cfg), null, 2));
} else if (cmd === 'doctor') {
  const rt = await rtFor();
  const r = await runDoctor(rt, a || '');
  console.log(`ok=${r.ok} code=${r.code} ${r.ms}ms`);
  console.log(r.text);
} else if (cmd === 'ask') {
  const rt = await rtFor();
  const r = await askRole(rt, { role: a, text: b || '在吗', speaker: c || '我' });
  console.log(JSON.stringify(r, null, 2));
} else if (cmd === 'fail') {
  const rt = await rtFor({ python: 'Z:\\不存在的\\python.exe' });
  const r = await askRole(rt, { role: '大肥鱼', text: '在吗', speaker: '我' });
  console.log(JSON.stringify(r, null, 2));
  console.log('兜底句是否符合预期：', r.reply === '（她那边暂时没有回话）' ? '是' : '否！');
} else if (cmd === 'noconf') {
  // 不读配置文件、不给环境变量：模拟陌生机器第一次装
  const rt = await createRuntimeAsync({ workspace: '', python: '', engine: '' });
  rt.cfg.python = '';
  rt.cfg.engine = '';
  const r = await askRole(rt, { role: '大肥鱼', text: '在吗', speaker: '我' });
  console.log(JSON.stringify(r, null, 2));
  console.log('是否给出可照做的提示：', r.reply === '（她那边暂时没有回话）' ? '是（兜底句 + 下面那句）' : '否！');
} else {
  console.log('用法：status | doctor <角色> | ask <角色> <话> [说话人] | fail | noconf');
  console.log('默认配置：', JSON.stringify(DEFAULTS, null, 2));
}

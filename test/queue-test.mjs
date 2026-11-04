// 验证「同一个角色串行排队」：用假引擎（慢 1.5s），并发丢两条，
// 正确的结果是总耗时 ≈ 两条之和，且两次的发言顺序与调用顺序一致。
// 全程不碰真引擎、不写她的记忆；工作区指向临时目录，日志也落在那里。
import { mkdtempSync, rmSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRuntime, askRole } from '../lib/engine.js';

const here = dirname(fileURLToPath(import.meta.url));
const tmp = mkdtempSync(join(tmpdir(), 'role-bridge-queue-'));

const rt = createRuntime({
  workspace: tmp,
  engine: join(here, 'slow-engine.py'),
});

const t0 = Date.now();
const [a, b] = await Promise.all([
  askRole(rt, { role: '排队测试', text: '第一条', speaker: '我' }),
  askRole(rt, { role: '排队测试', text: '第二条', speaker: '我' }),
]);
const elapsed = Date.now() - t0;

const logPath = join(tmp, '角色', '排队测试', '轮次.log');
const log = existsSync(logPath) ? readFileSync(logPath, 'utf8') : '';
const firstAt = log.indexOf('第一条');
const secondAt = log.indexOf('第二条');
const serial = firstAt >= 0 && secondAt > firstAt;
const bothOk = a.ok && b.ok && a.reply.includes('第一条') && b.reply.includes('第二条');
// 串行的话总耗时应接近 2 × 1.5s = 3.0s；并发的话会接近 1.5s。
const passed = serial && bothOk && elapsed >= 2600;

console.log(
  JSON.stringify(
    {
      passed,
      elapsedMs: elapsed,
      串行: serial,
      两条都成功: bothOk,
      回话: [a.reply, b.reply],
      日志: log.trim().split('\n').map((l) => l.slice(0, 90)),
    },
    null,
    2,
  ),
);

rmSync(tmp, { recursive: true, force: true });
process.exit(passed ? 0 : 1);

// 账本护栏（外骨骼的「硬」那一半的一半）：
//   她/它的账（`角色\<名>\memory.sqlite` 与同目录的 .tsv）只由引擎写；
//   会话里的 AI 通过插件调工具时，任何一条路想直接改这些文件都会被拦下来。
//
// 为什么只守这一条：账能改，整套东西（只追加、可核查、沉底、权重）就都不成立。
// 别的文件不拦——那是拿笼子关人，作者明确否过（m06963）。
//
// 语义照本机 hard-gate 的约定：同步、不抛错、闸自己坏了就放行。

const WRITE_TOOLS = new Set([
  'write',
  'edit',
  'multi_edit',
  'apply_patch',
  'str_replace',
  'notebook_edit',
  'create_file',
  'delete',
  'delete_file',
  'move',
  'rename',
  'copy',
  'time_machine_file_rollback',
  'time_machine_checkpoint_rollback',
]);

const PATH_KEYS = [
  'file_path',
  'filePath',
  'path',
  'target',
  'targetPath',
  'from',
  'to',
  'source',
  'destination',
  'paths',
  'files',
  'dir',
  'directory',
];

// 「角色\某名\memory.sqlite」或同目录下的账类文件
const LEDGER_RE = /角色[\\/][^\\/"'\s]+[\\/](memory\.sqlite|账[^\\/"'\s]*\.tsv|账\.链[^\\/"'\s]*|原话[^\\/"'\s]*\.tsv|护住\.tsv|分类\.tsv)/i;

// 命令里出现这些才算「要写」；只是 read/grep/cat 不算
const SHELL_WRITE_RE = /(set-content|add-content|out-file|remove-item|move-item|copy-item|new-item|del\s|rm\s|mv\s|cp\s|sqlite3\s|>\s|>>|truncate|:>)/i;

export function ledgerGuardEnabled(cfg) {
  return Boolean(cfg && cfg.exo && cfg.exo.enabled && cfg.exo.protect !== false);
}

function collectStrings(value, out, depth = 0) {
  if (depth > 3 || value == null) return out;
  if (typeof value === 'string') {
    out.push(value);
    return out;
  }
  if (Array.isArray(value)) {
    for (const v of value) collectStrings(v, out, depth + 1);
    return out;
  }
  if (typeof value === 'object') {
    for (const v of Object.values(value)) collectStrings(v, out, depth + 1);
  }
  return out;
}

function formatReason(tool, where) {
  return (
    `账本护栏：${tool} 想直接动她自己的账（${where}）。` +
    '这一笔只能由引擎记（这一步不执行）。真要手改，先把 ~/.dsh/role-bridge.json 里 exo.protect 设成 false。'
  );
}

/** 返回拒绝理由字符串表示要拦；返回 undefined 表示放行。 */
export function checkLedger(exec, cfg) {
  if (!ledgerGuardEnabled(cfg)) return undefined;
  try {
    const tool = String((exec && exec.name) || '');
    const args = (exec && exec.arguments) || {};
    if (!tool) return undefined;

    if (tool === 'pwsh' || tool === 'bash' || tool === 'shell' || tool === 'powershell') {
      const cmd = String(args.command || args.cmd || '');
      if (cmd && LEDGER_RE.test(cmd) && SHELL_WRITE_RE.test(cmd)) {
        return formatReason(tool, '命令行里');
      }
      return undefined;
    }

    if (!WRITE_TOOLS.has(tool)) return undefined;

    const values = [];
    for (const key of PATH_KEYS) {
      if (args[key] !== undefined) collectStrings(args[key], values);
    }
    for (const v of values) {
      if (LEDGER_RE.test(v)) return formatReason(tool, v.slice(-60));
    }
    return undefined;
  } catch {
    // 闸自己出错时放行：宁可漏一次，也不能因为闸坏了把正常活卡死
    return undefined;
  }
}

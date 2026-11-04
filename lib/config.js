// dsh-role-bridge —— 路径与配置解析
//
// 上架前的第一条要求：**不许写死任何一台机器的路径**。
// 解析顺序（后面的覆盖前面的）：
//   1) 内置推断（从 DSH 运行时/工作区里找，不假设盘符）
//   2) ~/.dsh/role-bridge.json（首次配置向导写的那份）
//   3) 环境变量 ROLE_WS / ROLE_PYTHON / ROLE_ENGINE
//   4) DSH 插件配置（cordis config 里的 workspace/python/engine）
//
// 找不到就返回 missing[] + hint，让上层给出“该干什么”，而不是抛 ENOENT。

import { existsSync, promises as fsp, readdirSync, statSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const CONFIG_FILE = path.join(os.homedir(), '.dsh', 'role-bridge.json');

const isWin = process.platform === 'win32';

/** 候选 python：不写死盘符，先找 DSH 自带运行时，再找 PATH 上的 */
export function pythonCandidates() {
  const out = [];
  const dshHome = path.join(os.homedir(), '.dsh');
  const runtimes = path.join(dshHome, 'dsh-runtimes');
  if (existsSync(runtimes)) {
    for (const dir of safeList(runtimes)) {
      for (const rel of [
        ['dependencies', 'python', isWin ? 'python.exe' : 'python3'],
        ['python', isWin ? 'python.exe' : 'python3'],
        [isWin ? 'python.exe' : 'python3'],
      ]) {
        const p = path.join(runtimes, dir, ...rel);
        if (existsSync(p)) out.push(p);
      }
    }
  }
  // 本机若装了独立 Python，交给 PATH（spawn 时会自己找）
  out.push(isWin ? 'python.exe' : 'python3', isWin ? 'python' : 'python');
  return out;
}

/** 候选引擎：工作区下的 tools/role_core.py、~/.dsh/role-engine/role_core.py */
export function engineCandidates(workspace) {
  const out = [];
  if (workspace) out.push(path.join(workspace, 'tools', 'role_core.py'));
  out.push(path.join(os.homedir(), '.dsh', 'role-engine', 'role_core.py'));
  return out;
}

function safeList(dir) {
  try {
    return readdirSync(dir).filter((n) => {
      try {
        return statSync(path.join(dir, n)).isDirectory();
      } catch {
        return false;
      }
    });
  } catch {
    return [];
  }
}

async function readConfigFile() {
  try {
    const raw = await fsp.readFile(CONFIG_FILE, 'utf8');
    const obj = JSON.parse(raw);
    return obj && typeof obj === 'object' ? obj : {};
  } catch {
    return {};
  }
}

/** 首次配置向导调它写盘 */
export async function saveConfig(obj) {
  await fsp.mkdir(path.dirname(CONFIG_FILE), { recursive: true });
  const cur = await readConfigFile();
  const next = { ...cur, ...obj };
  await fsp.writeFile(CONFIG_FILE, JSON.stringify(next, null, 2) + '\n', 'utf8');
  return next;
}

export function hasFile(p) {
  try {
    return Boolean(p) && existsSync(p);
  } catch {
    return false;
  }
}

/**
 * @param {object} overrides DSH 插件配置
 * @returns {{workspace?:string, python?:string, engine?:string, timeoutMs:number, doctorTimeoutMs:number, missing:string[], hint:string}}
 */
export async function resolveConfig(overrides = {}) {
  const file = await readConfigFile();
  const env = {
    workspace: process.env.ROLE_WS,
    python: process.env.ROLE_PYTHON,
    engine: process.env.ROLE_ENGINE,
  };

  // workspace：配置 > 文件 > 环境 > 推断（DSH 工作区常在 ~/Documents/deepseek-harness/default-workspace）
  const wsGuess = [
    overrides.workspace,
    file.workspace,
    env.workspace,
    path.join(os.homedir(), 'Documents', 'deepseek-harness', 'default-workspace'),
  ].find((x) => x && existsSync(x));

  const cfg = {
    workspace: wsGuess,
    python: [overrides.python, file.python, env.python].find((x) => x) || firstExisting(pythonCandidates()),
    engine: [overrides.engine, file.engine, env.engine].find((x) => x) || firstExisting(engineCandidates(wsGuess)),
    timeoutMs: Number(
      overrides.timeoutMs || file.timeoutMs || process.env.ROLE_TIMEOUT_MS || 120000
    ),
    doctorTimeoutMs: Number(
      overrides.doctorTimeoutMs ||
        file.doctorTimeoutMs ||
        process.env.ROLE_DOCTOR_TIMEOUT_MS ||
        180000
    ),
  };

  const missing = [];
  if (!cfg.workspace) missing.push('workspace');
  if (!cfg.python) missing.push('python');
  if (!cfg.engine) missing.push('engine');

  let hint = '';
  if (missing.length) {
    hint =
      '还没找齐运行条件（' +
      missing.join('、') +
      '）。跑一次 `node lib/setup.mjs` 写配置，或在 DSH 插件设置里填 workspace / python / engine。配置会存到 ' +
      CONFIG_FILE;
  } else if (!hasFile(cfg.engine)) {
    hint = `引擎文件不存在：${cfg.engine}`;
  } else if (cfg.engine && !hasFile(path.join(path.dirname(cfg.engine), '..', 'role_base.py')) && !hasFile(path.join(path.dirname(cfg.engine), 'role_base.py'))) {
    // 只是提示，不阻断：有的部署把 role_base.py 放在一起
    hint = '';
  }

  return { ...cfg, missing, hint };
}

function firstExisting(list) {
  for (const p of list) {
    if (hasFile(p)) return p;
  }
  return '';
}

export const configPaths = { CONFIG_FILE };

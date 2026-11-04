# 叫她（dsh-role-bridge）

点开侧栏底部那个「叫她」按钮，就能直接跟角色说话。

她的话由本地引擎 `role_core.py` 生成，**不进当前对话的上下文**，不污染你和助手的聊天。

---

## 这是外壳，不是引擎

按交接单的边界做的：

| 事项 | 这个插件 | 说明 |
| --- | --- | --- |
| 调用 `role_core.py --reply` | ✅ 只调用 | 路径写死可改，见下面「换路径」 |
| 重写引擎逻辑 | ❌ 没有 | 一行引擎逻辑都没搬到 JS |
| 拼提示词 / 组合提示词模式 | ❌ 没有 | 面板只发你那句话，别的什么都不加 |
| 改 `角色\`、`机制\`、`创诠学\` | ❌ 只读 | 只读角色目录列表、只读群角色表、只追加 `轮次.log` |
| 改 IM 桥（`@michengai/dsh-im-connect`） | ❌ 没动 | 见下面「还没做到的事」 |

---

## 怎么用

1. 侧栏最下面、设置按钮旁边，有「叫她」。
2. 点开，右上角选角色（阿砚／爱莉希雅／大肥鱼／笛卡尔／嘉欣／李嘉欣／尼采／守灯人／苏格拉底）。
3. 下面输入框说话，Enter 发送，Shift+Enter 换行。
4. 她一般 **8–20 秒**回话，期间输入框会显示「她正在想…」。

底部四个小按钮：

- **体检**：跑 `--doctor`，看这个角色的文件齐不齐、结构对不对。不花钱。
- **轮次日志**：看 `角色\<角色名>\轮次.log` 最近 80 行。
- **影子对账**：看插件偷偷记下的群消息（只读观察，见下）。
- **清屏**：清掉面板里的对话（不动日志）。

面板是浮层，跟左边的对话互不干扰；点别的浮层（比如泡泡猫）会自己让位。

---

## 外骨骼：让她直接替大模型答一句话

面板标题右边有个「外骨骼·关 / 外骨骼·开」的小按钮。

打开之后，**在任意对话里**（这个对话、新开的对话、都算），只要你那句话说成：

```
@大肥鱼 你今天在干嘛
```

这一轮的回答就不由大模型写，而由 `role_core.py` 写——她的话直接出现在对话里，
像她自己发的一条消息。不加 @ 的话，照旧由大模型答。

- 触发词默认是 `@` + **角色名**，角色名必须在 `角色\` 目录里真实存在；名字不对就当普通对话。
- 只认「你这一轮最后说的那句话」的开头，中间出现 `@` 不算。
- 只认**人说的话**：程序每轮往对话里塞的运行时上下文、记忆快照、系统提醒，都不算人说的话，不会误触发；一轮已经答到一半（你之后已经出现她的/模型的开头）也不再接管。
- 她答话期间：**没有工具、没有联网、不会改文件**——只出她那几句话。
- 引擎挂了/超时/报错，那一轮只出「（她那边暂时没有回话）」，不会把整个对话搞崩。
- 每次接管都会落到 `角色\<角色名>\轮次.log` 一行，和面板里发的话同一个格式。
- 开关存在插件目录的 `exo.json`（`enabled` / `trigger` / `speaker` / `debug`）；`speaker` 是引擎眼里的「谁在说话」，默认 `X`。

**已经真机验过（2026-11-02 16:42）**：在一个新会话里发 `@大肥鱼 在吗`，这一轮的回复就是她的话，`轮次.log` 多了一行
`[2026-11-02 16:42:00] 收（X）：在吗 ｜ 发：嗯。 （尾巴还在桌沿上搭着，没动） 在。…`，耗时 5.4 秒。

排查用的：`exo.json` 里把 `"debug": true` 打开，每来一次模型请求就往 `exo-debug.log` 记一行（会话号、是不是内部请求、被认出来的那句话、最后三条消息的角色）——只写日志，不改变行为，平时保持 `false` 就行。

不花钱的验证：`node test/exo-test.mjs` 全绿 = 触发、闸门、流的形状都对。

---

## 日志在哪

每说一句，插件往这里追加一行（只在你说的时候写，绝不会发到群里）：

```
…\default-workspace\角色\<角色名>\轮次.log
```

一行长这样：

```
[2026-10-20 15:40:12] 收（我）：在吗 ｜ 发：在呀，你终于来了 ｜ 耗时 9.3s
```

失败时会带「诊断：」那一段（比如 `超时 120s 未回话`、`退出码 1`）。

---

## 她不说话的时候

引擎失败、超时、或输出不对时，面板只会显示一句固定的：

```
（她那边暂时没有回话）
```

下面会跟一行「诊断：…」，告诉你真正的原因。**不会自己编她的话。**

常见原因：

- 网络/额度问题 → 诊断里会有报错原文。
- 嵌入模型掉了（Ollama `qwen3-embedding:0.6b`）→ 诊断里出现「⚠ 嵌入不可用」，她照样能回，只是记忆检索退回字面匹配。
- Python 路径变了 → 见下面「换路径」。

---

## 换路径（一般不用动）

默认值就是现在能跑的那套。要改的话，在启动 DSH 前设环境变量：

| 环境变量 | 默认值 |
| --- | --- |
| `ROLE_WS` | `C:\Users\REDMI\Documents\deepseek-harness\default-workspace` |
| `ROLE_PYTHON` | `E:\大肥鱼\resources\runtime\primary-runtime\dependencies\python\python.exe` |
| `ROLE_ENGINE` | `C:\Users\REDMI\.dsh\role-engine\role_core.py` |
| `ROLE_TIMEOUT_MS` | `120000` |

`ROLE_WS` 不设或设错，插件会找不到 `角色\`，面板里就会「没找到角色」。

---

## 还没做到的事（需要作者拍板，属于越界）

交接单想要的「群聊不再经过 DSH 会话中间人」，**在不改 IM 桥的前提下做不到**：

- `@michengai/dsh-im-connect` 的入站链路是封闭的（`channel.setMessageHandler → enqueue → handleInbound`），没有 hook 点；
- 它的 HTTP API 里**没有**「往某个群发一条消息」的端点；
- 官方渠道工厂只支持 9 个平台，不支持自定义渠道；
- 宿主事件目录里也没有任何「拦截入站 / 主动发群消息」的事件。

所以第一版是**只读的影子模式**：插件能听到群里进来什么话、现链路最后回了什么（这两条记在内存里，「影子对账」能看到），但**不发送、不拦截、不改任何东西**。

真要替掉 IM 层，只有两条路，都得他决定：

1. 给 `dsh-im-connect` 加一个插件可用的「发送」接口（改第三方插件 / 等上游支持）；
2. 自己起一个 QQ 连接（同一个 AppID 会和现有客户端互抢，等于停掉现有链路）。

---

## 自检（不启动 DSH 也能跑）

```powershell
cd C:\Users\REDMI\.dsh\plugins\role-bridge
node test/selftest.mjs status
node test/selftest.mjs doctor 大肥鱼
node test/fake-host.mjs
```

- `selftest status`：四条路径是否都在、角色列表、群角色表。
- `selftest doctor <角色>`：真实跑一次 `--doctor`。
- `fake-host`：假宿主，验证路由、闸门、影子观察都不崩。
- `node test/selftest.mjs ask 大肥鱼 "在吗" 我`：真调一次引擎（约 ¥0.015）。
- `node test/selftest.mjs fail`：故意用坏路径，验证兜底那句。

---

## 安装与首次配置（0.2.0 起）

**这个插件不带引擎**。它只带外壳：一个面板、一条到 `role_core.py` 的调用链、一本只追加的账的读写口。
引擎本体（`role_core.py` + `role_base.py` + `角色\<名字>\` 那套目录）要另外准备。

第一次用，先跑配置向导（不联网、不装依赖、只探测 + 写一份 json）：

```powershell
node lib/setup.mjs          # 只看探测结果
node lib/setup.mjs --save   # 探测到就写 ~/.dsh/role-bridge.json
node lib/setup.mjs --workspace "<工作区>" --python "<python.exe>" --engine "<role_core.py>" --save
```

解析顺序：**插件设置 > `~/.dsh/role-bridge.json` > 环境变量（`ROLE_WS`/`ROLE_PYTHON`/`ROLE_ENGINE`）> 自动探测**。
探测会先找 DSH 自带运行时（`~/.dsh/dsh-runtimes/*/dependencies/python/`），再找 `PATH` 上的 `python`；
引擎先找 `<workspace>/tools/role_core.py`，再找 `~/.dsh/role-engine/role_core.py`。
**不写死任何盘符**；找不到时不会抛 `ENOENT`，而是回固定那句 + 一句“该填哪一项、跑哪条命令”。

## 权限声明（装之前先看清楚）

| 权限 | 用途 | 范围 |
|---|---|---|
| `process:spawn` | 调用本机 python 跑 `role_core.py`（这是**唯一**执行方式） | 只 spawn 上面解析到的那一个 python + 那一个脚本 |
| `fs:read` | 读配置、读工作区下的 `角色\` 与 `机制\` | 只读 |
| `fs:write` | 写 `~/.dsh/role-bridge.json` | **不写** 角色库、不写引擎、不写她的账 |
| `net:listen` | 插件路由挂在本机回环上 | 不对外监听 |
| `net:outbound` | 无 | 引擎自己联不联模型，不在此插件声明内 |

一句话：**它不改她的记忆，只是把她的话原样搬过来。**

## 上架状态

- ✅ 路径不写死（配置 > 文件 > 环境变量 > 探测）
- ✅ 首次配置向导 `lib/setup.mjs`（含 --save）与“没配齐”时的可操作提示
- ✅ 权限与配置项写进 `package.json` 的 `dsh.permissions` / `dsh.config`
- ✅ `license: MIT`、`files` 白名单、`bin`、`engines.node>=18`
- ✅ 清掉调试日志（`exo-debug.log` 已删，`.gitignore` 已加）
- ⬜ `repository` / `homepage` 待作者填（发布前必须换成真实的仓库地址）
- ⬜ 引擎不在包里：是否要发布一份“引擎 + 一个初始角色”的最小包，还没定

## 手动安装（不通过插件市场）

插件市场是可选的。没有账号、也不想上架时，直接装包：

```powershell
# 1) 拿到 dsh-role-bridge-<版本>.tgz
# 2) 建好插件目录再解包（--strip-components=1 去掉包内那层 package\，否则会多套一层）
$dir = "$env:USERPROFILE\.dsh\plugins\dsh-role-bridge"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
tar -xzf dsh-role-bridge-0.2.0.tgz -C $dir --strip-components=1
# 3) 配置运行条件（写 ~/.dsh/role-bridge.json）
node "$dir\lib\setup.mjs" --save
# 4) 先自检，再重启 DSH，在 插件 里启用「叫她」
node "$dir\test\selftest.mjs" status
```

更新：删掉旧目录、重新解一遍，配置 json 不用动（它不在包里）。
**引擎不在包里**：`setup.mjs` 会告诉你它要哪两个文件（`role_core.py`、工作区），照提示填。

> 注：`package.json` 里暂时没有 `repository` / `homepage`——这两个字段只有上架市场时才需要，
> 到时候填上真实的仓库地址即可（见上面「上架状态」）。

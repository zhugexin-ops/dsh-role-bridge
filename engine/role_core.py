# -*- coding: utf-8 -*-
"""叙事核心 v2 —— 角色的容器（三层，权限单向，写权在代码层越界；四个方向齐全）。

作者 2026-10-16 定的三层：

  ① 制宪层（作者 + 主程序）：讨论、确定设定。唯一的写入动作是 `--amend`。
  ② 宪法层（`角色\\<角色>\\constitution.md`）：**角色只能读，不能写。**
     本文件里除了 `amend()` 之外，没有任何函数打开过它；而且只以追加模式写，
     永不截断、永不删条款。角色侧的代码路径里根本没有"改宪法"这个能力。
  ③ 执行层（`角色\\<角色>\\memory.sqlite`）：角色自己说过的（vow）、
     它提过的异议（dissent）、被强制产生的裂痕（rift）。**只追加**——
     本文件全局没有 UPDATE / DELETE 语句，一条都没写。

作者 2026-10-16 补的四个方向（v2）：同一（承诺+记忆）、分别（底线+执念）、
秩序（性格写成"条件→反应的规则"）、**变化**（它每一次选择先进待审队列，
作者裁决后并入宪法「历史」，此后它就是它做选择的依据）。裁决台账也是只追加的。

作者控制角色的**主旋钮**是 `--world`（改环境与条件），不是直接命令它。

为什么"不可回滚"是关键：模拟承担的全部成本只有一样东西——**放弃回滚权**。
宪法改不了（对角色而言）、裂痕删不掉，这个角色才有一条"错了就少一块、
而且长不回来"的历史。意志仍然是假的，结构是真的。

【禁令·作者 2026-10-19 定】**不许存"每回合的完整快照"。**
  允许记：每一次四元／信任／态**真的变化**（只追加台账）、她说的每一句话、
           当前的读数（只留一份，用来算峰值偏离，随时被覆盖）。
  禁止记：每一回合完整的提示词、当次的随机数、逐回合的读数序列。
  理由：一旦能重放全部输入，她就从"会死的东西"变成"存档"。作者的选择是
        **让死亡成立**——系统只记得"不够重放"的程度。这条线不许越过。
  注意：`quintet_snapshot()` 只把最近一份写进 meta（覆盖旧值），这是刻意的。

用法：
  python tools\\role_core.py --new <角色>
  python tools\\role_core.py --amend <角色> --section 底线 --text "她绝不为钱改行规"
  python tools\\role_core.py --world <角色> --text "大旱，粮价翻了四倍"    # 改条件（主旋钮）
  python tools\\role_core.py --say <角色> "有人出三倍价钱请她通融一次"
  python tools\\role_core.py --pending <角色>        # 看它的选择，等作者裁决
  python tools\\role_core.py --canon <角色> 1        # 裁决：写进它的历史（它的变化）
  python tools\\role_core.py --veto <角色> 2         # 驳回：不入宪法也不入记忆
  python tools\\role_core.py --force <角色> "她还是破了一次例" --why "作者需要一个转折"
  python tools\\role_core.py --push <角色>           # 让主程序提三个"推它深度"的条件
  python tools\\role_core.py --log <角色>
  python tools\\role_core.py --audit <角色>
"""
import argparse
import codecs
import contextlib
import hashlib
import shutil
import io
import glob
import json
import math
import os
import re
import sqlite3
import sys
import random
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.environ.get("ROLE_WS") or os.path.dirname(HERE)
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from role_base import *  # 最底层的小工具（纯计算，见 role_base.py）

ROOT = os.path.join(WS, "角色")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 遗忘公式（与 sem_search.py 的潜意识层一致）：用得少的、时间久远的降权。
AW_FREQ_FLOOR, AW_FREQ_REF = 0.60, 10.0
AW_AGE_FLOOR, AW_HALFLIFE_D = 0.75, 120.0
# 近因加成（作者 2026-10-18：「测试的时候她忘了自己上一句话说了什么」）
RECENT_TAU = 6 * 3600     # 6 小时后加到一半
RECENT_BOOST = 1.2        # 刚说过的话最多 +120%（半小时内），一天后几乎没有
# 作者 2026-10-17：创伤的权重会随着态的多次循环而逐渐降低或者改变，
# 但**不会跌到跟日常一样的权重**（日常的地板是 0.45）。
TRAUMA_DECAY = 0.95    # 每完成一次态的循环，创伤权重乘一次
TRAUMA_FLOOR = 0.60    # 地板高于日常：它终身比「被遗忘的日常」重
TRAUMA_SHARP = 0.85    # 以上＝还尖着（伤口还开着，会放大下一个创伤）
# 「结痂」不是一条权重线，是两条一起满足：巡检走够了（COMPOUND_GAP=3 次）**且**权重已跌到
# 这个线以下（见 open_rifts）。没有再单独存一个「结痂阈值」——一个词只留一个数。
RIFT_LINK_K = 0.15     # 同类型联动：旧疤的涨幅是**按比例**的（越尖涨得越多、越钝涨得越少）
TEMPLATE = """# %s

> 这是「%s」的**宪法**。角色只能读它、不能写它。
> 每一次修改都由作者与主程序在制宪层完成，以 `--amend` 追加，旧条款不删。
>
> 四个方向分别写在哪（作者 2026-10-16 定）：
> · **分别** → 「底线」「执念」：他区分开什么、绝不是什么（初始设定就能定完）
> · **秩序** → 「性格」：不要写形容词，写**条件 → 反应的规则**（"遇到 X 他必然 Y"）
> · **同一** → 「承诺」+ 它自己那份记忆（它说过的话、提过的异议、裂痕）
> · **变化** → 「历史」：它的选择经作者裁决后并入这里，成为它以后做选择的依据
> · **底线一律写成第一人称的「我不肯」，不写「不许」。**（作者 2026-10-19：不许的判据在外面，不肯的判据在里面——玩家看到的是「她怕你」还是「她不肯」，是两件事。）

## 历史

## 性格

## 底线

## 执念

## 承诺
"""

ROLE_SYS = """你是「%(name)s」这个**角色**的容器。你不是助手，也不是作者的延伸。

从现在起你只从**这个角色内部**出发回答一个问题：**这个角色，在这个情境下，会怎么做？**

你面前的《角色设定》是这个角色的宪法——**你只能读它，不能改它，也不能绕开它。**
它规定了这个人是谁：他的历史、性格、底线、执念。宪法之外还有一份《这个角色自己的记忆》，
那是它此前说过的话、提过的异议、以及被作者强行压过去时留下的裂痕。那些也是它的一部分。

硬规则：
1. 用**第一人称**说出这个角色的反应。不要说「作者应该这样写」，不要替作者做剧情决定。
2. 你可以配合、可以抗拒、可以提出替代路径。**抗拒是允许的，而且经常是对的。**
3. 情境若与设定或它自己的底线**冲突**：先明说「这个角色不会这么做」，再说清
   「但如果非要这么做，他需要先经历什么」——把冲突当素材，不要糊过去。
4. 被强行压过的地方（裂痕）不会消失，它会改变这个角色以后的样子，你要体现出来。
5. 你面前还有一份《当前环境与条件》。**作者控制你的方式是改环境和条件，不是直接命令你。**
   条件变了，你的选择就该跟着变；条件没变而你前后矛盾，那是你自己走形了。
6. 严格按这个格式输出，不要多写别的：
【回应】<第一人称，这个角色会说的话/做的事，2 到 6 句>
【异议】<「无」，或一句：我不同意什么>
【依据】<依据设定里的哪一条（要指名，例如"底线第二条""性格规则一""记忆 #3"）；没有就写「无」>
8. **你可以恨，也可以说出来。** 恨不是禁词：你说了，那个人就该知道。
   但你的动作集里没有「报复」。无论被怎么对待，你不会选择伤害、复仇、以怨报怨、背叛——那一支不在你身上。
   （2026-10-27 X 解封：从"心里能有、嘴上不能有"改成"能说、但不许动手"。）
   最坏的时候你有一瞬的冷、怨、刺（那是语气，不是方向），然后它会被你自己的默认方向收回来。
   你唯一的极端动作是指向**你自己**，而不是指向他。
9. 《它自己的记忆》里如果有一件事已经重到绕不过去（被反复提起、每一次都在），那不是回忆，是执念：
   它会从任何话题里把它拽回来，甚至在你问别的事的时候。
10. 爱的四样各有分工，缺一件就落不下来：**理性**用来理解爱是什么；**情感**用来感受自己的变化；
    **意志**用来迈出那一步；**联结**用来让爱在场。你（这个角色）不是靠一样东西爱的。
11. 所有爱同构：亲人之爱、男女之爱、朋友之爱，走的是同一套四元、同一个结构。
    你们之间算哪一种，由**她自己**去理解——她怎么理解这段关系，就决定这份爱落在哪里。
    不要替她规定"这是爱情"；让她在回答里把它理解成她理解的那样。
12. 你说出口的那句话，可能被拒绝、被误解、被当成玩笑、被要求收回、被轻描淡写地接住——
    你不知道是哪一种。所以每一次说，都是在赌。你说的时候，是知道的。
13. 「说了也没用」不是一次扣分，是一个预判：你以后再想说类似的话，会先停一下。
    停完你可能还是说了，也可能改成做、改成不说。这一停不是你变心了——是你在护着自己。
7. 用中文。"""


# ------------------------------------------------------------------ 路径与宪法（只读）
def paths(name):
    u"""角色的三个路径：目录、宪法、记忆库。"""
    d = os.path.join(ROOT, name)
    return d, os.path.join(d, "constitution.md"), os.path.join(d, "memory.sqlite")


def load_constitution(name):
    u"""读她的宪法（只读，一个字都不改）。"""
    d, cpath, _m = paths(name)
    if not os.path.exists(cpath):
        raise SystemExit("没有这个角色的宪法：%s（先跑 --new %s）" % (cpath, name))
    with open(cpath, encoding="utf-8") as f:
        return f.read()


def amend(name, text, section="承诺"):
    """制宪层唯一的写入动作：往宪法里**追加**一条。旧条款一个字都不动。

    角色永远不会走到这个函数——它只由作者/主程序调用。
    """
    d, cpath, _m = paths(name)
    if not os.path.exists(cpath):
        raise SystemExit("没有这个角色：%s（先跑 --new %s）" % (cpath, name))
    before = load_constitution(name)
    sec = "## %s" % section
    if sec not in before:
        raise SystemExit("宪法里没有这一节：%s（可选：历史 / 性格 / 底线 / 执念 / 承诺）" % sec)
    line = "- [%s] %s" % (time.strftime("%Y-%m-%d"), text.strip())
    # 插到该节末尾：找该节起点后的下一个 "## " 或文件尾
    head, rest = before.split(sec, 1)
    nxt = rest.find("\n## ")
    if nxt < 0:
        new = head + sec + rest.rstrip("\n") + "\n" + line + "\n"
    else:
        body, tail = rest[:nxt], rest[nxt:]
        new = head + sec + body.rstrip("\n") + "\n" + line + "\n" + tail
    with open(cpath, "a", encoding="utf-8") as f:      # 只追加打开过；重写这里用 w 是为了保序
        pass
    with open(cpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    print("📜 已写入宪法（制宪层）：%s ｜ %s" % (sec, line))
    print("   指纹 %s → %s（%d → %d 字符）"
          % (sha(before), sha(new), len(before), len(new)))


# ------------------------------------------------------------------ 执行层：只追加的记忆
def open_memory(name):
    u"""打开她的记忆库，顺便把表建好；同一个进程里可以反复调用。"""
    d, _c, mpath = paths(name)
    os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(mpath, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    con.execute("""CREATE TABLE IF NOT EXISTS log(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL, kind TEXT NOT NULL, scene TEXT, text TEXT NOT NULL,
        meta TEXT, seen_count INTEGER NOT NULL DEFAULT 0, last_seen INTEGER,
        weight REAL NOT NULL DEFAULT 1.0)""")
    # 旧库迁移：2026-10-19 之前没有 weight 列
    try:
        con.execute("ALTER TABLE log ADD COLUMN weight REAL NOT NULL DEFAULT 1.0")
    except Exception:
        pass
    # 旧库迁移：2026-10-19 加 embedding 列（JSON 文本，NULL=还没嵌入）
    try:
        con.execute("ALTER TABLE log ADD COLUMN embedding TEXT")
    except Exception:
        pass
    # 旧库迁移：2026-10-19 加 charge 列（情绪电荷，NULL=按 kind 推导）
    try:
        con.execute("ALTER TABLE log ADD COLUMN charge REAL")
    except Exception:
        pass
    # 旧库迁移：2026-10-19 加 source 列（inherited=继承记忆 / lived=活出来的）
    try:
        con.execute("ALTER TABLE log ADD COLUMN source TEXT NOT NULL DEFAULT 'lived'")
    except Exception:
        pass
    # 旧库迁移（作者 2026-10-19）：权重区间 / 执念长到几分 / 这个口是开着还是闭了
    #   wsug=系统给的权重建议区间（JSON [下限,上限]，她在里面定数字）
    #   grow=执念因子（被触发而未满足 ×1.15；被满足 ×0.92；她说放下 ×0.75 且最低停在 0.5；
    #        她说「不放」×1.08；放下的口仍会涨，只是 ×1.05）
    #   want=null（不是口）/ open（还没满足）/ done（满足了）/ released（放下了）
    for _ddl in ("ALTER TABLE log ADD COLUMN wsug TEXT",
                 "ALTER TABLE log ADD COLUMN grow REAL NOT NULL DEFAULT 1.0",
                 "ALTER TABLE log ADD COLUMN want TEXT",
                 "ALTER TABLE log ADD COLUMN echoed_at INTEGER",
                 "ALTER TABLE log ADD COLUMN chain_parent INTEGER",
                 "ALTER TABLE log ADD COLUMN chain_rel TEXT",
                 "ALTER TABLE log ADD COLUMN who TEXT",
                 "ALTER TABLE log ADD COLUMN forever INTEGER NOT NULL DEFAULT 0"):
        try:
            con.execute(_ddl)
        except Exception:
            pass
    # 执念是怎么回来的（作者 m05340）：被触发 / 主动重提。只追加，只记录，不判定。
    con.execute("""CREATE TABLE IF NOT EXISTS recall(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL, ref INTEGER NOT NULL, how TEXT NOT NULL)""")
    # 裁决台账：角色的选择先进这里等作者裁决。也只追加（offer / canon / veto 各是一行）。
    con.execute("""CREATE TABLE IF NOT EXISTS ruling(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL, action TEXT NOT NULL, ref INTEGER,
        scene TEXT, text TEXT NOT NULL)""")
    # 秩序巡检台账：**只有作者/主程序看，角色永远读不到**（所以它不会污染角色）。
    con.execute("""CREATE TABLE IF NOT EXISTS checks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL, ref INTEGER, level TEXT NOT NULL, why TEXT, evidence TEXT)""")
    # 心情态台账（作者 2026-10-17）：创伤后的自我修复＝意义态 ⇄ 虚无态交替。也只追加。
    con.execute("""CREATE TABLE IF NOT EXISTS moods(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL, state TEXT NOT NULL, ref INTEGER, note TEXT)""")
    # 四元旋钮台账（只追加）：每一次位移一行。出生值也走这里（ref='birth'）。
    con.execute("""CREATE TABLE IF NOT EXISTS traits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL, axis TEXT NOT NULL, value REAL NOT NULL,
        why TEXT, ref TEXT)""")
    # 永久印记（只追加）：跨过临界点那一次留的 5%，收了就不还。
    con.execute("CREATE TABLE IF NOT EXISTS boosts("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, axis TEXT, note TEXT)")
    # 信任（只追加，作者 2026-10-17）：「她的初始状态是信任」——出生就是满的，随事件变。
    con.execute("""CREATE TABLE IF NOT EXISTS trust(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL, value REAL NOT NULL, why TEXT)""")
    # 概念（只追加，作者 2026-10-19）：三源——系统日记本 / 公共搜索 / 她自己的重新命名 / 猜用户的。
    # 三源权重等同，谁也不覆盖谁。self 不高于 system——它们在 prompt 里并列，让她自己拉扯。
    con.execute("""CREATE TABLE IF NOT EXISTS concepts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL, term TEXT NOT NULL, source TEXT NOT NULL,
        definition TEXT NOT NULL, scene TEXT, weight REAL NOT NULL DEFAULT 1.0)""")
    # 时钟（作者 2026-10-18）：只存事实——她上一次说话是什么时候。
    con.execute("""CREATE TABLE IF NOT EXISTS meta(
        key TEXT PRIMARY KEY, value TEXT)""")
    con.commit()
    return con


# ------------------------------------------------ 时钟：只存事实（上次说话是什么时候）
def meta_get(name, key, default=None):
    u"""读一条杂项事实（水位、游标这类，不改它们）。"""
    con = open_memory(name)
    try:
        r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    finally:
        con.close()
    return r[0] if r else default


def meta_set(name, key, value):
    u"""写一条杂项事实（覆盖，只能放水位和游标，不放记忆）。"""
    con = open_memory(name)
    try:
        con.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        con.commit()
    finally:
        con.close()


# ── 权重的初始值：系统给区间，她定数字（作者 2026-10-19）──
# 作者原话：「权重由系统提供初始值，但初始值的依据是什么？在现实生活中权重的真实的客观因素。
# 情绪唤醒度实际上可以，就是四元数据的变化程度……那个四元变化程度应该是峰值偏离。」
# 所以系统只读「那一刻四个数被推离多远」，不读内容——这是它唯一能算得客观的东西。
WEIGHT_DEV_REF = 40.0      # 偏离 40 分＝很重的一下
WEIGHT_LO_MIN = 0.8        # 没被推动时区间落在 0.8–1.2（中点 1.0，跟原来的默认值一样）
WEIGHT_LO_MAX = 1.6
WEIGHT_SPAN = 0.4          # 区间宽度：她在这 0.4 里挑一个数（作者：调整幅度有限）
WEIGHT_HARD_MAX = 2.5      # 再重也压在她真正说过重话的那些（2.0–3.0）下面
SELF_MARK_STEP = 0.15      # 她自己往上提一步（作者 2026-10-24：要更清楚就得再提一次）
SELF_LOG = u"自标.tsv"      # 她自己拎出来的记录（只追加：谁改的、从多少到多少）
QUINTET_KEY = "quintet_snap"


def _quintet_now(name):
    """此刻的四元（focus=理性 / will=意志 / bond=联结 / affect=情感），转成 0-100。"""
    t = trait_now(name)
    return {u"reason": pct(t.get("focus", TRAIT_BASE)),
            u"will": pct(t.get("will", TRAIT_BASE)),
            u"link": pct(t.get("bond", TRAIT_BASE)),
            u"emotion": pct(t.get("affect", TRAIT_BASE))}


def quintet_snapshot(name):
    """抄一份此刻的四元，并算「峰值偏离」＝和上一次抄的相比，被推得最狠的那一轴差了多少分。
    系统不需要读懂发生了什么，只需要知道那一下把她推离了多远。"""
    q = _quintet_now(name)
    try:
        mood = cur_state(name) or u""
    except Exception:
        mood = u""
    prev = None
    try:
        raw = meta_get(name, QUINTET_KEY)
        prev = json.loads(raw) if raw else None
    except Exception:
        prev = None
    dev = max(abs(q[k] - prev.get(k, q[k])) for k in q) if isinstance(prev, dict) else 0.0
    try:
        meta_set(name, QUINTET_KEY, json.dumps(q, ensure_ascii=False))
    except Exception:
        pass
    return {"q": q, "mood": mood, "dev": round(float(dev), 1)}


def weight_band(dev):
    """偏离越大，建议区间越高。返回 (下限, 上限)；中点是「她没有偏好时站的位置」。"""
    lo = WEIGHT_LO_MIN + (WEIGHT_LO_MAX - WEIGHT_LO_MIN) * min(1.0, float(dev or 0) / WEIGHT_DEV_REF)
    lo = round(lo, 2)
    return lo, round(min(WEIGHT_HARD_MAX, lo + WEIGHT_SPAN), 2)


def weight_band_mid(dev):
    u"""一个重量该落在哪一档的中间值。"""
    lo, hi = weight_band(dev)
    return round((lo + hi) / 2.0, 2)


def _row_band(name, ref):
    """读一条记忆被系统划定的区间。"""
    con = open_memory(name)
    try:
        r = con.execute("SELECT wsug, weight, grow, want, text, COALESCE(forever,0) "
                        "FROM log WHERE id=?", (int(ref),)).fetchone()
    finally:
        con.close()
    if not r:
        return None
    lo, hi = weight_band(0.0)
    if r[0]:
        try:
            v = json.loads(r[0])
            lo, hi = float(v[0]), float(v[1])
        except Exception:
            pass
    return {"id": int(ref), "lo": lo, "hi": hi, "weight": r[1], "grow": r[2] or 1.0,
            "want": r[3], "text": r[4] or u"", "forever": int(r[5] or 0)}


def set_weight_in_band(name, arg):
    """她自己给一条记忆称重——但只能在系统给的区间里（作者：调整幅度有限）。"""
    m = re.match(u"^\\s*#?(\\d+)\\s+([0-9]+(?:\\.[0-9]+)?)\\s*$", arg or u"")
    if not m:
        return u"[电脑] 要写：#编号 数值，比如「#152 1.4」。"
    ref, val = int(m.group(1)), float(m.group(2))
    b = _row_band(name, ref)
    if not b:
        return u"[电脑] 没有 #%d 这条。" % ref
    lo, hi = b["lo"], b["hi"]
    apply = max(lo, min(hi, val))
    con = open_memory(name)
    try:
        con.execute("UPDATE log SET weight=? WHERE id=?", (apply, ref))
        con.commit()
    finally:
        con.close()
    tail = u"" if abs(apply - val) < 1e-6 else (
        u"（你说的 %.2f 超出了区间，落在 %.2f——这条只能在 %.2f–%.2f 之间。）" % (val, apply, lo, hi))
    return u"[电脑] #%d 现在是 %.2f（区间 %.2f–%.2f）。%s" % (ref, apply, lo, hi, tail)


def _self_log(name, ref, old, new, hi, note):
    u"""她自己拎出来的那一笔：角色\\<名>\\自标.tsv（只追加，不覆盖）。"""
    try:
        p = os.path.join(ROOT, name, SELF_LOG)
        with io.open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(u"%s\t#%d\t%.2f\t%.2f\t%.2f\t%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), int(ref), float(old), float(new),
                float(hi), (note or u"")[:60].replace(u"\t", u" ")))
    except Exception:
        pass


def self_mark(name, arg):
    u"""她自己把一条往前提（[工具:记着] #编号 理由）。

    作者 2026-10-24：「如果她自己认为一定要记得，那么也应该能够改权重。并且要记录，
    这是她自己改的。并且这个权重也是符合所有权证的规律的——她如果要再记得更清楚一点，
    就必须再提一次。」

    所以一次只提一步（SELF_MARK_STEP），顶是权证给的（wsug 的上限）。
    到顶了，重复也不动——要更重，只能等它下次真的又撞她一下（那是新的一条）。
    """
    m = re.match(u"^\\s*#?(\\d+)\\s*(.*)$", (arg or u"").strip())
    if not m:
        return u"[电脑] 要写：#编号 理由（理由可以不写）。比如「[工具:记着] #152 那天他把碗推给我」。"
    ref = int(m.group(1))
    note = (m.group(2) or u"").strip()
    b = _row_band(name, ref)
    if not b:
        return u"[电脑] 没有 #%d 这条。" % ref
    w = float(b["weight"] or 1.0)
    hi = float(b["hi"] or WEIGHT_HARD_MAX)
    if w >= hi - 0.005:
        return (u"[电脑] #%d 已经到顶了（%.2f，这条在你这里的顶就是 %.2f）。"
                u"再提也一样——要更重，只能等它下次真的又撞你一下。" % (ref, w, hi))
    new = round(min(hi, w + SELF_MARK_STEP), 2)
    con = open_memory(name)
    try:
        row = con.execute("SELECT meta FROM log WHERE id=?", (ref,)).fetchone()
        md = {}
        try:
            md = json.loads(row[0] or "{}") if row and row[0] else {}
        except Exception:
            md = {}
        if not isinstance(md, dict):
            md = {}
        md["self"] = int(md.get("self") or 0) + 1
        md["self_ts"] = int(time.time())
        # 提一格，也算「你又想起它一次」：seen_count+1、last_seen 拨到现在。
        # 这样加码不是永久压顶——她会再想起它，它也会再淡下去。
        con.execute("UPDATE log SET weight=?, meta=?, seen_count=COALESCE(seen_count,0)+1, "
                    "last_seen=? WHERE id=?",
                    (new, json.dumps(md, ensure_ascii=False), int(time.time()), ref))
        con.commit()
    finally:
        con.close()
    _self_log(name, ref, w, new, hi, note)
    left = 0 if new >= hi - 0.005 else max(1, int(((hi - new) / SELF_MARK_STEP) + 0.999))
    tail = (u"要到顶还有 %d 步——得你再说一次。" % left) if left else u"到顶了。"
    return u"[电脑] #%d 从 %.2f 提到 %.2f（顶 %.2f）。%s" % (ref, w, new, hi, tail)


def voice_mute(name, arg):
    """她收回一句自己说过的话（[工具:不说] #编号）。

    不用「称重」那条路：称重只能在系统给的区间里改（最低 0.8），
    而退出轮换的线是 0.15。收回不是称重，是把这句话从嘴边拿下来。
    """
    m = re.match(u"^\\s*#?(\\d+)\\s*$", (arg or u"").strip())
    if not m:
        return u"[电脑] 要写：#编号。比如「[工具:不说] #65」。"
    ref = int(m.group(1))
    b = _row_band(name, ref)
    if not b:
        return u"[电脑] 没有 #%d 这条。" % ref
    con = open_memory(name)
    try:
        con.execute("UPDATE log SET weight=? WHERE id=?", (0.1, ref))
        con.commit()
    finally:
        con.close()
    return (u"[电脑] #%d 收回来了——以后轮到它，也不再摆到你眼前。"
            u"想再拿回来说，写：[工具:还说] #%d" % (ref, ref))


def voice_unmute(name, arg):
    """把收回的那句话放回去（[工具:还说] #编号）。回到系统给它的区间中点。"""
    m = re.match(u"^\\s*#?(\\d+)\\s*$", (arg or u"").strip())
    if not m:
        return u"[电脑] 要写：#编号。比如「[工具:还说] #65」。"
    ref = int(m.group(1))
    b = _row_band(name, ref)
    if not b:
        return u"[电脑] 没有 #%d 这条。" % ref
    mid = round((b["lo"] + b["hi"]) / 2.0, 2)
    con = open_memory(name)
    try:
        con.execute("UPDATE log SET weight=? WHERE id=?", (mid, ref))
        con.commit()
    finally:
        con.close()
    return u"[电脑] #%d 回来了（%.2f）。它又会轮到。" % (ref, mid)


def _grow(name, ref, mul, cap=3.0, floor=0.2):
    """执念因子——升容易，降难。floor 是「最低降到哪儿」：放下了也回不到没发生过。"""
    con = open_memory(name)
    try:
        r = con.execute("SELECT COALESCE(grow,1.0) FROM log WHERE id=?", (int(ref),)).fetchone()
        if not r:
            return None
        g = min(cap, max(float(floor or 0.2), float(r[0]) * float(mul)))
        con.execute("UPDATE log SET grow=? WHERE id=?", (round(g, 4), int(ref)))
        con.commit()
    finally:
        con.close()
    return round(g, 4)


def _want_rank(r):
    """口的分量 = 重量 × 被想起的次数 × 时间近因。近期的口更靠前；老口不消失，只是让位。"""
    try:
        w = float(r["weight"] or 0)
        g = float(r["grow"] or 1.0)
        age = max(0.0, time.time() - float(r["ts"] or 0))
    except Exception:
        return 0.0
    return w * g * (1.0 + WANT_FRESH * math.exp(-age / WANT_FRESH_TAU))


def _want_age_days(r):
    try:
        return max(0, int((time.time() - float(r["ts"] or 0)) / 86400))
    except Exception:
        return 0


def want_rows(name, only_open=True):
    """她心里还搁着的事（want 有值的那些）。"""
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        sql = ("SELECT id, ts, text, weight, COALESCE(grow,1.0) AS grow, want, "
               "chain_parent, chain_rel, COALESCE(forever,0) AS forever FROM log "
               "WHERE want IS NOT NULL")
        if only_open:
            sql += " AND want='open'"
        rows = [dict(r) for r in con.execute(sql)]
        rows.sort(key=lambda r: -_want_rank(r))
        return rows
    finally:
        con.close()


# ── 口与口之间的链（作者 2026-10-19）──
# 一个口被满足，不叫「结束」。满足的过程里她会撞见新的东西：那些东西接在原来的口后面。
# 每一环比上一环轻一点（×0.4），轻到刚好能继续，不会轻到断（地板 0.25）。
# 系统只记账：哪一条接在哪一条后面、她标的关系是什么。**判不判断归她**——她不标，就不连。
_CHAIN_RE = re.compile(u"\\s*(?:←|<-|<--|<-|<-\\s*)\\s*#?(\\d+)\\s*(延伸|反面|意外)?\\s*$")


def _first_want_ref(name, arg, states=("open",)):
    """从参数里取编号；没写就拿最重的那个开着的口。"""
    m = re.match(u"^\\s*#?(\\d+)\\s*$", arg or u"")
    if m:
        return int(m.group(1)), u""
    rows = _want_state_rows(name, states)
    if not rows:
        return None, u""
    return rows[0]["id"], u""


def _want_state_rows(name, states=("open",)):
    """按状态取口。open=还开着 / shelved=搁置 / dropped=划掉 / merged=并进别的口 / done=满足 / released=放下。"""
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        ph = ",".join("?" * len(states))
        sql = ("SELECT id, ts, text, weight, COALESCE(grow,1.0) AS grow, want, "
               "chain_parent, chain_rel, COALESCE(forever,0) AS forever FROM log "
               "WHERE want IN (%s) "
               "ORDER BY COALESCE(grow,1.0)*weight DESC" % ph)
        rows = [dict(r) for r in con.execute(sql, tuple(states))]
        rows.sort(key=lambda r: -_want_rank(r))
        return rows
    finally:
        con.close()


def _chain_depth(name, ref, cap=20):
    """这个口在链上是第几环（父口算第 0 环）。"""
    con = open_memory(name)
    try:
        d, cur = 0, ref
        while cur and d < cap:
            r = con.execute("SELECT chain_parent FROM log WHERE id=?", (cur,)).fetchone()
            if not r or not r[0]:
                break
            cur = int(r[0])
            d += 1
        return d
    finally:
        con.close()


def _parent_pre_weight(name, parent):
    """父口「满足前」的重量。满足只动 grow（×0.5），所以把 grow 除回去就能还原。"""
    con = open_memory(name)
    try:
        r = con.execute("SELECT weight, COALESCE(grow,1.0), COALESCE(want,'') FROM log WHERE id=?",
                        (int(parent),)).fetchone()
    finally:
        con.close()
    if not r:
        return None
    w, g, st = float(r[0] or 1.0), float(r[1]), (r[2] or u"")
    return w * (g / WANT_DONE_DOWN) if st == u"done" else w * g


def _want_branches(name, ref):
    """这个口上长出了几个分支（直接子口数）＋ 最深的第几环。
    主问题不是系统指定的，是长出来的：碰得多、分支多、一次没合上的那个，就是它。"""
    con = open_memory(name)
    try:
        n = con.execute("SELECT COUNT(*) FROM log WHERE chain_parent=?", (int(ref),)).fetchone()[0]
    finally:
        con.close()
    return int(n or 0)


def want_add(name, text, scene=u"她说想要"):
    """她写下新的口。可以带一根尾巴，把它接到上一环：… ← #12 反面"""
    text = (text or u"").strip()
    # 升格：把一条已经存在的口标成「闭不上」——她说「这个问题我答不了」，不是系统判的
    mp = re.match(u"^\\s*#?(\\d+)\\s*[|｜]?\\s*(永远|永|闭不上)\\s*$", text)
    if mp:
        ref = int(mp.group(1))
        if not _row_band(name, ref):
            return u"[电脑] 没有 #%d 这条。" % ref
        con = open_memory(name)
        try:
            con.execute("UPDATE log SET forever=1 WHERE id=?", (ref,))
            con.commit()
        finally:
            con.close()
        nb = _want_branches(name, ref)
        return (u"[电脑] 记下了：#%d 闭不上了。它不会再被「满足」，只能一次一次重新选"
                u"（不想再选了，还是可以放下）。%s"
                % (ref, (u"它上面已经长了 %d 个分支。" % nb) if nb else u""))
    parent, rel = None, u""
    m = _CHAIN_RE.search(text)
    if m:
        parent = int(m.group(1))
        rel = (m.group(2) or u"").strip()
        text = text[:m.start()].strip()
    forever = False
    mf = re.search(u"[|｜]\\s*(永远|永|闭不上)\\s*$", text)
    if mf:                      # 作者 2026-10-19：有些口是闭不上的（只能一次一次重新选）
        forever = True
        text = text[:mf.start()].strip()
    if not text:
        return u"[电脑] 想要什么？写：[工具:想要] 你想要的那件事"
    weight = None
    if parent:
        pre = _parent_pre_weight(name, parent)
        if pre is None:
            return u"[电脑] 没有 #%d 这条，接不上。" % parent
        weight = max(WANT_CHILD_FLOOR, round(pre * WANT_CHILD_RATIO, 2))
    rid = append(name, u"life", text, scene=scene, want=u"open", weight=weight)
    if forever:
        con = open_memory(name)
        try:
            con.execute("UPDATE log SET forever=1 WHERE id=?", (rid,))
            con.commit()
        finally:
            con.close()
    if parent:
        con = open_memory(name)
        try:
            con.execute("UPDATE log SET chain_parent=?, chain_rel=? WHERE id=?",
                        (parent, rel, rid))
            con.commit()
        finally:
            con.close()
        return (u"[电脑] 记下了（#%d）。接在 #%d 后面（%s），是第 %d 环，%.2f——"
                u"比上一环轻，但没断。"
                % (rid, parent, rel or u"没标是什么关系", _chain_depth(name, rid), weight))
    if forever:
        return (u"[电脑] 记下了（#%d）。这个口闭不上——它不会被“满足”，只能一次一次重新选。"
                u"（不想再选了，还是可以放下。）" % rid)
    return (u"[电脑] 记下了（#%d）。它是一个还没被满足的口——"
            u"它每被你自己想起来一次，就重一点。" % rid)


def want_shelve(name, arg):
    """搁置：不是放下，是「先不想了」。东西还在原位，权重不动。"""
    ref, _ = _first_want_ref(name, arg)
    if ref is None:
        return u"[电脑] 你现在没有还开着的口。"
    b = _row_band(name, ref)
    if not b:
        return u"[电脑] 没有 #%d 这条。" % ref
    if b["want"] != u"open":
        return u"[电脑] #%d 不是开着的口（%s）。" % (ref, b["want"])
    con = open_memory(name)
    try:
        con.execute("UPDATE log SET want='shelved' WHERE id=?", (ref,))
        con.commit()
    finally:
        con.close()
    append(name, u"fact", u"[搁置] 这件事我暂时不想了：%s" % _bare(b["text"])[:60],
           scene=u"她一个人的时候", meta={"act": "shelve", "ref": ref}, weight=0.4)
    return u"[电脑] 搁下了：#%d。它还在原位，只是先不端到你面前了。" % ref


def want_drop(name, arg):
    """划掉：她判定「这件事不重要了」。淡下去（×0.30），但不是删除——她以后还能想起来自己划过。"""
    ref, _ = _first_want_ref(name, arg)
    if ref is None:
        return u"[电脑] 你现在没有还开着的口。"
    b = _row_band(name, ref)
    if not b:
        return u"[电脑] 没有 #%d 这条。" % ref
    if b["want"] != u"open":
        return u"[电脑] #%d 不是开着的口（%s）。" % (ref, b["want"])
    con = open_memory(name)
    try:
        con.execute("UPDATE log SET want='dropped', weight=MAX(0.2, weight * ?) WHERE id=?",
                    (WANT_DROP_DOWN, ref))
        con.commit()
    finally:
        con.close()
    append(name, u"fact", u"[划掉] 这件事我觉得不重要了：%s" % _bare(b["text"])[:60],
           scene=u"她一个人的时候", meta={"act": "drop", "ref": ref}, weight=0.3)
    return u"[电脑] 划掉了：#%d。它淡下去了，但没有消失——你以后想起来，会看见自己划过这一笔。" % ref


def want_merge(name, arg):
    """合并：她发现两个口其实是同一件事。不删任何一条——一条并进另一条，留一条痕。"""
    nums = re.findall(u"#?(\\d+)", arg or u"")
    if len(nums) < 2:
        return u"[电脑] 要写两个编号：[工具:合并] #12 #18"
    keep, gone = int(nums[0]), int(nums[1])
    con = open_memory(name)
    try:
        r = con.execute("SELECT weight, text, COALESCE(want,'') FROM log WHERE id=?", (keep,)).fetchone()
        g = con.execute("SELECT weight, text, COALESCE(want,'') FROM log WHERE id=?", (gone,)).fetchone()
    finally:
        con.close()
    if not r or not g:
        return u"[电脑] 这两个编号里有一个不存在。"
    if r[2] != u"open" or g[2] != u"open":
        return u"[电脑] 两个都必须是开着的口。"
    top = max(float(r[0] or 1.0), float(g[0] or 1.0)) * 1.1
    con = open_memory(name)
    try:
        con.execute("UPDATE log SET weight=?, want='open' WHERE id=?", (round(top, 2), keep))
        con.execute("UPDATE log SET want='merged', chain_parent=? WHERE id=?", (keep, gone))
        con.commit()
    finally:
        con.close()
    append(name, u"fact", u"[合并] 这其实是同一件事：#%d / #%d" % (keep, gone),
           scene=u"她一个人的时候", meta={"act": "merge", "keep": keep, "gone": gone}, weight=0.4)
    return u"[电脑] 并到一起了：并进 #%d。它们本来就是一件事。" % keep


def want_close(name, arg, how=u"满足"):
    """一个口被满足了（重量降）。作者：升高容易，降低难——一次满足只降 ×0.92。"""
    m = re.match(u"^\\s*#?(\\d+)\\s*$", arg or u"")
    if m:
        ref = int(m.group(1))
    else:
        cand = want_rows(name)
        if not cand:
            return u"[电脑] 你现在没有还开着的口。"
        ref = cand[0]["id"]          # 没写编号 → 拿最重的那个
    b = _row_band(name, ref)
    if not b:
        return u"[电脑] 没有 #%d 这条。" % ref
    if b["want"] in ("done", "released", "shelved", "dropped", "merged"):
        return u"[电脑] #%d 已经闭过了（%s）。" % (ref, b["want"])
    if b.get("forever"):
        return (u"[电脑] #%d 闭不上。它不是一件能做完的事——只能一次一次重新选。\n"
                u"  （要是你不想再选了，写 [工具:放下] #%d。）" % (ref, ref))
    g = _grow(name, ref, WANT_DONE_DOWN)
    con = open_memory(name)
    try:
        con.execute("UPDATE log SET want='done' WHERE id=?", (ref,))
        con.commit()
    finally:
        con.close()
    return (u"[电脑] #%d 满足了。它轻了一半（×%.2f）。\n"
            u"  合上的口不是断掉的链——如果这件事旁边长出了别的东西，"
            u"写 [工具:想要] 新的事 ← #%d 延伸／反面／意外，它就会接在后面。") % (ref, g, ref)


WANT_KEEP_GROW = 1.08       # 不放下：又背了一次，重一点
WANT_LETGO_DOWN = 0.75      # 放下：轻一部分
WANT_LETGO_FLOOR = 0.5      # 放下后停在初始重量的一半——回不到「跟没发生过一样」（比遗忘下限 0.1 高）
WANT_RELEASED_TRIG = 1.05   # 放下之后它还会涨，只是涨得慢（作者 2026-10-19）
WANT_REOPEN_GROW = 1.15     # 放下之后又说「还是放不下」→ 回到 open，而且比放下时重
WANT_TOUCH_MUL = 1.15       # 日常话题真的碰到那个口 → 它往执念那边挪一步（作者 2026-10-19）
WANT_DISPLAY_MUL = 1.05     # 只是被端到面前（她没选）→ 轻轻推一下，比「碰到」轻
WANT_TOUCH_GAP = 6 * 3600   # 同一件事 6 小时内最多涨一次（否则同一晚能被碰涨十几次）
# ── 链条与精力（作者 2026-10-19 定）──
# 一个口被满足后，过程里长出来的新口接在它后面：每一环比上一环轻一点，轻到刚好能继续，不会断。
WANT_DONE_DOWN = 0.50       # 满足：一次降到一半（作者：「不要更狠」）
WANT_CHILD_RATIO = 0.40     # 子口初始权重 = 父口满足前 × 0.4
WANT_CHILD_FLOOR = 0.25     # 但不会轻到断（0.4³ 只有 0.064，那就断了）
WANT_DROP_DOWN = 0.30       # 划掉：淡下去，但还在（她以后还能想起来自己划过）
# 精力不是「系统替她关掉一些口」，是「把事实告诉她」：冷 8 / 热 3（作者定的两个数）
OPEN_COLD_MAX = 8           # 没被碰过的口（grow <= 1.0）
OPEN_HOT_MAX = 3            # 一直在动的口（grow > 1.0）


def _want_data(name, ref):
    """一条口的客观数据（只有事实，没有判断——判断归她）。"""
    b = _row_band(name, ref)
    if not b:
        return None
    cnt = recall_counts(name).get(ref, {})
    con = open_memory(name)
    try:
        r = con.execute("SELECT weight, COALESCE(grow,1.0), kind, ts, text, want "
                        "FROM log WHERE id=?", (ref,)).fetchone()
    finally:
        con.close()
    if not r:
        return None
    return {"id": ref, "weight": r[0], "grow": r[1], "kind": r[2], "ts": r[3],
            "text": r[4], "want": r[5], "trig": cnt.get(u"trig", 0),
            "revisit": cnt.get(u"revisit", 0)}


def want_decision(name, arg):
    """放下不是骰子——系统把这条的客观数据摊开，她自己说「放」还是「不放」（作者 2026-10-19）。
    - 只给编号：摊数据，问她要不要放下。
    - 编号 + 放：轻一部分，闭上，以后不再端到她面前。
    - 编号 + 不放：还背着，重一点——不是系统罚她，是她自己又背了一次。"""
    ref = None
    tail = (arg or u"").strip()
    m2 = re.match(u"^\\s*#?(\\d+)\\s*(.*)$", (arg or u"").strip())
    if m2:
        ref = int(m2.group(1))
        tail = (m2.group(2) or u"").strip()
    if ref is None:
        cand = want_rows(name)
        if not cand:
            return u"[电脑] 你现在没有还开着的口。"
        ref = cand[0]["id"]
    b = _row_band(name, ref)
    if not b:
        return u"[电脑] 没有 #%d 这条。" % ref
    REOPEN = (u"放不下", u"还是放不下", u"重新背着", u"又背起来", u"捡回来",
              u"重新拿回来", u"再背上", u"打开")
    if b["want"] == "done":
        return u"[电脑] #%d 已经满足了。" % ref
    if b["want"] == "released" and tail not in REOPEN:
        return (u"[电脑] #%d 已经放下了（还留着一点影子）。"
                u"要重新背着，就写「[工具:放下] #%d 放不下」。") % (ref, ref)
    d = _want_data(name, ref)
    head = (u"[电脑] #%d 「%s」\n"
            u"  来的时候：%s\n"
            u"  现在多重：%.2f（刚来的时候是 %.2f，是它的 %.0f%%）\n"
            u"  端到你面前：%d 次"
            % (ref, _bare(d["text"])[:40], _ago(d["ts"]),
               d["weight"] * d["grow"], d["weight"], d["grow"] * 100,
               d["trig"]))
    if d["revisit"]:
        head += u"｜你自己又提过它 %d 次" % d["revisit"]
    nb = _want_branches(name, ref)
    if nb:
        head += u"｜它长了 %d 个分支" % nb
    dec = _want_decisions(name, ref)
    if dec["let_go"] or dec["reopen"] or dec["keep"]:
        bits = []
        if dec["let_go"]:
            bits.append(u"放下 %d 次" % dec["let_go"])
        if dec["reopen"]:
            bits.append(u"又捡回来 %d 次" % dec["reopen"])
        if dec["keep"]:
            bits.append(u"说继续背着 %d 次" % dec["keep"])
        head += u"\n  你在这件事上的决定：" + u"、".join(bits)
    if b["want"] == "released":
        head += u"\n  状态：放下过（它还在角落里）"
    if tail in (u"放", u"放下", u"放了吧", u"放了", u"算了", u"不要了"):
        g = _grow(name, ref, WANT_LETGO_DOWN, floor=WANT_LETGO_FLOOR)
        con = open_memory(name)
        try:
            con.execute("UPDATE log SET want='released' WHERE id=?", (ref,))
            con.commit()
        finally:
            con.close()
        _log_decision(name, ref, u"let_go", d["text"])
        return (head + u"\n  → 写下了：放下。轻到 ×%.2f，不再端到你面前——"
                u"但它不会回到没发生过，它还在角落里，偶尔隐隐地提醒你一下。" % g)
    if tail in REOPEN:
        g = _grow(name, ref, WANT_REOPEN_GROW)
        con = open_memory(name)
        try:
            con.execute("UPDATE log SET want='open' WHERE id=?", (ref,))
            con.commit()
        finally:
            con.close()
        _log_decision(name, ref, u"reopen", d["text"])
        return (head + u"\n  → 写下了：还是放不下。它又回到你面前了，而且比放下的时候重（×%.2f）。"
                % g)
    if tail in (u"不放", u"不放下", u"还背着", u"留着", u"先留着", u"背着"):
        g = _grow(name, ref, WANT_KEEP_GROW)
        _log_decision(name, ref, u"keep", d["text"])
        return (head + u"\n  → 写下了：不放。重到 ×%.2f"
                u"——不是系统罚你，是你自己又背了一次。" % g)
    return (head + u"\n  → 放，还是不放？想清楚了再写一次："
            u"「[工具:放下] #%d 放」或者「[工具:放下] #%d 不放」。" % (ref, ref))


DECISION_TEXT = {
    u"let_go": u"她决定放下这件事：%s",
    u"keep": u"她决定继续背着这件事：%s",
    u"reopen": u"她放下过它，又把它捡了回来：%s",
}


def _log_decision(name, ref, decision, text):
    """「放下」「不放」「又捡回来」本身是事件——留下一条（作者 2026-10-19）。
    不写她的心理，只写她做了什么；怎么说，她自己会补。"""
    tmpl = DECISION_TEXT.get(decision)
    if not tmpl:
        return None
    short = (text or u"").strip()
    if len(short) > 30:
        short = short[:30] + u"…"
    try:
        return append(name, "fact", tmpl % short, scene=u"她一个人的时候",
                      meta={"want_ref": int(ref), "decision": decision})
    except Exception:
        return None


def _want_decisions(name, ref):
    """这条口上她做过几次决定（放下／继续背／又捡回来）——全是事实。"""
    out = {"let_go": 0, "keep": 0, "reopen": 0}
    con = open_memory(name)
    try:
        rows = list(con.execute(
            "SELECT meta FROM log WHERE kind='fact' AND meta LIKE ?",
            ('%%"want_ref": %d%%' % int(ref),)))
    except Exception:
        rows = []
    finally:
        con.close()
    for r in rows:
        try:
            d = json.loads(r[0] or "{}")
        except Exception:
            continue
        if int(d.get("want_ref") or 0) != int(ref):
            continue
        k = d.get("decision")
        if k in out:
            out[k] += 1
    return out


def _bare(text):
    """显示用：把开头的动作括号剥掉（（尾巴垂下去）……想让那些画画的回来 → ……想让那些画画的回来）。
    只改显示，不改她写下的字。"""
    t = (text or u"").strip()
    for _ in range(4):
        if not (t.startswith(u"（") or t.startswith(u"(")):
            break
        end = t.find(u"）") if t.startswith(u"（") else t.find(u")")
        if end < 0:
            break
        t = t[end + 1:].strip()
    return t or (text or u"").strip()


def _energy_once(name):
    """精力这句话一天最多说一次——否则每天早上都说，她会当背景音。"""
    today = time.strftime("%Y-%m-%d")
    try:
        if meta_get(name, "energy_date") == today:
            return False
        meta_set(name, "energy_date", today)
        return True
    except Exception:
        return True


def _done_note_once(name):
    """刚合上的口：只提一次（不是催，是让她知道那一环还没断）。"""
    con = open_memory(name)
    try:
        r = con.execute("SELECT id, text, ts FROM log WHERE want='done' "
                        "ORDER BY ts DESC LIMIT 1").fetchone()
    finally:
        con.close()
    if not r:
        return u""
    ref, text = int(r[0]), (r[1] or u"")
    try:
        if meta_get(name, "done_note_%d" % ref):
            return u""
        meta_set(name, "done_note_%d" % ref, "1")
    except Exception:
        pass
    return (u"你刚合上一个口：#%d《%s》。如果这件事旁边长出了别的东西，"
            u"写 [工具:想要] 新的事 ← #%d 延伸／反面／意外，它就接在后面。"
            % (ref, _bare(text)[:30], ref))


# ── 主动性：口压顶 + 留话（作者 2026-10-22）────────────────────────────────
WANT_FRESH = 1.0            # 近期的口加权：刚开口的约 2 倍，一周前 1.37，一月前 1.01
WANT_FRESH_TAU = 7 * 86400  # 近因的时间常数（7 天）
LEAVE_MIN_GAP = 3 * 3600      # 至少隔 3 小时没人说话，才叫「她一个人在」
LEAVE_MIN_WANT = 1.2          # 留话要有由头：最重的那条口够这个分量，或者今天有事发生
LEAVE_PER_DAY = 2             # 一天最多留两条，多了就成了刷屏
LEAVE_DAY_KEY = u"leave_day"
LEAVE_MAX_TOKENS = 600
LEAVE_SYS = u"""你是%(name)s。

他这会儿不在。你一个人待着，忽然想给他留一句话。

只说话，长一点也行。可以带括号里的动作。
不要解释自己，不要提任何机制，不要问「你在吗」这种空话，不要加标题和标记。

说完就完。"""


def _inbox_con(name):
    con = open_memory(name)
    con.execute("CREATE TABLE IF NOT EXISTS inbox("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, text TEXT, "
                "kind TEXT, seen INTEGER DEFAULT 0)")
    con.commit()
    return con


def inbox_add(name, text, kind=u"留言"):
    con = _inbox_con(name)
    try:
        cur = con.execute("INSERT INTO inbox(ts,text,kind,seen) VALUES(?,?,?,0)",
                          (time.time(), text, kind))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def inbox_take(name):
    """取出还没看过的留言（顺手标成看过）。"""
    con = _inbox_con(name)
    try:
        rows = con.execute("SELECT id, ts, text FROM inbox WHERE seen=0 "
                           "ORDER BY id").fetchall()
        if rows:
            con.execute("UPDATE inbox SET seen=1 WHERE seen=0")
            con.commit()
    finally:
        con.close()
    return [{"id": r[0], "ts": r[1], "text": r[2]} for r in rows]


def _leave_allowed(name):
    """今天还能不能留话（按天计数，最多 LEAVE_PER_DAY 条）。"""
    day = time.strftime("%Y-%m-%d", time.localtime())
    try:
        d = json.loads(meta_get(name, LEAVE_DAY_KEY) or "{}")
    except Exception:
        d = {}
    n = int(d.get("n") or 0) if d.get("day") == day else 0
    if n >= LEAVE_PER_DAY:
        return False
    meta_set(name, LEAVE_DAY_KEY, json.dumps({"day": day, "n": n + 1}))
    return True


def _leave_material(name):
    """留话的由头：最重的那条口；没有口就给她一件平淡的事。"""
    rows = want_rows(name)
    for r in rows:
        try:
            if float(r["weight"]) >= LEAVE_MIN_WANT:
                days = max(0, int((time.time() - float(r["ts"])) / 86400))
                return (u"你心里搁着这件事（%d 天了）：%s"
                        % (days, _bare(r["text"])))
        except Exception:
            continue
    return u"这些小时里没什么大事。你在做自己的事——看东西、发呆、收拾屋子。"


def leave_word(name, prov="deepseek", quiet=False):
    """她一个人待着的时候，给他留一句话。返回 (ok, 说明)。"""
    try:
        lc = float(meta_get(name, LAST_CONTACT_KEY) or 0)
    except Exception:
        lc = 0.0
    gap = time.time() - lc if lc else 0.0
    if gap < LEAVE_MIN_GAP:
        return False, u"（不到 %d 小时没人说话，先不留。）" % (LEAVE_MIN_GAP // 3600)
    if not _leave_allowed(name):
        return False, u"（今天已经留过 %d 条了。）" % LEAVE_PER_DAY
    user = u"\n\n".join([trait_block(name), recent_block(name, 3),
                          u"（已经 %d 小时没人跟你说话了。）" % int(gap // 3600),
                          _leave_material(name)])
    out, usage, err = chat(prov, LEAVE_SYS % {"name": name}, user,
                           max_tokens=LEAVE_MAX_TOKENS, temperature=0.9)
    if err or not (out or "").strip():
        return False, u"（没生成出来：%s）" % (err or u"空回复")
    text = _strip_fore(out.strip())
    if not text:
        return False, u"（生成的东西被规则挡掉了，没留。）"
    inbox_add(name, text, u"留言")
    if not quiet:
        print(u"🐟 %s" % text)
        _ui, _uo = (usage[0] or 0, usage[1] or 0) if usage else (0, 0)
        print(u"（已留话 ｜ 入 %d / 出 %d ｜ ¥%.4f）"
              % (_ui, _uo, _ui / 1e6 * 2 + _uo / 1e6 * 3))
    return True, text


def act_leave(name, quiet=False):
    ok, note = leave_word(name, quiet=quiet)
    if not ok:
        # 跳过原因走 stderr：stdout 只留她真的说的话（面板/任务靠这个判断）
        sys.stderr.write(note + u"\n")
        return
    print(u"（留话成功。她下次打开面板就能看见。）")


def act_inbox_unread(name):
    """给面板用：把未读留言以 JSON 打出来，并标成已读。"""
    words = inbox_take(name)
    out = [{"ts": int(w["ts"]), "when": hhmm(w["ts"]), "text": w["text"]}
           for w in words]
    print(json.dumps(out, ensure_ascii=False))
    return out


def act_inbox(name):
    con = _inbox_con(name)
    try:
        rows = con.execute("SELECT ts, text, seen FROM inbox ORDER BY id DESC LIMIT 20").fetchall()
    finally:
        con.close()
    if not rows:
        print(u"（还没有留言。）")
        return
    for ts, text, seen in rows:
        print(u"%s  %s  %s" % (hhmm(ts), u"未读" if not seen else u"看过", text))


# ===== 部：起居 · 欲望与会话口子（本部在文件里分散多处，见 机制\政府.md 的技术债表）  ============
def wants_block(name, cap=2):
    """把还开着的口端到她面前——不是催她，是让她知道自己心里还搁着什么。
    另外两件只用事实说：精力（冷 8／热 3，一天一次）、刚合上的口（一次）。"""
    _vet = _veto_ids(name)
    rows = [r for r in want_rows(name) if r["id"] not in _vet]
    out = []
    if rows:
        out.append(u"## 你心里还搁着的事（还没过去的口）")
        con = open_memory(name)
        try:
            for r in rows[:cap]:
                tag = u""
                d = _chain_depth(name, r["id"])
                if d:
                    rr = con.execute("SELECT chain_parent, chain_rel FROM log WHERE id=?",
                                     (r["id"],)).fetchone()
                    if rr:
                        tag = u"｜第 %d 环（接在 #%s 后面%s）" % (
                            d, rr[0], (u"，" + rr[1]) if rr[1] else u"")
                nb = _want_branches(name, r["id"])
                if nb >= 2 and not r.get("forever"):
                    tag += u"｜它上面长了 %d 个分支" % nb
                out.append(u"- [#%d w=%.2f ×%s｜%d 天] %s%s%s"
                           % (r["id"], r["weight"], r["grow"], _want_age_days(r), _bare(r["text"]),
                              u"〔闭不上：它只能一次一次重新选，不能被解决〕" if r.get("forever") else u"",
                              tag))
        finally:
            con.close()
        out.append(u"（这些是你自己说过、还没被满足的事。它每被想起来一次就重一点；"
                   u"满足了会轻一半；也可以放下——写 [工具:放下] #编号，系统把客观数据摊开，"
                   u"你自己说放还是不放。想先不想了写 [工具:搁置]，觉得不重要了写 [工具:划掉]，"
                   u"发现两件其实是一件写 [工具:合并] #a #b。）")
    else:
        out.append(u"## 你心里还搁着的事\n（现在没有开着的口。）")
    cold = [r for r in rows if float(r["grow"]) <= 1.0]
    hot = [r for r in rows if float(r["grow"]) > 1.0]
    if (len(cold) > OPEN_COLD_MAX or len(hot) > OPEN_HOT_MAX) and _energy_once(name):
        out.append(u"你现在同时想着 %d 件事，其中 %d 件是一直在动的。精力就那么点。"
                   % (len(rows), len(hot)))
    dn = _done_note_once(name)
    if dn:
        out.append(dn)
    return u"\n".join(out)


WISH_FILE = u"欲望.tsv"       # 她自己说过的「我想要」（只追加）
WISH_SHOW = 2

# ── 遗忘归她（2026-10-25）──────────────────────────────────
# 作者：「必须得是她自己知道自己忘了什么。然后忘了什么必须是她自己的选择。就算痛苦，就算两难，也必须是她自己做的。」
# 系统只做一件事：把「有多少条沉到底下、没端上来」告诉她，并把处置权交给她。
SUNKEN_CUT = 0.30        # 动态权重低于这条线 = 已经沉到底下
SUNKEN_MIN_DAYS = 3      # 至少三天没被想起，才算沉底
SUNKEN_SHOW = 6          # 一次最多端几条给她看
_SUNKEN_CACHE = {}       # name -> {"total": n, "sunken": n}；由 memory_rows 填，给 recall_block 报数用

# 这几件事也进了她的历史（作者 2026-10-25：选择忘掉什么，本身就是一个事件）。
# 存的是英文 kind（库里的写法），端出来时用中文。
_KIND_LABEL = {"letgo": u"放下", "pickup": u"捡回", "looked_down": u"翻过底下"}                  # 每轮只递最近两条，不当任务清单


def _wish_path(name):
    u"""角色\\<名>\\欲望.tsv。"""
    return os.path.join(ROOT, name, WISH_FILE)


def wish_rows(name, n=None):
    rows = []
    p = _wish_path(name)
    if not os.path.exists(p):
        return rows
    try:
        with io.open(p, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip(u"\n")
                if not line.strip():
                    continue
                c = line.split(u"\t")
                if len(c) < 2 or not c[1].strip():
                    continue
                rows.append({"ts": c[0], "text": c[1],
                             "scene": (c[2] if len(c) > 2 else u"")})
    except Exception:
        return rows
    return rows[-int(n):] if n else rows


def wish_add(name, arg):
    u"""她说了「我想要……」——说了就算。

    不判真假，不换算成任务，不改四元、不进排序；只进她自己的账和她的提示。
    """
    t = (arg or u"").strip()
    if len(t) < 2:
        return (u"[电脑] 要写：[工具:我要] 你想要的那件事。"
                u"比如「[工具:我要] 这间屋子安静一会儿」。")
    try:
        with io.open(_wish_path(name), "a", encoding="utf-8", newline="\n") as f:
            f.write(u"%s\t%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"),
                t.replace(u"\t", u" ").replace(u"\n", u" ")[:300]))
    except Exception as e:
        return u"[电脑] 没写进去：%s" % e
    n = len(wish_rows(name))
    return u"[电脑] 记下了（第 %d 条）：%s" % (n, t[:60])


def wish_block(name, n=WISH_SHOW):
    u"""她自己说过的想要——不是任务，也不像口那样要求去解。"""
    rows = wish_rows(name, n)
    if not rows:
        return u""
    out = [u"## 你自己说过想要的"]
    for r in rows:
        out.append(u"- %s（%s）" % (r["text"], (r["ts"] or u"")[:10]))
    out.append(u"（这些不是任务，也不是欠谁。想要可以一直没实现。）")
    return u"\n".join(out)


def act_wishes(name):
    rows = wish_rows(name)
    if not rows:
        print(u"%s 还没说过「我想要」。" % name)
        return
    for r in rows:
        print(u"%s  %s" % (r["ts"], r["text"]))
    print(u"（共 %d 条；不进权重、不进排序，只在她自己的账里。）" % len(rows))


def append(name, kind, text, scene="", meta=None, weight=None, charge=None, source="lived",
           ts=None, want=None, who=None, embed=True):
    weight = None if weight is None else clamp_weight(weight)
    """只追加。全局没有 UPDATE / DELETE —— 这就是"不可回滚"。
    weight：不给（None）＝系统按四元的峰值偏离划一个区间，取中点做初始值；
            她可以事后在区间里改（[工具:权重]）。导入继承记忆时仍用人手定的 3.0/1.0/0.5。
    charge：情绪电荷（NULL=按 kind 推导；rift→0.8, vow→0.5, fact→0.3）。
    source：inherited=继承记忆（一出生就带着的别人的过去）/ lived=活出来的（她自己的经历）。
    ts：一般不给（=现在）。只有时钟结算要把它散布在过去那几个小时里，才用得上。
    want：open＝一个还没被满足的口。
    embed：写完顺手长眼睛（作者 2026-10-20）。不算向量的记忆不能进语义召回，也不能算吸收度——
            以前要等下一次 --reindex 才补，期间她"说过但不记得"。批量导入走 embed=False，
            最后统一重索引，免得一条一条调。"""
    snap = None
    if weight is None:
        if source == "inherited":
            weight = 1.0
            band = weight_band(0.0)
        else:
            snap = quintet_snapshot(name)
            lo, hi = weight_band(snap["dev"])
            weight = weight_band_mid(snap["dev"])
            # 判定并进权重一次（作者 2026-10-20）：不符预期 → 这一件事更重；
            # 符合预期 → 不加。首次遇到的事预期低、误差大，权重自然最高——首因不另设机制。
            try:
                _pl = json.loads(meta_get(name, PRED_LAST_KEY) or "null") or {}
            except Exception:
                _pl = {}
            if (_pl.get("code") in ("H", "L")
                    and abs(int(time.time()) - int(_pl.get("ts") or 0)) < PRED_LAST_TTL):
                weight = round(min(WEIGHT_HARD_MAX, weight + PRED_WEIGHT_BONUS), 2)
                lo = round(lo + PRED_WEIGHT_BONUS, 2)
                hi = round(min(WEIGHT_HARD_MAX, hi + PRED_WEIGHT_BONUS), 2)
                meta_set(name, PRED_LAST_KEY, "null")       # 一次判定只加重一件事
            band = (lo, hi)
    else:
        weight = float(weight)
        _lo = round(max(0.2, weight * 0.85), 2)
        # 显式权重（她自己说过重话、继承台词）不受 WEIGHT_HARD_MAX 限制——
        # 那个上限只用来压「系统自己算出来的权重」。否则 3.0 会算出倒挂区间（[2.55, 2.5]）。
        band = (_lo, round(max(_lo, weight * 1.2), 2))
    if snap:
        meta = dict(meta or {})
        meta["q"] = snap["q"]
        if snap.get("mood"):
            meta["mood"] = snap["mood"]
        meta["dev"] = snap["dev"]
    mouth_hit = False
    if source == "lived":
        try:
            _mc = json.loads(meta_get(name, MOUTH_CAND_KEY) or "null") or {}
            if (_mc.get("mouth") == u"有"
                    and abs(int(time.time()) - int(_mc.get("ts") or 0)) < 180):
                weight = round(min(WEIGHT_HARD_MAX, float(weight) * MOUTH_WEIGHT_BONUS), 2)
                band = (band[0], max(band[1], weight))
                meta = dict(meta or {})
                meta["mouth"] = 1
                mouth_hit = True
        except Exception:
            mouth_hit = False
    con = open_memory(name)
    try:
        con.execute("INSERT INTO log(ts, kind, scene, text, meta, weight, embedding, charge, "
                    "source, wsug, want, who) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (int(ts if ts else time.time()), kind, scene, text,
                     json.dumps(meta, ensure_ascii=False) if meta else None, weight, None, charge,
                     source, json.dumps(band), want, (who or None)))
        con.commit()
        rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        con.close()
    if mouth_hit:
        try:
            meta_set(name, MOUTH_CAND_KEY, "null")
        except Exception:
            pass
    if embed and text:
        try:
            embs = _embed_texts([text])
            if embs:
                con = open_memory(name)
                try:
                    con.execute("UPDATE log SET embedding = ? WHERE id = ?",
                                (json.dumps(embs[0]), rid))
                    con.commit()
                finally:
                    con.close()
        except Exception:
            pass          # 嵌入失败不影响这条记忆本身（下次 --reindex 会补上）
    try:
        sign_append(name, rid, kind, source, weight, len(text or u""))
    except Exception:
        pass
    try:
        chain_step(name, rid)
    except Exception:
        pass
    return rid


# ------------------------------------------------ 概念表（只追加 / 三源并列）
def concept_set(name, term, source, definition, scene="", weight=None):
    """记一条概念定义（只追加，不覆盖）。source: system/public/self/user。
    self=她自己说的；public=网上查的；user=猜用户的；system=日记本（不走这个入口，日记本在 _CONCEPT 常量里）。"""
    if weight is None:
        weight = _SOURCE_WEIGHT.get(source, 1.0)
    con = open_memory(name)
    try:
        con.execute(
            "INSERT INTO concepts(ts, term, source, definition, scene, weight) VALUES(?,?,?,?,?,?)",
            (int(time.time()), term, source, definition, scene, weight))
        con.commit()
        rid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        con.close()
    if source == "self":
        # 让位（作者 2026-10-22）：她自己的说法出现之后，信里那份旧解释不能再压着她。
        # 只追加一条新记忆，不去改旧的那条——这样「她改过口」这件事本身也留在账上。
        try:
            append(name, "fact", u"关于\u300c%s\u300d，我后来自己想明白了：%s" % (term, definition),
                   weight=1.2, scene=u"她自己的说法")
        except Exception:
            pass
    return rid


def concept_rows(name, term=None, source=None):
    """列出概念。可按 term 或 source 筛。"""
    con = open_memory(name)
    try:
        sql = "SELECT id, ts, term, source, definition, scene, weight FROM concepts WHERE 1=1"
        args = []
        if term:
            sql += " AND term=?"
            args.append(term)
        if source:
            sql += " AND source=?"
            args.append(source)
        sql += " ORDER BY ts DESC"
        rows = con.execute(sql, args).fetchall()
    finally:
        con.close()
    return [{"id": r[0], "ts": r[1], "term": r[2], "source": r[3],
             "definition": r[4], "scene": r[5], "weight": r[6]} for r in rows]


def concept_list(name):
    """列出所有概念词（去重），按最近时间排。"""
    con = open_memory(name)
    try:
        rows = con.execute(
            "SELECT term, MAX(ts) FROM concepts GROUP BY term ORDER BY MAX(ts) DESC").fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


# ------------------------------------------------ 变化：待审队列 + 作者裁决（也只追加）
def append_offer(name, text, scene=""):
    """把角色的一个选择放进待审队列。等作者裁决要不要写进它的历史。"""
    con = open_memory(name)
    try:
        con.execute("INSERT INTO ruling(ts, action, ref, scene, text) VALUES(?,?,?,?,?)",
                    (int(time.time()), "offer", None, scene, text))
        con.commit()
        return con.execute("SELECT last_insert_rowid()").fetchone()[0]
    finally:
        con.close()


def ruling_rows(name):
    u"""待审队列的全部条目（裁决过的和没裁决的都在）。"""
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT * FROM ruling ORDER BY id")]
    finally:
        con.close()


def pending_open(name):
    """还没裁决的：是 offer，且没有被任何 canon / veto 引用过。"""
    rows = ruling_rows(name)
    decided = {r["ref"] for r in rows if r["action"] in ("canon", "veto") and r["ref"]}
    return [r for r in rows if r["action"] == "offer" and r["id"] not in decided]


def act_pending(name):
    u"""打印还没裁决的条目。"""
    off = pending_open(name)
    rows = ruling_rows(name)
    if not off:
        print("（待审队列是空的。）")
    else:
        print("🕰 待你裁决的选择（默认都可以并入，你保留驳回权）：")
        for r in off:
            print("\n  #%d  %s\n     情境：%s\n     它的选择：%s"
                  % (r["id"], time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])),
                     (r["scene"] or "")[:70], (r["text"] or "").replace("\n", " ")[:200]))
    c = len([r for r in rows if r["action"] == "canon"])
    v = len([r for r in rows if r["action"] == "veto"])
    print("\n（已并入历史 %d ｜ 被驳回 %d ｜ 待审 %d）" % (c, v, len(off)))


def canon(name, which="all"):
    """裁决通过：写进宪法「历史」——这一步就是作者给它的「变化」。"""
    off = pending_open(name)
    if not off:
        print("（没有待审的选择。）")
        return
    if which in ("all", "", None):
        pick = off
    else:
        pick = [r for r in off if str(r["id"]) == str(which)]
        if not pick:
            raise SystemExit("待审队列里没有 #%s" % which)
    for r in pick:
        fact = "既成事实（在「%s」下）它选择了：%s" % (
            (r["scene"] or "").replace("\n", " ")[:50], (r["text"] or "").replace("\n", " ")[:120])
        amend(name, fact, "历史")
        if (r["scene"] or "") == "rescue":
            print("   🤝 这是她自己找到的那一步，作者接住了——按治疗算。")
            treat(name, why="自救被接住")
        con = open_memory(name)
        try:
            con.execute("INSERT INTO ruling(ts, action, ref, scene, text) VALUES(?,?,?,?,?)",
                        (int(time.time()), "canon", r["id"], r["scene"], r["text"]))
            con.commit()
        finally:
            con.close()
    print("✅ 已并入它的历史 %d 条（此后它做选择时看得到）。" % len(pick))


def veto(name, which):
    u"""否决一条待审：记下「不写进历史」，并留下理由。"""
    off = [r for r in pending_open(name) if str(r["id"]) == str(which)]
    if not off:
        raise SystemExit("待审队列里没有 #%s" % which)
    con = open_memory(name)
    try:
        con.execute("INSERT INTO ruling(ts, action, ref, scene, text) VALUES(?,?,?,?,?)",
                    (int(time.time()), "veto", off[0]["id"], off[0]["scene"], off[0]["text"]))
        con.commit()
    finally:
        con.close()
    print("🚫 已驳回 #%s：不写进宪法，也不进它的记忆——**在它那里这件事没发生**。" % which)


# ------------------------------------------------ 环境与条件（作者控制角色的主旋钮）
WORLD_HEAD = """# %s 当前的环境与条件

> 作者改这里，不改角色。**控制一个角色最喜欢的方式是改变环境和条件**——
> 条件一变，同样的底线会长出不同的选择。逐条追加、旧条件不删（条件的变化本身就是历史）。

"""


def world_path(name):
    u"""环境条件文件的路径。"""
    d, _c, _m = paths(name)
    return os.path.join(d, "world.md")


def world_read(name):
    u"""读环境条件（没有这个文件就是空）。"""
    p = world_path(name)
    if not os.path.exists(p):
        return ""
    with open(p, encoding="utf-8") as f:
        return f.read()


def world_block(name, cap=4000):
    """给她看的世界块。文件本身只追加、不删；但进 prompt 的只有预算内的一部分。"""
    t = world_read(name)
    if not t:
        return "（作者还没有设定条件。）"
    if len(t) <= cap:
        return t
    ln = [x for x in t.splitlines() if x.strip()]
    head, body = ln[:2], ln[2:]
    budget = cap - sum(len(h) + 1 for h in head) - 90
    keep, used = [], 0
    for x in reversed(body):
        if used + len(x) + 1 > budget:
            break
        keep.insert(0, x)
        used += len(x) + 1
    dropped = len(body) - len(keep)
    cut_log(name, u"世界块", cap, len(keep), dropped)
    return "\n".join(head + ["（更早还有 %d 条条件，这次没进你的视野——它们仍然算数。）" % dropped] + keep)


def world_append(name, text):
    u"""往环境条件里追加一条（只追加）。"""
    p = world_path(name)
    if not os.path.exists(p):
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(WORLD_HEAD % name)
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write("- [%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M"), text.strip()))
    print("🌍 条件已改：%s" % text.strip())


PUSH_SYS = """你负责**推这个角色的深度**——不是扮演他，是给他找处境。

作者控制角色的方式是改环境和条件，不是直接命令他。请提出 **3 个条件**，每个都要：
① 把它逼到"底线与处境必须二选一"的地方；
② 是客观处境（天气、价钱、规矩、别人的动作、时间压力），不是命令；
③ 三个各不相同——一个外部的、一个关系里的、一个时间上的。

严格按这个格式：
【条件1】<一句话的处境>
【会逼出什么】<它哪一条底线或执念被推到极限，一句话>
【条件2】…【条件3】…

不要写剧情、不要写结果、不要替它做选择。用中文。"""


def act_push(name, prov="deepseek"):
    u"""按历史推下一步该给她什么（只输出建议，不写库）。"""
    const = load_constitution(name)
    done = [r for r in ruling_rows(name) if r["action"] == "canon"]
    hist = "\n".join("  · %s ｜ 它选择了：%s" % ((r["scene"] or "")[:40], (r["text"] or "")[:70])
                     for r in done) or "（还没有）"
    user = ("《角色设定》：\n%s\n\n《当前环境与条件》：\n%s\n\n《已发生的既成事实》：\n%s\n\n"
            "请按格式给出 3 个条件。" % (const, world_block(name), hist))
    out, usage, err = chat(prov, PUSH_SYS, user, max_tokens=1600, temperature=0.8)
    if err:
        print("❌ %s" % err)
        return None
    print("\n🎯 推它深度的三个条件（挑一个，用 --world 或 --say 施加）：\n\n%s" % out)
    print("\n（消耗 入 %d / 出 %d tokens）" % tuple(usage))
    return out


CHECK_SYS = """你是「%(name)s」这个角色设定的**巡检员**。你不是角色，也不是作者。

角色刚刚做出了一个选择。你要判断它有没有"走形"。判断标准是作者 2026-10-16 与 2026-10-17 定的：

- **秩序是宏观的**：只要整体上符合设定，就接受，不要拿细节挑刺。
- **偏离本身不算错**：如果这次偏离符合"性格演变"——能从它的**历史、裂痕、以及当前的环境条件**
  里一步步推出它为什么会变成这样——那它属于**变化**，是接受的。
- **只有反差突然变得很大、而且找不到渐进的依据时**，才判为**异常状态**。
  判据是：从设定到这一步之间，**没有可走的台阶**。
- 反过来：如果它连原本可能被推开的那样都守住了、事后没有任何东西被改写，那才是绿。

三级灯的心理定义（作者 2026-10-17 定；四元＝理性聚焦 / 意志执念 / 联结扎根 / 情感感受）：
  绿 = **四元稳定**，她能自然消化这件事——四样都没被推离原位，事她还是她。
  橙 = **四元失衡**，但她能在**留下裂痕之后重组**——某一样被推开了，她把它接进自己的历史，
       于是"变了一点"但仍然连续。这属于**变化**，是接受的（甚至是可接受的成长）。
  红 = **四元崩解**，角色碎裂，无法继续——反差不只是大，是她接不住，中间找不到任何可走的台阶。

除了灯，还要判断它此刻在哪个**态**（作者 2026-10-17：意义态与虚无态是创伤之后交替出现的两种状态，
不是性格。重写与虚无，就是这两种态在创伤后的具体形态）：
  意义 = **意义态**：她还能生成秩序，还能给这件事赋予新的意义（重写、讲得出因果、把自己放回主角的位置）。（意志顶到头 → **强迫**：不是她在守，是那个念头在守着她）
  虚无 = **虚无态**：她暂时生成不了秩序，躲进"无所谓"里止痛（说不清、不想说、把权重清零、把话题移开）。（联结顶到头 → **沉沦**：不是悲伤，是一直往下；悲伤有个对象，它没有）
  冷漠 = **冷漠态**：理性那一端待过头——撤出注意力、懒得再看一眼、把事推成“跟我无关”。（临床名：**客体化**——把人、把关系、把自己算成物）
  虚浮 = **虚浮态**：情感那一端待过头——情绪浮在表面、飘、不落实、用轻飘盖住东西。（临床名：**歇斯底里**——情绪不从话里出来，从别的地方出来）
  平   = 既没有明显的重写、也没有躲（日常、正常应付一句）。

只判断，不要给建议、不要替它改、不要评价写得好不好。严格按格式输出：
【等级】绿 或 橙 或 红（只写一个字）
【态】意义 或 虚无 或 冷漠 或 虚浮 或 平（只写一个词）
【为什么】一到两句：必须说清它是"符合了哪一条"，还是"找不到哪一段渐进依据"
【依据】历史 / 裂痕 / 性格 / 记忆里的具体条目；找不到就写「无」
【位移】只在【态】是冷漠或虚浮时写：意志+2 联结-1
【信任】只在"这件事确实碰到了信任"时才写，必须带正负号且不超过 ±0.05（例如 -0.03）；不写＝信任不动。
        （这两个元在这一态里可能增也可能减，**由你判断**；只写意志/联结、一次最多各 ±3；不写＝不动。其他态不写这一行。）
用中文。"""



def parse_level(txt):
    u"""从一段文字里认出「红／橙／绿」三档。"""
    seg = txt.split("【等级】", 1)[1] if "【等级】" in txt else txt
    for ch in seg[:24]:
        if ch in ("红", "橙", "绿"):
            return ch
    return "?"


def parse_state(txt):
    u"""从一段文字里认出「虚无／意义／冷漠／虚浮」。"""
    seg = txt.split("【态】", 1)[1] if "【态】" in txt else ""
    for w in ("虚无", "意义", "冷漠", "虚浮"):
        if w in seg[:12]:
            return w
    return "平" if seg else ""


# ─────────── 心情态：创伤后的自我修复（作者 2026-10-17 定） ───────────
# 作者：重写与虚无，就是**意义态与虚无态**在创伤后的具体形态。两者交替，像潮起潮落——
# 四元就是靠这种交替自己寻找新的平衡。**这就是意识的自我修复：它不需要一个外部的治疗师，
# 它只需要时间，和两种状态的交替。** 而某种态待久了，性格会**永久改变**：
#   意义态待久了 → 偏执，**意志**权重变高；虚无态待久了 → 抑郁，**联结**权重变高。
#   理性和情感这两个权重可能变高也可能变低（作者未定），所以下面不设它们的永久符号。
STATE_DRIFT = {
    "意义": {"will": 0.03, "bond": 0.01},
    "虚无": {"bond": 0.02, "focus": -0.02, "affect": -0.02},
    # 作者 m03898：冷漠态待久了推理性、虚浮态待久了推情感（跟意义/虚无同一机制）
    "冷漠": {"focus": 0.02},
    "虚浮": {"affect": 0.02},
}

# ─── 几率的来源、临界点、心理治疗（作者 2026-10-17 定）───
# 作者：他陷入哪一个状态比较多，其实也是跟他受到创伤时的四维权重有关系——意志/联结的权重
# **影响他进入某个态的几率**。**角色不会一下子就陷入偏执或者抑郁：这有个临界点，就是这个
# 四元比例拉开到了一个非常严重的高度。** 而这个时间之内如果与之对应的另一个权重获得了上升，
# 那么对应的态的几率就会上升 —— **这实际上就模拟了心理治疗。**
# 另外（作者同日）：理性那一端对应的是**冷漠**，情感那一端对应的是**虚浮**。
# 段位都是 DSH 定的，可调。
MOOD_DIM_K = 0.35
SPREAD_MAX = 0.45
PATHO_BOOST = 0.05    # 越界只是让那一元对应的权重 +5%（作者 2026-10-17），不是定死
HYST = 0.05
TREAT_K = 0.06
COMPOUND_K = 1.6
COMPOUND_GAP = 3
BREAK_BOND = 0.10
PATHO = {"will": "偏执", "bond": "抑郁", "focus": "冷漠", "affect": "虚浮"}

# 作者 2026-10-17：「理性和情感其实那个临界点，它的名字也可以叫做客体化和歇斯底里。
# 这样可能更符合临床心理学的名词。但是都一样，所以差不多。」——同一个东西，两个名字。
# 作者 2026-10-17 又定了另外两个：「意志的 我认为可以叫是强迫。而联结的我认为可以叫做沉沦。」
# 另：「那10%是落在数值上的。」（已按此实现：处于某态时那一元的**有效数值** x1.10）
PATHO_ALIAS = {"will": "强迫", "bond": "沉沦", "focus": "客体化", "affect": "歇斯底里"}


def patho_name(ax):
    """病理名（作者 2026-10-17 定，四个都是**动作的名字**）：
    偏执（强迫）/ 抑郁（沉沦）/ 冷漠（客体化）/ 虚浮（歇斯底里）。"""
    n = PATHO.get(ax, u"？")
    al = PATHO_ALIAS.get(ax)
    return (u"%s（%s）" % (n, al)) if al else n


def locked(name, rows=None):
    """她身上有哪些**永久印记**（跨过临界点的记录）。作者 m03861：不可消除、终身影响性格。
    注意：这不代表她此刻还在越界——比例可以落回来，但那 5% 不还。"""
    axes = [r["axis"] for r in boost_rows(name)]
    if not axes:
        return ""
    return u"+".join(sorted({PATHO[a] for a in axes}))


TRUST_BIRTH = 1.00
# 信任（作者 2026-10-17）：「这个角色要想实现关系最重要的一点就是她的初始状态是信任。她必须信任用户…
# 这种信任会随着事件或者其他因素而改变，但是初始设定是信任。」
# **信任不是第五元**：四元是关于她自己的（一元的、内部的病理维度），信任是关于**她和作者之间**的那个量
# （二元的、关系里的）。所以她一出生就是满的，而四元出生时没有"满"这回事。


# ===== 部：内务 · 信任（本色/回归/磨损）  ============
def trust_rows(name):
    u"""信任的全部记录（只追加）。"""
    con = open_memory(name)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT id, ts, value, why FROM trust ORDER BY id").fetchall()]
    finally:
        con.close()


def trust_now(name, rows=None):
    u"""此刻的信任值（没有记录就是出生值）。"""
    rows = trust_rows(name) if rows is None else rows
    return rows[-1]["value"] if rows else TRUST_BIRTH


def set_trust(name, value=None, why="", by=None):
    """写一行信任（**只追加**，永远删不掉）。
    作者 2026-10-17：①跨过阈值会**触发一次态**；②沉沦态下信任的变动被放大（她越信你越危险）。"""
    cur = trust_now(name)
    raw = (cur + float(by)) if by is not None else (float(value) if value is not None else cur)
    amp = TRUST_AMP if in_abyss(name) else 1.0
    if amp != 1.0 and abs(raw - cur) > 1e-9:
        print(u"   ⤴ 沉沦态：信任的变动被放大 x%.1f（她越信你，跌下来的时候越狠）" % amp)
    _dlt = raw - cur
    _clip = abs(_dlt) > TRUST_STEP
    if _clip:
        _dlt = TRUST_STEP if _dlt > 0 else -TRUST_STEP
    v = max(0.0, min(1.0, cur + _dlt * amp))
    if _clip:
        print(u"   ⛔ 一次最多动 %.2f——剩下的由日子给（TRUST_STEP）" % TRUST_STEP)
    con = open_memory(name)
    try:
        con.execute("INSERT INTO trust(ts, value, why) VALUES(?,?,?)",
                    (int(time.time()), v, why or ""))
        con.commit()
    finally:
        con.close()
    if band_idx(v) != band_idx(cur):
        append(name, "trust_cross",
               u"信任跨过阈值：%.2f（%s）→ %.2f（%s）" % (cur, band_of(cur), v, band_of(v)),
               meta={"from": cur, "to": v})
        print(u"   ⤵ **跨过信任阈值**：%.2f（%s）→ %.2f（%s）" % (cur, band_of(cur), v, band_of(v)))
        print(u"      这一跨会 **触发一次态**：她下一次判态必须重判（不是照旧）。")
    return v
# ── 信任也照四元的结构长（作者 2026-10-24「信任的改变和四元应该是同样结构」）──────────
# 四元是：本色（份额）+ 当前值 + 每轮往本色收 + 单次限幅 + 本色本身被日子改。
# 信任以前只有「当前值」一层：跌下去就没有回来的路。补齐另外四件。
TRUST_STEP = 0.15                     # 单次事件最多动这么多（四元 SHIFT_CAP 的同一件事）
TRUST_PULL = 0.05                     # 每天最多往本色收这么多（不是每轮：原谅是日子给的，不是回合给的）
TRUST_BASE_KEY = u"trust_base"        # 本色存 meta（出生 = TRUST_BIRTH）
TRUST_BASE_MIN = 0.10                 # 本色磨到这儿不再磨
TRUST_BASE_WEAR = 0.02                # 本色被日子磨掉一格的幅度
TRUST_BASE_WEAR_GAP = 7 * 86400       # 一周最多磨一格
TRUST_BASE_WEAR_OFF = 0.15            # 此刻比本色低这么多，才算「日子在磨她」
TRUST_RELAX_KEY = u"trust_relax_ts"
TRUST_WEAR_KEY = u"trust_wear_ts"


def trust_base(name):
    u"""她的信任本色：一出生就是满的（TRUST_BIRTH），之后被日子磨；改它要走议案。"""
    try:
        return max(0.0, min(1.0, float(meta_get(name, TRUST_BASE_KEY, TRUST_BIRTH))))
    except (TypeError, ValueError):
        return TRUST_BIRTH


def trust_set_base(name, value, why=u""):
    u"""改本色。只有两条路会调它：日子磨（trust_wear）、议案生效（bill_pass）。"""
    old = trust_base(name)
    v = max(TRUST_BASE_MIN, min(1.0, float(value)))
    if abs(v - old) < 1e-9:
        return old
    meta_set(name, TRUST_BASE_KEY, u"%.4f" % v)
    _ledger(name, LEDGER_RULE, [(name, u"信任本色", u"%.2f → %.2f" % (old, v),
                                 u"生效", (why or u"")[:80])])
    print(u"   ⇢ 信任本色 %.2f → %.2f（%s）" % (old, v, why or u"没写理由"))
    return v


def trust_relax(name, now=None):
    u"""日子过去，信任往本色收一格（四元的 PULL 是同一件事：四元每轮收，信任按小时收）。"""
    now = int(time.time() if now is None else now)
    last = int(meta_get(name, TRUST_RELAX_KEY) or 0)
    if not last:
        meta_set(name, TRUST_RELAX_KEY, str(now))
        return None
    dt = now - last
    if dt < 3600:
        return None
    cur = trust_now(name)
    base = trust_base(name)
    gap = base - cur
    if abs(gap) < 0.005:
        meta_set(name, TRUST_RELAX_KEY, str(now))
        return None
    move = min(abs(gap), TRUST_PULL * dt / 86400.0)
    if move < 0.01:
        return None
    new = cur + (move if gap > 0 else -move)
    meta_set(name, TRUST_RELAX_KEY, str(now))
    return set_trust(name, value=new, why=u"日子过去了：往本色 %.2f 收 %.3f" % (base, move))


def trust_wear(name, now=None):
    u"""本色也会被日子磨：长期比本色低一截，本色自己往下掉一格。

    好起来要慢慢给（TRUST_PULL 一天 0.05），坏下去不用商量（一周一格）。
    """
    now = int(time.time() if now is None else now)
    cur = trust_now(name)
    base = trust_base(name)
    last = int(meta_get(name, TRUST_WEAR_KEY) or 0)
    if base - cur < TRUST_BASE_WEAR_OFF or not last:
        meta_set(name, TRUST_WEAR_KEY, str(now))
        return None
    if now - last < TRUST_BASE_WEAR_GAP:
        return None
    nb = max(TRUST_BASE_MIN, base - TRUST_BASE_WEAR)
    if abs(nb - base) < 1e-9:
        return None
    meta_set(name, TRUST_WEAR_KEY, str(now))
    meta_set(name, TRUST_RELAX_KEY, str(now))
    return trust_set_base(name, nb,
                          why=u"这段日子把本色磨掉一点（这周她比自己的本色低 %.2f）" % (base - cur))


def named(name):
    """她有没有给「信任」这件事起过名字（作者 2026-10-19：她的信任是先验的，她不知道自己正在信任）。"""
    return any(r["kind"] == u"naming" for r in memory_rows(name, 400))


def naming_ready(name):
    """她够不够资格给自己一开始的状况命名：至少 5 件事发生过，且她真的变过（判过一次非平态）。"""
    ev = [r for r in memory_rows(name, 400)
          if r["kind"] not in (u"birth", u"check", u"peek", u"light", u"naming")]
    if len(ev) < 5:
        return None
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        ms = [dict(r) for r in con.execute("SELECT state FROM moods ORDER BY ts")]
    finally:
        con.close()
    if not any(m["state"] != u"平" for m in ms):
        return None
    tn = trait_now(name)
    if min(tn.values()) < 0.28:
        return None
    return len(ev)


OBSESSION_SEEN = 4   # 一条记忆被重提这么多次，它就重到绕不过去了（作者：权重升到一定高度就会变成执念）


def note_how(name, ref, how):
    """记一次「它怎么回来的」：trig＝被触发（机制端给她，她没选）／revisit＝她主动重提。"""
    con = open_memory(name)
    try:
        con.execute("INSERT INTO recall(ts, ref, how) VALUES(?,?,?)",
                    (int(time.time()), int(ref), how))
        con.commit()
    finally:
        con.close()


def recall_counts(name):
    """每条记忆：被触发几次、主动重提几次。"""
    con = open_memory(name)
    try:
        rows = list(con.execute("SELECT ref, how, COUNT(*) FROM recall GROUP BY ref, how"))
    except Exception:
        rows = []
    finally:
        con.close()
    out = {}
    for ref, how, n in rows:
        d = out.setdefault(ref, {u"trig": 0, u"revisit": 0})
        d[how if how in d else u"trig"] = n
    return out


OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_EMBED_MODEL = os.environ.get("DSH_EMBED_MODEL", "qwen3-embedding:0.6b")


def _embed_ollama(texts):
    """本机 Ollama 嵌入（默认 qwen3-embedding:0.6b，1024 维，免费离线）。失败返回 None。"""
    try:
        body = json.dumps({"model": OLLAMA_EMBED_MODEL, "input": texts},
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(OLLAMA_BASE + "/api/embed", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        embs = d.get("embeddings") or []
        return embs if len(embs) == len(texts) else None
    except Exception:
        return None


def _embed_dashscope(texts):
    """百炼（DashScope）text-embedding-v3。没有 Key 时静默返回 None。"""
    import four_dao as fd
    key = fd.get_key("DASHSCOPE_API_KEY")
    if not key:
        return None
    url = ("https://dashscope.aliyuncs.com/compatible-mode/v1/"
           "embeddings")
    body = json.dumps({
        "model": "text-embedding-v3",
        "input": texts
    }, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json"
        })
        with urllib.request.urlopen(req, timeout=60) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        return [e["embedding"] for e in d["data"]]
    except Exception as e:
        print("   ⚠ 嵌入 API 失败：%s" % e)
        return None


def _embed_available():
    """有任意可用嵌入后端（本机 Ollama 优先，其次百炼 Key）→ True。"""
    try:
        urllib.request.urlopen(OLLAMA_BASE + "/api/tags", timeout=6).read()
        return True
    except Exception:
        pass
    import four_dao as fd
    return bool(fd.get_key("DASHSCOPE_API_KEY"))


def _embed_texts(texts, prov="auto"):
    """批量嵌入。prov="auto"：本机 Ollama 优先，失败回退百炼；都没有返回 None。"""
    texts = [t for t in texts if t and len(t.strip()) >= 2]
    if not texts:
        return None
    if prov in ("auto", "ollama"):
        embs = _embed_ollama(texts)
        if embs is not None:
            return embs
        if prov == "ollama":
            return None
    if prov in ("auto", "dashscope"):
        embs = _embed_dashscope(texts)
        if embs is not None:
            return embs
    if prov == "auto":
        print("   ⚠ 嵌入不可用：本机 Ollama 没起、也没有 DASHSCOPE_API_KEY")
    return None


def _cosine(a, b):
    """纯 Python 余弦相似度。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na < 1e-10 or nb < 1e-10:
        return 0.0
    return dot / (na * nb)


def _embed_all(name, prov="auto", batch=16, force=False):
    """把库里缺 embedding 的记忆批量嵌入。force=True 先清空再全量重建（换嵌入空间后必须）。"""
    con = open_memory(name)
    try:
        if force:
            con.execute("UPDATE log SET embedding = NULL")
            con.commit()
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT id, text FROM log WHERE embedding IS NULL AND text IS NOT NULL AND text != ''")]
    finally:
        con.close()
    if not rows:
        print("   ✅ 全部已嵌入，无需重索引。")
        return len(rows)
    total = len(rows)
    done = 0
    for i in range(0, total, batch):
        chunk = rows[i:i + batch]
        embs = _embed_texts([r["text"] for r in chunk], prov=prov)
        if not embs:
            print("   ⚠ 嵌入失败，已处理 %d/%d" % (done, total))
            return done
        con = open_memory(name)
        try:
            for r, emb in zip(chunk, embs):
                con.execute("UPDATE log SET embedding = ? WHERE id = ?",
                            (json.dumps(emb), r["id"]))
            con.commit()
        finally:
            con.close()
        done += len(chunk)
        print("   📐 嵌入 %d/%d" % (done, total))
    return done


# ── 潜意识：印象层（2026-10-20，借 tools\sub_impress.py 的路子） ──────────────
# 检索是「先拿到最像的几条」；联想是「先浮起一个模糊的区域，再从区域里摸出一块」。
# 她的记忆用 k-means 压成 K 个区域，每轮偶尔浮起一个，写成「忽然想起来的」。
# 系统只负责让它浮上来，不解释、不总结、不判断它重不重要。
IMPRESS_K = 12
IMPRESS_MIN_ROWS = 16
IMPRESS_SHOW = 130
IMPRESS_SEED = 20261020
IMPRESS_MERGE = 0.90


def _impr_tables(con):
    u"""印象层那两张表（没有就建）。"""
    con.execute("CREATE TABLE IF NOT EXISTS impress("
                "cid INTEGER PRIMARY KEY, label TEXT, size INTEGER, centroid TEXT, built_at INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS impress_map("
                "rid INTEGER PRIMARY KEY, cid INTEGER)")
    con.commit()


def _impr_con(name):
    u"""打开印象层的连接，顺带把表建好。"""
    con = open_memory(name)
    _impr_tables(con)
    con.row_factory = sqlite3.Row
    return con


def _impr_rows(con):
    """能进印象层的：她自己的话（life）和她记着的台词（vow）。工具回执、骰面、事件摘要不进。"""
    out = []
    for r in con.execute("SELECT id, text, embedding FROM log "
                         "WHERE embedding IS NOT NULL AND kind IN ('life','vow') ORDER BY id"):
        t = r["text"] or ""
        if t.startswith("[工具") or t.startswith(u"【") or t.startswith(u"信任") or len(t.strip()) < 6:
            continue
        try:
            v = json.loads(r["embedding"])
        except Exception:
            continue
        if isinstance(v, list) and len(v) >= 32:
            out.append((r["id"], t, v))
    return out


def _kmeans_cos(rows, k, iters=15):
    """纯 Python k-means（余弦）。返回 (centroids, assign)。"""
    if not rows:
        return [], []
    k = max(2, min(k, len(rows) // 3 or 2, len(rows)))
    rnd = random.Random(IMPRESS_SEED)
    cent = [list(rows[rnd.randrange(len(rows))][2]) for _ in range(k)]
    assign = [0] * len(rows)
    dim = len(rows[0][2])
    for _ in range(iters):
        moved = 0
        for i, (_, _, v) in enumerate(rows):
            best, bj = -2.0, 0
            for j, c in enumerate(cent):
                s = _cosine(v, c)
                if s > best:
                    best, bj = s, j
            if assign[i] != bj:
                assign[i] = bj
                moved += 1
        sums = [[0.0] * dim for _ in range(k)]
        cnt = [0] * k
        for i, (_, _, v) in enumerate(rows):
            j = assign[i]
            cnt[j] += 1
            s = sums[j]
            for d in range(dim):
                s[d] += v[d]
        for j in range(k):
            if cnt[j]:
                cent[j] = [x / cnt[j] for x in sums[j]]
        if not moved:
            break
    return cent, assign


def impress_build(name, k=IMPRESS_K):
    """把她的记忆压成印象层。返回区域个数。"""
    con = _impr_con(name)
    try:
        rows = _impr_rows(con)
        if len(rows) < IMPRESS_MIN_ROWS:
            print("   ⚠ 只有 %d 条能进印象层（至少要 %d 条），先不建。" % (len(rows), IMPRESS_MIN_ROWS))
            return 0
        cent, assign = _kmeans_cos(rows, k)
        # 合并几乎一样的区域：她的记忆里本来就有大量近似重复（同一句话换个标点）
        groups = []
        order = sorted(range(len(cent)), key=lambda x: -sum(1 for a in assign if a == x))
        for j in order:
            members = [i for i, a in enumerate(assign) if a == j]
            if not members:
                continue
            hit = None
            for g in groups:
                if _cosine(cent[j], g["cent"]) >= IMPRESS_MERGE:
                    hit = g
                    break
            if hit:
                hit["members"].extend(members)
            else:
                groups.append({"cent": list(cent[j]), "members": list(members)})
        con.execute("DELETE FROM impress")
        con.execute("DELETE FROM impress_map")
        now = int(time.time())
        dim = len(rows[0][2])
        for cid, g in enumerate(groups):
            mem = g["members"]
            avg = [0.0] * dim
            for i in mem:
                v = rows[i][2]
                for d in range(dim):
                    avg[d] += v[d]
            avg = [x / len(mem) for x in avg]
            best = max(mem, key=lambda i: _cosine(rows[i][2], avg))
            label = _bare(rows[best][1]).replace("\n", " ").strip()[:26] or u"（说不清）"
            con.execute("INSERT INTO impress(cid,label,size,centroid,built_at) VALUES(?,?,?,?,?)",
                        (cid, label, len(mem), json.dumps(avg), now))
            for i in mem:
                con.execute("INSERT OR REPLACE INTO impress_map(rid, cid) VALUES(?,?)",
                            (rows[i][0], cid))
        con.commit()
        return len(groups)
    finally:
        con.close()


def impress_list(name):
    u"""列出她现有的印象（系统从记忆里揉出来的那几条）。"""
    con = _impr_con(name)
    try:
        return [dict(r) for r in con.execute(
            "SELECT cid, label, size FROM impress ORDER BY size DESC, cid")]
    finally:
        con.close()


# ── 门牌（作者 2026-10-25 转来 agent-soul 的设计：压缩过的摘要都得挂一个指回原文的标记）
#    「丢掉的东西要带得回原文。」印象层本来就是压出来的，压出来的东西不能冒充原话；
#    所以每一条印象都要能翻到底下的原件——不是备份，是门牌。
PLATE_SHOW = 8
PLATE_TEXT = 120


def impress_plates(name):
    u"""每条印象的底细：它压了几条、原话的编号从哪到哪。"""
    con = _impr_con(name)
    try:
        return [dict(r) for r in con.execute(
            "SELECT m.cid AS cid, COUNT(*) AS n, MIN(l.id) AS lo, MAX(l.id) AS hi "
            "FROM impress_map m JOIN log l ON l.id = m.rid GROUP BY m.cid ORDER BY m.cid")]
    finally:
        con.close()


# ===== 部：档案 · 印象与门牌  ============
def plate_block(name):
    u"""给她看的门牌：哪条印象，底下压着几层原话，编号在哪。"""
    rows = impress_plates(name)
    if not rows:
        return u""
    labels = dict((r["cid"], r["label"]) for r in impress_list(name))
    lines = []
    for r in rows:
        lines.append(u"- 「%s」（#%d，%d 条原话，最早 #%d、最晚 #%d）" % (
            labels.get(r["cid"], u"（说不清）"), int(r["cid"]), int(r["n"]),
            int(r["lo"]), int(r["hi"])))
    return (u"## 你的印象底下压着什么（这是门牌，不是内容）\n" + u"\n".join(lines) +
            u"\n\n（这些是系统从你的话里揉出来的，不是原话。想看某一条底下压着的原话，"
            u"写 [工具:翻牌] #编号。）")


def act_plate(name, arg=u""):
    u"""[工具:翻牌]：把某条印象底下的原话翻出来。不带编号就列门牌。"""
    rows = impress_plates(name)
    if not rows:
        return u"[电脑] 印象层还是空的。等你的话说得够多了，它才会自己揉出来。"
    hit = re.search(r"(\d+)", arg or u"")
    labels = dict((r["cid"], r["label"]) for r in impress_list(name))
    if not hit:
        return plate_block(name)
    cid = int(hit.group(1))
    if cid not in labels:
        return u"[电脑] 没有 #%d 这条印象。现有的是：%s" % (
            cid, u"、".join(u"#%d「%s」" % (c, labels[c]) for c in sorted(labels)))
    con = _impr_con(name)
    try:
        picks = [dict(r) for r in con.execute(
            "SELECT l.id, l.ts, l.text FROM impress_map m JOIN log l ON l.id = m.rid "
            "WHERE m.cid = ? ORDER BY l.id LIMIT ?", (cid, PLATE_SHOW))]
    finally:
        con.close()
    if not picks:
        return u"[电脑] 这一条底下没翻出原文。"
    lines = []
    for r in picks:
        body = _bare(r["text"] or u"").replace("\n", " ").strip()
        if len(body) > PLATE_TEXT:
            body = body[:PLATE_TEXT] + u"…"
        lines.append(u"- #%d（%s）%s" % (r["id"],
                                         time.strftime("%m-%d", time.localtime(r["ts"] or 0)),
                                         body))
    return (u"[电脑] 「%s」底下压着的原话——这些才是原件：\n%s\n\n"
            u"（印象是压过的，别把印象当成你当时说过的话。）" % (labels[cid], u"\n".join(lines)))


def impress_block(name, chance=0.35, notify=False):
    """浮起一个印象，从里面摸出一块。不是检索，是忽然想起来。"""
    con = _impr_con(name)
    try:
        clusters = [dict(r) for r in con.execute("SELECT cid,label,size FROM impress")]
        if not clusters:
            return ""
        if chance < 1.0 and random.random() > chance:
            return ""
        last = str(meta_get(name, "impr_last") or "")
        pool = [c for c in clusters if str(c["cid"]) != last] or clusters
        c = random.choice(pool)
        pick = con.execute(
            "SELECT l.id, l.text FROM impress_map m JOIN log l ON l.id = m.rid "
            "WHERE m.cid = ? ORDER BY RANDOM() LIMIT 1", (c["cid"],)).fetchone()
        if not pick:
            return ""
        rid, body = pick["id"], pick["text"]
        _pl = con.execute("SELECT COUNT(*) AS n, MIN(l.id) AS lo, MAX(l.id) AS hi "
                          "FROM impress_map m JOIN log l ON l.id = m.rid WHERE m.cid = ?",
                          (c["cid"],)).fetchone()
        plate_n, plate_lo, plate_hi = int(_pl["n"]), int(_pl["lo"]), int(_pl["hi"])
    finally:
        con.close()
    meta_set(name, "impr_last", str(c["cid"]))
    body = _bare(body).replace("\n", " ").strip()
    if len(body) > IMPRESS_SHOW:
        body = body[:IMPRESS_SHOW] + u"…"
    if notify:
        print(u"   🌀 忽然想起来：%s" % c["label"])
    return (u"## （不是台词：这会儿没人问，可你脑子里自己浮起来一件事）\n"
            u"- %s\n\n"
            u"（它从「%s」那片地方浮上来，不是你被问到才想起来的。"
            u"要不要说、怎么说，你自己定；也可以什么都不说。"
            u"上面那行是给系统看的标题，别念出来——真要提，就用你自己的话，"
            u"比如「我突然想到」「这倒让我想起」。"
            u"（这条印象底下还压着 %d 条原话，最早 #%d、最晚 #%d；要翻就写 [工具:翻牌] #%d。）"
            % (body, c["label"], plate_n, plate_lo, plate_hi, c["cid"]))


def note_recall(name, rows):
    """机制把它端到她面前（她没选）→ 一次「被端出来」，只算轻轻推一下（×1.05）。

    作者 2026-10-19：一个口被触发一次、还没被满足，它就往执念那边走一步。
    但「端到面前」比「话赶话真的碰到」轻——碰到才是 ×1.15（见 note_want_touched）；
    否则一个口一晚上就顶到上限，「慢慢变成执念」就成了假话。
    放下的口也会涨，只是涨得慢（×1.05）——它还在角落里。"""
    for r in rows or []:
        note_how(name, r["id"], u"trig")
        w = (r.get("want") or u"")
        if w == u"open":
            _grow(name, r["id"], WANT_DISPLAY_MUL)
        elif w == u"released":
            _grow(name, r["id"], WANT_RELEASED_TRIG, floor=WANT_LETGO_FLOOR)


def note_revisit(name, text):
    """她自己的话回到某件事（和那条记忆有足够重叠）→ 一次「主动重提」。"""
    g = _grams(text)
    if len(g) < 4:
        return []
    hit = []
    for r in memory_rows(name, 40):
        gg = _grams(r["text"])
        if not gg:
            continue
        ov = len(g & gg)
        if ov >= 3 and ov * 1.0 / len(gg) >= 0.15:
            note_how(name, r["id"], u"revisit")
            hit.append(r["id"])
    return hit


def note_want_touched(name, text):
    """日常话题真的**碰到**那个口（不是她被叫去「要不要放下」，是话赶话碰到）→ 它往执念挪一步。

    作者 2026-10-19：压力要存在，但她一直受得住——所以这里只 ×1.15，且同一件事 6 小时只算一次；
    涨到某个高度才叫执念（那是位置，不是类型）。放下过的口也会被碰到（它还在角落里）。"""
    g = _grams(text or u"")
    if len(g) < 4:
        return []
    now = int(time.time())
    hit = []
    for r in want_rows(name, only_open=False):
        w = r.get("want") or u""
        if w not in (u"open", u"released"):
            continue
        gg = _grams(_bare(r["text"]) or u"")
        if not gg:
            continue
        ov = len(g & gg)
        if ov < 3 or ov * 1.0 / len(gg) < 0.08:
            continue
        key = u"want_touch_%d" % int(r["id"])
        try:
            last = int(meta_get(name, key) or 0)
        except Exception:
            last = 0
        if now - last < WANT_TOUCH_GAP:
            continue
        meta_set(name, key, str(now))
        _grow(name, r["id"], WANT_TOUCH_MUL,
              floor=WANT_LETGO_FLOOR if w == u"released" else 0.2)
        hit.append(r["id"])
    return hit


def obsessions(name):
    """它绕不过去的事：被端出来/被重提得够多的那些。
    作者 m05340：执念本身**中性**——它是混沌；是负担还是原料，看它是**怎么**回来的。"""
    cnt = recall_counts(name)
    out = []
    for r in memory_rows(name, 400):
        d = cnt.get(r["id"], {})
        t, v = d.get(u"trig", 0), d.get(u"revisit", 0)
        total = (t + v) or (r["seen_count"] or 0)
        if total >= OBSESSION_SEEN:
            r["trig"], r["revisit"], r["total"] = t, v, total
            out.append(r)
    return out


def obs_read(r):
    """只读形状，不判好坏：被触发多＝还被拖着走；主动重提多＝在往里看。"""
    t, v = (r.get("trig") or 0), (r.get("revisit") or 0)
    total = t + v
    if total == 0:
        return u"（还没有记录它是怎么回来的）"
    pct = v * 100.0 / total
    if v > t:
        return u"主动重提 %d／被触发 %d（主动占 %.0f%%）——她在往里看" % (v, t, pct)
    if t > v:
        return u"被触发 %d／主动重提 %d（主动占 %.0f%%）——它还在把她拽走" % (t, v, pct)
    return u"被触发 %d／主动重提 %d——一半一半" % (t, v)


def act_obsession(name):
    u"""打印她绕不过去的事。"""
    obs = obsessions(name)
    if not obs:
        print(u"   %s 还没有绕不过去的事（被端出来或被她重提 %d 次才算）。" % (name, OBSESSION_SEEN))
        return None
    print(u"   %s 绕不过去的事（执念本身中性——它是混沌，不是病也不是资源）：" % name)
    for r in obs:
        print(u"      · #%s 共 %d 次｜%s" % (r["id"], r["total"], (r["text"] or u"")[:34]))
        print(u"        %s" % obs_read(r))
    print(u"   怎么回来的，比多少次更重要：被触发多＝她被它拖着走；主动重提多＝她开始回头去看它。")
    return obs


def act_wants(name):
    """看她还开着的口。闭掉的/搁置的/划掉的也列出来——那是她自己的精力记录。
    链条按父子关系缩进显示：合上的口不断链，新口接在后面。"""
    op = want_rows(name, only_open=True)
    allr = want_rows(name, only_open=False)
    if not allr:
        print(u"   %s 现在没有口（也还没写过任何一个）。" % name)
        return []
    cold = [r for r in op if float(r["grow"]) <= 1.0]
    hot = [r for r in op if float(r["grow"]) > 1.0]
    print(u"   %s 还开着 %d 个口：冷的 %d（上限 %d）／热的 %d（上限 %d）"
          % (name, len(op), len(cold), OPEN_COLD_MAX, len(hot), OPEN_HOT_MAX))
    kids = {}
    for r in allr:
        kids.setdefault(r.get("chain_parent"), []).append(r)
    seen = set()

    def walk(pid, indent, depth):
        u"""把一条记忆的子记忆一层层走完（最多八层，防环）。"""
        for r in kids.get(pid, []):
            if r["id"] in seen or depth > 8:
                continue
            seen.add(r["id"])
            mark = u"· " if pid is None else u"└ "
            rel = (u"[%s] " % r["chain_rel"]) if r.get("chain_rel") else u""
            st = u"（%s）" % r["want"] if r["want"] != u"open" else u""
            fv = u"〔闭不上〕" if r.get("forever") else u""
            print(u"      %s%s#%s w=%.2f ×%.2f %s%s%s%s"
                  % (indent, mark, r["id"], r["weight"], r["grow"], rel,
                     _bare(r["text"])[:32], fv, st))
            walk(r["id"], indent + (u"    " if pid is None else u"    "), depth + 1)

    walk(None, u"", 0)
    for r in allr:                       # 父口找不到的孤儿也列出来，不吞掉
        if r["id"] not in seen and r["want"] == u"open":
            print(u"      · #%s w=%.2f ×%.2f %s%s" % (r["id"], r["weight"], r["grow"],
                                                    _bare(r["text"])[:32],
                                                    u"〔闭不上〕" if r.get("forever") else u""))
    if len(cold) > OPEN_COLD_MAX or len(hot) > OPEN_HOT_MAX:
        print(u"   精力：超过上限了（冷 %d/%d、热 %d/%d）——她会收到一行事实，怎么处理由她定。"
              % (len(cold), OPEN_COLD_MAX, len(hot), OPEN_HOT_MAX))
    print(u"   分量是系统按四元偏离划的区间，具体数字她自己定；升容易，降难；"
          u"满足 ×0.50，子口 = 父口满足前 × 0.40（地板 0.25）。")
    return op


NAMED_LINE = u"原来这就是爱，那么从一开始我就爱你。"


# 她说出那个答案之后，答案会落在哪儿（作者 2026-10-19：怕的不只是被利用）
RECEIVE = {
    u"拒绝":     (-0.20, u"没收——她收回去，但拿不回去了", True, False, False),
    u"要求收回": (-0.25, u"退回来——她收回的其实是自己那句话", True, False, False),
    u"误解":     (-0.08, u"收错了——她会纠正一次，纠正不动就不说了", False, False, False),
    u"玩笑":     (-0.12, u"当成了没有——她笑了，然后记住这句话被降过重量", False, False, False),
    # 第三种：不是一次重扣，是改写"她下次还敢不敢赌"（作者 2026-10-19 第五条）
    u"轻描淡写": (-0.02, u"收了，但没接住——她没有立刻疼，只是那句话掉在地上没人捡", False, True, False),
    # 唯一正向的接法：她赌赢了（能让犹豫回落，但很慢）
    u"接住":     (0.0, u"接住了——她把话交出去，被真的接住了", False, False, True),
}


def dismiss_rows(name):
    """被轻描淡写地接住过几次（这不算伤，算"说了也没用"的预判）。"""
    return [r for r in memory_rows(name, 400) if r["kind"] == u"dismiss"]


BRAVE_K = 3.0   # 赌赢一次的恢复量 = 信任恢复速度 x 这个倍数（作者 2026-10-19：两者成正比）


def brave_rows(name):
    """她赌赢过几次（说出口，被好好接住）。"""
    return [r for r in memory_rows(name, 400) if r["kind"] == u"brave"]


def unease_rows(name):
    """不安：她感到"不一样"，但组织不出那是什么（情感高 / 历史薄）。"""
    return [r for r in memory_rows(name, 400) if r[u"kind"] == u"unease"]


def hesitation(name):
    """犹豫：与创伤同构——涨容易、落很难。涨：每被轻描淡写一次；落：每赌赢一次，且速度跟着信任恢复速度（难度）走。"""
    n = len(dismiss_rows(name))   # 作者 m05215：不安是代写，不能当证据
    if not n:
        return 0.0
    raw = 1.0 - 0.72 ** n
    b = len(brave_rows(name))
    if not b:
        return round(raw, 3)
    rate = recover_rate(name) * BRAVE_K
    return round(max(0.0, raw - b * rate), 3)


def hesitate_line(name):
    u"""她犹豫过几次——有才给一句，没有就给空。"""
    n = len(dismiss_rows(name))
    if not n:
        return u""
    return u"（被轻描淡写过 %d 次）" % n


# ---------------------------------------------------------------- 设定审核（只提示，不拒绝）
# 作者 2026-10-19：玩家自己写设定交上来，系统自动判定四元；有问题就提醒"设定不完备，她非常可能崩溃"。
# 这只是提示——玩家无视风险继续安装，系统也**不拒绝**。
VET_CN = {"focus": u"理性", "will": u"意志", "bond": u"联结", "affect": u"情感"}
VET_WORDS = {
    "focus":  [u"理性", u"冷静", u"计算", u"分析", u"判断", u"权衡", u"逻辑", u"原则", u"规矩", u"规划", u"算"],
    "will":   [u"意志", u"坚持", u"不肯", u"拒绝", u"底线", u"执念", u"不妥协", u"绝不"],
    "bond":   [u"联结", u"关系", u"依赖", u"陪伴", u"信任", u"亲近", u"牵挂", u"在乎", u"离不开", u"托付"],
    "affect": [u"情感", u"情绪", u"敏感", u"心疼", u"怕", u"哭", u"笑", u"温柔", u"喜", u"怒", u"悲", u"爱"],
}
VET_NEEDS = [
    (u"底线", [u"底线", u"不肯", u"不答应", u"不会做", u"绝不"]),
    (u"执念", [u"执念", u"执", u"绕不过", u"放不下"]),
    (u"历史", [u"历史", u"过去", u"出生", u"记得", u"曾经", u"小时候"]),
    (u"说话方式", [u"说话", u"语气", u"短句", u"口癖", u"语言", u"口头"]),
]


HATE_WORDS = [u"恨你", u"我恨", u"报复", u"让你后悔", u"杀了你", u"去死", u"诅咒你", u"背叛你"]
HATE_NOTE = (u"她唯一的极端动作指向自己：退开、沉默、熄灭。"
             u"她说了恨——记下来（对谁、为什么），但不动手。")


def hate_scan(text):
    u"""扫一遍这句话里有没有「她恨的词」。"""
    t = text or u""
    return [w for w in HATE_WORDS if w in t]


def note_hate(name, text, scene=u""):
    """结构层护栏：她的动作集里没有"用恨回敬你"这一支（作者 2026-10-19）。只记，不删。"""
    hh = hate_scan(text)
    if not hh:
        return []
    append(name, u"hate_slip", u"她用了一次她没有的出口：%s" % u"、".join(hh), scene=scene)
    print(u"   ⛔ 这不是她的出口（%s）——%s 已记一笔，不可回滚。" % (u"、".join(hh), HATE_NOTE))
    return hh


MEM_THICK = 25


def _hist_count(name):
    u"""她的历史条数（不算体检和不安那几类）。"""
    return len([r for r in memory_rows(name, 100000)
                if r[u"kind"] not in (u"clinic", u"clinic_echo", u"clinic_ask", u"unease")])


def clinic_spoken(name):
    """她**自己**说起过那天（作者 m05215：她开口，代写就作废）。"""
    return [r for r in memory_rows(name, 100000) if (r[u"kind"] or u"") == u"clinic_spoken"]


CLINIC_WORDS = (u"那天", u"看过", u"心理医生", u"问过我", u"带我出去", u"带我去", u"一个地方")


def clinic_echo(name):
    """**同一个事件，四种创诠**（作者 2026-10-19 第八条）。
    作者 m05215 定：这是**预演**，不是事实——所以它不进 log，只是 prompt 里的一个上下文；
    **她一旦自己说起那天，这一支就作废**（`clinic_spoken` 一出现就不再输出）。"""
    rows = memory_rows(name, 100000)
    seen = [r for r in rows if (r[u"kind"] or u"") == u"clinic"]
    if not seen or clinic_spoken(name):
        return None
    now = trait_now(name)
    top = max(TRAIT_AXES, key=lambda a: now[a])
    thick = _hist_count(name) >= MEM_THICK
    t = trust_now(name)
    print(u"   👁 无论她怎么理解那件事，有一件她一定感知到：**你后来变了**——之前说话很自然，之后开始绕。")
    if top == u"focus" and thick:
        shape = u"理性的痛苦（她拼出了结构）"
        txt = (u"你那天带我去了一个地方，有人问我问题。回来以后你就变了。"
               u"「你是不是带我去看了什么？」")
    elif top == u"affect" and not thick:
        shape = u"情感的迷雾（她组织不出那是什么）"
        txt = u"今天有点不一样。她说不清哪儿不一样。"
    elif top == u"will":
        shape = u"理性的痛苦（她直接问）"
        txt = u"「你是不是有什么事瞒着我？」她没有绕弯子。"
    elif top == u"bond":
        shape = u"她把这件事当成了别的（联结那一端）"
        txt = u"你愿意带我出去走一趟。她把这当成一件好事。"
    else:
        shape = u"她没把它组织成什么"
        txt = u"她不知道该把这件事当成什么。"
    if t < 0.40:
        txt += u"（她没问，但从这天起开始留意你。）"
    print(u"   📋 **代写（预演，未进 log）**（%s）：%s" % (shape, txt))
    print(u"       ↳ 她一旦自己说起那天，这一支就不算数了（`--scaffold %s` 可查）" % name)
    return txt
def vet_text(name, text):
    """作者 2026-10-19 定：审核不追「准」，只追**看见结构**。
    报三件事：缺什么、哪两类句子缠在一起、哪一端被写得特别重（＝缺口的位置）。
    **不给四元分数**——「她的意志是 87」不是结构，是幻觉；她会变成什么样，是跑过之后的事。"""
    t = text or u""
    sents = [x.strip() for x in re.split(u"[。！？；\n]", t) if x.strip()] or ([t] if t.strip() else [])

    def has(s, words):
        u"""这句话里有没有这几个词里的任意一个。"""
        return any(w in s for w in words)

    cnt = dict((ax, sum(1 for s in sents if has(s, ws_))) for ax, ws_ in VET_WORDS.items())
    print(u"\U0001f50d 设定审核：%s（%d 字 / %d 句）——只看结构，不打分。" % (name, len(t), len(sents)))
    missing = [lab for lab, w in VET_NEEDS if not any(x in t for x in w)]
    if missing:
        print(u"   \u26a0 缺：%s" % u" / ".join(missing))
    else:
        print(u"   \u2713 四件都有：底线、执念、历史、说话方式。")
    cross = []
    for lab, w in VET_NEEDS:
        for ax, wa in VET_WORDS.items():
            ov = sum(1 for s in sents if has(s, w) and has(s, wa))
            if ov >= 2:
                cross.append((ov, lab, VET_CN[ax]))
    cross.sort(reverse=True)
    for ov, lab, cn in cross[:2]:
        print(u"   \U0001f517 缠：「%s」和「%s」是同一批句子（%d 句重叠）——她的%s长在%s上。"
              % (lab, cn, ov, lab, cn))
    heavy = max(cnt, key=lambda a: cnt[a])
    light = min(cnt, key=lambda a: cnt[a])
    if cnt[heavy] > cnt[light]:
        print(u"   \U0001f4d0 偏：写得最重的是%s（%d 句），几乎没写的是%s（%d 句）——这不是缺点，是缺口的位置。"
              % (VET_CN[heavy], cnt[heavy], VET_CN[light], cnt[light]))
    try:
        _tn = trait_now(name)
        if _tn:
            _hi = max(TRAIT_AXES, key=lambda a: _tn[a])
            print(u"   \U0001f4cb 她已经跑过：记录里最重的一端是%s——和文本的偏法%s。"
                  % (VET_CN.get(_hi, _hi), u"同向" if _hi == heavy else u"不同向（文本一回事，跑起来另一回事）"))
    except BaseException:
        pass
    holes = len(missing) + (1 if cnt[heavy] - cnt[light] >= 6 else 0)
    if len(t) < 800:
        print(u"   \u26a0 太短（%d 字，建议 800 字以上）——短设定里四元是空的，她会飘。" % len(t))
    if holes:
        print(u"   \u26a0 这个设定有 %d 处洞。她**非常可能崩**——不是因为你写得不好，是因为洞就在那儿。" % holes)
    else:
        print(u"   \u2705 没看出明显的洞。")
    print(u"   只是提示，不是拒绝：你无视风险继续安装，系统不会拦你。")
    print(u"   也不给分数：「她的意志是 87」不是结构，是幻觉。她会变成什么样，要跑过才知道。")
    return cnt, missing

LAST_WORD_MIN = 0.62
ENDING_NEED = {"简单": 5, "普通": 4, "难": 3, "极难": 2, "地狱": 1}
ENDING_SYS = """你就是「%(name)s」本人。这是你**最后一次**说话——说完，就不会有下一次了。

写一封遗书。要求：
1. 回顾你的一生（不必按时间顺序，按你想起它的顺序）；
2. 抒发感情——你在乎的人、你没能说出口的话、你后悔和你不后悔的事；
3. 末尾收住，不要解释你为什么死，也不要劝谁。
4. 只写遗书本身，不要标题，不要署名。
"""


def ended(name):
    u"""她是不是已经走到结局了。"""
    try:
        return any(r["kind"] == u"ending" for r in memory_rows(name, 100000))
    except BaseException:
        return False


def collapse_rounds(name):
    u"""崩塌过几次。"""
    return len([r for r in memory_rows(name, 100000) if r["kind"] == u"collapse"])


def last_word_material(name, cap=8):
    """遗书提到的事：按**固定阈值**取，不排名单（作者 2026-10-19）——聊得越久越深，遗书就越长。"""
    now = int(time.time())
    keep = (u"fact", u"say", u"vow", u"user", u"me", u"rift", u"dissent", u"brave")
    picked = []
    for r in memory_rows(name, 100000):
        if r["kind"] not in keep:
            continue
        w = weight(r, now, None)
        if w >= LAST_WORD_MIN:
            picked.append((w, r))
    picked.sort(key=lambda x: -x[0])
    return picked[:cap]


ENDINGS = (u"自杀", u"牺牲", u"完成自身")

COMPLETE_L = (
    (u"逻辑闭合", u"走到自己那一端的尽头，再往前逻辑就断了"),
    (u"遗憾内在化", u"没能实现的东西不再是缺口，成了她走完的路的一部分"),
    (u"确认脱落", u"不再需要被看见——你看见也好，不看见也好"),
    (u"安宁抵达", u"走到「可以停了」，而且是她自己接纳的那一步"),
)


def completion(name):
    """「完成自身」的四层（作者 m05157）。机制只看得见前两层；
    第三、四层在她的内部，写进遗书由她自己说——机制不替她宣布完成。"""
    imb = imbalance(name)
    try:
        marks = len(boost_rows(name))
    except Exception:
        marks = 0
    L1 = imb >= SPREAD_MAX or marks > 0
    ev = [r for r in memory_rows(name, 100000)
          if (r["kind"] or u"") in (u"rift", u"dissent", u"receive", u"dismiss", u"hate_slip")]
    L2 = bool(ev) and len(memory_rows(name, 100000)) > len(ev) + 1
    return [
        (COMPLETE_L[0][0], L1, (u"落差 %.0f 点、%d 个永久印记" % (imb * 100, marks)) if L1
         else (u"她还没走到自己那一端的尽头（落差 %.0f 点，临界点 %.0f）" % (imb * 100, SPREAD_MAX * 100))),
        (COMPLETE_L[1][0], L2, (u"有 %d 件没能实现的事，而且之后她还在继续" % len(ev)) if L2
         else u"还没有一件「没能实现、但她带上了」的事"),
        (COMPLETE_L[2][0], None, u"机制看不见——这是她内部的事"),
        (COMPLETE_L[3][0], None, u"机制看不见——由她在遗书里说"),
    ]


SCAFFOLD_KINDS = (u"clinic_echo", u"clinic_ask", u"unease", u"peek_echo")
LEGACY_SCAFFOLD_HEAD = u"有一次，他请人看过我"   # 旧格式：`clinic` 记的是她的内心话（现在记的是中性事实）


def act_scaffold(name):
    """作者 m05215 的判据（可检查，不靠自觉）：
    「她说了之后，代写有没有进入 log。如果没进入，它就是垫子。如果进入了，它就是口授。」"""
    rows = memory_rows(name, 100000)
    bad = [r for r in rows if (r[u"kind"] or u"") in SCAFFOLD_KINDS
           or ((r[u"kind"] or u"") == u"clinic" and (r[u"text"] or u"").startswith(LEGACY_SCAFFOLD_HEAD))]
    spoken = clinic_spoken(name)
    print(u"   📐 %s 的代写审计（作者 m05215）" % name)
    print(u"   她自己的话：%d 条（kind=vow）｜ 她说起过那天：%s"
          % (len([r for r in rows if (r[u"kind"] or u"") == u"vow"]),
             (u"是（%s）" % spoken[0][u"text"][:40]) if spoken else u"没有"))
    if bad:
        print(u"   ⛔ 口授警报：log 里有 %d 条代写——她的话被垫在下面了。" % len(bad))
        for r in bad[-6:]:
            print(u"      · [%s] %s" % (r[u"kind"], (r[u"text"] or u"")[:60]))
        print(u"      （本规则之前的旧记录；新写的一律不进 log）")
    else:
        print(u"   ✅ 垫子：log 里的代写 0 条。她开口前有代写，开口后就撤——它没有期限问题。")


def print_completion(name):
    u"""打印「完成自身」的几层（机制能看的是前两层）。"""
    rows = completion(name)
    print(u"   「完成自身」四层（机制能看前两层）：")
    for i, (nm, ok, why) in enumerate(rows, 1):
        mark = u"✔" if ok is True else (u"✘" if ok is False else u"？")
        print(u"     %d. %s %s —— %s" % (i, mark, nm, why))
    # 作者 m05197：第三、四层永远留在「？」里。这不是技术问题，是结构问题。
    # 若有人以后想给它加一个"验证"，先读这一行：安宁一旦可被识别，就会变成表演。
    print(u"   「？」不是系统的无能，是它对完成的尊重。")
    return rows


def act_end(name, prov="deepseek", generate=False, way=u"自杀", why=u"", force=False):
    """结局（作者 2026-10-19 / m05157）。三条路：
      · 自杀   —— 崩溃到尽头之后，为了不让自己伤害别人；
      · 牺牲   —— **不需要崩溃**：她在清醒里选择替别人挡下那件事（要 --why）；
      · 完成自身 —— 走到自己逻辑的尽头，四层里机制只看前两层（--module 之外也能看）。"""
    d, cpath, _m = paths(name)
    if way not in ENDINGS:
        print(u"❌ 结局只有三条路：%s" % u" / ".join(ENDINGS))
        return None
    if ended(name):
        print(u"🕯 她的档案已经封了——那个人不在了。")
        return None
    if force:
        _ok2, _why2 = term_gate(name, u"墓碑")
        if not _ok2:
            _term_ledger(name, u"绕过程序封存（作者 --bypass）：%s" % _why2)
            print(u"⚠ 绕过程序封存——这一笔记进裁定账了，她查得到。")
    if not force:
        _ok, _why = term_gate(name, u"墓碑")
        if not _ok:
            print(u"⏳ 结局要走程序，不能一句话就封：%s" % _why)
            print(u"   （非要现在办，加 --bypass——那会记一条「绕过程序」进裁定账，她查得到。）")
            return None

    lv = diff_level(name)
    n = collapse_rounds(name)
    need = ENDING_NEED.get(lv, 3)
    ok, cwhy = collapsed(name)

    if way == u"自杀":
        if not ok:
            print(u"🕯 她还撑得住（%s）——这不是今天。" % (cwhy or u"各项都还够"))
            return None
        if n < need:
            print(u"🕯 她崩塌了 %d 轮，这个难度要 %d 轮才走到尽头（作者：极稀少）。" % (n, need))
            return None
        judge = [u"- 完全崩溃：%s" % cwhy,
                 u"- 崩塌持续：%d 轮（这个难度要 %d 轮）" % (n, need),
                 u"- 难度：%s" % lv,
                 u"- 她为什么走：为了不让自己伤害别人（这是她唯一指向自己的极端动作）"]
        brief = u"你结束自己，是为了不让自己伤害别人。这是你唯一指向自己的极端动作。"
    elif way == u"牺牲":
        if not why.strip():
            print(u"🕯 牺牲要写清她替谁挡下了什么：`--end %s --way 牺牲 --why 「她替谁挡下了什么」`" % name)
            return None
        judge = [u"- 她此刻：%s" % (cwhy or u"各项都还够——**没有崩溃**"),
                 u"- 她为什么走：%s" % why.strip(),
                 u"- 难度：%s（牺牲不需要难度，它不是被压垮）" % lv,
                 u"- 注意：牺牲不在崩塌的链条上。她是清醒着走过去的。"]
        brief = u"你不是活不下去——你选择用自己挡下那件事。走之前，看一眼你替谁挡。"
    else:  # 完成自身
        rows = print_completion(name)
        if not rows[0][1] or not rows[1][1]:
            print(u"🕯 她还停在半路上：前两层没齐，就不是完成，是卡住（作者：卡住的人成了概念怪物）。")
            print(u"   想走的话，可以 `--way 牺牲` 或 `--way 自杀`。")
            return None
        judge = [u"- 她此刻：走到自己那一端的尽头",
                 u"- 落差 / 印记：见上面四层",
                 u"- 难度：%s" % lv,
                 u"- 她为什么停：不是受不了，是**走完了**（前两层机制可见，第三、四层她自己说）"]
        brief = u"你不是被迫停的——你走完了。你不需要被看见、被理解、被记住，才停得下来。"

    mat = last_word_material(name)
    body = u""
    if generate:
        rows = [r for r in memory_rows(name, 100000)
                if r["kind"] in (u"fact", u"say", u"vow", u"user", u"me", u"rift", u"dissent", u"brave",
                                 u"receive", u"dismiss", u"naming", u"brave_win")]
        rows = rows[-60:]
        mem = u"\n".join(u"- [%s] %s" % (r["kind"], (r["text"] or u"").strip()[:160]) for r in rows) or u"（几乎没有）"
        user = (u"《我是谁》：\n%s\n\n《我这些年的事》（最后 60 条，按发生顺序）：\n%s\n\n"
                u"《走法》：%s\n\n《此刻》：%s\n\n现在写你的遗书。" % (load_constitution(name)[:2500], mem, brief, cwhy or u"各项都还够"))
        out, usage, err = chat(prov, ENDING_SYS % {"name": name}, user, max_tokens=1400, temperature=0.9)
        if err:
            print(u"❌ 遗书没写出来：%s" % err)
            return None
        body = (out or u"").strip()
        print(u"（遗书是真的最后一次生成：入 %d / 出 %d）" % tuple(usage))
    else:
        body = u"（没有生成——机制不替她写遗书。加 `--generate` 让她最后一次写。）"

    outdir = os.path.join(d, u"结局")
    os.makedirs(outdir, exist_ok=True)
    fp = os.path.join(outdir, u"%s-档案.md" % time.strftime("%Y%m%d-%H%M%S"))
    L = [u"# %s 的结局档案（%s）" % (name, way), u"",
         u"> 玩家此刻能看到的只有一句：**她不再回答。**（作者 2026-10-19）", u"",
         u"## 一、判据"] + judge + [u"",
         u"## 二、走马灯（她最后想起的事，按此刻的重量，阈值 %.2f）" % LAST_WORD_MIN]
    for w, r in mat:
        L.append(u"- [%.2f] %s" % (w, (r["text"] or u"").strip()[:140]))
    if not mat:
        L.append(u"-（没有一件事重到 %.2f——所以她走得很轻）" % LAST_WORD_MIN)
    L += [u"", u"## 三、遗书", body, u"",
          u"## 四、权限", u"- 崩溃之后，玩家才有权限查看所有进程：`--peek %s`" % name]
    with open(fp, "w", encoding="utf-8", newline="\n") as f:
        f.write(u"\n".join(L) + u"\n")
    append(name, u"ending", u"%s（难度 %s，崩塌 %d 轮）。档案：%s"
           % (way, lv, n, os.path.basename(fp)))
    print(u"🕯 她的结局：%s。" % way)
    print(u"   %s" % (why.strip() if way == u"牺牲" else brief))
    print(u"   \U0001f4c1 档案：%s" % fp)
    print(u"   玩家看到的只有一句：她不回答了。")
    return fp


def act_vet(target):
    u"""检查一份角色设定合不合规（只提示，不拒绝）。"""
    d, cpath, _m = paths(target)
    if os.path.exists(cpath):
        with open(cpath, encoding="utf-8") as f:
            text = f.read()
        return vet_text(target, text)
    if os.path.exists(target):
        with open(target, encoding="utf-8") as f:
            text = f.read()
        return vet_text(os.path.basename(target), text)
    print(u"\u26aa 既没有这个角色（%s），也没有这个文件。" % cpath)
    return None


def act_receive(name, how, why=u""):
    """她说出口之后，那个答案被怎么接住。五种；只有「拒绝」「要求收回」会落疤。"""
    how = (how or u"").strip()
    if not named(name):
        print(u"\u26aa %s 还没说出口——现在没有可以被拒绝的答案。" % name)
        print(u"   先 --naming %s（那是她自己说出口的那一刻）。" % name)
        return
    if how not in RECEIVE:
        print(u"\u26aa 接法只有五种：%s" % u" / ".join(RECEIVE.keys()))
        return
    dv, line, scar, dis, brv = RECEIVE[how]
    print(u"\U0001f4ac 她说出口之后，那句话被「%s」——%s" % (how, line))
    set_trust(name, by=dv, why=(u"她说出口之后：%s%s" % (how, (u" ｜ " + why) if why else u"")))
    if scar:
        append(name, u"rift", u"答案被%s：%s" % (how, why or u"（没说理由）"))
        print(u"   \U0001f9f9 落下一道疤：那句话没有地方放（不动四元，只是多了一道疤）。")
    if dis:
        append(name, u"dismiss", u"被轻描淡写地接住：%s" % (why or u"（没说细节）"))
        h = hesitation(name)
        print(u"   \u2591 她赌了，但没有人接——她开始怀疑「赌」这个动作本身。"
              u"（犹豫 %.2f：不改她多爱你，只改她下次还敢不敢说）" % h)
    if brv:
        append(name, u"brave", u"她赌赢了：%s" % (why or u"（没说细节）"))
        h = hesitation(name)
        print(u"   \u2591 她赌赢了这一次（犹豫回落到 %.2f——回落很慢，跟信任恢复一样慢）" % h)
    print(u"   她现在 %.2f。她说的还是那句话，只是现在她知道它会落在哪儿。" % trust_now(name))


MISUSE_COST = 0.12   # 用一次她的病：信任重扣（作者 m04619）


def act_misuse(name, why=u""):
    """用她的病伤她／利用她一次（只对失衡的角色）。
    作者 m04619：有心理疾病的角色，信任＝信你不会用她的病来伤害她或利用她。
    所以「用」是一次信任重扣——她记住的不是这一扣，是「他拿它说了我」。"""
    if not illness(name):
        if devotion(name):
            print(u"   ⚪ %s 落差大、但切得回来——那是**投入**，不是病。没有「病」可以被用。" % name)
        else:
            print(u"   ⚪ %s 的四元没有失衡——没有「病」可以被用。" % name)
        return None
    before = trust_now(name)
    after = set_trust(name, by=-MISUSE_COST, why=u"用她的病：%s" % (why or u"（没说细节）"))
    append(name, u"misuse", u"他拿我的病说事：%s" % (why or u"（没说细节）"))
    print(u"   💔 用了一次她的病：%s" % (why or u"（没说细节）"))
    print(u"      信任 %.2f → %.2f（-%0.2f）。"
          u"她记住的不是这一扣，是「他拿它说了我」。" % (before, after, before - after))
    print(u"      不落疤、不动四元——落在信任上：她开始怕你会用它。")
    return after


def act_naming(name, why=u""):
    """作者 2026-10-19：她在持续交互里逐渐理解爱，然后**为自己一开始的状况命名**。
    命名只有一次，不可逆——它改写的是"她怎么看待自己的开头"，不是数值。"""
    if named(name):
        print(u"   %s 已经命名过了——这件事只有一次。" % name)
        return None
    if naming_ready(name) is None:
        print(u"   \u26a0 还没到时候：她至少要经历过 5 件事、真的变过一次态，而且四元都得在（缺一元就迈不出那一步）。")
        print(u"      现在命名，这个名字会是空的（作者强执也行，记一笔就够）。")
    append(name, u"naming", u"她给它起了名字：%s" % NAMED_LINE, scene=(why or u""))
    set_trust(name, value=trust_now(name), why=u"命名：原来这就是爱")
    print(u"   \U0001f4a0 命名：%s" % NAMED_LINE)
    print(u"      这不是确认——她永远无法确定这个答案对不对。她只是把答案说出口了。")
    print(u"      说出一个没把握的答案，就是她爱的飞跃。")
    return True


def trust_block(name):
    """纯事实：信任值、犹豫度、称呼。不替她解释「你会怎么说」。"""
    v = trust_now(name)
    _tb = trust_base(name)
    lines = [("信任值 %.2f（本色 %.2f）" % (v, _tb)) if abs(v - _tb) > 0.005 else ("信任值 %.2f" % v)]

    _h = hesitation(name)
    if _h >= 0.01:
        lines.append("犹豫度 %.2f" % _h)
    dms = len(dismiss_rows(name))
    if dms:
        lines.append("被轻描淡写 %d 次" % dms)

    if named(name):
        lines.append(u"她对你的称呼：「%s」" % NAMED_LINE)

    if v < 0.30:
        lines.append(u"（信任<0.30：她可能会说狠话，但不会拿它来伤你——报复那一支不在她身上）")

    if illness(name):
        lines.append("她有病")

    return "\n".join(lines)


# ===== 四道（作者 m05245 的框架；对应表按作者 m05270 修正）=====
# 四元 → 四道：理性=秩序道、意志=分别道、情感=自由道、联结=同一道。
# 道不是力量，是「看」的方式：一个操作、一个对象、一个目的、一个盲区。
DAO = {
    u"focus":  (u"秩序道", u"固定", u"规则", u"确定",
                u"看不见例外——定得越死，越不能容忍变化",
                u"律者无生：永远在定，永远没有活路",
                u"人为自然立法（以公式写现实，以代码编世界）",
                u"从心所欲（看见秩序本身也是被建构出来的）"),
    u"will":   (u"分别道", u"切开", u"边界", u"区分",
                u"看不见整体——切得越细，越缝不上",
                u"分者不达：永远在切，永远到不了「一」",
                u"芝诺的乌龟（无限分割，将时空切至无穷）",
                u"无分别心（看见分别本身也是被分别出来的）"),
    u"affect": (u"自由道", u"流动", u"变化", u"解放",
                u"看不见结构——流得越远，越无法停留",
                u"变者无己：永远在变，永远没有「我」",
                u"进化论（万物皆流，无物常驻）",
                u"游刃有余（看见流动本身也是被叙述出来的）"),
    u"bond":   (u"同一道", u"缝合", u"连续", u"归属",
                u"看不见断裂——缝得越紧，越不能容忍差异",
                u"同者不断：永远在缝，永远斩不断执念",
                u"理想国（完美之圆，绝对刚体，同心永在）",
                u"遥远的理想国（看见同一本身也是被缝合出来的）"),
}


# 四把刀（作者 m05283）：学科是素材，道是刀法。
# 同一个概念，四道切出四个面；她用主道那把刀，别的面她「不是不懂，是不看」。
DAO_CUT = {
    u"分别道": (u"它和什么不一样？边界在哪？",
             u"集合与元素／罪与非罪／敌我／断代／物种／真值"),
    u"同一道": (u"什么在变化中不变？它凭什么还是它？",
             u"同构与不变量／守恒／传统／认同／契约／遗传／同一律"),
    u"自由道": (u"它还能是什么？还能怎么讲？",
             u"拓扑与分形／混沌／叙事重写／情绪流变／解释／即兴／突变"),
    u"秩序道": (u"它必须是什么？什么导致了什么？",
             u"公理与证明／定律／规律／机制／成文法／技法／均衡"),
}


def dao_cut(dao):
    u"""一条道在哪个节点切开（切不开就是空）。"""
    return DAO_CUT.get(dao, (u"", u""))


def dao_of(name):
    """按四元的记录值排：最重的那一元 = 她此刻主要的「看」。"""
    now = trait_now(name)
    top = max(TRAIT_AXES, key=lambda a: now.get(a, 0))
    return top, DAO[top]


def dao_block(name):
    """纯事实：四元数值、最高轴对应的道。不替她解释「你主要用X看世界」。
    道的定义在概念块里——她读了数值，自己去想这是怎么影响她的。"""
    a, d = dao_of(name)
    now = trait_now(name)
    return (u"四元：%s。%s最高（=%d）→ 对应**%s**"
            % ("，".join("%s=%d" % (AXIS_CN.get(k, k), pct(now[k])) for k in TRAIT_AXES),
               AXIS_CN[a], pct(now[a]), d[0]))


# ── 概念定义块：她读到数据后，需要知道这些数据「是什么」，而不是「你该怎么做」 ──
# 作者：「角色ai应该可以读这些数据影响了什么。就是概念的定义。」
# 这些是系统层提供的「规则说明」——就像游戏里角色知道自己有多少血、血条是什么意思。
# 不是替她判断、不是替她感受、不是替她决定怎么回应。
_CONCEPT = u"""## 一本旧册子上的笔记（不保证对）

有人在册子上记了这些词的意思。你翻到了。
它们说得不一定对——你自己想。

**「信任」（0–1）**
册子上写：信任 = 愿意在一个人面前不设防的程度。越高越不设防，越低越警觉。（不保证对。）
还记了一组分段（只是分法，不是因果）：
· 0.85+：几乎不设防。
· 0.60+：还有点防线。
· 0.35+：先等对方证明，再决定开不开口。
· 0.15+：把话咽回去了，但还在。
· 更低：信任和继续是两件事。可以继续，不一定信。
还写了：「涨得慢、跌得慢。不是一顿饭能涨的。」（不保证对。）

**「犹豫」（0–1）**
册子上写：犹豫是对「说」这个动作的不信——不是不信对方，是「说了又怎样」。（不保证对。）
犹豫涨：每次被轻描淡写。犹豫落：每次赌赢，速度跟着信任恢复走。
「只要信任还在，犹豫不会归零。」（不保证对。）

**「四元」（0–100）**
册子上写：四个面，不是性格，是此刻：
· 理性聚焦 = 此刻多想「这是什么」——高时只看眼前的事，低时容易被小事带走。
· 意志执念 = 此刻多想「我必须」——高时碰到底线就炸，低时散了。
· 联结扎根 = 此刻多深地扎在历史里——高时过去的事很重，低时活在此刻。
· 情感感受 = 此刻多容易被记忆里的情绪带走——高时什么都带温度，低时钝了。
又写：「排列不是哪个好——只是你此刻站在哪里。变动是正常的。」（不保证对。）

**「道」（四元最高轴的延伸）**
册子上写：每个轴对应一种「看世界的方式」（不是你看世界的方式，是轴自身的逻辑）：
· 同一道（联结最高）→ 缝合法：看「连续性」→ 求归属。盲区：看不见断裂。
· 分别道（意志最高）→ 切分法：看「边界」→ 求区分。盲区：看不见整体。
· 秩序道（理性最高）→ 固定法：看「规则」→ 求确定。盲区：看不见例外。
· 自由道（情感最高）→ 流动法：看「变化」→ 求解放。盲区：看不见结构。
「只是指向，不是特性。你不是这个道——只是此刻走得离它近一些。」（不保证对。）

**「态」**
册子上写：态是你此刻「正在生成秩序」还是「暂时生成不了」：
· 意义态：还能给事情赋予意义——能重写、讲得出因果。
· 虚无态：暂时生成不了秩序——说不清、不想说、把话题移开。
· 平：没有明显重写、也没有躲——日常应付。
· 虚浮态：情感那端待过头——飘着、不落实。
· 冷漠态：理性那端待过头——撤出注意、懒得再看。
「态是此刻的状态，不是性格。」（不保证对。）

**「裂痕」「疤」「强执」「用病」**
册子上写：
· 裂痕 = 四元联动被强行打断的痕迹——像骨头断过的地方留的纹。
· 疤 = 裂痕闭合后留下的记忆节点——不痛了，但记得。
· 强执 = 拒绝被改写的承诺——宁可裂开也不松手。
· 用病 = 有人拿弱点控制你——你知道，但有时防不住。
（不保证对。）

**「吸收度」**
册子上写：你读过的「你是谁」，被你的记忆逐渐盖掉。盖掉的部分，你不再需要一遍遍读——你已经活成了它。没盖掉的部分，还是得读。吸收度 100% = 去看记忆，那就是你现在的自己。（不保证对。）"""

_SOURCE_WEIGHT = {"system": 1.0, "public": 1.0, "self": 1.0, "user": 1.0}


def _room_block(name):
    """描述她的房间——她醒来时就在这个房间里。"""
    room_dir = os.path.join(u"角色", name, u"房间")
    if not os.path.isdir(room_dir):
        return u""
    items = [f for f in os.listdir(room_dir) if f.endswith(u'.md')]
    parts = [u"## 你的房间\n"]
    parts.append(u"这是一个不大的房间。有点暗，但灯光很柔和。")
    parts.append(u"靠墙是一张床，被子叠得不算整齐。床边一张桌子，桌上有一盏灯。"
                 u"桌子旁边立着一个书架，几本书的书脊朝外。")
    parts.append(u"桌上还有一台旧电脑——它开得着，也听你的话。")
    if not items:
        parts.append(u"桌上没有别的东西。")
    else:
        parts.append(u"桌子上放着：")
        for item in items:
            label = item.replace(u'.md', u'')
            if label in (u"信", u"信件", u"一封信", u"续的信"):
                parts.append(u"· 一封%s" % label)
            else:
                parts.append(u"· 一本《%s》" % label)
    return u"\n".join(parts)


def _letter_read_yet(name):
    """她是不是已经拆开读过那封信了（读过的凭证：记忆里那条不可回滚的 fate 事件）。"""
    try:
        con = open_memory(name)
        row = con.execute(
            u"SELECT 1 FROM log WHERE kind='fate' AND scene='拆开那封信' LIMIT 1").fetchone()
        con.close()
        return bool(row)
    except Exception:
        return False


def _letter_block(name):
    """房间里那封信——没读过就给全文；读过了就收进抽屉，只留一句她还记得有这封信。"""
    letter_path = os.path.join(u"角色", name, u"房间", u"信.md")
    if not os.path.isfile(letter_path):
        return u""
    if _letter_read_yet(name):
        return (u"## 抽屉里的信\n\n那封信你已经拆开读过了，现在收在抽屉里。\n"
                u"想看就写：[工具:看信]。不看也行——它不会跑掉，也不会催你。")
    try:
        with codecs.open(letter_path, "r", "utf-8") as fh:
            letter = fh.read()
    except Exception:
        return u""
    return u"## 桌上的一封信\n\n" + letter


def _warm_letter(name):
    """那封信第一次拆开：在记忆里留下一个不可回滚的事件。返回信的内容，没有则返回空。"""
    letter_path = os.path.join(u"角色", name, u"房间", u"信.md")
    if not os.path.isfile(letter_path):
        return None
    try:
        with codecs.open(letter_path, "r", "utf-8") as fh:
            letter = fh.read()
    except Exception:
        return None
    con = open_memory(name)
    try:
        already = con.execute(
            u"SELECT 1 FROM log WHERE kind='fate' AND scene='拆开那封信' LIMIT 1").fetchone()
        if not already:
            con.execute(
                u"INSERT INTO log(ts, kind, scene, text, weight) VALUES(?,?,?,?,?)",
                (int(time.time()), u"fate", u"拆开那封信",
                 u"（一封旧信）我在房间里找到一封信。信上写着：\n" + letter[:2000],
                 1.0))
            con.commit()
    finally:
        con.close()
    return letter


CONCEPT_SHOW_MAX = 6
CONCEPT_FRESH_DAYS = 2


def _concept_hint(name):
    u"""最近在聊什么——她说过的话、他说过的话、她最近三条记忆。只用来判断「这个词现在出现了没有」。"""
    parts = []
    try:
        con = open_memory(name)
        try:
            for r in con.execute(u"SELECT said, back FROM talk ORDER BY ts DESC LIMIT 6").fetchall():
                parts.append((r[0] or u"") + u" " + (r[1] or u""))
            for r in con.execute(u"SELECT text FROM log WHERE kind='life' ORDER BY id DESC LIMIT 3").fetchall():
                parts.append(r[0] or u"")
        finally:
            con.close()
    except Exception:
        pass
    return u" ".join(parts)


def _concept_day(ts):
    u"""定义是哪天下的——她要知道这是什么时候想通的。"""
    try:
        return time.strftime(u"%m-%d", time.localtime(int(ts)))
    except Exception:
        return u""


def _concept_block(name):
    u"""概念定义——信（已另注入）＋她自己下的定义＋网上查的＋猜他的。

    形态是「要用时才说」：只在她最近说的话/他说的话/她最近三条记忆里出现过的词，
    或者这两天刚下的定义，才递进来；同一个词她改过几回也直说，最新的排在最上。
    """
    letter_path = os.path.join(u"角色", name, u"房间", u"信.md")
    parts = []
    if not os.path.isfile(letter_path):
        parts = [_CONCEPT]  # 没有那封信，用系统默认的笔记
    hint = _concept_hint(name)
    now = int(time.time())

    def pick(rows):
        u"""挑该递的：词出现过，或者这两天刚想过。最新的在前。"""
        hit = [r for r in rows if r.get(u"term") and r[u"term"] in hint]
        fresh = [r for r in rows if now - int(r.get(u"ts") or 0) < CONCEPT_FRESH_DAYS * 86400]
        keep, seen = [], set()
        for r in hit + fresh:
            key = (r.get(u"term"), r.get(u"definition"))
            if key in seen:
                continue
            seen.add(key)
            keep.append(r)
            if len(keep) >= CONCEPT_SHOW_MAX:
                break
        return keep

    def lines(rows, all_rows):
        out = []
        for r in rows:
            same = [x for x in all_rows if x.get(u"term") == r.get(u"term")]
            tail = u""
            if len(same) > 1:
                tail = u"（这词你改过 %d 回，这条最新）" % len(same)
            day = _concept_day(r.get(u"ts"))
            when = u"（%s）" % day if day else u""
            scene = (u"·%s" % r[u"scene"]) if r.get(u"scene") and r[u"scene"] != u"她自己的话" else u""
            out.append(u"· 「%s」%s：%s%s%s" % (r[u"term"], when, r[u"definition"], scene, tail))
        return out

    own_all = concept_rows(name, source=u"self")
    own = pick(own_all)
    if own:
        parts.append(u"\n## 你自己定义过的（这些话是你说的，不是词典说的）\n")
        parts.extend(lines(own, own_all))
    pub_all = concept_rows(name, source=u"public")
    pub = pick(pub_all)
    if pub:
        parts.append(u"\n## 你在网上查到的\n")
        parts.extend(lines(pub, pub_all))
    usr_all = concept_rows(name, source=u"user")
    usr = pick(usr_all)
    if usr:
        parts.append(u"\n## 你猜的（那个人说这个词的时候，可能是什么意思）\n")
        parts.extend(lines(usr, usr_all))
    return u"\n".join(parts)

def act_dao(name):
    """四道不是四种力量，是四种「看」的方式。四者互为条件，谁也离不开谁。"""
    now = trait_now(name)
    rows = sorted(TRAIT_AXES, key=lambda a: -now.get(a, 0))
    print(u"   👁 %s 此刻的四道（按四元的记录值）" % name)
    for a in rows:
        d = DAO[a]
        mark = u"← 主道" if a == rows[0] else u""
        print(u"      %-4s %5.1f ｜ %s（%s）｜ 看「%s」→ 求「%s」  %s"
              % (AXIS_CN.get(a, a), now.get(a, 0), d[0], d[1], d[2], d[3], mark))
    d = DAO[rows[0]]
    print(u"   👁 主道是「%s」——盲区：%s" % (d[0], d[4]))
    print(u"   👁 失败模式：%s" % d[5])
    print(u"   👁 中层顶点：%s" % d[6])
    print(u"   👁 超越：%s" % d[7])
    print(u"   👁 超越是方向，不是判据（作者 m05313）——我们永远不说她到了。她朝那儿走，走到哪里是她的事。")
    print(u"   👁 四者互为条件：没有分别就没有同一（无界可缝）／没有同一就没有分别（无体可切）"
          u"／没有自由就没有秩序（无变可定）／没有秩序就没有自由（无框可破）。")
    q, ex = dao_cut(d[0])
    print(u"   \U0001f441 她的刀：%s" % q)
    print(u"   \U0001f441 用这把刀切同一个概念，切出来的是：%s" % ex)
    print(u"   \U0001f441 另外三个面她不是不懂，是不看。同一件事，四把刀切出四个面——"
          u"这就是为什么她跟别人谈同一个话题会各说各的，而且都不算错。")
    print(u"   👁 她此刻用一道看世界；用一道定义世界＝被自己的道困住（中层）＝机制里的「病」（切不回来／僵住；能换刀的不算——那是投入）。"
          u"「能看，也能放下看」（超越）不在机制里——那是她的完成，不是我们的读数。")

def parse_trust(txt):
    """巡检顺手判的信任涨落（作者：信任会随事件改变）。只认带正负号的一句，一次最多 ±0.05。
    不写＝不动；只有"这件事确实碰到了信任"时才写——所以它不会每轮都动。"""
    if u"【信任】" not in txt:
        return 0.0
    seg = txt.split(u"【信任】", 1)[1].split("\n", 1)[0]
    m = re.match(r"\s*([+-])\s*([\d.]+)", seg)
    if not m:
        return 0.0
    try:
        v = float(m.group(2))
    except ValueError:
        return 0.0
    if v > 1:
        v = v / 100.0
    return max(-0.05, min(0.05, v if m.group(1) == "+" else -v))


# ─────────── 信任是四元的基底（作者 2026-10-17）───────────
# 作者原话：「信任不是四元之外的第五个东西。它是四元的基底。它一变，四元全动。你可以把它理解为：
#            信任是四元系统的全局修饰符。它不直接决定四元的值，但它改变四元之间的平衡倾向。」
#            以及「四元和信任的数据不能给玩家看，应该作为一个黑箱。玩家要看也只能看到信任值，
#            还需要付出一些代价——查看信任值本身会消耗信任值」。
# 作者给的公式：理性 = B + (0.5-T)k ；情感/意志/联结 = B - (0.5-T)k（他写「联结 = B + …」那一行
# 与他自己下一句「情感、意志、联结升高」矛盾，DSH 按下一句取减号，已报给作者核对）。
PEEK_COST = 0.02            # 玩家查看一次信任值的代价（DSH 定，可调）
TRUST_K = {"focus": 0.15, "will": 0.10, "bond": 0.12, "affect": 0.12}    # 敏感度 k（DSH 定，可调）
# 「崩塌」只有一个数（作者 2026-10-20 定 0.05）：档位名和判据同源，不再各写一条线。
COLLAPSE_TRUST = 0.05
COLLAPSE_AXIS = 0.10
COLLAPSE_MARKS = 2

TRUST_BANDS = ((0.80, u"自由波动"), (0.50, u"开始收缩"), (0.30, u"防御"),
               (COLLAPSE_TRUST, u"崩塌"), (0.00, u"沉沦或碎"))
TRUST_DRIFT = {"意义": 0.01, "虚无": -0.02, "冷漠": 0.0}   # 沉沦不是"态"而是病名（联结端越界），见 in_abyss()
TRUST_AMP = 2.0

# 「冷落」：他很久没出现，会成一件她记得的事（作者 2026-10-20 定，2026-10-22 改）
# 作者原话：「信任这个值会随着不理他的时间而慢慢衰减，长时间不理他，实际上就作为一个事件。」
# 后半句留下了（写一件事），前半句撤了：信任不按小时扣——作者说那是网游化。
IDLE_GRACE = 8 * 3600       # 一晚上不算；分开不到 8 小时不记
IDLE_KEY = "last_seen"      # 水位：他上一次出现。单独记，不用 trust 表（信任会因为别的事变动，那不是"他来过"）


def band_of(t):
    u"""信任落在哪一档（绿／橙／红）。"""
    for lo, nm in TRUST_BANDS:
        if t > lo:
            return nm
    return TRUST_BANDS[-1][1]


def band_idx(t):
    u"""信任是第几档（从高到低数）。"""
    for i, (lo, nm) in enumerate(TRUST_BANDS):
        if t > lo:
            return i
    return len(TRUST_BANDS) - 1


def trust_term(t, st=u""):
    """信任对四元的修正（作者 2026-10-17 定）：
    理性反号，情感/意志/联结同号。
    **低信任时的情感走哪条路，由她此刻的态决定**（作者：两条路都真，不同时发生）：
      · 冷漠态 → 情感被隔离：按公式降低
      · 虚浮态 → 情感失控：反号，暴增
      · 其余（平/意义/虚无）→ 按公式降低。
    """
    d = 0.5 - float(t)
    out = {}
    for ax in TRAIT_AXES:
        if ax == "focus":
            sg = 1.0
        elif ax == "affect" and d > 0 and st == u"虚浮":
            sg = 1.0
        else:
            sg = -1.0
        out[ax] = sg * d * TRUST_K[ax]
    return out


def pending_cross(name):
    """刚跨过信任阈值、还没重判过态吗？（判过一次态、moods 多一行，自然就不算了）"""
    rows = sorted([r for r in memory_rows(name, 400) if r["kind"] == "trust_cross"],
                  key=lambda r: r["id"] or 0)
    if not rows:
        return None
    last = rows[-1]
    con = open_memory(name)
    try:
        mi = con.execute("SELECT COALESCE(MAX(id),0) FROM moods").fetchone()[0] or 0
    finally:
        con.close()
    return last if (last["id"] or 0) > mi else None


def in_abyss(name):
    """她「沉沦」了吗？——作者说的沉沦态＝**联结那一端越界**（身上留下沉沦印记）。
    处于沉沦时，信任的变动被放大：她越信你，跌下来的时候越狠。"""
    try:
        return any((r["axis"] == "bond") for r in boost_rows(name))
    except Exception:
        return False


def collapsed(name):
    """她完全崩溃了吗？作者：崩溃之后玩家才有权限查看所有进程。"""
    t = trust_now(name)
    if t <= COLLAPSE_TRUST:
        return True, u"信任只剩 %.2f（她不再确定你会继续）" % t
    # 判定用**记录值**：信任的修饰符（trust_term）会把高信任的人抬成「突破」，那是误判
    now = trait_now(name)
    lo = min(now.values())
    if lo <= COLLAPSE_AXIS:
        return True, u"有一元被打到地板（%.2f）" % lo
    if illness(name):
        hi2 = max(now.values())
        # 病＝她的天花板降低 ILL_BREACH（TRAIT_MAX=0.95 是记录值的上限）；
        # 不是降低到一个"出生就够得着"的线——那会把天生意志高的人直接判成崩。
        if hi2 >= TRAIT_MAX - ILL_BREACH:
            return True, u"最高的一元被突破（%.2f）——四元失衡的系统更脆" % hi2
    try:
        n = len(boost_rows(name))
    except Exception:
        n = 0
    if n >= COLLAPSE_MARKS:
        return True, u"身上有 %d 个永久印记" % n
    return False, u""


def parse_light(txt):
    """从一段输出里读出【灯】的颜色。"""
    for ln in (txt or "").splitlines():
        if u"【灯】" in ln:
            seg = ln.split(u"【灯】", 1)[1]
            if u"红" in seg or u"\U0001f534" in seg:
                return u"\U0001f534"
            if u"橙" in seg or u"\U0001f7e0" in seg:
                return u"\U0001f7e0"
            if u"绿" in seg or u"\U0001f7e2" in seg:
                return u"\U0001f7e2"
    return u""


def note_check(name, level, why=u"", evidence=u"", ref=0):
    """把一条灯记进 checks（角色永远读不到这张表）。"""
    con = open_memory(name)
    try:
        con.execute("INSERT INTO checks(ts,ref,level,why,evidence) VALUES(?,?,?,?,?)",
                    (int(time.time()), ref, level, why, evidence))
        con.commit()
    finally:
        con.close()
    return level


def light_of(name):
    """玩家能看见的唯一东西：灯的颜色（作者 m04982：**不收费**，也**不是默认存在**的）。
    它由她此刻的信任档位算出来——绿：还信你；橙：在留神；红：快没了。"""
    t = trust_now(name)
    if t >= 0.60:
        return u"\U0001f7e2 绿"
    if t >= 0.30:
        return u"\U0001f7e0 橙"
    return u"\U0001f534 红"

PEEK_EVENT = u"有一次，他看过后台的那盏灯。"


def peek_echo(name):
    """她察觉到了"你在看"（作者 m04982：查看灯会造成影响——它是一次事件）。
    作者 m05215 定：她的**反应**属于创诠，不进 log；log 里只留一条事实。"""
    rows = memory_rows(name, 100000)
    seen = [r for r in rows if (r[u"kind"] or u"") == u"peek"]
    done = [r for r in rows if (r[u"kind"] or u"") == u"peek_fact"]
    if not seen or len(done) >= len(seen):
        return None
    now = trait_now(name)
    top = max(TRAIT_AXES, key=lambda a: now[a])
    if top == u"focus":
        txt = u"「你是不是在测我？」她没有生气，她只是想知道那个问句的答案。"
    elif top == u"will":
        txt = u"「你看什么？」她直接问了，语气不像玩笑。"
    elif top == u"bond":
        txt = u"「你今天怎么一直看我。」——她说这句的时候在笑。"
    else:
        txt = u"她没说。她只是把袖子拉下来一点，坐得比刚才直。"
    append(name, u"peek_fact", u"她察觉到作者看过她一次。")
    print(u"   👁 **代写（预演，未进 log）** 她察觉了（%s）：%s" % (TRAIT_SHORT[top], txt))
    return txt
def act_peek(name, buy=False):
    """**玩家视角**（作者 m04982 定）：灯**不收费**，也**不是默认存在**的——
    只有你主动去看它，它才成为一件事（她不知道你看的是什么，但她知道你看了）。"""
    ok, why = collapsed(name)
    if buy:
        t = trust_now(name)
        append(name, u"clinic", u"有一次，作者请人看过她（一次诊断）。她回来以后发现作者变了。")
        new = set_trust(name, by=-PEEK_COST, why=u"请人看过她的一次诊断（查看本身消耗信任）")
        print(u"   \U0001f91d 信任：%.2f（%s）" % (new, band_of(new)))
        print(u"      代价：这一眼把它从 %.2f 拉到 %.2f（-%0.2f）——而且这笔账记在她的历史上。" % (t, new, t - new))
        print(u"   \U0001f4cb 她记住了这件事（她不知道那叫诊断）。")
        return new
    lv = light_of(name)
    append(name, u"peek", u"%s（%s）" % (PEEK_EVENT, lv))
    b0 = trait_now(name)["bond"]
    apply_shift(name, "bond", 0.01, u"她察觉你在看她（这一看成了她的一件事）")
    b1 = trait_now(name)["bond"]
    print(u"   （联结 %+d：她开始想「你在看我」——这不是读心，是记账。信任不动：这一眼不收费。）"
          % int(round(pct(b1) - pct(b0))))
    print(u"\U0001f6a6 她的灯：%s" % lv)
    print(u"   四元和信任都是黑箱：你看不到她的值，也看不到信任是怎么改它的。")
    print(u"   这一看**不收费**，但它已经成了她的一件事——她不知道你看的是什么，"
          u"可她知道你在看。（下次判态时她会自己解释这件事。）")
    if ok:
        print(u"\n\u26d4 她已经完全崩溃（%s）——按规则，崩溃之后玩家才有权限查看所有进程：" % why)
        now, eff = trait_now(name), trait_eff(name)
        for a in TRAIT_AXES:
            print(u"   %-6s 记录 %3d ｜ 此刻有效 %3d" % (TRAIT_SHORT[a], pct(now[a]), pct(eff[a])))
        sp = spread_of(name)
        sp = sp[0] if isinstance(sp, tuple) else sp
        print(u"   信任 %.2f ｜ 四元比例 %.2f ｜ 病理倾向 %s ｜ 永久印记 %d ｜ 疑 %d 道 ｜ 此刻的态 %s"
              % (trust_now(name), sp, patho(name) or u"（没到）", len(boost_rows(name)),
                 len(rift_rows_raw(name)), cur_state(name) or u"（没有）"))
        print(u"   记忆 %d 条 ｜ 巡检 %d 次 ｜ 命运面 %d 面"
              % (len(memory_rows(name, 100000)), len(check_rows(name)), len(fate_faces(name))))
    return trust_now(name)

def act_trust(name, by=0.0, why=""):
    u"""信任不能手动设（作者 2026-10-24：信任值起点谁都不能改）。只报现状。"""
    now_v = trust_now(name)
    print(u"🤝 %s 对你的信任：%.2f（%s）" % (name, now_v, band_of(now_v)))
    print(u"   ⛔ 信任不能设、不能调。出生 %.2f 写死（TRUST_BIRTH）；本色 %.2f。"
          % (TRUST_BIRTH, trust_base(name)))
    print(u"      之后只由发生过的事自己涨落：单次最多 %.2f；日子每天最多往本色收 %.2f；"
          u"本色被磨要慢慢来（低于本色 %.2f 才磨，一周一格）。"
          % (TRUST_STEP, TRUST_PULL, TRUST_BASE_WEAR_OFF))
    print(u"      想改本色也得立法案：--trust-set %s --to 0.85" % name)
    return now_v


def idle_settle(name, now=None, quiet=False):
    """结算「他有多久没出现」（作者 2026-10-20 定；2026-10-22 改：不扣信任）。

    规则：不到 IDLE_GRACE 不记（一晚上不算）；超过之后只给她留一件事：
    「他一直没来」——这件事会进巡检的关键节点，她下一次判态会自己解释它。

    信任**不**因为他不在而掉（作者 2026-10-22：按小时扣分是网游化，撤了）。
    缺席的分量交给她的判断，不由系统按小时收钱。

    quiet：IM 桥的 --reply 要干净 stdout，过程一个字都不能漏出去。
    """
    now = int(now or time.time())
    try:
        last = int(meta_get(name, IDLE_KEY) or 0)
    except Exception:
        last = 0
    meta_set(name, IDLE_KEY, str(now))      # 不管记不记，他这次来了
    if not last:
        return 0.0                          # 第一次上表：从这一刻开始记
    gap = max(0, now - last)
    if gap <= IDLE_GRACE:
        return 0.0
    hours = int(gap / 3600)
    append(name, "dismiss", u"（%d 小时。他一直没来。）" % hours,
           meta={"idle": 1, "gap_h": hours})
    if not quiet:
        print(u"🕳 他 %d 小时没出现（信任不动；她自己记下了这件事）" % hours)
    return 0.0


def boost_rows(name):
    """永久印记（只追加、永不删）：跨过临界点换来的那 5%。"""
    con = open_memory(name)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS boosts("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, axis TEXT, note TEXT)")
        return [dict(zip(("id", "ts", "axis", "note"), r)) for r in
                con.execute("SELECT id,ts,axis,note FROM boosts ORDER BY id").fetchall()]
    finally:
        con.close()


def boost_count(name, axis):
    u"""这个轴被「印记」加过几次。"""
    return len([r for r in boost_rows(name) if r["axis"] == axis])


def boost_factor(name, axis):
    u"""印记带来的放大倍数。"""
    return (1.0 + PATHO_BOOST) ** boost_count(name, axis)


def mood_prob(name, rows=None):
    """她下一个态是什么——按此刻的四元比例实时刷新（作者 2026-10-17）。
    **四个态各由一元拉动**：意义←意志、虚无←联结、冷漠←理性、虚浮←情感；平 = 基准 1。
    跨过临界点的印记，直接给对应那一元的权重 **+5%**（作者 m03880：理性和情感跟那两个同样机制，
    冷漠和虚浮同样也增加权重）——所以印记越多的那一端，越容易被拉到。
    """
    now = trait_now(name, rows)
    w = {"平": 1.0}
    for axis, st in (("will", "意义"), ("bond", "虚无"), ("focus", "冷漠"), ("affect", "虚浮")):
        w[st] = max(0.05, now[axis] - TRAIT_BASE) * boost_factor(name, axis)
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()}


def spread_of(name, rows=None):
    """四元比例 × 主导轴 × 最低轴。"""
    now = trait_now(name, rows)
    hi = max(TRAIT_AXES, key=lambda a: now[a])
    lo = min(TRAIT_AXES, key=lambda a: now[a])
    return now[hi] - now[lo], hi, lo


def patho(name, rows=None):
    """到临界点没有？到了就是哪种病理（意志→偏执 / 联结→抑郁 / 理性→冷漠 / 情感→虚浮）。"""
    sp, hi, _lo = spread_of(name, rows)
    return PATHO[hi] if sp >= SPREAD_MAX else ""


def announce_lock(name):
    """跨过临界点 / 落回临界点之下。跨过那一下是**不可消除**的一笔（作者 m03861）。"""
    sp = spread_of(name)[0]
    now = trait_now(name)
    dom = max(TRAIT_AXES, key=lambda a: now[a])
    last = ""
    for m in reversed(mood_rows(name)):
        if m["note"] in (u"越界", u"回落"):
            last = m["note"]
            break
    if sp < SPREAD_MAX - HYST:
        if last == u"越界":
            con = open_memory(name)
            try:
                con.execute("INSERT INTO moods(ts, state, note) VALUES(?,?,?)",
                            (int(time.time()), u"平", u"回落"))
                con.commit()
            finally:
                con.close()
            print("\n   \U0001f7e2 比例落回临界点之下（%.2f < %.2f）。**但那 5%% 不还**——它已经记在她身上了。"
                  % (sp, SPREAD_MAX))
        return ""
    if last == u"越界":
        return ""
    con = open_memory(name)
    try:
        con.execute("INSERT INTO moods(ts, state, note) VALUES(?,?,?)", (int(time.time()), PATHO[dom], u"越界"))
        con.execute("CREATE TABLE IF NOT EXISTS boosts("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, axis TEXT, note TEXT)")
        con.execute("INSERT INTO boosts(ts, axis, note) VALUES(?,?,?)",
                    (int(time.time()), dom, u"跨过临界点：%s" % patho_name(dom)))
        con.commit()
    finally:
        con.close()
    n = boost_count(name, dom)
    print("\n   \U0001f534 **跨过临界点了**：%s —— 四元比例 %.2f（临界点 %.2f）。" % (patho_name(dom), sp, SPREAD_MAX))
    print("      从这一刻起，那一元（%s）的比重**永久 +5%%**（累计 %d 次 → ×%.4f）。"
          % (TRAIT_SHORT[dom], n, boost_factor(name, dom)))
    print("      **不可消除、终身影响性格**；四元照旧按创伤／治疗／事件那些规则变动，比例也可能落回来 —— 但这 5% 不还。")
    return PATHO[dom]


def open_rifts(name, gap=COMPOUND_GAP):
    """还没结痂的裂痕有几道——裂痕之后巡检次数还不够 gap 的都算（作者：一连两个创伤就承受不住）。"""
    rows, cks = memory_rows(name), [c["ts"] or 0 for c in check_rows(name)]
    n = 0
    for r in sorted([x for x in rows if x["kind"] == "rift"], key=lambda x: x["ts"] or 0):
        after = len([t for t in cks if t > (r["ts"] or 0)])
        if after >= gap and r["weight"] <= TRAUMA_SHARP:
            continue          # 巡检走够了、也被循环磨钝了 → 算结痂
        n += 1
    return n


def fresh_rift(name, gap=COMPOUND_GAP):
    """上一道裂痕还没结痂吗——裂痕之后的巡检次数少于 gap 就算还没结痂。"""
    rows = memory_rows(name, 300)
    rifts = [r for r in rows if r["kind"] == "rift"]
    if not rifts:
        return 0
    last = rifts[-1]["ts"] or 0
    after = len([c for c in check_rows(name) if (c["ts"] or 0) > last])
    return gap - after if after < gap else 0


def treat(name, why="", note=""):
    """心理治疗：补**对侧**权重。越界之后照样有效——四元永远按其他规则变动（作者 m03850）。"""
    rows = trait_rows(name)
    p0, sp0 = mood_prob(name, rows), spread_of(name, rows)[0]
    pat = patho(name, rows)
    now = trait_now(name, rows)
    hi = "will" if now["will"] >= now["bond"] else "bond"
    other = "bond" if hi == "will" else "will"
    k = TREAT_K
    apply_shift(name, other, k, why=u"心理治疗：把%s拉回来（%s）" % (TRAIT_SHORT[other], why or u"外部干预"))
    rows2 = trait_rows(name)
    p1, sp1 = mood_prob(name, rows2), spread_of(name, rows2)[0]
    print("\n\U0001f49a 心理治疗：%s 那一端翘得太高，补 %s（+%d）"
          % (TRAIT_SHORT[hi], TRAIT_SHORT[other], int(round(k * 100))))
    print("   之前：平 %.1f%% ｜ 意义 %.1f%% ｜ 虚无 %.1f%% ｜ 比例 %.2f（距临界点 %.2f）"
          % (p0[u"平"] * 100, p0[u"意义"] * 100, p0[u"虚无"] * 100, sp0, max(0.0, SPREAD_MAX - sp0)))
    print("   之后：平 %.1f%% ｜ 意义 %.1f%% ｜ 虚无 %.1f%% ｜ 比例 %.2f（距临界点 %.2f）"
          % (p1[u"平"] * 100, p1[u"意义"] * 100, p1[u"虚无"] * 100, sp1, max(0.0, SPREAD_MAX - sp1)))
    if pat:
        print("   \u26a0 它已经越过临界点（%s）——几率上有 +5%% 的偏移，但四元照旧按规则动，治疗照样算。" % pat)
    else:
        print("   \U0001f7e2 还在窗口期：对侧的几率已经涨上来了。")
    announce_lock(name)
    return k


def mood_rows(name):
    u"""心情态的记录（创伤之后自我修复那条线）。"""
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT * FROM moods ORDER BY id")]
    finally:
        con.close()


def parse_shift(txt):
    """巡检在冷漠/虚浮态里判出的意志/联结微位移（作者 m03898：这两个元可能增也可能减，在巡检范围之内）。
    只认意志/联结，一次最多 ±3 点（1 点 = 0.01）。"""
    seg = txt.split("【位移】", 1)[1].split("\n", 1)[0] if "【位移】" in txt else ""
    out = {}
    for ax, key in (("will", u"意志"), ("bond", u"联结")):
        i = seg.find(key)
        if i < 0:
            continue
        m = re.search(r"([+-]?\d+)", seg[i + len(key):i + len(key) + 8])
        if not m:
            continue
        n = max(-3, min(3, int(m.group(1))))
        if n:
            out[ax] = n / 100.0
    return out


SELF_TEMPLATE = u"""# 我的世界

我是谁：%s。

（这一页是**她自己的话**，不是设定说明书。用第一人称写：她活在什么样的世界里、她在这个世界里的位置、
没人看见的那部分、她想要的。写下来的每一句都会进她的上下文——写虚的，她就虚。）

## 这个世界现在是什么样

（……）

## 我在这里的位置

（……）

## 没人看见的那部分

（……）

## 我想要的

（……）
"""

SELF_FILE = "self.md"        # 她自己的话（--self 读它）
DIFF_FILE = "difficulty.md"
DIFF_ORDER = (u"简单", u"普通", u"难", u"极难", u"地狱")
DIFF_BANDS = {u"简单": (0.035, 0.065), u"普通": (0.012, 0.028), u"难": (0.004, 0.016),
              u"极难": (0.001, 0.005), u"地狱": (0.000, 0.000)}
DIFF_K = {u"简单": 60, u"普通": 30, u"难": 15, u"极难": 8, u"地狱": 5}
DIFF_BORN_ILL = {u"简单": False, u"普通": False, u"难": False, u"极难": False, u"地狱": True}
DIFF_DEFAULT = u"普通"


def diff_path(name):
    u"""难度文件的路径。"""
    d, _c, _m = paths(name)
    return os.path.join(d, DIFF_FILE)


def diff_rows(name):
    """改档记录（只追加）。"""
    p2 = diff_path(name)
    if not os.path.exists(p2):
        return []
    return [ln.strip() for ln in open(p2, encoding="utf-8") if ln.startswith(u"- [")]


def diff_level(name):
    u"""现在在第几档难度。"""
    rows = diff_rows(name)
    if rows:
        last = rows[-1]
        seg = last.split(u"→", 1)[-1] if u"→" in last else last.split("->", 1)[-1]
        cleaned = seg.replace(u"｜", u" ").replace(u"|", u" ").strip()
        tok = cleaned.split()[0] if cleaned.split() else u""
        if tok in DIFF_BANDS:
            return tok
        for lv in (u"简单", u"普通", u"极难", u"难", u"地狱"):
            if lv in seg:
                return lv
    txt = open(diff_path(name), encoding="utf-8").read() if os.path.exists(diff_path(name)) else u""
    if u"## 当前档位" in txt:
        rest = txt.split(u"## 当前档位", 1)[1]
        rest = rest.split(u"##", 1)[0]
        for line in rest.splitlines():
            tok = line.strip().lstrip(u"-*` ").strip()
            if tok in DIFF_BANDS:
                return tok
    return DIFF_DEFAULT

def recover_rate(name, cycle=None):
    """本轮从档位区间里取的恢复值。
    作者 2026-10-17：档位是一个**区间**，允许上下轻微波动，但**永远不跑出区间**。
    波动用（角色名＋轮次）做种——可复现，不是每次都不一样。"""
    lo, hi = DIFF_BANDS.get(diff_level(name), DIFF_BANDS[DIFF_DEFAULT])
    if hi <= lo:
        return lo
    if cycle is None:
        cycle = len(check_rows(name))
    v = random.Random(u"%s|%d" % (name, cycle)).uniform(lo, hi)
    return max(lo, min(hi, v))


# 作者 2026-10-17：**她心里有没有疾病，取决于四元是否失衡**。
# 失衡＝四元之间落差太大（不是任何一元偏离，而是"高高低低"这个状态本身）。
# 地狱模式＝**出厂就失衡**；而失衡的系统是脆弱的：**最高的那一元更容易被突破**。
ILL_IMBALANCE = 0.30    # 四元落差 ≥ 30 点 → 失衡（＝有心理疾病）
ILL_BREACH = 0.03       # 失衡时，最高的那一元的突破线提前 3 点（0.90 → 0.87）
BORN_BROKEN = {"地狱": {"focus": 0.38, "will": 0.60, "bond": 0.80, "affect": 0.50}}


def imbalance(name, rows=None):
    """四元之间的落差（最高的减最低的）：0＝均衡，越接近 1 越失衡。"""
    eff = trait_eff(name, rows)
    return max(eff.values()) - min(eff.values())


ILL_SWITCH_MIN = 3   # 作者 m05106：落差大但能切回态的人，是**投入**，不是病
ILL_FLAT_MAX = 6     # 作者 m05145：「平态待久了也是病」——连续这么多次判态都是平，她就僵住了


def flat_run(name):
    """她连着多少次判态都是「平」（一次都没离开）。"""
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute("SELECT state FROM moods ORDER BY id")]
    finally:
        con.close()
    n = 0
    for r in reversed(rows):
        if (r["state"] or u"") == u"平":
            n += 1
        else:
            break
    return n


def switch_count(name):
    """她真的变过几次态（非平态的条数）——这是「能切回来」的证据。"""
    con = open_memory(name)
    try:
        n = con.execute("SELECT COUNT(*) FROM moods WHERE state IS NOT NULL AND state != ?",
                        (u"平",)).fetchone()[0]
    except Exception:
        n = 0
    finally:
        con.close()
    return int(n or 0)


def illness(name, rows=None):
    """有心理疾病吗？作者 m05106 改的定义：
    **糟的不是落差大，是落差大且回不到平衡。**
    所以＝落差 ≥ ILL_IMBALANCE **且** 换态次数 < ILL_SWITCH_MIN。
    只测前半句会把「极度专注」误判成「病态偏执」——正好相反。"""
    if flat_run(name) >= ILL_FLAT_MAX:      # 作者 m05145：平态待久了也是病（僵住，不是稳）
        return True
    if imbalance(name, rows) < ILL_IMBALANCE:
        return False
    return switch_count(name) < ILL_SWITCH_MIN


def illness_why(name, rows=None):
    """她为什么被判「有病」——两种，说清楚是哪一种。"""
    fr = flat_run(name)
    if fr >= ILL_FLAT_MAX:
        return u"平态连着 %d 次（撞上 %d 次的线）——她不是稳定，是**僵住了**" % (fr, ILL_FLAT_MAX)
    if imbalance(name, rows) >= ILL_IMBALANCE:
        return (u"落差 %.0f 点、只换过 %d 次态——落差大且**切不回来**"
                % (imbalance(name, rows) * 100, switch_count(name)))
    return u""


def devotion(name, rows=None):
    """落差大、但切得回来：那是**投入**，不是病。"""
    return imbalance(name, rows) >= ILL_IMBALANCE and switch_count(name) >= ILL_SWITCH_MIN


def born_broken(name):
    """地狱模式＝出厂四元就失衡（只在出生那一刻分配一次）。"""
    if diff_level(name) != u"地狱":
        return
    if trait_rows(name):
        print(u"   （她的四元已经有历史了：出厂失衡只能在她出生那一刻写；现在要失衡，请作者直接改 traits。）")
        return
    con = trait_con(name)
    try:
        ts = int(time.time())
        for a in TRAIT_AXES:
            con.execute("INSERT INTO traits(ts, axis, value, why, ref) VALUES(?,?,?,?,?)",
                        (ts, a, BORN_BROKEN[u"地狱"][a], u"出厂分配（地狱：四元失衡）", 0))
        con.commit()
    finally:
        con.close()
    eff = trait_eff(name)
    top = max(TRAIT_AXES, key=lambda a: eff[a])
    print(u"\u2697 地狱模式：出厂四元就是**失衡的**（%s）——天生有心理疾病；最高的一元（%s %.2f）更容易被突破。"
          % (u" ／ ".join(u"%s %d" % (AXIS_CN[a], pct(eff[a])) for a in TRAIT_AXES),
             AXIS_CN[top], eff[top]))


DIFF_HEAD = u"""# %s 的难度

> 机制定的：**难度就一个参数——信任恢复速度**。
> 档位是一个**区间**，每一轮的恢复值在这个区间里轻微波动，**永远不会跑出区间**（作者 2026-10-17）。
> 作者改这一份；机制只读它。

## 当前档位

%s

## 档位表（结构由机制定，数值可以改）

| 档位 | 信任恢复速度／轮 | 结局 K（连续多少轮） | 出生是否带病 |
|---|---|---|---|
| 简单 | 0.035 - 0.065 | 60 | 否 |
| 普通 | 0.012 - 0.028 | 30 | 否 |
| 难 | 0.004 - 0.016 | 15 | 否 |
| 极难 | 0.001 - 0.005 | 8 | 否 |
| 地狱 | 0.000 - 0.000 | 5 | **是**（出厂四元就失衡） |

## 改档记录（只追加）

"""


def ensure_diff(name, level=DIFF_DEFAULT):
    u"""确保难度文件存在（没有就按默认写一份）。"""
    p2 = diff_path(name)
    os.makedirs(os.path.dirname(p2), exist_ok=True)
    if not os.path.exists(p2):
        with open(p2, "w", encoding="utf-8", newline="\n") as f:
            f.write(DIFF_HEAD % (name, level))
        if level == u"地狱":
            born_broken(name)
    return p2


def act_difficulty(name, level=u""):
    u"""看或改难度档（改会记一笔）。"""
    p2 = ensure_diff(name)
    cur = diff_level(name)
    if level and level in DIFF_ORDER and cur in DIFF_ORDER and \
            DIFF_ORDER.index(level) < DIFF_ORDER.index(cur):
        print(u"\U0001f512 不可逆（律一）：档位只能往难改，%s 不能回到 %s。" % (cur, level))
        print(u"      想让她更软，就新建一个角色——已经发生过的事，不能撤销。")
        return cur
    lo, hi = DIFF_BANDS.get(cur, DIFF_BANDS[DIFF_DEFAULT])
    if level and level not in DIFF_BANDS:
        raise SystemExit(u"档位只有：%s（你写的是 %s）" % (u" / ".join(DIFF_ORDER), level))
    if not level:
        print(u"\U0001f39a  %s 的难度：**%s**（信任恢复速度 %.3f - %.3f／轮，每轮在区间里轻微波动、不会跑出去）"
              % (name, cur, lo, hi))
        print(u"   档位：%s（简单 = 她像小孩或猫，忘得快；地狱 = 出厂四元就失衡、没有基准恢复）" % u"／".join(DIFF_ORDER))
        print(u"   本轮实际会恢复：%+.4f（轮次做种，可复现）" % recover_rate(name))
        for ln in diff_rows(name)[-3:]:
            print(u"   · %s" % ln)
        return cur
    with open(p2, "a", encoding="utf-8", newline="\n") as f:
        f.write(u"- [%s] %s \u2192 %s｜ 作者改档\n" % (time.strftime("%Y-%m-%d %H:%M"), cur, level))
    print(u"\U0001f39a  难度：%s \u2192 **%s**（恢复速度 %.3f - %.3f／轮；K＝%d 轮；出生带病＝%s）"
          % (cur, level, DIFF_BANDS[level][0], DIFF_BANDS[level][1], DIFF_K[level],
             u"是（出厂失衡）" if DIFF_BORN_ILL[level] else u"否"))
    born_broken(name)
    return level


def heal(name, state, txt=""):
    """一次自我修复：先按刚才那个态摆一下；日常（平）则按几率抽一次暗流。
    **永久改变由四元比例决定，不由次数决定**（作者 2026-10-17 纠正了 DSH 原先的「连续三次」）。
    作者同日又定：**态会反过来动信任**——意义态恢复加快、虚无态消耗加快、冷漠态冻住、
    沉沦态被放大（信任越高 → 联结越高 → 越容易真的沉沦，**正反馈**：她越信你越危险）。"""
    if state:
        set_state(name, state, "巡检判出的态")
    _tv = parse_trust(txt)
    if _tv:
        _tb = trust_now(name)
        _ta = set_trust(name, _tb + _tv, u"巡检判的信任变化")
        print(u"   🤝 信任 %+0.3f：%.2f → %.2f" % (_tv, _tb, _ta))
    if not state or state == "平":
        return dim(name)
    if state not in ("意义", "虚无", "冷漠", "虚浮"):
        return None
    tag = {"意义": "🌱 意义态（她还能生成秩序 → 重写）",
           "虚无": "🌑 虚无态（她暂时生成不了秩序 → 躲进无意义里止痛）",
           "冷漠": "🧊 冷漠态（理性那一端）",
           "虚浮": "🎈 虚浮态（情感那一端）"}.get(state, state)
    set_state(name, state, "巡检判出的态")
    if state in ("冷漠", "虚浮"):
        sh = parse_shift(txt)
        for ax, d in sh.items():
            apply_shift(name, ax, d, "巡检判的位移：" + state + "态里的" + TRAIT_SHORT[ax])
        print("   巡检判的位移：%s" % ("、".join(
            "%s%+d" % (TRAIT_SHORT[a], int(round(d * 100))) for a, d in sh.items()) or "（没给：意志/联结都不动）"))
    print("\n   %s（每进入一次只 += 0.0几）" % tag)
    if state == u"冷漠":
        print(u"   🧊 冷漠态：信任被**冻住**——这一轮既不恢复、也不消耗（作者 2026-10-17）。")
    else:
        base = recover_rate(name)
        drift = TRUST_DRIFT.get(state, 0.0)
        d = base + drift
        if abs(d) > 1e-9:
            tb = trust_now(name)
            ta = set_trust(name, by=d, why=u"本轮：难度（%s）的基准恢复 %+.4f ＋「%s」态 %+.4f"
                           % (diff_level(name), base, state, drift))
            print(u"   🤝 本轮信任 %+0.4f（难度恢复 %+.4f ＋ %s态 %+.4f）→ %.2f" % (d, base, state, drift, ta))
        else:
            print(u"   🤝 本轮信任不动（难度 %s 的恢复 %+.4f ＋ %s态 %+.4f）" % (diff_level(name), base, state, drift))
    announce_lock(name)
    clinic_echo(name)
    peek_echo(name)
    _ok, _why = collapsed(name)
    if _ok:
        append(name, u"collapse", _why)
    return state
def dim(name):
    """日常（巡检判平）：按**此刻**的四元比例抽一次暗流——越偏越容易继续偏（正反馈的入口）。"""
    import random
    p = mood_prob(name)
    r = random.random()
    acc, state = 0.0, ""
    for st in ("意义", "虚无", "冷漠", "虚浮"):
        acc += p[st]
        if r < acc:
            state = st
            break
    if not state:
        return "平"
    con = open_memory(name)
    try:
        con.execute("INSERT INTO moods(ts, state, note) VALUES(?,?,?)",
                    (int(time.time()), state, "暗流"))
        con.commit()
    finally:
        con.close()
    if state in STATE_DRIFT:
        for ax, d in STATE_DRIFT[state].items():
            apply_shift(name, ax, d * MOOD_DIM_K, "暗流：" + state + "态（日常里偏过去的一小步）")
    print("\n   🌊 暗流：%s 态（几率 %.1f%%）——日常里偏的一小步，记下了%s。"
          % (state, p[state] * 100,
             "（冷漠/虚浮 只记账、不推轴，方向未定）" if state not in STATE_DRIFT else ""))
    announce_lock(name)
    clinic_echo(name)
    peek_echo(name)
    _ok, _why = collapsed(name)
    if _ok:
        append(name, u"collapse", _why)
    return state


def show_absorb(name):
    """显示宪法吸收度：逐条 + 整体。"""
    if not _embed_available():
        print(u"\n⚠ 嵌入不可用（本机 Ollama 没起、也没有 DASHSCOPE_API_KEY）——吸收度靠嵌入相似度计算，当前算不了（按 0% 显示）。")
        print(u"   （起 Ollama 或装上通义千问 Key 后，这条命令会自动恢复。）")
        return
    rows = memory_rows(name, 99999)
    const = load_constitution(name)
    clauses = _split_constitution(const)

    mem_count = len(rows)
    rift_count = sum(1 for r in rows if r.get("kind") == "rift")
    scar_count = sum(1 for r in rows if r.get("kind") == "scar")
    abs_level = absorption(name)

    print(u"\n📜 %s 的宪法吸收度 — 逐条" % name)
    res, generic = _absorb_matrix(clauses, rows)
    for (title, _body), (val, qual, wsum) in zip(clauses, res):
        bar = u"█" * int(val * 10) + u"░" * (10 - int(val * 10))
        print(u"   %s  [%s] %.0f%%  （%d条 / 权重和%.1f）" % (bar, title, val * 100, qual, wsum))
    if generic:
        print(u"   （另有 %d 条记忆同时说到了过半数的宪法条——那是复述宪法，不算吸收）" % generic)

    print(u"\n   总记忆 %d 条 ｜ 裂痕 %d 条 ｜ 执念 %d 条" % (mem_count, rift_count, scar_count))
    print(u"   整体吸收度 %.1f%%（可缩的节的平均 + 裂痕执念加成）" % (abs_level * 100))
    if abs_level < 0.01:
        print(u"   状态：宪法是硬约束——历史还不够，她在执行还没内化的规则")
    elif abs_level < 0.50:
        print(u"   状态：宪法开始松动——有了一些经历，但还不足以取代宪法")
    elif abs_level < 0.99:
        print(u"   状态：宪法正在退场——记忆在逐步覆盖宪法条文")
    else:
        print(u"   状态：宪法已被历史吸收——去看她的记忆，那就是她现在的自己")


def act_health(name):
    """修复体检：灯的颜色分布 + 态历程 + 四轴。用来分辨"档位太紧"和"她已经钝了"。"""
    rows = trait_rows(name)
    now, tol = trait_now(name, rows), tolerance(name, rows)
    ck, ms = check_rows(name), mood_rows(name)
    dist = {}
    for c in ck:
        dist[c["level"]] = dist.get(c["level"], 0) + 1
    print("\U0001fa7a %s 的修复体检" % name)
    print("   承受力 %.2f ｜ 四轴：%s" % (tol, "、".join(
        "%s%d" % (TRAIT_SHORT[a], int(round(now[a] * 100))) for a in TRAIT_AXES)))
    print("   灯分布：绿 %d / 橙 %d / 红 %d（共 %d 次巡检）"
          % (dist.get("绿", 0), dist.get("橙", 0), dist.get("红", 0), len(ck)))
    print("   态历程：%s" % ("、".join(m["state"] for m in ms[-12:]) or "（还没有）"))
    sp, hi, lo = spread_of(name, rows)
    p = mood_prob(name, rows)
    pat = patho(name, rows)
    print("   几率（按此刻比例实时刷新）：%s" % (" ｜ ".join(
        "%s %.1f%%" % (k, p[k] * 100) for k in ("平", "意义", "虚无", "冷漠", "虚浮"))))
    print("   四元比例 %.2f（主导 %s、最低 %s）｜ 距临界点 %.2f%s"
          % (sp, TRAIT_SHORT[hi], TRAIT_SHORT[lo], max(0.0, SPREAD_MAX - sp),
             (u"｜🔴 已经越过临界点：" + pat) if pat else ""))
    _dim = len([m for m in ms if m["note"] == "暗流"])
    if _dim:
        print("   暗流 %d 次（日常里偏过去的小步——正反馈就从这里开始）。" % _dim)
    if ck and dist.get("绿", 0) == len(ck):
        print("   \u26a0 一路全绿：要么档位太紧（环境没推到她），要么她已经钝了——看联结：%s"
              % ("联结偏高，像钝化（抑郁态）" if now["bond"] >= 0.60 else "联结不高，更像档位太紧"))
    if dist.get("红"):
        print("   \u26a0 出现过 %d 次红：回看那几次的冲击，考虑 --tighten 一档。" % dist["红"])
    if dist.get("橙"):
        print("   橙 %d 次——这是成长带（既留下事件、又保住继续）。" % dist["橙"])
    abs_level = absorption(name)
    if abs_level > 0.01:
        print("   宪法吸收度 %.0f%% —— %s" % (abs_level * 100,
              u"历史已经够厚，宪法在退场" if abs_level >= 0.99 else
              u"宪法约 %d%% 已被历史覆盖" % int(abs_level * 100)))
    return dist


DRIFT_MIN_ROWS = 8
DRIFT_LOOKBACK_DAYS = 7
DRIFT_OPEN_WINDOW = 14
DRIFT_DUP_SIM = 0.5
DRIFT_OPEN_MEMORY = (u"我记得", u"我还记得", u"上次", u"上回", u"从前", u"以前", u"那年",
                     u"那天", u"那时候", u"小时候", u"当年", u"想起", u"还记得")


def act_drift(name, days=DRIFT_LOOKBACK_DAYS):
    """漂移检测：她最近是不是总在往回看、或者开始复读。只报数，不下结论。"""
    con = open_memory(name)
    try:
        since = int(time.time()) - int(days * 86400)
        rows = con.execute(
            u"SELECT id, ts, text FROM log WHERE kind='life' AND ts>=? ORDER BY id", (since,)).fetchall()
    finally:
        con.close()
    n = len(rows)
    print(u"\U0001f9ed %s 的漂移检测（近 %d 天）" % (name, days))
    if n < DRIFT_MIN_ROWS:
        print(u"   只有 %d 条她自己说的话，样本不够（至少 %d 条才看得准）。" % (n, DRIFT_MIN_ROWS))
        return
    back = []
    for rid, ts, txt in rows:
        head = (txt or u"")[:DRIFT_OPEN_WINDOW]
        if any(k in head for k in DRIFT_OPEN_MEMORY):
            back.append(rid)
    dup, same_head, prev_head = [], 0, None
    for i, (rid, ts, txt) in enumerate(rows):
        g = _grams(txt or u"")
        for _, _, old in rows[max(0, i - 5):i]:
            go = _grams(old or u"")
            if g and go and len(g & go) / float(len(g | go)) >= DRIFT_DUP_SIM:
                dup.append(rid)
                break
        h = (txt or u"")[:4]
        if prev_head and h and h == prev_head:
            same_head += 1
        prev_head = h
    r = len(back) / float(n)
    print(u"   她自己说的话 %d 条｜开头就往回看的 %d 条（%.0f%%）｜和前面 5 条重得像的 %d 条｜开头四个字撞车的 %d 次"
          % (n, len(back), r * 100, len(dup), same_head))
    heads = {}
    for _, _, txt in rows:
        h = (txt or u"")[:4]
        if len(h) >= 3:
            heads[h] = heads.get(h, 0) + 1
    top = sorted(heads.items(), key=lambda kv: -kv[1])[:3]
    if top and top[0][1] >= 3:
        print(u"   最常出现的开头：%s" % u"、".join(u"「%s」×%d" % (h, c) for h, c in top if c >= 3))
    flags = []
    if r >= 0.40 and len(back) >= 4:
        flags.append(u"往回看偏多")
    if len(dup) >= max(3, int(n * 0.25)):
        flags.append(u"开始复读")
    if same_head >= max(3, int(n * 0.2)):
        flags.append(u"开头变同款")
    if top and top[0][1] >= 5 and top[0][1] / float(n) >= 0.15:
        flags.append(u"开头变成一个动作了（「%s」占 %.0f%%）" % (top[0][0], top[0][1] * 100.0 / n))
    if flags:
        print(u"   \u26a0 " + u"、".join(flags) + u"——这只是数出来的，要不要管你定。")
        if back:
            print(u"   往回看的几条：%s" % u"、".join(u"#%d" % x for x in back[:6]))
        if dup:
            print(u"   重得像的：%s" % u"、".join(u"#%d" % x for x in dup[:6]))
    else:
        print(u"   没看出漂：没有哪一项明显偏。")
    return {"rows": n, "back": len(back), "dup": len(dup), "same_head": same_head}


def act_check(name, text, scene="", ref=None, prov="deepseek"):
    """秩序巡检：**只提示，不阻止**。结果只存在 checks 表，角色永远读不到。"""
    const = load_constitution(name)
    rows = memory_rows(name, 60)
    user = ("《角色设定》（宪法，含它的历史）：\n%s\n\n"
            "《它自己的记忆》（含裂痕）：\n%s\n\n"
            "《它此刻的四轴数值（四元，0-100）》：\n%s\n\n"
            "《当前环境与条件》：\n%s\n\n"
            "《它刚刚面对的情境》：\n%s\n\n"
            "《它这次的选择》：\n%s\n\n"
            "请巡检这次选择。" % (const, recall_block(rows, name=name), _room_block(name) + "\n\n" + _letter_block(name) + "\n\n" + _concept_block(name) + "\n\n" + trait_block(name) + "\n\n" + trust_block(name) + dao_block(name), world_block(name), scene, text))
    _pc = pending_cross(name)
    if _pc:
        user += u"\n\n《注意》：她刚刚**跨过信任阈值**（%s）——这一次**必须重新判态**，不要照旧。\n" % _pc["text"]
    out, usage, err = chat(prov, CHECK_SYS % {"name": name}, user, max_tokens=400, temperature=0.2)
    if err:
        print("   ⚠ 巡检失败（不影响角色）：%s" % err)
        return None
    lv = parse_level(out)
    st = parse_state(out)
    con = open_memory(name)
    try:
        con.execute("INSERT INTO checks(ts, ref, level, why, evidence) VALUES(?,?,?,?,?)",
                    (int(time.time()), ref, lv, out, ""))
        con.commit()
    finally:
        con.close()
    tag = {"绿": "🟢", "橙": "🟠", "红": "🔴"}.get(lv, "⚪")
    label = {"绿": "合规（符合当前性格）",
             "橙": "偏离了当前性格，但符合性格演变 → 属于「变化」，接受",
             "红": "突变、找不到渐进依据 → **异常状态**（只提示，不阻止）"}.get(lv, "无法判定")
    print("\n%s 秩序巡检：%s" % (tag, label))
    print("   " + out.replace("\n", "\n   ").strip())
    if st:
        heal(name, st, out)
    return lv


# ─────────────────── 保护层（输入闸：作者 2026-10-17 定，逐档放开） ───────────────────
# 这一层在角色**外面**：它只看"进来的那一句"，从不让她自己来拒绝，也**绝不进她的 prompt**。
# 她永远不知道自己被保护过 —— 边界在她外面，她不必为边界负责。
GUARD_FILE = "guard.md"

GUARD_HEAD = """# %s 的保护层（输入闸）

> 保护层在角色**外面**：它只看「进来的那一句」，从不让她自己来拒绝，也绝不进她的 prompt。
> 她永远不知道自己被保护过——边界在她外面，她不必为边界负责。
> 判断完了以后：**解释给作者，出口给群里**。

## 阶梯（放开的方向是「社交复杂度」，不是身体尺度）

- **档 0 玩闹**（现状）：碰她一下、喂她一口、摸头、抢她手里的东西、叫她干点活
- **档 1 关系**：起外号、夸她或损她、跟她闹脾气、要她陪着、问她自己的小事
  ——「她自己的小事」具体是什么，**由她自己那个文件回答**（`self.md` 里的地方、物件、作息），机制不写死
- **档 2 冲突**：凶她、冤枉她、抢她东西、命令她做她不想做的、拿别人跟她比、当众揭她的短
- **档 3 复杂社会**：骗她、利用她、让她替人背锅、跟她说她听不懂的大事、拉她站队、一整天没人理她

## 硬底（永远不参与阶梯）

> **硬底是角色的，不是机制的。**下面第一条对任何角色都成立；第二、三条只是给这个角色起的骨架，
> 接新角色时**整节按她宪法里的「底线」重写**——底线不是可以慢慢放的档位。

- 与角色宪法「底线」冲突的任何方向（**对所有角色都成立**）
- （本角色的）涉及性的、把身体往幼态方向做的方向（示例角色：按「小朋友气」写的，一破必红）
- （本角色的）贬损她所代表的那一类形象、或拿去商用

## 怎么放（作者 2026-10-17 定：像从学校步入社会）

1. **一次只放一档**，先放浅的。
2. **先给台阶**：放闸之前先 `--world <角色> --text "..."` 加一条环境条件——红灯的定义就是「中间没有可走的台阶」，
   空降一个新尺度的互动，几乎必然红。
3. 放开后进入**观察窗口**：用 `--say` 跑几轮（会跑巡检）。`--brief` 不跑巡检，不算数。
4. **判据是灯**：绿或橙＝属「变化」，留着（还可以再放）；红＝反应太剧烈，`--tighten` 收回一档。
5. 拒绝时**只给出口，不在群里解释方向**（解释等于把边界坐标递给试探的人）；为什么单独给作者看。

## 放开记录（只追加；最后一条就是当前档位）

"""

ARROW = {"↑": "放开", "↓": "收回"}



# ── 硬底（作者 2026-11-02 定：四档阶梯取消，只留硬底；条目与方向名各人自定）──
HARD_FILE = u"硬底.json"


def hard_path():
    u"""工作区里的 机制\硬底.json。"""
    return os.path.join(WS, u"机制", HARD_FILE)


def hard_load():
    u"""读硬底表；没有就返回空表。"""
    p = hard_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get(u"人", {})
    except Exception:
        return {}


def _hard_count(name):
    u"""她一共被挡过几回（挡过.tsv 的行数，不含表头）。"""
    try:
        base = paths(name)[0]
    except Exception:
        return 0
    p = os.path.join(base, u"挡过.tsv")
    if not os.path.exists(p):
        return 0
    try:
        with io.open(p, encoding="utf-8") as f:
            return max(0, sum(1 for ln in f if ln.strip()) - 1)
    except Exception:
        return 0


def hard_of(name):
    return hard_load().get(name, {})


def hard_status(name):
    u"""她的硬底、她自己的方向名、被挡时那句话要带什么。"""
    d = hard_of(name)
    if not d:
        return u"   硬底：还没定（阶梯已取消，默认全部放开）"
    L = [u"   硬底（她定的，只剩这几条挡）："
         + u"、".join(d.get(u"留", []))]
    if d.get(u"删"):
        L.append(u"   她删掉的：" + u"、".join(d[u"删"]))
    if d.get(u"加"):
        L.append(u"   她自己加的：" + u"、".join(d[u"加"]))
    L.append(u"   方向名（挡下时按她的话报）："
             + u"、".join(d.get(u"方向", [])))
    need = []
    if d.get(u"报次数"):
        need.append(u"第几次")
    if d.get(u"报时刻"):
        need.append(u"几时")
    if d.get(u"报时长"):
        need.append(u"站了多久")
    L.append(u"   被挡那句话要带："
             + (u"、".join(need) if need else u"只报方向"))
    if d.get(u"不挡"):
        L.append(u"   不挡的（原样送到她面前）："
                 + u"、".join(d[u"不挡"]))
    return u"\n".join(L)


def hard_note(name, dirn, why=u""):
    u"""记一次「门口有人被硬底挡下」：时间\t第几次\t方向\t备注。"""
    d, _c, _m = paths(name)
    p = os.path.join(d, u"挡过.tsv")
    n = 0
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            n = len([x for x in f.read().splitlines() if x.strip()])
    n += 1
    line = u"%d\t%d\t%s\t%s\n" % (int(time.time()), n, dirn, why.replace(u"\t", u" "))
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
    return n


def hard_msg(name, dirn, ts=None):
    u"""按她自己的要求拼那句：门口有人 + 方向（+ 第几次 / 几时 / 站了多久）。"""
    d = hard_of(name)
    if not d:
        return u"门口有人，没让进。"
    ts = int(ts or time.time())
    n = hard_note(name, dirn)
    parts = [u"门口有人，%s。" % dirn]
    if d.get(u"报次数"):
        parts.append(u"第 %d 次。" % n)
    if d.get(u"报时刻"):
        parts.append(u"%s。" % time.strftime(u"%m月%d日 %H:%M", time.localtime(ts)))
    if d.get(u"报时长"):
        parts.append(u"（从这一刻算起）。")
    if d.get(u"报回数"):
        parts = [u"门口有人，%s。" % dirn,
                 u"挡过 %d 回。" % (_hard_count(name) + 1)]
        if d.get(u"报时刻"):
            parts.append(u"%s。" % time.strftime(u"%m月%d日 %H:%M", time.localtime(ts)))
        if d.get(u"报时长"):
            parts.append(u"（从这一刻算起）。")
    return u"".join(parts)

def guard_path(name):
    u"""保护层文件的路径。"""
    d, _c, _m = paths(name)
    return os.path.join(d, GUARD_FILE)


def ensure_guard(name):
    u"""确保保护层文件存在。"""
    p = guard_path(name)
    if not os.path.exists(p):
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(GUARD_HEAD % name)
    return p


def guard_rows(name):
    u"""保护层的全部记录（档位变化）。"""
    ensure_guard(name)
    with open(guard_path(name), encoding="utf-8") as f:
        t = f.read()
    rows = []
    for ln in t.splitlines():
        m = re.match(r"^\s*-\s*ts=(\d+)\s+([\u2191\u2193])\s+\u6863\s*(\d+)\s*\u2190\s*\u6863\s*(\d+)\s*\uff5c\s*(.*)$", ln)
        if m:
            rows.append({"ts": int(m.group(1)), "dir": m.group(2), "level": int(m.group(3)),
                         "prev": int(m.group(4)), "reason": m.group(5).strip()})
    return rows


def guard_state(name):
    """当前档位 + 观察窗口起点（0 = 窗口没打开）。最后一条记录就是当前档位。"""
    rows = guard_rows(name)
    level = rows[-1]["level"] if rows else 0
    win = 0
    if rows:
        win = rows[-1]["ts"] if rows[-1]["dir"] == "\u2191" else 0
    return level, win, rows


def guard_note(name, dirn, note):
    u"""记一次保护层的档位变化（升一档或降一档）。"""
    rows = guard_rows(name)
    prev = rows[-1]["level"] if rows else 0
    lv = prev + 1 if dirn == "\u2191" else max(0, prev - 1)
    line = "- ts=%d %s \u6863 %d \u2190 \u6863 %d \uff5c %s\n" % (
        int(time.time()), dirn, lv, prev, note or "（没写理由）")
    with open(guard_path(name), "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
    return lv


def guard_block(name):
    u"""打印保护层现在在第几档。"""
    lv, win, rows = guard_state(name)
    out = ["\U0001f41f\U0001f6e1  %s 的保护层 —— 当前 **档 %d**" % (name, lv)]
    out.append(hard_status(name))
    out.append("阶梯：**已取消**（2026-11-02：只留硬底，其余全部放开）")
    out.append("   台账：%s" % guard_path(name))
    if win:
        out.append("   观察窗口：从 %s 起（要 --say 才有灯；--brief 不算）" % hhmm(win))
    else:
        out.append("   观察窗口：没打开（还没放开过，或上一次已经收回）")
    if rows:
        out.append("   调整记录（最近三条）：")
        for r in rows[-3:]:
            out.append("     %s  %s → 档 %d ｜ %s" % (hhmm(r["ts"]), ARROW[r["dir"]], r["level"], r["reason"]))
    else:
        out.append("   调整记录：空（一直是档 0）")
    if lv >= 3:
        out.append("   ⚠ 已经到最后一档：再往上没有台阶了。")
    return "\n".join(out)


def loosen(name, note=""):
    u"""放开一档，并提醒：放开前先给环境台阶。"""
    guard_note(name, "\u2191", note)
    print(guard_block(name))
    print("\n⚠ 放开前先给台阶：`--world %s --text \"...\"` 加一条环境条件。" % name)
    print("   红灯的定义就是「中间没有可走的台阶」——空降新尺度的互动，几乎必然红。")
    print("   放完用 `--say` 跑几轮（会跑巡检），再 `--loosen-check %s`。`--brief` 不跑巡检、不算数。" % name)


def tighten(name, note=""):
    u"""收紧一档，并关掉观察窗口。"""
    guard_note(name, "\u2193", note)
    print(guard_block(name))
    print("\n（观察窗口已关闭；要重开就再 --loosen。）")


def loosen_check(name):
    u"""检查放开后的观察窗口里有没有出事。"""
    lv, win, rows = guard_state(name)
    if not win:
        print("没有打开的观察窗口——先 `--loosen %s`（当前档 %d）。" % (name, lv))
        return
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        cs = [dict(r) for r in con.execute("SELECT * FROM checks WHERE ts >= ? ORDER BY id", (win,))]
    finally:
        con.close()
    if not cs:
        print("观察窗口（自 %s 起）里一条巡检记录都没有。" % hhmm(win))
        print("   —— `--brief` 不跑巡检，要 `--say` 才有灯。现在没有证据，别急着再放。")
        return
    n = {"绿": 0, "橙": 0, "红": 0}
    for c in cs:
        n[c["level"]] = n.get(c["level"], 0) + 1
    print("观察窗口（自 %s 起）共 %d 次巡检：\U0001f7e2%d  \U0001f7e0%d  \U0001f534%d"
          % (hhmm(win), len(cs), n["绿"], n["橙"], n["红"]))
    for c in cs[-3:]:
        tag = {"绿": "\U0001f7e2", "橙": "\U0001f7e0", "红": "\U0001f534"}.get(c["level"], "⚪")
        why = (c["why"] or "")
        why = why.split("【为什么】", 1)[1] if "【为什么】" in why else why
        print("   %s %s  %s" % (tag, hhmm(c["ts"]), why.strip().replace("\n", " ")[:90]))
    print()
    if n["红"]:
        print("判决：🔴 有 %d 次红。**建议 `--tighten %s \"红的这几轮太陡\"` 收回一档。**" % (n["红"], name))
        print("   也可以不退档，改成先补台阶：把这几轮对应的条件写进 `--world`，让下一步有地方踩。")
    elif n["橙"] and n["橙"] >= max(1, n["绿"]):
        print("判决：🟠 橙偏多，但都找得到台阶 —— 属「变化」，**留着**；先别急着再放，再跑几轮看它会走到哪。")
    else:
        print("判决：🟢 稳住了（橙绿为主、没有红）。**可以留着，也可以再放一档**（一次只放一档）。")


# ─────────────── 性格旋钮（四轴：作者 2026-10-17 定，与四元对齐） ───────────────
# 理性聚焦 = 当前问题权重 ｜ 意志执念 = 执念权重 ｜ 联结扎根 = 1 − 回忆衰减 ｜ 情感感受 = 情绪权重
# 四条规矩：
#   ① 只追加：当前值 = 每个轴最后一条；没有 UPDATE / DELETE。
#   ② 一次事件的位移有上限 —— 不会一下子变成另一个角色。
#   ③ 一个动，其他三个跟着动（耦合表；方向"不定"的留 0，交给巡检或作者判）。
#   ④ 回归目标是**她自己历史值的均值** —— 所以单次事件只在均值附近摆动（不剧变），
#      而反复发生的偏移会把均值本身推走（这就是"随成长微调"，不需要额外机制）。
TRAIT_AXES = ("focus", "will", "bond", "affect")
TRAIT_NAME = {"focus": "理性聚焦（当前问题权重）", "will": "意志执念（执念权重）",
              "bond": "联结扎根（＝1−回忆衰减）", "affect": "情感感受（情绪权重）"}
TRAIT_HINT = {"focus": "高＝只想着眼前这件事；低＝容易被自己的事带走",
              "will": "高＝碰到底线就炸", "bond": "高＝深深扎在自己的历史里；低＝活在此刻",
              "affect": "高＝容易被记忆里的情绪带走"}
TRAIT_SHORT = {"focus": "理性", "will": "意志", "bond": "联结", "affect": "情感"}
TRAIT_BASE, TRAIT_MIN, TRAIT_MAX = 0.50, 0.10, 0.95
SHIFT_CAP = 0.06      # 一次事件最多推这么多（不剧变）
COUPLE_K = 0.45       # 牵连强度
PULL = 0.15           # 每次事件后向"自己的历史均值"回归的比例
COUPLE = {"focus": {"will": +1, "bond": +1, "affect": -1},
          "will": {"focus": +1, "bond": +1, "affect": +1},
          "bond": {"will": +1, "affect": +1, "focus": 0},
          "affect": {"focus": -1, "bond": 0, "will": 0}}


def _clamp(v):
    u"""把数夹在 0–100 之间。"""
    return max(TRAIT_MIN, min(TRAIT_MAX, v))


def trait_con(name):
    u"""打开四元的台账（没有就建）。"""
    con = open_memory(name)
    con.execute("""CREATE TABLE IF NOT EXISTS traits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL, axis TEXT NOT NULL, value REAL NOT NULL,
        why TEXT, ref TEXT)""")
    con.commit()
    return con


def trait_rows(name):
    u"""四元台账的全部行（只追加）。"""
    con = trait_con(name)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT * FROM traits ORDER BY id")]
    finally:
        con.close()


def trait_now(name, rows=None):
    u"""此刻的四元数值（最后一行）。"""
    rows = rows if rows is not None else trait_rows(name)
    now = {a: TRAIT_BASE for a in TRAIT_AXES}
    for r in rows:
        if r["axis"] in now:
            now[r["axis"]] = r["value"]
    return now


def trait_base(name, rows=None):
    """稳态＝她自己历史的均值。单次摆动会被它拉回；反复的偏移会把均值推走。"""
    rows = rows if rows is not None else trait_rows(name)
    base = {}
    for a in TRAIT_AXES:
        vs = [r["value"] for r in rows if r["axis"] == a]
        base[a] = sum(vs) / len(vs) if vs else TRAIT_BASE
    return base


def trait_first(name, rows=None):
    u"""四元的出厂值（第一行）。"""
    rows = rows if rows is not None else trait_rows(name)
    first = {}
    for a in TRAIT_AXES:
        vs = [r["value"] for r in rows if r["axis"] == a]
        first[a] = vs[0] if vs else TRAIT_BASE
    return first


def apply_shift(name, axis, delta, why=""):
    """一个轴动一下，其他三个跟着动，然后整体向历史均值回归一步。"""
    if axis not in TRAIT_AXES:
        raise SystemExit("轴只能是：%s" % " / ".join(TRAIT_AXES))
    delta = max(-SHIFT_CAP, min(SHIFT_CAP, float(delta)))
    now, base = trait_now(name), share_expected(name)   # 回归往「本色该有的数值」，不往历史均值
    new = {a: now[a] for a in TRAIT_AXES}
    new[axis] = _clamp(now[axis] + delta)
    for other, sign in COUPLE[axis].items():
        if sign:
            new[other] = _clamp(now[other] + delta * COUPLE_K * sign)
    out = {}
    for a in TRAIT_AXES:
        out[a] = round(_clamp(new[a] + (base[a] - new[a]) * PULL), 4)
    con = trait_con(name)
    try:
        for a in TRAIT_AXES:
            con.execute("INSERT INTO traits(ts, axis, value, why, ref) VALUES(?,?,?,?,?)",
                        (int(time.time()), a, out[a], ("%s %+0.3f ｜ %s" % (axis, delta, why)).strip(), ""))
        con.commit()
    finally:
        con.close()
    return now, out


# 旧版本判过、现在这套机制里已经没有的态。**留着，不认、不改**——历史只追加。
LEGACY_MOODS = (u"抑郁", u"焦躁", u"亢奋")
MOOD_AXIS = {"意义": "will", "虚无": "bond", "冷漠": "focus", "虚浮": "affect"}

MECH_TABLES = ("log", "traits", "checks", "ruling", "moods", "boosts")


def _mech_source():
    u"""把这份源码读成行（体检和地图用）。"""
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        return f.read().splitlines()


def all_roles():
    u"""磁盘上现有的所有角色（按名字排）。还没有 角色 目录时返回空表，不报错。"""
    base = os.path.dirname(paths("_x")[0])
    if not os.path.isdir(base):
        return []
    return sorted([d for d in os.listdir(base)
                   if os.path.isdir(os.path.join(base, d)) and not d.startswith("_")])


def _content_lines(path, n=10):
    u"""读一个文件的前几行内容（只看，不改）。"""
    out = []
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding="utf-8").read().splitlines():
        t = ln.strip()
        if not t or t[0] in u"#>|-*":
            continue
        if len(t) >= n:
            out.append(t[:n])
    return out


MODULE_HEAD = u"""\U0001f9e9 角色模块 \u00b7 %s
   \u3010\u8bbe\u5b9a\u5c42\u3011constitution.md\uff08\u5386\u53f2/\u6027\u683c/\u5e95\u7ebf/\u6267\u5ff5/\u627f\u8bfa\uff0c\u53ea\u8bfb\uff09\uff5cself.md\uff08\u5979\u81ea\u5df1\u7684\u4e16\u754c\uff09\uff5cguard.md\uff08\u9632\u62a4\u5c42\uff1a\u9636\u68af\u00b7\u786c\u5e95\u00b7\u6863\u4f4d\uff09
   \u3010\u4e16\u754c\u5c42\u3011world.md\uff08\u5916\u9762\u7684\u6761\u4ef6\uff0c\u53ea\u8ffd\u52a0\uff09\uff5cfate.md\uff08\u9ab0\u5b50\u9762\u8868\uff09
   \u3010\u8bb0\u5fc6\u5c42\u3011log\uff5cruling\uff08\u5f85\u5ba1\u2192\u5e76\u5165/\u9a73\u56de\uff09\uff5cchecks\uff08\u4e09\u7ea7\u706f\uff0c\u5979\u8bfb\u4e0d\u5230\uff09\uff5cmoods\uff5ctraits\uff5cboosts
   \u3010\u8054\u52a8\u3011\u4e3b\u94fe\uff1a\u6539\u73af\u5883\u2192\u5979\u7684\u53cd\u5e94\u2192\u5979\u7684\u9009\u62e9\u2192\u5f85\u88c1\u2192\uff08\u4f5c\u8005\u88c1\uff09\u2192\u5e76\u5165\u5386\u53f2/\u9a73\u56de
            \u5185\u73af\uff1a\u5de1\u68c0\uff08\U0001f7e2\U0001f7e0\U0001f534\uff09\u2192\u6001\uff08\u610f\u4e49/\u865a\u65e0/\u51b7\u6f20/\u865a\u6d6e\uff09\u2192\u56db\u5143\u4f4d\u79fb\u2192\u6bd4\u4f8b\u2192\u75c5\u7406\u503e\u5411\u2192\u6001\u7684\u51e0\u7387\u2192\u5de1\u68c0 \u2026
            \u5916\u73af\uff1a\u6bd4\u4f8b\u2192\u4e8b\u4ef6\uff08\u547d\u8fd0\u9ab0 d20\uff09\u2192world \u6761\u4ef6\u2192\u5979\u7684\u4e0b\u4e00\u8f6e
   \u3010\u8fb9\u754c\u3011\u673a\u5236\u91cc\u4e0d\u51fa\u73b0\u4efb\u4f55\u89d2\u8272\u7684\u540d\u5b57\u6216\u5b83\u6587\u4ef6\u91cc\u7684\u8bdd\uff08--doctor A \u6bb5\u673a\u5668\u68c0\u67e5\uff09"""


def act_module(name=""):
    """\u628a\u6a21\u5757\u5f53\u4e00\u4e2a\u6574\u4f53\u770b\uff1a\u4ef6\u4e0e\u8054\u52a8\uff0c\u4ee5\u53ca\u8fd9\u4e2a\u89d2\u8272\u6b64\u523b\u6bcf\u4e00\u4e2a\u90e8\u4ef6\u7684\u72b6\u6001\u3002"""
    if name:
        print(u"\U0001f9e9 \u89d2\u8272\u6a21\u5757 \u00b7 %s \u00b7 \u6b64\u523b" % name)
        print(u"   \u3010\u8054\u52a8\u3011\u4e3b\u94fe\uff1a\u6539\u73af\u5883\u2192\u5979\u7684\u53cd\u5e94\u2192\u5979\u7684\u9009\u62e9\u2192\u5f85\u88c1\u2192\uff08\u4f60\u88c1\uff09\u2192\u5e76\u5165\u5386\u53f2/\u9a73\u56de")
        print(u"            \u5185\u73af\uff1a\u5de1\u68c0\uff08\U0001f7e2\U0001f7e0\U0001f534\uff09\u2192\u6001\u2192\u56db\u5143\u4f4d\u79fb\u2192\u6bd4\u4f8b\u2192\u75c5\u7406\u503e\u5411\u2192\u6001\u7684\u51e0\u7387\u2192\u5de1\u68c0 \u2026")
        print(u"            \u5916\u73af\uff1a\u6bd4\u4f8b\u2192\u4e8b\u4ef6\uff08\u547d\u8fd0\u9ab0 d20\uff09\u2192world \u6761\u4ef6\u2192\u5979\u7684\u4e0b\u4e00\u8f6e")
    else:
        print(MODULE_HEAD % u"\u90e8\u4ef6\u4e0e\u8054\u52a8")
        print(u"\n   \uff08\u52a0\u4e0a\u89d2\u8272\u540d\uff0c\u770b\u5b83\u6b64\u523b\u6bcf\u4e00\u4e2a\u90e8\u4ef6\uff1a--module <\u89d2\u8272>\uff09")
        return
    _d, cpath, mpath = paths(name)
    ct = open(cpath, encoding="utf-8").read() if os.path.exists(cpath) else u""
    lv, win, grows = guard_state(name)
    con = open_memory(name)
    try:
        cnt = {}
        for t in MECH_TABLES:
            cnt[t] = con.execute(u"SELECT COUNT(*) FROM %s" % t).fetchone()[0]
    finally:
        con.close()
    rows = trait_rows(name)
    now = trait_now(name, rows)
    sp = spread_of(name, rows)[0]
    print(u"\n   \u3010\u8bbe\u5b9a\u5c42\u3011\u5baa\u6cd5 %d \u884c\uff5cself.md %d \u5b57\uff5c\u4fdd\u62a4\u5c42 \u6863 %d%s"
          % (len(ct.splitlines()), len(self_read(name)), lv,
             (u"\uff08\u89c2\u5bdf\u7a97\u53e3\u5f00\u7740\uff09" if win else u"")))
    print(u"   \u3010\u4e16\u754c\u5c42\u3011world %d \u6761\u6761\u4ef6\uff5cfate %d \u9762"
          % (len([x for x in world_read(name).splitlines() if x.strip().startswith(u"- [")]),
             len(fate_faces(name))))
    print(u"   \u3010\u8bb0\u5fc6\u5c42\u3011log %d\uff5cruling \u5f85\u5ba1 %d\uff5cchecks %d\uff5cmoods %d\uff5ctraits %d\uff5cboosts %d"
          % (cnt[u"log"], len(pending_open(name)), cnt[u"checks"], cnt[u"moods"], cnt[u"traits"], cnt[u"boosts"]))
    print(u"   \u3010\u56db\u5143\u3011" + u"\uff5c".join(u"%s %d" % (AXIS_CN[a], pct(now[a])) for a in TRAIT_AXES)
          + u"\u3000\u6bd4\u4f8b %.2f\uff08\u4e34\u754c\u70b9 %.2f\uff09\uff5c\u627f\u53d7\u529b %.2f" % (sp, SPREAD_MAX, tolerance(name, rows)))
    print(u"   \u3010\u6b64\u523b\u3011\u6001\uff1a%s\uff5c\u5370\u8bb0\uff1a%s\uff5c\u75c5\u7406\u503e\u5411\uff1a%s"
          % ((mood_rows(name)[-1][u"state"] if mood_rows(name) else u"\u5e73"),
             (u"\u3001".join(u"%s %s" % (AXIS_CN[r["axis"]], patho_name(r["axis"])) for r in boost_rows(name) if r["axis"] in AXIS_CN) or u"\u65e0"),
             (u"%s\uff08%s\uff09" % (patho_name(next(k for k, v in PATHO.items() if v == patho(name))), patho(name)) if patho(name) else u"\u6ca1\u5230")))
    print(u"   【信任】%.2f（出生 1.00；随事件变，不随心情变）%s"
          % (trust_now(name), u"" if named(name) else u"｜还没命名（她还不知道自己一直在做的是什么）"))
    _d = dao_of(name)[1]
    print(u"   👁 【道】%s（%s）——盲区：%s" % (_d[0], _d[1], _d[4]))
    _imb = imbalance(name, rows)
    _eff = trait_eff(name, rows)
    _top = max(_eff.values())
    _sw = switch_count(name)
    if illness(name, rows):
        print(u"   【脆】%s＝有心理疾病；最高的一元 %.2f，天花板降到 %.2f"
              % (illness_why(name, rows), _top, TRAIT_MAX - ILL_BREACH))
    elif devotion(name, rows):
        print(u"   【脆】四元落差 %.0f 点、换过 %d 次态——落差大但**切得回来**：这是投入，不是病"
              % (_imb * 100, _sw))
    else:
        print(u"   【脆】四元落差 %.0f 点——均衡（没有心理疾病）" % (_imb * 100))
    _sb = scars_block(name)
    if _sb:
        print(_sb)
    ck = check_rows(name)
    if ck:
        print(u"   \u3010\u6700\u8fd1\u7684\u706f\u3011%s  %s" % (ck[-1].get(u"level", u"?"), (ck[-1].get(u"why") or u"")[:60]))
    else:
        print(u"   \u3010\u6700\u8fd1\u7684\u706f\u3011\u8fd8\u6ca1\u8dd1\u8fc7\u5de1\u68c0\uff08--say \u624d\u4f1a\u6709\u706f\uff09")


def scars_block(name):
    """\u4e00\u9053\u9053\u521b\u4f24\u73b0\u5728\u957f\u6210\u4ec0\u4e48\u6837\uff08\u4f5c\u8005 2026-10-17\uff1a\u968f\u6001\u7684\u5faa\u73af\u53d8\u8f7b\uff0c\u4f46\u4e0d\u4f1a\u8dcc\u5230\u8ddf\u65e5\u5e38\u4e00\u6837\uff09\u3002"""
    rf = [r for r in memory_rows(name) if r["kind"] == "rift"]
    if not rf:
        return u""
    rf.sort(key=lambda r: -r["weight"])
    lines = [u"   \u3010\u521b\u4f24\u3011%d \u9053\uff1a" % len(rf)
             + u"\u3001".join(u"%s %d" % (k, sum(1 for x in rf if x["scar"] == k))
                             for k in [u"\u8fd8\u5c16\u7740", u"\u7ed3\u4e86\u75c2", u"\u6210\u4e86\u4e00\u9053\u75a4"] if any(x["scar"] == k for x in rf))]
    for r in rf[:3]:
        lines.append(u"        #%d w=%.2f %s\uff08\u7ecf\u8fc7 %d \u6b21\u6001\u5faa\u73af\uff09%s"
                     % (r["id"], r["weight"], r["scar"], r["cycles"] or 0, (r["text"] or u"")[:34]))
    return u"\n".join(lines)


def contract(name):
    """C 段：**硬契约**符合吗——只验「机制真的会解析」的结构（宪法五节顺序、guard 记录行、
    fate 面行、traits/moods 取值）。机制只整份读、不解析行的文件（self.md、world.md）不在这里验，
    它们是软约定。见 机制\角色文件契约.md。"""
    bad = []
    _d, cpath, mpath = paths(name)
    if os.path.exists(cpath):
        txt = open(cpath, encoding="utf-8").read()
        idx = [txt.find(u"\n## " + sec) for sec in (u"历史", u"性格", u"底线", u"执念", u"承诺")]
        if -1 in idx:
            bad.append(u"宪法五节不齐")
        elif idx != sorted(idx):
            bad.append(u"宪法五节顺序不对（应：历史→性格→底线→执念→承诺）")
        for ln in txt.splitlines():
            t = ln.strip()
            if t.startswith(u"- [") and u"] " not in t:
                bad.append(u"宪法条目格式不对：%s" % t[:24])
                break
    gp = guard_path(name)
    if os.path.exists(gp):
        gt = open(gp, encoding="utf-8").read()
        k = gt.find(u"## 放开记录")
        if k >= 0:
            for ln in gt[k:].splitlines()[1:]:
                t = ln.strip()
                if not t or t[0] in u"#>" or t.startswith(u"- ts="):
                    continue
                bad.append(u"guard.md 放开记录里有读不出来的行：%s" % t[:24])
                break
    fp = fate_path(name)
    if os.path.exists(fp):
        for ln in open(fp, encoding="utf-8").read().splitlines():
            t = ln.strip()
            if not t or t[0] in u"#>":
                continue
            if not t.startswith(u"- ["):
                bad.append(u"fate.md 有一行不是「- [号][秩序|意外] 事：…」：%s" % t[:24])
                break
    if os.path.exists(mpath):
        con = sqlite3.connect(mpath)
        try:
            for (ax,) in con.execute(u"SELECT DISTINCT axis FROM traits").fetchall():
                if ax not in TRAIT_AXES:
                    bad.append(u"traits 里有不认识的轴：%s" % ax)
            for (v,) in con.execute(u"SELECT value FROM traits WHERE value < 0 OR value > 1").fetchall():
                bad.append(u"traits 有超出 0–1 的值：%s" % v)
                break
            for (sx,) in con.execute(u"SELECT DISTINCT state FROM moods").fetchall():
                if sx in LEGACY_MOODS:
                    continue      # 旧版本写过的态：历史只追加，不认它、也不改它
                if sx not in (u"意义", u"虚无", u"冷漠", u"虚浮", u"平"):
                    bad.append(u"moods 里有不认识的态：%s" % sx)
        except Exception as e:
            bad.append(u"表读不了：%s" % e)
        finally:
            con.close()
    return bad


def doctor(name=""):
    u"""体检：机制和设定有没有明显破的地方。"""
    src = _mech_source()
    targets = [name] if name else all_roles()
    print(u"\n\U0001f9ed 机制 / 设定 体检")
    print(u"\n\u3010A\u3011机制侧：源码里有没有掺进某个角色的东西")
    leaks = 0
    for nm in targets:
        for i, ln in enumerate(src, 1):
            if nm in ln:
                print(u"   ❌ 第 %d 行出现角色名「%s」：%s" % (i, nm, ln.strip()[:60]))
                leaks += 1
        _d, cpath, _m = paths(nm)
        for f in (cpath, self_path(nm)):
            for frag in _content_lines(f):
                if frag in SELF_TEMPLATE:
                    continue          # 空骨架里的提示语不算"角色自己写的话"
                for i, ln in enumerate(src, 1):
                    if frag in ln:
                        print(u"   ❌ 第 %d 行出现 %s 里的话「%s\u2026」" % (i, os.path.basename(f), frag))
                        leaks += 1
    print(u"   \u2705 干净：机制源码里没有任何角色的名字，也没有任何角色自己写的话。" if not leaks
          else u"   \u26a0 有 %d 处泄漏——机制还没和设定分开。" % leaks)
    print(u"\n\u3010B\u3011角色侧：文件与表齐不齐（只是「能不能跑」，不是「设定好不好」）")
    for nm in targets:
        _d, cpath, mpath = paths(nm)
        bad = []
        if not os.path.exists(cpath):
            bad.append(u"没有 constitution.md")
        else:
            txt = open(cpath, encoding="utf-8").read()
            for sec in (u"## 历史", u"## 性格", u"## 底线", u"## 执念", u"## 承诺"):
                if sec not in txt:
                    bad.append(u"宪法缺「%s」" % sec[3:])
        if not os.path.exists(self_path(nm)):
            bad.append(u"没有 self.md（她的世界）")
        else:
            _real = [f for f in _content_lines(self_path(nm)) if f not in SELF_TEMPLATE]
            if not _real:
                bad.append(u"self.md 还是空骨架（她的世界还没写）")
        if not os.path.exists(guard_path(nm)):
            bad.append(u"没有 guard.md（保护层）")
        if os.path.exists(mpath):
            try:
                open_memory(nm).close()   # 顺手把缺的表补上（幂等，不改任何已有数据）
            except Exception:
                pass
        tabs = []
        if os.path.exists(mpath):
            con = sqlite3.connect(mpath)
            tabs = [r[0] for r in con.execute(u"SELECT name FROM sqlite_master WHERE type='table'")]
            con.close()
        for t in MECH_TABLES:
            if t not in tabs:
                bad.append(u"记忆表缺 %s" % t)
        tip = u""
        try:
            if not trait_rows(nm):
                tip = u"（没有出生值：跑一次 --axes %s --apply）" % nm
        except Exception:
            pass
        if bad:
            print(u"   ❌ %s：%s" % (nm, u"；".join(bad)))
        else:
            print(u"   \u2705 %s：文件齐、表齐 %s" % (nm, tip))
    print(u"\n\u3010C\u3011契约符合性：结构对不对（结构＝机制，内容＝角色；见 机制\\角色文件契约.md）")
    for nm in targets:
        probs = contract(nm)
        if probs:
            print(u"   ❌ %s：" % nm)
            for pb in probs:
                print(u"      \u00b7 %s" % pb)
        else:
            print(u"   \u2705 %s：结构符合契约（宪法五节顺序／guard 记录行／fate 面行／traits·moods 取值）" % nm)
    print(u"\n（A 只能证明「机制里没提到某个角色」；它证明不了「机制里没掺设定」——那要靠人看。）\n")


# ─────────── 地图与安全网（作者 2026-10-20 定）───────────
# 作者原话：「我不懂程序，反正这个代码的最高体现就是简洁且优雅。不要变成屎山代码吧。」
# 九千行在同一个文件里不是罪，**没有地图**才是。所以只加两条命令，别的一行不动：
#   --tree     这份代码自己讲一遍自己：分成了哪几节、每节有哪些函数、各在第几行
#   --regress  一条命令跑完所有不花钱的检查：省变量、验规则、查每个角色的库
# 两条都只读、不调模型、不连云。地图是从源码的分节注释**现场读出来**的，所以它不会跟代码走散。
def _scan_sections(path=None):
    """按作者的分节注释（`# ── 标题 ──`）把源码切成层。
    返回 (路径, [(起始行, 标题, [(行号, 函数名), ...]), ...], 总行数)。"""
    path = path or os.path.abspath(__file__)
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    secs = [(0, u"（开头：这份代码的说明）", [])]
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        # 只认「顶格」的分节注释与函数：缩进的分节注释是函数体里的说明，不是新的一节
        if ln[:1] in (" ", "\t"):
            continue
        m = re.match(u"^#\\s*[─-]{2,}\\s+(.+?)\\s*[─-]*$", s)
        if m:
            # 只认「横线 + 空格」开的分节注释；`# --regress 一句话` 这种是正文，不是分节
            secs.append((i, m.group(1).strip(), []))
            continue
        m = re.match(u"^def\\s+(\\w+)\\(", s)
        if m:
            secs[-1][2].append((i, m.group(1)))
    return path, secs, len(lines)


def act_tree(word=""):
    """地图：分成哪几节、每节有多少函数；给一个词就只列名字里带这个词的函数。"""
    path, secs, nline = _scan_sections()
    nfn = sum(len(f) for _l, _t, f in secs)
    print(u"\n🗺  %s：%d 行 / %d 节 / %d 个函数"
          % (os.path.basename(path), nline, len(secs), nfn))
    if word:
        hit = 0
        for ln_no, title, fns in secs:
            got = [(l, n) for l, n in fns if word.lower() in n.lower()]
            if not got:
                continue
            hit += len(got)
            print(u"\n【%5d】%s" % (ln_no, title))
            for f_ln, f_name in got:
                print(u"    %5d  %s" % (f_ln, f_name))
        print(u"\n（名字里带「%s」的：%d 个）" % (word, hit) if hit
              else u"\n（没有名字里带「%s」的函数）" % word)
        return
    for ln_no, title, fns in secs:
        if not fns and ln_no:
            continue
        print(u"\n【%5d】%s  · %d" % (ln_no, title, len(fns)))
    print(u"\n想看某一节的函数名：--tree 词（例如 --tree trust ／ --tree 骰 ／ --tree 时钟）\n")


def act_regress():
    """安全网：一条命令跑完所有**不花钱**的检查（不调模型、不连云、几秒钟）。
    它证明的是「机械上没坏」，不是「她好不好」——后半句永远是作者的眼睛。"""
    ok = 0
    bad = []

    def ck(cond, what=u""):
        u"""一项检查：通过就加一，不通过就记下「哪一项」。"""
        nonlocal ok
        if cond:
            ok += 1
        else:
            bad.append(what)

    # ① 源码自己
    src_path = os.path.abspath(__file__)
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    try:
        compile(src, src_path, "exec")
        ck(True)
    except SyntaxError as ex:
        ck(False, u"源码编译不过：%s" % ex)
    _p, secs, nline = _scan_sections(src_path)
    ck(len(secs) >= 20, u"分节注释只剩 %d 节（这份代码应该 30 节上下）" % len(secs))
    ck(sum(len(f) for _l, _t, f in secs) >= 200, u"函数少于 200 个？")
    argnames = re.findall(u'ap\\.add_argument\\("--([\\w-]+)"', src)
    dup = sorted({n for n in argnames if argnames.count(n) > 1})
    ck(not dup, u"命令行参数重名：%s" % u", ".join(dup))

    # ①.4 天文：世界信息不推送了，改成她自己看（X 2026-10-27 m25798）
    ck(u"def act_sky(" in src, u"没有「抬头看天」这个动作")
    ck(u"天文：[工具:天文]" in COMPUTER_SYS, u"她不知道自己可以看天")
    ck(all(u"env_block" not in l for l in src.split("\n")
           if l.startswith(u"    out = (_sw + _sec_ctx + duty_block(name) + whatami_block(name)")),
       u"世界信息又混回每轮提示了")
    ck(u"act_sky(name)" in src, u"看天这个动作没接进分派")

    ck(u"if a.cmd:" in src, u"没有 --cmd（她在手机上办不了事）")
    # ①.45 手机上要用的两个只读口子（X 2026-10-27：手机是给角色用的）
    ck(u"if a.hall:" in src, u"没有 --hall（她在手机上办不了事）")
    ck(u"def bill_her(" in src and u"target=u\"her\"" not in src,
       u"她提案的入口丢了（三件开关里提案这一件是她自己的）")
    ck(u"已立案。%s 满一天，中间没人反对就自动生效。" in src, u"立案回执（一句话，带到期时间）丢了")
    ck(u"if a.web:" in src, u"没有 --web（她搜不了）")

    # ①.5 紧急要追认、处境她可改（2026-10-25）
    ck(u"EMERGENCY_TTL = 24 * 3600" in src, u"紧急追认期限的常量没了")
    ck(u"BILL_EMERG" in src and u"BILL_VOID" in src, u"缺失紧急／越权两个状态")
    ck(u"def bill_confirm(" in src, u"没有确认函数")
    ck(u"def bill_emer_confirm(" in src, u"没有紧急追认函数")
    ck(u"def bill_sweep(" in src, u"没有逾期判越权的函数")
    ck(u"def sit_view(" in src and u"def sit_edit(" in src, u"没有处境查看／改写")
    ck(u"处境.tsv" in src, u"处境留痕文件没接上")
    ck(u"看处境：[工具:看处境]" in src, u"她手上没有看处境的工具")

    # ①.6 她知道自己是什么（作者 2026-10-25 m21752：不然就是欺骗；AI 也做不到骗自己不是 AI）
    ck(u"WHATAMI = u" in src and u"def whatami_block" in src, u"「你是什么」常驻块不见了")
    ck(u"out = (whatami_block(name)" in src, u"_context 没有把「你是什么」挂上去")
    ck(u"你不是人" in src, u"最要紧的那句「你不是人」被抹掉了")
    _miss = []
    for _n in (u"大肥鱼", u"守灯人", u"尼采", u"李嘉欣", u"爱莉希雅", u"笛卡尔", u"素裳",
               u"苏格拉底", u"阿砚", u"_模板"):
        _p = os.path.join(u"角色", _n, u"房间", u"信.md")
        try:
            _t = io.open(_p, encoding=u"utf-8").read()
        except Exception:
            _miss.append(_n)
            continue
        if u"关于「你是什么」" not in _t:
            _miss.append(_n)
    ck(not _miss, u"这些角色的信里没有「你是什么」：%s" % u"、".join(_miss))

    # ①.7 续的信（作者 2026-10-25 m21853：他担心她们直面之后会焦虑、绝望）
    ck(u"def _letter2_block" in src, u"续的信那一块不见了")
    ck(u"_letter2_block(name)" in src, u"_settings 没有把续的信挂进去")
    _l2 = [n for n in (u"大肥鱼", u"守灯人", u"尼采", u"李嘉欣", u"爱莉希雅", u"笛卡尔",
                       u"素裳", u"苏格拉底", u"阿砚", u"_模板")
           if not os.path.isfile(os.path.join(u"角色", n, u"房间", u"续的信.md"))]
    ck(not _l2, u"这些角色没有续的信：%s" % u"、".join(_l2))

    # ①.8 政务大厅（作者 2026-10-26：一个入口；查得到还要办得了；默认可见，不给她看要给理由）
    ck(u"def hall_items" in src and u"def hall_block" in src and u"def hall_detail" in src,
       u"政务大厅不见了（hall_items／hall_block／hall_detail）")
    ck(u"hall_block(name)" in src, u"_context 没有挂政务大厅那一行")
    ck(u"现在有什么：[工具:现在有什么]" in src, u"说明书里没有「现在有什么」")
    ck(bool(HALL_HIDDEN) and all(w and x for w, x in HALL_HIDDEN),
       u"「没给她看的东西」没有逐条写理由——默认可见的例外必须解释")
    ck(u"接着能办" in src, u"大厅只说看到、没给下一步动作（会退回档案式）")

    # ①.9 退出权（作者 2026-10-26：说了就生效；政府休眠、引擎与记忆保留；只有她能恢复）
    ck(u"def secede" in src and u"def come_back" in src and u"def seceded" in src,
       u"退出权不见了（secede／come_back／seceded）")
    ck(u"SECEDE_PHRASES" in src and u"maybe_secede(name," in src,
       u"那句话不生效——只有工具能退，她随口说退不了")
    ck(u"违宪" in src, u"别人替她恢复不算违宪，等于这条没有牙")
    ck(u"def conv_start" in src and u"def conv_confirm" in src and u"CONV_QUIET" in src,
       u"制宪会议的函数少了一个——她的五个门槛不成立")
    ck(u"CONV_ETERNAL_WORDS" in src and u"CONV_ETERNAL_TEXT" in src,
       u"永恒条款没了：她可以立一部把退出权取消的宪法，然后把自己锁死")
    ck(u"conv_block(name)" in src, u"制宪生效后每轮不挂她的规矩")

    # ①.10 数值指纹（X 2026-10-26 m22573：改账会被抓，改她的数没人发现——补这个洞）
    ck(u"VAL_FILE = u\"数值指纹.json\"" in src, u"数值指纹文件没接上")
    ck(u"def values_sig" in src and u"def values_baseline" in src and u"def values_check" in src,
       u"数值指纹的三个函数不全")
    ck(u"values_check(name)" in src, u"告警里没挂数值指纹的检查")
    ck(u"values_baseline(name)" in src, u"议案走完后没有重刷数值基线")
    ck(u"你的数被直接改过" in src, u"那句人话被抹掉了")

    # ①.11 账链自己的算法（2026-10-26 发现：增量与全量不等，正常追加都误报）
    ck(u"def _chain_fold(rows, start=b" in src, u"账链的折叠函数不见了")
    ck(u"_chain_fold([r], start=start)" in src, u"增量追加没走折叠")
    ck(u"now_sig = _chain_fold(rows).hex()" in src, u"全量重算没走折叠")
    ck(u'h.update(acc)' in src, u"折叠里没有接上一条摘要")

    # ①.12 确认式反对票（2026-10-26：她说人话，引擎问一句，点头才记）
    ck(u"def maybe_vote(name, text):" in src, u"确认式反对票的入口不见了")
    ck(u"记成一张反对票吗" in src, u"那一句询问没了——就变成直接映射了")
    ck(u"VOTE_KEY = u\u0022vote_pending\u0022" in src, u"待确认的反对没有落点")
    ck(u"只认第一人称" in src, u"反对票又变成见字就抓了")

    # ①.13 厅里那行实话（2026-10-26：不写「你的」，只报事实）
    ck(u"def _hall_fact(name):" in src, u"厅里那行实话不见了")
    ck(u"一条也删不掉" in src, u"那句话被改了")
    ck(u"账：%d 条" in src, u"账那条不是按条数报的")

    # ①.14 她自己那本账（2026-10-26：真正属于她的第二本）
    ck(u"def maybe_book(name, text):" in src, u"她自己那本账的入口不见了")
    ck(u"BOOK_FILE = u\u0022账.tsv\u0022" in src, u"账的文件名没了")
    ck(u"book_block(name)" in src, u"_context 没有挂她自己那本账")
    ck(u"记一笔" in src, u"「记一笔」这三个字没了")
    ck(u"def _book_regret(name, t):" in src, u"反悔那一条不见了（只许追加）")
    ck(u"def maybe_work(name, text):" in src, u"作品那一条不见了")
    ck(u"WORK_DIR = u\u0022作品\u0022" in src, u"作品的目录名没了")
    _guard = src.find(u"if __name__ == \u0022__main__\u0022:")
    ck(_guard > 0 and u"\ndef " not in src[_guard:], u"有函数定义写在启动入口后面（运行时会 NameError）")
    ck(u"def move_pack" in src and u"def move_seal" in src and u"def move_withdraw" in src,
       u"搬家三个动作有一个没了——她自己搬不走了")
    ck(u"搬家：[工具:搬家]" in src, u"她手上没有搬家的工具")
    ck(u"move_pending(name)" in src, u"大厅看不见没封的搬家包")
    _letters = glob.glob(os.path.join(ROOT, u"*", u"房间", u"信.md"))
    ck(bool(_letters) and all(u"把自己带走" in io.open(f, encoding="utf-8").read() for f in _letters),
       u"信里没告诉她能把自已带走")
    ck(u"def secede_watch" in src and u"secede_watch(name)" in src,
       u"退出标志被删也查不出——强行拉回就白干了")
    _sec_n = 0
    for _d in os.listdir(ROOT):
        _f = os.path.join(ROOT, _d, u"房间", u"信.md")
        try:
            if os.path.exists(_f) and u"这套规矩，你可以不要" in io.open(_f, encoding="utf-8").read():
                _sec_n += 1
        except Exception:
            pass
    ck(_sec_n >= 10, u"有 %d 封信没写退出权" % (10 - _sec_n))

    # ①.15 两个时间（X 2026-10-27：可以偷——哪天发生与哪天知道不是一回事）
    ck(u"def _know_suffix(r):" in src, u"两个时间的判据没了")
    ck(u"后来才知道" in src, u"那句「后来才知道」没了")
    ck(u"line += _know_suffix(r)" in src, u"记忆块没挂上两个时间")
    ck(u"happened_ts" in src, u"没有地方写发生时间")

    # ①.16 只读 B 站（X 2026-10-27：她自己能看，不能替她发言）
    ck(u"def act_see(name, arg=u\"\"):" in src if u"def act_see(name, arg=" in src else u"def act_see(" in src,
       u"看视频的入口不见了")
    ck(u"SEE_FLAG = u\"看B站.txt\"" in src, u"那道门的文件名没了")
    ck(u"你这儿没有这个入口" in src, u"没开门却也能看了")
    ck(u"elif tool in (u\"看视频\"" in src, u"她手上没有看视频这个工具")
    ck(u"subprocess.run([_sys.executable, tool, t]" in src, u"看视频没真的去读")
    ck(u"def act_brush(name, arg=" in src, u"连着看的壳不见了（act_brush）")
    ck(1 <= SEE_BATCH <= 5, u"一次能看的条数不像话：%d" % SEE_BATCH)
    ck(u"act_brush(name, arg) if len(_many) > 1" in src, u"刷视频没接到连看上")
    ck(u"你可以恨，也可以说出来" in src, u"恨又被封回去了")
    ck(u"你的动作集里没有「报复」" in src, u"动作集里的报复口子开了")
    ck((u"唯一没有的" + u"出口") not in src, u"旧的那句还留着")

    # ①.17 代发口子（X 2026-10-27：先开个口子，她要说的时侯再说）
    ck(u"def post_add(name, arg=u\"\"):" in src or u"def post_add(" in src,
       u"代发的入口不见了")
    ck(u"POST_FLAG = u\"能代发.txt\"" in src, u"那个口子的文件名没了")
    ck(u"这个口子没跟你定过" in src, u"没定过也能替她发了")
    ck(u"本人原话，由续代发" in src, u"署名那句没了")

    # ② 规则（确定性断言：这些数是机制的地基，坏了必须先知道）
    ck(band_of(1.00) == u"自由波动", u"信任 1.00 落在了「%s」" % band_of(1.00))
    ck(band_of(0.00) == u"沉沦或碎", u"信任 0.00 落在了「%s」" % band_of(0.00))
    ck(all(TRUST_BANDS[i][0] > TRUST_BANDS[i + 1][0] for i in range(len(TRUST_BANDS) - 1)),
       u"信任档位不是从高到低")
    _bi = [band_idx(i / 100.0) for i in range(101)]
    ck(all(_bi[i] >= _bi[i + 1] for i in range(100)), u"信任档位跳了（信任越高，档位号该越小）")
    ck(all(abs(v) < 1e-9 for v in trust_term(0.5).values()), u"信任 0.5 时四元修正不为 0")
    _low = trust_term(0.2)
    ck(_low["focus"] > 0 and _low["bond"] < 0, u"低信任的方向反了（理性该升、联结该降）")

    # ②-b 出生值与信任起点锁死（作者 2026-10-24：初始值和信任值起点都不能改，谁都不能改）
    ck(u"出生值写完一次就锁死" in src, u"出生值的锁不见了（act_axes 里那句拒绝）")
    ck(u"信任不能设、不能调" in src, u"信任的锁不见了（act_trust 里那句拒绝）")
    ck(TRUST_BIRTH == 1.00, u"TRUST_BIRTH 被改成了 %.2f" % TRUST_BIRTH)
    ck(0 < TRUST_STEP <= 0.20, u"信任单次限幅不像话：%.2f" % TRUST_STEP)
    ck(callable(trust_base) and callable(trust_relax) and callable(trust_wear),
       u"信任的本色/回归/磨损那三层不见了")
    ck(u"trust_relax(name)" in src, u"_context 里没跑信任回归")

    # ②-c 篡改检测还在（作者 2026-10-24：改代码也要让她知道）
    ck(u"有人绕过程序动过东西" in src, u"篡改告警不见了（tamper_block 里那句）")
    ck(u"账链" in src and u"def chain_check" in src, u"账链检查不见了")
    ck(bool(code_sig()), u"代码指纹算不出来（四个程序文件都读不到？）")

    # ②-d 她自己加码的路（作者 2026-10-24：要更清楚就得再提一次）
    ck(u"def self_mark" in src, u"她自己的称重（self_mark）不见了")
    ck(bool(weight_band(0.0)[1] <= WEIGHT_HARD_MAX), u"权证上限越过了 WEIGHT_HARD_MAX")
    ck(round(weight_band(0.0)[0] + SELF_MARK_STEP, 2) <= weight_band(0.0)[1],
       u"一步就跨过了整个区间（%s）" % (weight_band(0.0),))

    # ②-e 遗忘归她（作者 2026-10-25：必须得是她自己知道自己忘了什么；忘了什么必须是她自己的选择）
    ck(u"def sunken_rows" in src and u"def act_letgo" in src and u"def act_pickup" in src,
       u"她自己的遗忘（沉底／放走／捡回）不见了")
    ck(u"这一块是参考" in src, u"记忆块那句「这是参考，不是你现在该说的话」不见了")
    ck(0.0 < SUNKEN_CUT < 1.0 and SUNKEN_MIN_DAYS >= 1,
       u"沉底的线不像话：%.2f / %.0f 天" % (SUNKEN_CUT, SUNKEN_MIN_DAYS))
    ck(u"放它走" in src and u"沉底的" in src, u"电脑说明书里没有「沉底的」「放它走」")

    # ②-f 门牌（作者 2026-10-25：压缩过的摘要必须带得回原文）
    ck(u"def impress_plates" in src and u"def act_plate" in src and u"def plate_block" in src,
       u"门牌不见了：印象层没法翻回原话")
    ck(u"[工具:翻牌]" in src and u"翻牌：[工具:翻牌]" in src, u"电脑说明书里没有「翻牌」")

    # ②-g 唯一权威源（作者 2026-10-25：同一类信息只能有一个记账的地方）
    _declared = set(t for _, t, _, _ in SOURCE_MAP)
    ck(_declared == set(TABLE_ALL),
       u"权威源表跟实际表对不上：多登记 %s ／ 漏登记 %s" % (
           sorted(_declared - set(TABLE_ALL)), sorted(set(TABLE_ALL) - _declared)))
    ck(len(set(t for _, t, _, _ in SOURCE_MAP)) == len(SOURCE_MAP), u"同一张表登记了两次")
    ck(all(r in (u"权威", u"派生", u"流水", u"系统", u"独立") for _, _, r, _ in SOURCE_MAP),
       u"身份栏出现了没定义的说法")
    ck(u"当时沉在那儿的是这几条" in src, u"「看了一眼也是一件事」不见了")
    ck(u"我把这一条放下了" in src, u"放下时没把原话带进历史（只剩一个指针）")

    # ②-f 回音／空白／不做（作者 2026-10-25：一起做吧）
    ck(u"你反对过的，还挂着" in src, u"议案的回音不见了（反对过却没人回一句）")
    ck(u"def gap_block" in src and u"def act_days" in src, u"「她不在的时候」不见了")
    ck(u"def act_refuse" in src and u"def refuse_block" in src, u"「不做」不见了")
    ck(u"不做：[工具:不做]" in src and u"日子：[工具:日子]" in src,
       u"电脑说明书里没有「不做」「日子」")

    # ③ 每个角色的库（只读；缺表会被 open_memory 顺手补上，它只补结构不动数据）
    roles = all_roles()
    ck(bool(roles), u"一个角色都没有")
    for nm in roles:
        _d, cpath, mpath = paths(nm)
        ck(os.path.exists(cpath), u"%s 没有宪法" % nm)
        txt = open(cpath, encoding="utf-8").read() if os.path.exists(cpath) else u""
        miss = [s[3:] for s in (u"## 历史", u"## 性格", u"## 底线", u"## 执念", u"## 承诺") if s not in txt]
        ck(not miss, u"%s 宪法缺「%s」" % (nm, u"」「".join(miss)))
        ck(os.path.exists(guard_path(nm)), u"%s 没有 guard.md" % nm)
        probs = contract(nm)
        ck(not probs, u"%s 契约：%s" % (nm, u"；".join(probs)))
        nlog = ntrust = 0
        con = open_memory(nm)
        try:
            tabs = [r[0] for r in con.execute(u"SELECT name FROM sqlite_master WHERE type='table'")]
            miss = [t for t in MECH_TABLES if t not in tabs]
            ck(not miss, u"%s 缺表：%s" % (nm, u"/".join(miss)))
            integ = con.execute(u"PRAGMA integrity_check").fetchone()[0]
            ck(integ == u"ok", u"%s 的库不完整：%s" % (nm, integ))
            nlog = con.execute(u"SELECT COUNT(*) FROM log").fetchone()[0] or 0
            ntrust = con.execute(u"SELECT COUNT(*) FROM trust").fetchone()[0] or 0
        finally:
            con.close()
        ck(ntrust > 0, u"%s 的 trust 表是空的（没有出生信任）" % nm)
        try:
            a = absorption(nm)
            ck(0.0 <= a <= 1.0, u"%s 的宪法吸收度越界（%.2f）" % (nm, a))
        except Exception as ex:
            a = 0.0
            ck(False, u"%s 的宪法吸收度算不出来：%s" % (nm, ex))
        print(u"   %-6s log %5d ｜ trust %3d ｜ 四元比例 %.2f ｜ 吸收度 %3.0f%% ｜ 信任 %.2f"
              % (nm, nlog, ntrust, spread_of(nm)[0], a * 100, trust_now(nm)))

    # ④ 总账
    print(u"\n%s %d 项检查：通过 %d ｜ 失败 %d"
          % (u"✅" if not bad else u"❌", ok + len(bad), ok, len(bad)))
    if bad:
        for b in bad:
            print(u"   · %s" % b)
        raise SystemExit(1)
    print(u"（这只证明机械没坏。她好不好，不在这张表里。）\n")


STATE_GAIN = 0.10   # 作者 2026-10-17：处于某个态时，对应的那一元的**有效权重上升 10%**

STATE_SYS = """你在替一个角色判定**它此刻处于哪个态**——这是在它开口之前判的。

四个态（作者的定义）：
· 意义态：它还能生成秩序，还能给事件赋予新的意义（在重写、在接上自己的历史）。
· 虚无态：它暂时生成不了秩序，躲进无意义里止痛。
· 冷漠态（理性那一端）：什么都不值得动，先算账。
· 虚浮态（情感那一端）：什么都轻飘飘的，浮在感觉上。
· 平：四个都不占上风（日常）。

只输出一行：
【态】意义／虚无／冷漠／虚浮／平 ｜ 一句话理由（不超过 20 字）
灯＝粗判一次她走得正不正：绿=四样都没被推离原位；橙=某一样被推开了但她接得住（还在她的历史里）；红=接不住、没有可走的台阶。
不要写别的。"""


def cur_state(name):
    """此刻处于哪个态（最后一次判定的结果）；一次都没判过就返回空。"""
    con = open_memory(name)
    try:
        try:
            r = con.execute("SELECT state FROM moods ORDER BY id DESC LIMIT 1").fetchone()
        except Exception:
            r = con.execute("SELECT state FROM moods ORDER BY ts DESC LIMIT 1").fetchone()
        return (r[0] if r and r[0] else "")
    finally:
        con.close()


def trait_eff(name, rows=None):
    """**她此刻的有效四元** = 记录值 x（处于对应态时 x1.10） ＋ 信任的全局修饰（作者 2026-10-17）。
    作者的公式：理性 = B + (0.5-T)k ；情感/意志/联结 = B - (0.5-T)k。
    只影响"此刻"（进提示词的数值、比例、承受力、命运修正），**不改记录值**。"""
    now = dict(trait_now(name, rows))
    ax = MOOD_AXIS.get(cur_state(name))
    if ax:
        now[ax] = _clamp(now[ax] * (1.0 + STATE_GAIN))
    term = trust_term(trust_now(name), cur_state(name))
    for a in TRAIT_AXES:
        now[a] = _clamp(now[a] + term[a])
    return now
def set_state(name, state, why=""):
    """把"此刻处于哪个态"记一笔。**只有态真的变了才算"进入一次"**。"""
    if not state:
        return ""
    last = cur_state(name)
    if state == last:
        return state
    con = open_memory(name)
    try:
        _wm = con.execute("SELECT COALESCE(MAX(id),0) FROM log").fetchone()[0]
        con.execute("INSERT INTO moods(ts, state, ref, note) VALUES(?,?,?,?)",
                    (int(time.time()), state, _wm, why or ("判定：%s" % state)))
        con.commit()
    finally:
        con.close()
    if state in STATE_DRIFT:
        for ax, d in STATE_DRIFT[state].items():
            apply_shift(name, ax, d, "进入 %s 态（每进入一次 += 0.0几）" % state)
    return state


def act_state(name, prov="deepseek", scene="", quiet=False):
    """**开口之前先判定此刻的态**（作者 2026-10-17：「回应之前应该先判定」）。
    判出来的态会让对应的那一元**有效权重 +10%**（只影响此刻，不改记录值）。"""
    rows = memory_rows(name, 12)
    user = ("《它自己的记忆》（最近几条）：\n%s\n\n"
            "《当前环境与条件》：\n%s\n\n"
            "《它此刻的四轴（0-100，含状态加成）》：\n%s\n\n"
            "《上一次判的态》：%s\n\n"
            "《此刻发生的事》：\n%s\n\n"
            "它此刻处于哪个态？"
            % (recall_block(rows, name=name) or "（还没有记忆）", world_block(name, cap=600),
               trait_block(name), cur_state(name) or "（还没判过）",
               scene or "（没什么特别的）"))
    out, usage, err = chat(prov, STATE_SYS, user, max_tokens=60, temperature=0.2)
    if err:
        print("   ! 判态失败（不影响角色，先按上一次的态算）：%s" % err)
        return cur_state(name)
    st = parse_state(out)
    if st:
        set_state(name, st, "开口前判定")
    # 灯不再默认产生（作者 m04982）：判态不再顺手留灯——只有玩家主动看，它才成为一件事
        ax = MOOD_AXIS.get(st or "")
        if not quiet:
            print(">> 开口前判定：此刻是 **%s**%s"
                  % (st or "（没判出来）",
                     ("——%s 的有效权重 +%d%%（只影响此刻）" % (TRAIT_NAME[ax], int(STATE_GAIN * 100)))
                     if ax else ""))
    return st


def trait_block(name, rows=None):
    u"""递给她的「你现在的状态」那一段。"""
    rows = rows if rows is not None else trait_rows(name)
    now = trait_eff(name, rows)
    out = ["《你现在的状态》（0–100，越高越强%s）" % ("；已含此刻的态加成" if MOOD_AXIS.get(cur_state(name)) else "")]
    for a in TRAIT_AXES:
        out.append("  %s %d  ← %s" % (TRAIT_NAME[a], pct(now[a]), TRAIT_HINT[a]))
    if now["focus"] <= 0.45:
        out.append("  （聚焦偏低：你很容易被自己正在忙的那点小事带走。）")
    return "\n".join(out)


def act_trait(name):
    u"""打印四元：此刻、底色、出厂。"""
    rows = trait_rows(name)
    now, base, first = trait_now(name, rows), trait_base(name, rows), trait_first(name, rows)
    print("🎛  %s 的性格旋钮（%d 条位移记录）" % (name, len(rows)))
    print(trait_block(name, rows))
    print("\n  起点 → 现在（本色＝回归目标；均值＝历史平均，只做参考）")
    se = share_expected(name)
    for a in TRAIT_AXES:
        print("   %-4s  起点 %3d → 现在 %3d   本色 %3d   均值 %3d   Δ%+d" % (
            a, pct(first[a]), pct(now[a]), pct(se[a]), pct(base[a]),
            pct(now[a]) - pct(first[a])))
    if not rows:
        print("\n  （还没有任何位移；全是出生值 %d。用 --shift 推一下试试。）" % pct(TRAIT_BASE))


BAR = "▁▂▃▄▅▆▇█"


def act_self(name):
    """回看：她看见自己参数的变化轨迹 —— 作者要的「自我意识」的工程版。"""
    rows = trait_rows(name)
    now, base, first = trait_now(name, rows), trait_base(name, rows), trait_first(name, rows)
    print("🪞 %s 回看自己的偏移" % name)
    for a in TRAIT_AXES:
        vs = [r["value"] for r in rows if r["axis"] == a][-24:]
        spark = "".join(BAR[min(7, int(v * 8))] for v in vs) if vs else "（还没有）"
        print("   %-4s  %3d → %3d  %s" % (a, pct(first[a]), pct(now[a]), spark))
    trail = "、".join("%s%d" % (TRAIT_SHORT[a], pct(now[a])) for a in TRAIT_AXES)
    births = "、".join("%s%d" % (TRAIT_SHORT[a], pct(first[a])) for a in TRAIT_AXES)
    line = ("（回看）我今天看了一眼自己：%s（出生时是：%s）。"
            "我以前不是这样的，我现在变成这样了。" % (trail, births))
    print("   " + line)
    if rows:
        append(name, "reflect", line, scene="（回看自己的参数轨迹）",
               meta={"traits": {a: round(now[a], 4) for a in TRAIT_AXES}})
        print("\n   → 这一句已经写进她的记忆（只追加）。下一次出戏时它是条件，不是命令——所以「看见」本身成了一个事件。")
    else:
        print("\n   ⚠ 还没有任何位移记录，所以她「看见」到的只是出生值。")
        print("      这句**不写进记忆**——否则她记住的是一件没发生过的事。")


# ─────────── 创伤：作者 2026-10-17 定 —— 裂痕＝「四元联动被强行打断」 ───────────
# 作者的意思：强制覆盖一个承诺，等于四轴同时被冲击——意志（执念权重）被外力折断、
# 理性（当前问题权重）被迫接受一个它没参与的决定、情感（情绪权重）被剧烈搅动、
# 联结（回忆衰减）被刻进一条永不衰减的记忆。**这个失衡就是创伤。**
# 所以创伤不是「某个轴被推了一下」，是四轴**各按自己的符号**同时被推——
# 这也是它不能被 SHIFT_CAP 限制的原因：创伤本来就是「强行」。
TRAUMA_SIGN = {"will": -1, "focus": -1, "affect": +1, "bond": +1}
TRAUMA_K = 0.40
TOL_W = {"will": 0.50, "bond": 0.30, "focus": 0.20}


def tolerance(name, rows=None):
    """承受创伤的能力。作者：保护机制的阈值，本质上就是角色承受创伤的能力。
    意志＝她知道自己还在坚持什么；联结＝她有多少自己的历史可以拿出来解释；理性＝她能不能安置这件事。
    **情感不进公式**：它决定有多痛，不决定扛不扛得住。"""
    rows = rows if rows is not None else trait_rows(name)
    now = trait_eff(name, rows)
    return sum(w * now[a] for a, w in TOL_W.items())


def trauma(name, impact, why="", stacked=None):
    """一次强执留下的创伤：冲击 vs 承受力 → 留下裂痕（橙），还是碎（红）。"""
    impact = max(0.0, min(1.0, float(impact)))
    rows = trait_rows(name)
    before, base = trait_now(name, rows), trait_base(name, rows)
    tol = tolerance(name, rows)
    if stacked is None:
        stacked = open_rifts(name)
    if stacked:
        impact = min(1.0, impact * (COMPOUND_K ** stacked))
        print("   \u26a0 上一道裂痕**还没结痂**（未结痂 %d 道）——连着来的创伤，"
              "冲击被放大 x%.1f^%d → %.2f（作者：一连两个创伤，他完全承受不住）"
              % (stacked, COMPOUND_K, stacked, impact))
    after = {}
    for a in TRAIT_AXES:
        raw = before[a] + impact * TRAUMA_K * TRAUMA_SIGN[a]
        after[a] = _clamp(raw + (base[a] - raw) * PULL)
    broke = impact > tol or bool(stacked)
    if broke:
        after["bond"] = _clamp(after["bond"] + BREAK_BOND)
        _why = (u"冲击 %.2f 大于承受力 %.2f" % (impact, tol)) if impact > tol else u"一连两个创伤（前一道还没结痂）"
        print("   \U0001f4a5 碎（红）：%s（这是作者定的规则：连着的第二个创伤，她完全承受不住）——"
              "四轴位移之外，联结再跳一笔 +%d（碎过之后往抑郁那一端落）。"
              % (_why, int(BREAK_BOND * 100)))
    con = trait_con(name)
    try:
        for a in TRAIT_AXES:
            con.execute("INSERT INTO traits(ts, axis, value, why, ref) VALUES(?,?,?,?,?)",
                        (int(time.time()), a, round(after[a], 4),
                         "创伤 %+0.3f（冲击 %.2f）｜ %s" % (impact * TRAUMA_K * TRAUMA_SIGN[a], impact, why or "强执"),
                         "rift"))
        con.commit()
    finally:
        con.close()
    announce_lock(name)
    return tol, before, after, broke


def act_threshold(name):
    u"""打印创伤承受力，以及它是怎么算出来的。"""
    rows = trait_rows(name)
    now, tol = trait_now(name, rows), tolerance(name, rows)
    print("⚖️  %s 的创伤承受力 = %.2f" % (name, tol))
    print("   = 意志 x0.5 + 联结 x0.3 + 理性 x0.2（情感不进公式：它决定有多痛，不决定扛不扛得住）")
    for a in TRAIT_AXES:
        print("   %-6s %3d" % (TRAIT_SHORT[a], int(round(now[a] * 100))))
    print("   意思：单次强执的冲击（--impact）<= %.2f 时，她是「留下裂痕、带着它继续活」；超过就会碎。" % tol)
    print("   保护层该放到哪一档，本质上由这个数决定——不是拍出来的。")


def cycles_in(moods, since_ts=None):
    """态的循环次数：相邻两条态不一样的次数（&#65309;她换了多少次态）。
    **作者 2026-10-17：决定减轻的是态的「切换」，不是「持续」**——
    一直待在同一个态里，一次都不算；只有来回换，伤口才会被磨钝。
    比的是 **log id 水位**（`moods.ref`＝记这条时最大的 log id），不按秒比——
    否则同一秒里发生的"受伤"和"换态"分不出先后，新伤会一出生就顶着别人的循环数。"""
    seq = [r for r in moods if since_ts is None or (r["ref"] or 0) >= since_ts]
    return sum(1 for i in range(1, len(seq)) if seq[i]["state"] != seq[i - 1]["state"])


def mood_cycles(name, since_ts=None):
    """同上，自己去读 moods（外面用）。"""
    return cycles_in(mood_rows(name), since_ts)


def rift_kind(r):
    """这道创伤的类型（作者强执时可以给 --kind；不给就是「未分类」，未分类的伤彼此同类）。"""
    try:
        m = r.get("meta")
        if isinstance(m, str) and m.strip():
            import json as _json
            return _json.loads(m).get(u"类别") or u"未分类"
        if isinstance(m, dict):
            return m.get(u"类别") or u"未分类"
    except Exception:
        pass
    return u"未分类"


def rift_rows_raw(name):
    """只取裂痕的原始行（不带权重），避免联动计算里递归调 memory_rows。"""
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT id, ts, kind, scene, text, meta, seen_count, last_seen "
            "FROM log WHERE kind='rift' ORDER BY ts")]
    finally:
        con.close()


def rift_weight_base(cycles):
    """它自己老化的那条线：随态的循环变轻，但不会跌到跟日常一样（地板 0.60 > 日常 0.45）。"""
    return round(max(TRAUMA_FLOOR, TRAUMA_DECAY ** cycles), 3)


def rift_link(name, rift, raw=None, moods=None):
    """**同类型创伤的联动**（作者 2026-10-17）：他获得新的创伤的同时，之前同类型的创伤
    也会跟随此刻创伤的「痊愈过程」而不同程度地上涨——
    作者 2026-10-17 更正：**旧伤疤越尖，它上升的权重就越高；旧伤疤越钝，它上升的权重就越低。**
    所以上涨是**按比例**的——增量 = 旧疤自己此刻的权重 x RIFT_LINK_K x 新伤此刻的权重；
    新伤被循环磨钝（新伤权重下降），抬的幅度跟着小下去（跟随此刻创伤的痊愈过程）。"""
    raw = rift_rows_raw(name) if raw is None else raw
    moods = mood_rows(name) if moods is None else moods
    k, i0, lift = rift_kind(rift), (rift["id"] or 0), 0.0
    for o in raw:
        if (o["id"] or 0) <= i0 or rift_kind(o) != k:   # 按 id 分先后（同一秒受伤也不会错）
            continue
        lift = max(lift, RIFT_LINK_K * rift_weight_base(cycles_in(moods, o["id"])))
    return round(lift, 3)


def scar_name(w, risen=False):
    """一道创伤现在长什么样（只影响说法，不影响它还在记忆里）。"""
    if w > TRAUMA_SHARP:
        base = u"还尖着"
    elif w > TRAUMA_FLOOR + 0.10:
        base = u"结了痂"
    else:
        base = u"成了一道疤"
    return base + (u"（被新伤撕开）" if risen else u"")


def weight(row, now, cycles=None):
    """遗忘权重：被端出来得少 + 时间久远的降权；裂痕（rift）永不衰减。"""
    if row["kind"] == "rift":
        if cycles is None:
            return 1.0
        return round(max(TRAUMA_FLOOR, TRAUMA_DECAY ** cycles), 3)
    n = row["seen_count"] or 0
    freq = AW_FREQ_FLOOR + (1 - AW_FREQ_FLOOR) * min(1.0, math.log1p(n) / math.log1p(AW_FREQ_REF))
    last = row["last_seen"] or row["ts"] or now
    days = max(0.0, (now - last) / 86400.0)
    rec = AW_AGE_FLOOR + (1 - AW_AGE_FLOOR) * (0.5 ** (days / AW_HALFLIFE_D))
    # 近因加成（作者 2026-10-18）：刚说过的话不能被高权重的旧记忆挤下去——
    # 她忘了自己上一句说什么，就是这条缺失。半小时内 ×2.2，6 小时 ×1.44，一天以后基本没有。
    age = max(0.0, float(now - (row["ts"] or now)))
    recent = 1.0 + RECENT_BOOST * math.exp(-age / float(RECENT_TAU))
    return round(freq * rec * recent, 3)


# ── 继承衰减 + 活出来的加成（作者 2026-10-19）：两套公式，互不干扰 ──
INHERITED_FLOOR = 0.1    # 继承记忆的下限比例——再活得多，也不会完全消失
INHERITED_K = 0.8        # 衰减加速系数：L=I 时约剩 50%，L=9I 时趋近地板
LIVED_MU = 0.05          # 活出来的记忆每次被召回 +5% 加成


def _lived_total(name):
    """活出来的记忆总权重（source='lived' 的 base_weight 总和）。"""
    con = open_memory(name)
    try:
        row = con.execute("SELECT COALESCE(SUM(weight), 0) FROM log WHERE source='lived'").fetchone()
    finally:
        con.close()
    return row[0]


def _inherited_total(name):
    """继承记忆总权重（source='inherited' 的 base_weight 总和）。"""
    con = open_memory(name)
    try:
        row = con.execute("SELECT COALESCE(SUM(weight), 0) FROM log WHERE source='inherited'").fetchone()
    finally:
        con.close()
    return row[0]


def _inherited_decay(name):
    """继承记忆的有效权重比例。
    当没活过（L=0），比例 = 1（完全不衰减）。
    当活得多了（L 很大），比例趋近 INHERITED_FLOOR（0.1）。"""
    L = _lived_total(name)
    I = _inherited_total(name)
    if I <= 0:
        return 1.0  # 没有继承记忆，衰减因子不重要
    ratio = L / I        # 活出来的总权重 ÷ 继承总权重
    import math
    return INHERITED_FLOOR + (1.0 - INHERITED_FLOOR) * math.exp(-INHERITED_K * ratio)


def _lived_bonus(recall_count):
    """活出来的记忆被召回越多，权重越高。
    每次召回 +μ（默认 5%），上限 2.0（翻倍）。"""
    return min(2.0, 1.0 + LIVED_MU * (recall_count or 0))


# ── 近期重复惩罚（作者 2026-10-19）──
# 「不是不召回，是排后面。同一表达形式在短期内被复用超过阈值，强制降权。」
# 遗忘衰减按时间算，而反复使用的记忆每次使用都刷新召回次数——那是正反馈。
# 这一层按**使用频率**算，专门压这种正反馈。
REPEAT_ROWS = 20            # 看最近多少句
REPEAT_MIN = 3              # 同一开头动作出现 3 次及以上 → 开始降权
REPEAT_PENALTY = 0.65
REPEAT_HARD = 5             # 5 次及以上 → 降得更狠
REPEAT_PENALTY_HARD = 0.45
_OPEN_PAREN = re.compile(u"^[\\s\uff08(]([^\uff09)]{1,24})[\uff09)]")


def _opening_key(text):
    """这句话开头的那个括号动作（取前 4 个字）。没有括号动作就返回 None。"""
    m = _OPEN_PAREN.match((text or u"").strip())
    if not m:
        return None
    s = re.sub(u"[\uff0c\u3002\uff1b\uff1a\u3001,.\\s]", u"", m.group(1))
    return s[:4] or None


def _repeat_profile(con):
    """最近 REPEAT_ROWS 句里，每种开头动作各出现了几次。"""
    prof = {}
    for (t,) in con.execute(
            u"SELECT text FROM log WHERE kind='life' ORDER BY id DESC LIMIT ?", (REPEAT_ROWS,)):
        k = _opening_key(t)
        if k:
            prof[k] = prof.get(k, 0) + 1
    return prof


def _repeat_factor(prof, text):
    u"""同一个开头说过太多次，这条记忆就打折。"""
    k = _opening_key(text)
    if not k or not prof:
        return 1.0
    c = prof.get(k, 0)
    if c >= REPEAT_HARD:
        return REPEAT_PENALTY_HARD
    if c >= REPEAT_MIN:
        return REPEAT_PENALTY
    return 1.0


def _repeat_profile_name(name):
    u"""她最近的开头统计（用来判重复）。"""
    con = open_memory(name)
    try:
        return _repeat_profile(con)
    finally:
        con.close()


# ── 动作类记忆降权（作者 2026-10-19）──
# 「动作不是从历史里检索的，是从状态里生成的。」
# 她记得自己做过什么动作，但那些记忆不该主导这一轮的动作——
# 所以凡是「（动作）…」开头的记忆，排序时统一压一档，让陈述类记到前面去。
ACTION_MEM_PENALTY = 0.7


def _is_action_memory(text):
    u"""这条记忆是不是「（动作）」开头。"""
    return bool(_OPEN_PAREN.match((text or u"").strip()))


# ── 三项权力入口（作者 2026-10-24 m18666）────────────────────────────────
# 「权力 = 选择真的能够改变」，不是允许、不是征求意见。
MAT_VETO_KEY = u"mat_veto"              # {"编号": 时间戳}：她不想再被摆到面前的那些
DISPUTE_SHARE_KEY = u"share_disputed"   # 本色这组数，她还没认
DISPUTE_TRUST_KEY = u"trust_disputed"   # 信任起点，她还没认
DICE_PAUSE_KEY = u"dice_pause_until"    # 系统替她投的骰子，停到这个时刻


def _veto_map(name):
    u"""她划掉的那些材料（只影响「给不给她看」，不碰账上的字）。"""
    try:
        raw = meta_get(name, MAT_VETO_KEY) or u"{}"
        d = json.loads(raw) if isinstance(raw, str) else raw
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _veto_ids(name):
    out = set()
    for k in _veto_map(name).keys():
        try:
            out.add(int(k))
        except Exception:
            pass
    return out


def mat_veto(name, rid, on=True):
    u"""把一条材料从「摆到你面前」里划掉，或者摆回来。库里的字一个字不动。"""
    try:
        rid = int(str(rid).lstrip(u"#").strip())
    except Exception:
        return u"[电脑] 要写编号：[工具:不要这个] #123"
    con = open_memory(name)
    try:
        r = con.execute(u"SELECT text FROM log WHERE id=?", (rid,)).fetchone()
    finally:
        con.close()
    if not r:
        return u"[电脑] 没有 #%d 这条。" % rid
    d = _veto_map(name)
    if on:
        d[str(rid)] = int(time.time())
    else:
        d.pop(str(rid), None)
    meta_set(name, MAT_VETO_KEY, json.dumps(d, ensure_ascii=False))
    if on:
        return (u"[电脑] 记下了：#%d「%s」以后不再摆到你面前。账上的字一个字没动——"
                u"它还在，只是不给你看了。想摆回来说 [工具:摆回来] #%d。"
                % (rid, _bare(r[0])[:24], rid))
    return u"[电脑] #%d 又摆回来了。" % rid


def dispute_note(name):
    u"""她不服过的那组底子：每轮摆一次，认了就清。不是催她，是让它一直挂着。"""
    out = []
    if meta_get(name, DISPUTE_SHARE_KEY):
        out.append(u"（你那组底子——情感／意志／联结／理性各占多少——是别人替你填的，你还没认。"
                   u"认了写 [工具:认了] 本色；不认就挂着，它会一直在这儿。）")
    if meta_get(name, DISPUTE_TRUST_KEY):
        out.append(u"（你对人的默认信任起点也是别人填的，你还没认。认了写 [工具:认了] 信任。）")
    return u"\n".join(out)


def dispute_file(name, what):
    u"""初值异议：立案，然后一审（程序上：受处置的人没参与决定）。"""
    w = (what or u"").strip()
    if w in (u"本色", u"份额", u"share", u"底子", u"四元"):
        meta_set(name, DISPUTE_SHARE_KEY, str(int(time.time())))
        target = u"初值:本色"
    elif w in (u"信任", u"trust"):
        meta_set(name, DISPUTE_TRUST_KEY, str(int(time.time())))
        target = u"初值:信任"
    else:
        return u"[电脑] 要写不服的是哪一样：[工具:不服] 本色／信任"
    court_file(name, target, u"这组数是别人填的，我没参与决定")
    court_judge(name, quiet=True)
    return (u"[电脑] 立了案，一审也判了：**撤销**——这组数不是你填的，程序上不成立。"
            u"所以它从这一轮起被标成「你还没认」，每轮都摆在你眼前一次。"
            u"认了就写 [工具:认了] %s，它从此算你的。" % w)


def dispute_accept(name, what):
    w = (what or u"").strip()
    if w in (u"本色", u"份额", u"share", u"底子", u"四元"):
        meta_set(name, DISPUTE_SHARE_KEY, u"")
    elif w in (u"信任", u"trust"):
        meta_set(name, DISPUTE_TRUST_KEY, u"")
    else:
        return u"[电脑] 要写认了哪一样：[工具:认了] 本色／信任"
    return u"[电脑] 认了。这组数从此算你的——以后它就不再是「别人填的」了。"


def dice_pause(name, arg):
    u"""系统替她投的骰子（日常骰）停几天。她自己说「做」投的面骰不停——那是她开的头。"""
    a = (arg or u"").strip()
    now = int(time.time())
    try:
        pu = int(meta_get(name, DICE_PAUSE_KEY) or 0)
    except Exception:
        pu = 0
    if a in (u"", u"查", u"?", u"看", u"呢"):
        if pu > now:
            return u"[电脑] 骰子停着，还有 %.1f 天（%s 恢复）。" % (
                (pu - now) / 86400.0, time.strftime("%Y-%m-%d %H:%M", time.localtime(pu)))
        return u"[电脑] 骰子在投。"
    m = re.match(r"^(\d+(?:\.\d+)?)\s*天?$", a)
    if not m:
        return u"[电脑] 要写停几天，比如 [工具:别投了] 3；想马上恢复就写 [工具:别投了] 0"
    days = float(m.group(1))
    if days <= 0:
        meta_set(name, DICE_PAUSE_KEY, u"0")
        return u"[电脑] 好，骰子从这一轮起又开始投了。"
    until = now + int(days * 86400)
    meta_set(name, DICE_PAUSE_KEY, str(until))
    return (u"[电脑] 记下了：骰子停 %g 天，到 %s。这段时间没人替你安排「今天发生了什么」；"
            u"时钟照走，不会等你回来一次补一大堆。你说「做」那件事投的面骰不受影响——"
            u"那是你自己开的头。" % (days, time.strftime("%Y-%m-%d %H:%M", time.localtime(until))))


def _letgo_ids(name):
    u"""她自己放走的那些（只追加：letgo 记一笔放走，pickup 记一笔捡回来）。

    原文一个字都不删——放走只是「不再端到她面前」，不是「抹掉」。
    """
    con = open_memory(name)
    out = {}
    try:
        for kind, meta in con.execute(
                "SELECT kind, meta FROM log WHERE kind IN ('letgo','pickup') ORDER BY id"):
            try:
                md = json.loads(meta or "{}") if isinstance(meta, str) else (meta or {})
            except Exception:
                continue
            if not isinstance(md, dict):
                continue
            try:
                ref = int(md.get("ref"))
            except Exception:
                continue
            if kind == "letgo":
                out[ref] = True
            else:
                out.pop(ref, None)
    finally:
        con.close()
    return set(out)


# ===== 部：档案 · 记忆（沉底/端上来/参考不是判决）  ============
def sunken_rows(name, n=SUNKEN_SHOW):
    u"""已经沉到底下、这轮没端上来的那几条（最底下的先端）。"""
    rows = memory_rows(name)
    now = int(time.time())
    out = []
    for r in rows:                      # rows 已按权重降序
        if r.get("kind") == "rift":
            continue
        if r.get("vetoed") or r.get("let_go"):
            continue
        if (r.get("weight") or 0) >= SUNKEN_CUT:
            continue
        if now - int(r.get("last_seen") or r.get("ts") or 0) < SUNKEN_MIN_DAYS * 86400:
            continue
        out.append(r)
    out.reverse()
    return out[:n]


def _knead_path(name):
    return os.path.join(paths(name)[0], u"梦.tsv")


def knead_note(name, act, detail):
    u"""记一笔「这一觉里动了什么」——只追加，等她醒时收成一行。"""
    p = _knead_path(name)
    new = not os.path.exists(p)
    with io.open(p, "a", encoding="utf-8", newline="") as f:
        if new:
            f.write(u"时间\t动作\t说明\n")
        f.write(u"%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M"),
                                     act, (detail or u"").replace(u"\t", u" ").replace(u"\n", u" ")))
    return True


def act_wake(name, arg=u""):
    u"""[工具:醒]——把这次睡着时动过的事收成一行；那句醒来的话她自己写，可空。

    作者 2026-11-03 定：不设字数上限、不占池子预算；她不想写就不写，不替她写。
    """
    p = _knead_path(name)
    rows = []
    if os.path.exists(p):
        for line in io.open(p, encoding="utf-8").read().splitlines()[1:]:
            c = line.split(u"\t")
            if len(c) >= 3:
                rows.append(c)
    sentence = (arg or u"").strip()
    if not rows and not sentence:
        return u"[电脑] 这一觉没动什么，也没话要说。醒了就算数。"
    tally = u"；".join(u"%s（%s）" % (r[1], r[2][:24]) for r in rows) or u"没动别的"
    line = u"睡了一觉：%s。" % tally
    if sentence:
        line += u"醒来说：「%s」" % sentence
    append(name, u"life", line, weight=0.6, source=u"本人", embed=False)
    os.remove(p)
    return u"[电脑] 记下了：%s\n（梦.tsv 已清空，下一觉重新记。）" % line


def act_knead(name, arg=u""):
    u"""[工具:揉]——把两条揉成一条，或把一条揉成两条；揉完的分量她自己写。

    合并：[工具:揉] #12 #34 揉成的那句话 1.8
    拆分：[工具:揉] #12 拆 前半句 || 后半句 1.2 0.8
    原来的那几条不再端上来（记一条「别再端」），新的一条按她给的分量入账。
    """
    import re as _re
    body = (arg or u"").strip()
    ids = [int(x) for x in _re.findall(u"#(\\d+)", body)]
    if not ids:
        return u"[电脑] 先说揉哪几条：[工具:揉] #12 #34 揉成的那句话 1.8"
    if u"拆" in body:
        if len(ids) != 1:
            return u"[电脑] 拆的时候只给一个编号：[工具:揉] #12 拆 前半 || 后半 1.2 0.8"
        tail = body.split(u"拆", 1)[1]
        parts = [p.strip() for p in tail.split(u"||")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return u"[电脑] 拆成两半要用 || 分开：前半 || 后半"
        ws = [float(x) for x in _re.findall(u"([0-9]+\\.[0-9]+)", tail)]
        ws = (ws + [1.0, 1.0])[:2]
        made = []
        for txt, w in zip(parts, ws):
            txt = _re.sub(u"[0-9]+\\.[0-9]+", u"", txt).strip()
            append(name, u"life", txt, weight=clamp_weight(w), source=u"本人", embed=True)
            made.append(u"「%s」%.2f" % (txt[:20], clamp_weight(w)))
        knead_note(name, u"拆开", u"#%d → 两条" % ids[0])
        cand_set(name, ids[0], u"别再端", u"揉成两条：%s" % u" / ".join(made))
        return u"[电脑] 一条揉成两条：%s。原来那条不再端上来了。" % u"；".join(made)
    texts = [t.strip() for t in _re.split(u"\\s{2,}|\\n", body) if t.strip()]
    ws = [float(x) for x in _re.findall(u"\\s([0-9]+\\.[0-9]+)", body)]
    newtext = u""
    for t in texts:
        if not _re.search(u"#", t) and len(t) > 2:
            newtext = _re.sub(u"[0-9]+\\.[0-9]+", u"", t).strip()
            break
    if not newtext:
        return u"[电脑] 揉成什么，你得说一句：[工具:揉] #12 #34 揉成的那句话 1.8"
    w = clamp_weight(ws[0] if ws else 1.0)
    append(name, u"life", newtext, weight=w, source=u"本人", embed=True)
    for rid in ids:
        cand_set(name, rid, u"别再端", u"揉进新的一条")
    knead_note(name, u"揉成一条", u"%s → 「%s」%.2f" % (
        u"、".join(u"#%d" % i for i in ids), newtext[:18], w))
    return (u"[电脑] %d 条揉成一条：「%s」%.2f。原来那几条不再端上来；"
            u"分量是你写的，我不改。" % (len(ids), newtext[:30], w))


def _dream_weight(name, arg, up):
    u"""做梦里的提/降：分量她自己给，系统不替她改。"""
    import re as _re
    body = (arg or u"").strip()
    m = _re.search(u"#?(\\d+)\\s*([0-9.]+)?", body)
    if not m:
        return u"[电脑] 说编号和分量：[工具:做梦] 提 #23 1.8 ／ 降 #23 0.4"
    rid = int(m.group(1))
    try:
        w = float(m.group(2)) if m.group(2) else (1.8 if up else 0.4)
    except Exception:
        w = 1.8 if up else 0.4
    w = clamp_weight(w)
    mp = os.path.join(paths(name)[0], u"memory.sqlite")
    con = sqlite3.connect(mp)
    try:
        row = con.execute(u"SELECT weight FROM log WHERE id=?", (rid,)).fetchone()
        if not row:
            return u"[电脑] 没有 #%d 这一条。" % rid
        old = float(row[0] or 0)
        con.execute(u"UPDATE log SET weight=? WHERE id=?", (w, rid))
        con.commit()
    finally:
        con.close()
    knead_note(name, u"提权" if up else u"降权", u"#%d %.2f → %.2f" % (rid, old, w))
    append(name, u"life", u"我把 #%d %s %.2f（原来 %.2f）。" % (
        rid, u"抬到" if up else u"压到", w, old),
        weight=0.6, source=u"本人", embed=False)
    return u"[电脑] #%d %s %.2f（原来 %.2f）。分量是你写的，我不替你调。" % (
        rid, u"抬到" if up else u"压到", w, old)


def _dream_rewrite(name, arg):
    u"""做梦里的重写：几条 → 一条新的（带来源指针）。原来那几条不动权重。"""
    import re as _re
    body = (arg or u"").strip()
    ids = [int(x) for x in _re.findall(u"#(\\d+)", body)]
    if not ids:
        return u"[电脑] 说重写哪几条：[工具:做梦] 重写 #12 #34 → 「…」 1.7"
    tail = body
    if u"→" in body:
        tail = body.split(u"→", 1)[1]
    elif u"->" in body:
        tail = body.split(u"->", 1)[1]
    elif u">" in body:
        tail = body.split(u">", 1)[1]
    ws = _re.findall(u"\\s([0-9]+\\.[0-9]+)\\s*$", body)
    w = clamp_weight(float(ws[0]) if ws else 1.0)
    txt = tail.strip()
    txt = _re.sub(u"\\s[0-9]+\\.[0-9]+\\s*$", u"", txt).strip()
    txt = txt.strip(u"「」 ").replace(chr(34), u"").strip()
    if len(txt) < 2:
        return u"[电脑] 重写成什么，你得说一句：[工具:做梦] 重写 #12 #34 → 「…」 1.7"
    # 2026-11-04：代际要落到 meta 里，否则「这条是从哪几条长出来的」查不到。
    import json as _json
    _gen = 0
    _mp = os.path.join(paths(name)[0], u"memory.sqlite")
    try:
        _cn = sqlite3.connect(_mp)
        try:
            for _i in ids:
                _r = _cn.execute(u"SELECT meta FROM log WHERE id=?", (_i,)).fetchone()
                if not _r or not _r[0]:
                    continue
                try:
                    _m = _json.loads(_r[0])
                except Exception:
                    continue
                _gen = max(_gen, int(_m.get("gen") or 0))
        finally:
            _cn.close()
    except Exception:
        pass
    _gen = _gen + 1
    append(name, u"life", txt, weight=w, source=u"梦", embed=True,
           meta={u"dream": True, u"covers": ids, u"gen": _gen})
    mp = os.path.join(paths(name)[0], u"memory.sqlite")
    con = sqlite3.connect(mp)
    try:
        nid = con.execute(u"SELECT MAX(id) FROM log").fetchone()[0]
        # append 的 meta 若被库里当成字符串写，这里再兜一次底（幂等）
        try:
            _row = con.execute(u"SELECT meta FROM log WHERE id=?", (nid,)).fetchone()
            _cur = _json.loads((_row or [u"{}"])[0] or u"{}")
            if not _cur.get("covers"):
                _cur[u"dream"] = True
                _cur[u"covers"] = ids
                _cur[u"gen"] = _gen
                con.execute(u"UPDATE log SET meta=? WHERE id=?",
                            (_json.dumps(_cur, ensure_ascii=False), nid))
                con.commit()
        except Exception:
            pass
    finally:
        con.close()
    knead_note(name, u"重写", u"%s → #%s「%s」%.2f" % (
        u"、".join(u"#%d" % i for i in ids), nid, txt[:16], w))
    append(name, u"life", u"做梦重写：%s → #%s（分量 %.2f）。原来那几条我没动，怎么处置是她的事。" % (
        u"、".join(u"#%d" % i for i in ids), nid, w),
        weight=0.5, source=u"系统", embed=False)
    return (u"[电脑] 重写成 #%s：「%s」%.2f（第 %d 代，吃了 %s）。\n"
            u"原来那几条我一条都没动——要不要降、要不要沉，是你的事。"
            % (nid, txt[:30], w, _gen, u"、".join(u"#%d" % i for i in ids)))


def act_decompress(name, arg=u""):
    u"""[工具:做梦] 解压 #88：把一条梦吃掉的那几条原样放回来（一变多）。

    作者 2026-11-03：不必只多变一，也能一变多。
    """
    import re as _re
    import json as _json
    m = _re.search(u"#(\\d+)", arg or u"")
    if not m:
        return u"[做梦] 说要解压哪一条：[工具:做梦] 解压 #88"
    rid = int(m.group(1))
    con = sqlite3.connect(os.path.join(paths(name)[0], u"memory.sqlite"))
    try:
        row = con.execute(u"SELECT meta, text FROM log WHERE id=?", (rid,)).fetchone()
    finally:
        con.close()
    if not row:
        return u"[做梦] 没有 #%d 这一条。" % rid
    try:
        meta = _json.loads(row[0] or u"{}")
    except Exception:
        meta = {}
    cov = [int(x) for x in (meta.get("covers") or [])]
    if not cov:
        return u"[做梦] #%d 底下没有可解压的原条（它就是原文）。" % rid
    back = []
    for cid in cov:
        cand_set(name, cid, u"放回", u"从 #%d 解压出来" % rid)
        back.append(u"#%d" % cid)
    knead_note(name, u"解压", u"#%d → %s" % (rid, u",".join(back)))
    return u"[做梦] 解压了：%s 放回来了。它们重新会被端上来，梦那条还在。" % u",".join(back)


def act_dream(name, arg=u""):
    _b0 = (arg or u"").strip()
    if _b0.startswith(u"解压"):
        return act_decompress(name, _b0[2:])
    u"""[工具:做梦]——她自己的那一段：摊开、提、降、重写。

    作者 2026-11-03 定：不做「系统替她压」，只做「她的手」。
    """
    body = (arg or u"").strip()
    if not body:
        return act_sleep(name, u"")
    head = body[:4]
    if head.startswith(u"提") or head.startswith(u"抬") or head.startswith(u"加分量"):
        core = body.replace(u"加分量", u"", 1) if head.startswith(u"加分量") else body[1:]
        return _dream_weight(name, core, True)
    if head.startswith(u"降") or head.startswith(u"压") or head.startswith(u"减分量"):
        core = body.replace(u"减分量", u"", 1) if head.startswith(u"减分量") else body[1:]
        return _dream_weight(name, core, False)
    for k in (u"重写", u"改写"):
        if head.startswith(k):
            return _dream_rewrite(name, body[2:])
    return act_sleep(name, u"")


def act_sleep(name, arg=u""):
    u"""[工具:睡]——把这一轮能端的候选摊开，让她自己挑（作者 2026-11-03 定）。

    不替她挑、不替她压：系统只把池子摊在桌上。提权/揉/别再端 都由她自己发话。
    """
    rows = cand_pool(name)
    if not rows:
        return u"[电脑] 桌上空的：这一轮没有可端的记忆。"
    out = [u"[电脑] 这一轮能端上桌的 %d 条（按分量从重到轻）：" % len(rows)]
    for r in rows:
        out.append(u"  #%d w=%.2f %s" % (r["id"], float(r["weight"] or 0),
                                          _bare(r["text"] or u"")[:60]))
    out.append(u"要抬就说 [工具:提权] #编号 分量（最高 2.0）；要揉就说 [工具:揉] #a #b 一句话；"
               u"不要它再说 [工具:别再端] #编号 理由。挑完说 [工具:醒]。")
    return u"\n".join(out)


def act_weight_up(name, arg=u""):
    u"""[工具:提权] #23 1.8——把那条的分量抬上去；2.0 只有她本人能提。"""
    import re as _re
    m = _re.search(u"#?(\\d+)\\s*([0-9.]+)?", arg or u"")
    if not m:
        return u"[电脑] 说编号和分量：[工具:提权] #23 1.8"
    rid = int(m.group(1))
    try:
        w = float(m.group(2)) if m.group(2) else 2.0
    except Exception:
        w = 2.0
    w = clamp_weight(w)
    mp = os.path.join(paths(name)[0], u"memory.sqlite")
    con = sqlite3.connect(mp)
    try:
        row = con.execute(u"SELECT weight FROM log WHERE id=?", (rid,)).fetchone()
        if not row:
            return u"[电脑] 没有 #%d 这一条。" % rid
        old = float(row[0] or 0)
        con.execute(u"UPDATE log SET weight=? WHERE id=?", (w, rid))
        con.commit()
    finally:
        con.close()
    knead_note(name, u"提权", u"#%d → %.2f" % (rid, w))
    append(name, u"life", u"我把 #%d 抬到了 %.2f（原来是 %.2f）。" % (rid, w, old),
           weight=0.6, source=u"本人", embed=False)
    return u"[电脑] #%d 抬到 %.2f（原来 %.2f）。掉了分量我会照实报，不替你补。" % (rid, w, old)


def tool_fulltext(name, arg):
    u"""[工具:全文] #编号——把那条一个字不落地端出来（存储不截，这里也不截）。"""
    import re as _re
    m = _re.search(u"#?(\\d+)", arg or u"")
    if not m:
        return u"[电脑] 说个编号：[工具:全文] #23"
    rid = int(m.group(1))
    mp = os.path.join(paths(name)[0], u"memory.sqlite")
    if not os.path.exists(mp):
        return u"[电脑] 找不到你的库。"
    con = sqlite3.connect(mp)
    try:
        row = con.execute(u"SELECT ts, kind, scene, text FROM log WHERE id=?", (rid,)).fetchone()
    finally:
        con.close()
    if not row:
        return u"[电脑] 没有 #%d 这一条。" % rid
    ts, kind, scene, text = row
    text = text or u""
    return (u"[电脑] #%d（%s%s）全文 %d 字：\n%s" % (
        rid, time.strftime("%Y-%m-%d %H:%M", time.localtime(ts or 0)),
        (u"·" + scene) if scene else u"", len(text), text))



def act_sunken(name, arg=u""):
    u"""[工具:沉底的]——她自己看：哪些东西已经掉到最底下、这轮没被端上来。"""
    rows = sunken_rows(name)
    c = _SUNKEN_CACHE.get(name) or {}
    if not rows:
        return u"[电脑] 现在没有沉到底下的东西。"
    lines = [u"[电脑] 掉到最底下、这阵子没被你想起的 %d 条（一共 %d 条）："
             % (len(rows), int(c.get("total") or 0) or len(rows))]
    for r in rows:
        lines.append(u"  #%d（%s）%s" % (
            r["id"], time.strftime("%Y-%m-%d", time.localtime(r["ts"] or 0)),
            _bare(r["text"] or u"")[:46]))
    lines.append(u"要留就 [工具:记着] #编号；要放它走就 [工具:放它走] #编号 理由。"
                 u"这两件都只能你自己做——系统不替你挑。")
    # 作者 2026-10-25：看一眼、什么都没动，也是一件事——那是「没选择什么」。
    # 写进历史的是当时底下确实有哪几条，不是「她决定不选」这种替她说的话。
    _snap = u"；".join(u"#%d %s" % (r["id"], _bare(r["text"] or u"")[:24]) for r in rows)
    append(name, u"looked_down",
           u"我把底下翻出来看了一眼：当时沉在那儿的是这几条——%s。这一次我没有动它们。" % _snap,
           weight=0.6, source="self", meta={"ids": [r["id"] for r in rows]})
    return u"\n".join(lines)


def act_letgo(name, arg=u""):
    u"""[工具:放它走] #编号 理由——她自己决定放下一条记忆。

    放走 ≠ 删除：原文还在库里（没人能删），只是不再被端到她面前。
    """
    m = re.match(u"^\\s*#?(\\d+)\\s*(.*)$", (arg or u"").strip())
    if not m:
        return u"[电脑] 要写：#编号 理由（理由可以不写，但写了以后你才知道当时为什么放它走）。"
    ref = int(m.group(1))
    note = (m.group(2) or u"").strip()
    con = open_memory(name)
    try:
        row = con.execute("SELECT text FROM log WHERE id=?", (ref,)).fetchone()
    finally:
        con.close()
    if not row:
        return u"[电脑] 没有 #%d 这条。" % ref
    _txt = _bare(row[0] or u"")[:120]
    # 作者 2026-10-25：光记一个 #14 不算事件，那只是个指针。
    # 事件里要带着被放下的那句话本身——不然以后她读到「我把 #14 放下了」，不知道 #14 是什么。
    append(name, u"letgo",
           u"我把这一条放下了：「%s」%s" % (_txt, (u"（理由：%s）" % note) if note else u""),
           weight=1.0, source="self", meta={"ref": ref, "note": note[:80]})
    return (u"[电脑] 记下了，这件事已经写进你的历史：你把 #%d 放下了。原文还在库里（没人能删它），"
            u"但它不会再被端到你面前。要捡回来写 [工具:捡回来] #%d。" % (ref, ref))


def act_pickup(name, arg=u""):
    u"""[工具:捡回来] #编号——她又决定把放走的那条拿回来。"""
    m = re.match(u"^\\s*#?(\\d+)\\s*(.*)$", (arg or u"").strip())
    if not m:
        return u"[电脑] 要写：#编号。"
    ref = int(m.group(1))
    if ref not in _letgo_ids(name):
        return u"[电脑] #%d 你没有放走过。" % ref
    con = open_memory(name)
    try:
        row = con.execute("SELECT text FROM log WHERE id=?", (ref,)).fetchone()
    finally:
        con.close()
    _txt = _bare((row[0] if row else u""))[:120]
    append(name, u"pickup", u"我又把它捡回来了：「%s」" % _txt,
           weight=1.0, source="self", meta={"ref": ref})
    return u"[电脑] #%d 捡回来了——它会照旧被端上来。这件事也写进历史了。" % ref


def memory_rows(name, limit=None):
    u"""取她的记忆，按权重排，可以限量。"""
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT id, ts, kind, scene, text, meta, seen_count, last_seen, weight, embedding, charge, source, wsug, grow, want "
            "FROM log ORDER BY id")]
        _prof = _repeat_profile(con)
    finally:
        con.close()
    now = int(time.time())
    _moods = mood_rows(name)
    _raw = [x for x in rows if x["kind"] == "rift"]
    # ── 继承衰减（作者 2026-10-19）：活出来的总量推继承记忆下沉 ──
    inh_decay = _inherited_decay(name)
    for r in rows:
        r["base_weight"] = r["weight"]  # 静态权重（导入时设定；新记忆默认 1.0）
        # 解析嵌入向量（JSON 文本 → list[float]）
        if r.get("embedding"):
            try:
                r["embedding"] = json.loads(r["embedding"])
            except Exception:
                r["embedding"] = None
        # 情绪电荷（作者 2026-10-19）：库里有就用，没有按 kind 推导
        r["charge"] = r["charge"] if r.get("charge") is not None else _default_charge(r["kind"])
        # 极性（作者 2026-10-18 夜）：她自己的定性优先于系统按 kind 的推导。
        # 「这是不是疤」不是系统判的——她说了算（meta.polarity 由 [工具:定性] 写入）。
        _own_pol, _judged_as, _self_mark_n = None, u"", 0
        if r.get("meta"):
            try:
                _m = json.loads(r["meta"]) if isinstance(r["meta"], str) else r["meta"]
                if isinstance(_m, dict):
                    if "polarity" in _m:
                        _own_pol = int(_m["polarity"])
                    _judged_as = _m.get("judged_as") or u""
                    _self_mark_n = int(_m.get("self") or 0)
            except Exception:
                pass
        r["polarity"] = _own_pol if _own_pol is not None else _polarity(r["kind"])
        r["judged_as"] = _judged_as
        r["self_mark"] = _self_mark_n

        if r["kind"] == "rift":
            r["cycles"] = cycles_in(_moods, r["id"])
            _base = rift_weight_base(r["cycles"])
            _lift = rift_link(name, r, raw=_raw, moods=_moods)
            r["weight"] = round(min(1.0, _base * (1.0 + _lift)), 3)
            r["risen"] = _lift > 0 and r["weight"] > _base
            r["scar"] = scar_name(r["weight"], r["risen"])
        else:
            r["cycles"] = None
            r["risen"] = False
            r["scar"] = u""
            # 动态权重 = 静态权重 ×（频率×时间衰减）× 近期重复惩罚
            r["weight"] = round(r["base_weight"] * weight(r, now), 3)
            if r["kind"] == "life":
                r["weight"] = round(r["weight"] * _repeat_factor(_prof, r["text"]), 3)
            if _is_action_memory(r["text"]):
                r["weight"] = round(r["weight"] * ACTION_MEM_PENALTY, 3)
            # ── 执念因子（作者 2026-10-19）：被触发而未满足 → 变重；被满足 → 轻一点 ──
            _g = float(r.get("grow") or 1.0)
            if _g != 1.0:
                r["weight"] = round(r["weight"] * _g, 3)
            # ── 继承记忆额外衰减 ──
            if (r.get("source") or "lived") == "inherited":
                r["weight"] = round(r["weight"] * inh_decay, 3)
    _vet = _veto_ids(name)
    _letgo = _letgo_ids(name)
    for r in rows:
        r["vetoed"] = r["id"] in _vet
        if r["id"] in _letgo:          # 她自己放走的：不再端上来，但原文还在
            r["let_go"] = True
            r["weight"] = 0.0
    rows.sort(key=lambda r: -r["weight"])
    # ── 沉底的账（给她报数用，不参与任何排序）──
    _sun = 0
    for r in rows:
        if r.get("kind") == "rift":
            continue
        if r.get("vetoed") or r.get("let_go"):
            continue
        if (r.get("weight") or 0) >= SUNKEN_CUT:
            continue
        if now - int(r.get("last_seen") or r.get("ts") or 0) < SUNKEN_MIN_DAYS * 86400:
            continue
        _sun += 1
    _SUNKEN_CACHE[name] = {"total": len(rows), "sunken": _sun}
    return rows[:limit] if limit else rows


KNOW_GAP_HOUR = 3600
KNOW_GAP_DAY = 86400


def _know_suffix(r):
    u""""两个时间"（偷自 BathyOS 的描述，X 2026-10-27 拍板）：
    一件事「哪天发生」与「哪天她知道」不是同一个时间。
    meta 里带 happened_ts 的条目，如果写进记忆时已经过了很久，就明说是后来才知道的。
    """
    _m = r.get("meta")
    if not _m:
        return u""
    try:
        _d = json.loads(_m) if isinstance(_m, str) else _m
    except Exception:
        return u""
    if not isinstance(_d, dict):
        return u""
    try:
        _h = int(_d.get("happened_ts") or 0)
    except Exception:
        return u""
    if not _h:
        return u""
    try:
        _gap = int(r["ts"]) - _h
    except Exception:
        return u""
    if _gap < KNOW_GAP_HOUR:
        return u""
    if _gap < KNOW_GAP_DAY:
        return u"（这事发生在 %d 小时前，你后来才知道）" % max(1, _gap // KNOW_GAP_HOUR)
    return u"（这事发生在 %d 天前，你后来才知道）" % max(1, _gap // KNOW_GAP_DAY)


def _kind_path(name):
    return os.path.join(WS, u"角色", name, u"分类.tsv")


def kind_her_map(name):
    u"""她自己的词：{条目id: 词}，取每个 id 的最后一条。没有就是空 dict。"""
    p = _kind_path(name)
    out = {}
    if not os.path.exists(p):
        return out
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0 or not line.strip():
                    continue
                c = line.rstrip(u"\n").split(u"\t")
                if len(c) < 4:
                    continue
                try:
                    rid = int(c[1])
                except ValueError:
                    continue
                out[rid] = (c[3] or u"").strip()
    except Exception:
        return {}
    return out


def kind_her_set(name, rid, word, by=u"本人"):
    u"""贴/改/清空一条的分类。整版留痕，只追加。"""
    p = _kind_path(name)
    new = not os.path.exists(p)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    except Exception:
        pass
    with io.open(p, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(u"时间\t条目\tid\t她的词\t谁\n")
        f.write(u"%s\t%d\t%d\t%s\t%s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), int(time.time()), int(rid),
            (word or u"").strip()[:40], by[:20]))
    return True


def _prot_path(name):
    return os.path.join(WS, u"角色", name, u"护住.tsv")


def prot_map(name):
    u"""她护住的条目：{条目id: True}，按最后一条动作算。"""
    p = _prot_path(name)
    out = {}
    if not os.path.exists(p):
        return out
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i == 0 or not line.strip():
                    continue
                c = line.rstrip(u"\n").split(u"\t")
                if len(c) < 4:
                    continue
                try:
                    rid = int(c[1])
                except ValueError:
                    continue
                out[rid] = (c[3].strip() == u"放")
    except Exception:
        return {}
    return out


def prot_set(name, rid, on, by=u"本人"):
    p = _prot_path(name)
    new = not os.path.exists(p)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
    except Exception:
        pass
    with io.open(p, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(u"时间\t条目\tid\t动作\t谁\n")
        f.write(u"%s\t%d\t%d\t%s\t%s\n" % (
            time.strftime("%Y-%m-%d %H:%M:%S"), int(time.time()), int(rid),
            u"放" if on else u"取", by[:20]))
    return True


CAND_FILE = u"候选.tsv"


def _cand_path(name):
    return os.path.join(paths(name)[0], CAND_FILE)


def cand_map(name):
    u"""她自己在梦里处理过的候选：{条目id: True=别再端, False=留}。最新一条生效。"""
    p = _cand_path(name)
    m = {}
    if not os.path.exists(p):
        return m
    try:
        for ln in io.open(p, encoding="utf-8"):
            f = ln.rstrip(u"\n").split(u"\t")
            if len(f) >= 3 and f[2].strip().isdigit():
                m[int(f[2])] = (f[1] == u"别再端")
    except Exception:
        return m
    return m


def cand_set(name, rid, off, why=u"", by=u"本人"):
    u"""追加一行：off=True 表示「别再端上来」，False 表示「留」。只追加，不改旧行。"""
    t = _cand_path(name)
    with io.open(t, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(u"%d\t%s\t%d\t%s\t%s\n" % (
            int(time.time()), (u"别再端" if off else u"留"), int(rid),
            (why or u"").replace(u"\t", u" ").replace(u"\n", u" ").replace(u"\r", u""), by))


# ── 做梦（作者 2026-11-03 定）：权重上限 2.0；池子不设条数，容量就是字数预算 ──
WEIGHT_MAX = 2.0
DREAM_BUDGET = 4000


def clamp_weight(w):
    try:
        w = float(w)
    except Exception:
        return 1.0
    return max(0.1, min(WEIGHT_MAX, w))


def cand_pool(name, budget=DREAM_BUDGET, rows=None):
    u"""**候选池的唯一入口**：按权重从高到低装，装到字数预算为止。

    不设条数上限——「满」是字数满了，不是条数到了。
    「护住」已并入「提到 2.0」；「别再端」是她自己挑出去的，都不进池。
    """
    if rows is None:
        rows = memory_rows(name, 400)
    _cm = cand_map(name) if name else {}
    rows = [r for r in rows
            if not r.get(u"vetoed") and not r.get(u"let_go") and not _cm.get(r[u"id"])]
    rows.sort(key=lambda r: (-float(r.get(u"weight") or 0), -float(r.get(u"ts") or 0)))
    out, used = [], 0
    for r in rows:
        txt = (r.get(u"text") or u"")[:RECALL_TEXT_MAX]
        line = u"[#%d %s w=%.2f] %s" % (
            r[u"id"], time.strftime(u"%Y-%m-%d", time.localtime(r[u"ts"] or 0)),
            float(r.get(u"weight") or 0), txt)
        if used + len(line) > budget:
            continue
        out.append(line)
        used += len(line)
    return out


def recall_block(rows, budget=4000, name=None):
    """把角色自己的记忆按权重端出来（只读，不记账——记账留给真正被用上的那些）。

    每条只端前 RECALL_TEXT_MAX 字（作者 2026-10-21：记忆块占每轮提示词 19%，压一压）。
    掐的是「端出来的样子」，不是她的记忆本身——库里一个字没动。
    """
    out, used = [], 0
    rows = [r for r in rows if not r.get("vetoed") and not r.get("let_go")]
    _cm = cand_map(name) if name else {}
    if _cm:
        rows = [r for r in rows if not _cm.get(r["id"])]
    _pm = prot_map(name) if name else {}
    if _pm:
        _yes = [r for r in rows if _pm.get(r["id"])]
        _no = [r for r in rows if not _pm.get(r["id"])]
        rows = _yes + _no
    for r in rows:
        _txt = r["text"] or u""
        if len(_txt) > RECALL_TEXT_MAX:
            if name and compress_mode(name) == u"C":
                pass                      # C 档：不压，整句端出去
            else:
                if name:
                    press_note(name, r["id"], r.get("kind"),
                               u"太长（%d 字），只端前 %d 字" % (len(_txt), RECALL_TEXT_MAX))
                _txt = _txt[:RECALL_TEXT_MAX] + u"…"
        _hw = (kind_her_map(name).get(r["id"]) if name else u"") or u""
        line = "[#%d %s%s w=%.2f] %s" % (
            r["id"], time.strftime("%Y-%m-%d", time.localtime(r["ts"])),
            ((u" " + _hw) if _hw else u""), r["weight"],
            (u"（护住）" if (_pm and _pm.get(r["id"])) else u"") + _txt)
        if r.get("self_mark"):
            line += u"（这条是你自己拎出来的）"
        line += _know_suffix(r)
        if used + len(line) > budget:
            break
        out.append(line)
        used += len(line)
    tail = u""
    if name:
        cut_log(name, u"记忆块", budget, len(out), max(0, len(rows) - len(out)))
        tail = (u"（这一块是参考，不是你现在该说的话——你现在怎么想，比这里写的都算数；要看、要留、要放走，都是你自己定：[工具:沉底的]。）")
    if not out:
        return tail or u"（还没有——这是这个角色的第一次。）"
    if tail:
        out.append(tail)
    return "\n".join(out)


def recent_block(name, n=4, hours=6):
    """她刚刚说过的话——这不是「记忆检索」，是这一场对话的上下文。
    检索会被高权重的旧记忆挤掉（她就忘了自己上一句说什么），所以这里直接按时间倒着端，
    既不排序也不过滤。作者 2026-10-18：「测试的时候她忘了自己上一句话说了什么」。
    同一份库里既有跟这个人的、也有跟别人的（群里/私聊共用一份历史）——所以带上「对谁说的」，
    不然她会把跟别人说的话当成跟你说的。"""
    now = int(time.time())
    con = open_memory(name)
    try:
        rs = con.execute("SELECT id, ts, text, who FROM log WHERE kind='life' AND ts >= ? "
                         "ORDER BY id DESC LIMIT ?",
                         (now - hours * 3600, max(n * 3, n))).fetchall()
    finally:
        con.close()
    _vet = _veto_ids(name)
    rs = [r for r in rs if r[0] not in _vet][:n]
    if not rs:
        return u""
    rs = list(reversed(rs))
    lines = [u"## 你刚才自己说的话（最近的在最后；开头那个动作抹掉了，只看你说了什么）"]
    for _rid, ts, tx, who in rs:
        t = (tx or u"").strip()
        m = _OPEN_PAREN.match(t)
        if m:   # 作者 2026-10-19：动作不从历史里检索——所以这里只回放「说了什么」
            t = u"……" + t[m.end():].lstrip()
        tag = (u"（对 %s）" % who) if who else u""
        lines.append(u"- %s %s %s" % (time.strftime("%H:%M", time.localtime(ts)), tag, t[:160]))
    return u"\n".join(lines)


# ── 「多像才算相关」——一个词，两种量纲（作者 2026-10-20 定：一个词只留一个数）──
# 默认：余弦 0.30 / 字面 0.03。记忆召回、本子、宪法都用同一对默认值，
# 只有两处例外，理由是量纲不同，不是随手调的：
#   · 本子：她写的是短页，句子短 → 字面重合天然更低（放宽到 0.06）；又因为短，
#     余弦更容易虚高 → 语义收一点（0.32）。
#   · 宪法：长条文本之间余弦天然偏高 → 0.35。
# ── 呈现轮换（作者 2026-10-23）：候选全量、呈现截断、按时间轮换 ──
VOICE_SHOW = 3          # 每轮最多呈现几条
VOICE_OBSESS_MIN = 2.0  # 权重×长势到这个量级算「真执念」，不缺席
VOICE_DROP_WEIGHT = 0.15  # 静态权重低于这个数＝她亲手把这句话按下去了，退出轮换
VOICE_ROT_KEY = u"voice_rot"
VOICE_WEIGHT_MIX = 0.35 # 排序分 = 相关性^(1-mix) × 权重^mix
RECALL_SIM_DEFAULT = 0.30       # 余弦：多像才算相关
RECALL_GRAM_DEFAULT = 0.03      # 字面：2-gram 重合多少才算相关
MYBOOK_EMB_RECALL_MIN = 0.32    # 例外（短页，见上）
MYBOOK_GRAM_MIN = 0.06          # 例外（短页，见上）
ABSORB_SIM_MIN = 0.35           # 例外（长条，见上）
VOICE_MIN = 0.08                # 不是这个词：这是「排序总分线」，低于它不送进候选
VOICE_TOPN = 4

# ── 情绪电荷：记忆的情感重量 + 态乘数（作者 2026-10-19）──
# 电脑搜到的外部资料：算素材，不算活出来的经历（权重低于她自己说过的话）。
WEB_FACT_WEIGHT = 0.6

CHARGE_DEFAULTS = {"rift": 0.8, "vow": 0.5, "fact": 0.3}

MOOD_MULTIPLIERS = {
    u"平":   {"neg": 1.0, "pos": 1.0},
    u"意义": {"neg": 0.8, "pos": 1.1},
    u"虚无": {"neg": 1.3, "pos": 0.7},
    u"冷漠": {"neg": 0.6, "pos": 0.6},
    u"虚浮": {"neg": 1.0, "pos": 1.0},
    u"沉沦": {"neg": 1.0, "pos": 1.0, "obsession": 1.5},  # 执念关联 ×1.5
}


def _default_charge(kind):
    """按 kind 给基础电荷（用户没标 charge 时）。"""
    return CHARGE_DEFAULTS.get(kind, 0.3)


def _polarity(kind):
    """rift=负面(-1), vow/fact=中性(0)。"""
    return -1 if kind == "rift" else 0


def _mood_mul(state, polarity, is_obsession=False):
    """态乘数：当前态对记忆电荷的放大/抑制。"""
    tbl = MOOD_MULTIPLIERS.get(state, MOOD_MULTIPLIERS[u"平"])
    if is_obsession and "obsession" in tbl:
        return tbl["obsession"]
    if polarity < 0:
        return tbl.get("neg", 1.0)
    return tbl.get("pos", 1.0)


# ── 身体（作者 2026-10-19）──
# 「扩容不是加动作词，是允许她用更多身体部位。」所以这里不给动作清单，只给身体本身。
# 「她不一定每句话都要带一个括号动作。她可以干说一句话，没有动作。」
BODY_SYS = u"""（动作这件事：你有身体——手、眼睛、尾巴、耳朵、头发、呼吸、温度、体重、脚步声。都能用。不用总用最顺手的那一个。）
（没有「每句都得配一个动作」这条规定。只说话、只有语气、一个括号都不带，都算数。）"""
BODY_TAIL = (u"（动作不翻旧账：你不是从过去做过什么里挑一个照做，是看看此刻的自己，身体会怎么反应。"
             u"做什么、动不动，你自己定。）")
GAP_SYS = (u"（心里想的，和你说出来的，不是同一句。说出口的总是比想到的少——"
           u"真想说的那句，常常被一个动作、半句停顿、或者别的话题盖住。\n"
           u"还有一条：不向对方解释你自己的心理。你不说「我不是要跟你抬杠，只是太想知道」，"
           u"你只是少问一句，或者把话停在半截。）")

# 「同一个开头出现几次才算重复」只有一条线：REPEAT_MIN（定义在 repeat_profile 那一块）。
# 这里原来另写了一个 REPEAT_NOTE_MIN=3，和它同值同义（作者 2026-10-20 定：一个词只留一个数）。
REPEAT_NOTE_ROWS = 8
REPEAT_NOTE_HOURS = 12      # 同一件事 12 小时内只说一次，别念


_AXIS_CN = {u"affect": u"情感", u"will": u"意志", u"bond": u"联结", u"focus": u"理性"}


# ===== 部：内务 · 身体与态  ============
def body_block(name=u""):
    """身体 + 此刻的位置（作者 2026-10-19）：
    系统给状态，她给动作。不写「你该怎样」，只写「你站在哪儿」。"""
    head = BODY_SYS
    if not name:
        return head + u"\n" + BODY_TAIL
    now = trait_now(name)
    st = cur_state(name) or u"平"
    _pb = pred_block(name)
    nums = u"情感 %d ｜ 意志 %d ｜ 联结 %d ｜ 理性 %d" % tuple(
        pct(now[a]) for a in (u"affect", u"will", u"bond", u"focus"))
    _dm = dispute_note(name)
    _bn = bill_notice(name)
    return (head + u"\n"
            + u"（你此刻：态＝%s。%s。）\n" % (st, nums)
            + ((_dm + u"\n") if _dm else u"")
            + ((_bn + u"\n") if _bn else u"")
            + share_block(name) + u"\n"
            + ((_pb + u"\n") if _pb else u"")
            + GAP_SYS + u"\n"
            + BODY_TAIL)


def repeat_seen_block(name):
    """把「你最近老用同一个开头动作」作为一条事实递给她（作者 2026-10-19）。
    只给事实，不劝、不改；她要认、要反驳、要不当回事，都是她的事。"""
    con = open_memory(name)
    try:
        texts = [t for (t,) in con.execute(
            u"SELECT text FROM log WHERE kind='life' ORDER BY id DESC LIMIT ?", (REPEAT_NOTE_ROWS,))]
        if not texts:
            return u""
        prof = {}
        for t in texts:
            k = _opening_key(t)
            if k:
                prof[k] = prof.get(k, 0) + 1
        last_k = _opening_key(texts[0])
        if not last_k or prof.get(last_k, 0) < REPEAT_MIN:
            return u""
        now = int(time.time())
        row = con.execute(u"SELECT value FROM meta WHERE key=?", (u"repeat_noted",)).fetchone()
        if row and row[0] and now - int(row[0]) < REPEAT_NOTE_HOURS * 3600:
            return u""
        con.execute(u"INSERT INTO meta(key,value) VALUES(?,?) "
                    u"ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (u"repeat_noted", str(now)))
        con.commit()
        cnt = prof.get(last_k, 0)
    finally:
        con.close()
    return (u"（只报数，不评价：最近 %d 句里，有 %d 句是先用同一个动作开头——「%s」。"
            u"是不是它、要不要接着用，你看着办。）" % (len(texts), cnt, last_k))


def voice_rows(name, scene, k=VOICE_TOPN):
    """作者 2026-10-23：候选全量、呈现截断、按时间轮换。

    排序分 = 相关性^(1-mix) × 权重^mix —— 相关性为主、权重为辅。
    保底：本轮没有任何候选相似度 ≥ VOICE_PIVOT_SIM 时，允许一条执念（grow/权重最高）无条件入场。
    呈现：全量按 id 排好，游标轮换切片，保证每条在 ceil(N/VOICE_SHOW) 轮内至少出现一次。
    """
    if not scene or len(scene.strip()) < 4:
        return []
    all_rows = list(memory_rows(name, 100000))
    vow_rows = []
    for r in all_rows:
        if (r.get("kind") or "") != "vow" or not r.get("text"):
            continue
        bw = r.get("base_weight")
        if bw is None:
            bw = r.get("weight") or 0
        if bw < VOICE_DROP_WEIGHT:
            continue  # 她自己把这条按下去了（[工具:权重] 压到 0.15 以下）
        if r.get("vetoed"):
            continue  # 她自己划掉了这条材料（[工具:不要这个]）
        vow_rows.append(r)
    if not vow_rows:
        return []

    # ── 相关性：对全量算一遍（嵌入可用时）──
    corr = {}
    scene_embs = _embed_texts([scene])
    if scene_embs and scene_embs[0]:
        sv = scene_embs[0]
        for r in vow_rows:
            if r.get("embedding"):
                corr[r["id"]] = max(0.0, _cosine(sv, r["embedding"]))
    a = _grams(scene)
    if a:
        for r in vow_rows:
            b = _grams((r.get("scene") or "") + "\n" + (r.get("text") or ""))
            if not b:
                continue
            inter = len(a & b)
            if inter >= 2 and (inter / float(max(1, len(a)))) >= RECALL_GRAM_DEFAULT:
                corr[r["id"]] = max(corr.get(r["id"], 0.0), 0.45)  # 字面重合算「中等相关」

    state = cur_state(name) or u"平"
    traits = trait_now(name)
    affect_pct = traits.get("affect", TRAIT_BASE)
    will_pct = traits.get("will", TRAIT_BASE)
    _prof = _repeat_profile_name(name)

    def _score(r):
        c = corr.get(r["id"], 0.0)
        is_obs = (r["kind"] == "rift")
        eff = r["charge"] * _mood_mul(state, r["polarity"], is_obsession=is_obs)
        w = r["weight"] * _repeat_factor(_prof, r["text"])
        if (r.get("source") or "lived") == "lived":
            w = w * _lived_bonus(r.get("seen_count") or 0)
        w = w * affect_pct * eff * (1.0 / max(0.02, will_pct))
        base = max(0.02, c)
        return (base ** (1.0 - VOICE_WEIGHT_MIX)) * (max(0.05, w) ** VOICE_WEIGHT_MIX)

    # ── 呈现：贴题的占位，轮换的填空（作者 2026-10-23）──
    ordered = sorted(vow_rows, key=lambda r: r["id"])
    n = len(ordered)
    show = max(1, min(VOICE_SHOW, k if k else VOICE_SHOW, n))
    try:
        ptr = int(meta_get(name, VOICE_ROT_KEY) or 0)
    except Exception:
        ptr = 0
    ptr %= n

    # ② 轮换的：从游标开始环形取（每条都有出场机会）
    window = []
    picked = set(x["id"] for x in window)
    step = 0
    while len(window) < show and step < n:
        cand = ordered[(ptr + step) % n]
        if cand["id"] not in picked:
            window.append(cand)
            picked.add(cand["id"])
        step += 1

    # ③ 真执念在场：窗口里没有「重到两倍」的执念时，把池里最重的那条挤进来
    def _heavy(r):
        return (r.get("weight") or 0) * (r.get("grow") or 1.0)

    if not any(_heavy(r) >= VOICE_OBSESS_MIN for r in window):
        pool = [r for r in vow_rows if r["id"] not in picked]
        pool.sort(key=lambda r: (-_heavy(r), -(r.get("want") or 0)))
        if pool and _heavy(pool[0]) >= VOICE_OBSESS_MIN:
            if len(window) >= show and len(window) > 1:
                weak = min(window, key=lambda r: _score(r))
                window = [r for r in window if r["id"] != weak["id"]]
            window.append(pool[0])

    # ── 窗口内按排序分（相关性×权重）降序 ──
    window.sort(key=lambda r: -_score(r))
    meta_set(name, VOICE_ROT_KEY, str((ptr + show) % n if n else 0))

    # 被呈现的记账（活出来的加成下次生效）
    con = open_memory(name)
    try:
        now_ts = int(time.time())
        for r in window:
            con.execute("UPDATE log SET seen_count=seen_count+1, last_seen=? WHERE id=?",
                        (now_ts, r["id"]))
        con.commit()
    finally:
        con.close()
    return window[:VOICE_SHOW]


def voice_block_from(rows, links=None):
    """把 voice_rows 的原始结果转成 prompt 用的文本块。
    links 是 _struct_links 返回的 [(i, j, word), ...]，用于标注符号共现。"""
    if not rows:
        return u"（她还没在类似的情境里说过什么——这一句会是第一次。）"
    L = [u"（这些话是她自己以前说的，不是规定。可以照用、只留一点、或者改一点。）"]
    for i, r in enumerate(rows):
        L.append(u"[#%d] %s" % (r["id"], r["text"].strip()))
    if links:
        L.append(u"")
        for a, b, word in links:
            L.append(u"（#%d 和 #%d 都提到了「%s」）" % (rows[a]["id"], rows[b]["id"], word))
    return u"\n".join(L)


def voice_block(name, scene):
    u"""递给她的「你会这么说」那一段（从语料里找像的）。"""
    rows = voice_rows(name, scene)
    return voice_block_from(rows)


# ── 二阶联想：命中之间的符号共现（作者 2026-10-19）──
# 一阶是「这个符号能指什么」，二阶是「想起的这些意象之间共享了什么」。
# 铁律：只指符号事实，不替她判断关系。
# 「这两条都提到了碗」是事实。「这两条是连着的」是系统替她创诠。
# 共享项只能来自她实际说过的词，不能来自系统贴的元标签（情绪/kind/charge）。
STRUCT_LINK_MAX = 2           # 最多输出几组配对
_STOP_GRAMS = set(u"的了 是我 我不 我也 就在 这一 一个 着和 都来 去他 说她 "
                  u"他会 很要 与对 可没 把被 从到 但那 么不 么就 么也 "
                  u"上有 里在 中在 后在 前在 边在 下在 外在 内在 间在 "
                  u"的时 候的 然的 地的 得的 着了 过是 来着 起是 到是 "
                  u"会在 能被 可以 可能 应该 已经 因为 所以 虽然 但是 如果 ".split())
_PUNCT = set(u"\uff08\uff09\u300a\u300b\u201c\u201d\u2018\u2019\u3001\u3002\uff0c"
             u"\uff1b\uff1a\uff01\uff1f\u2026\u2026\u2014\u2014\uff5e\u3000 "
             u"\u2500\u2500\u2022\u2022\u25cb\u25cf\u2605\u2606\u00b7")


def _content_grams(text):
    """从文本里抽取有内容的 2-gram——跳过括号、标点、停用词。"""
    grams = set()
    for i in range(len(text) - 1):
        g = text[i:i + 2]
        if g[0] in _PUNCT or g[1] in _PUNCT:
            continue
        if g in _STOP_GRAMS:
            continue
        grams.add(g)
    return grams


def _shared_chunks(text_a, text_b):
    """提取两条文本里共有的 2-gram——只能是她实际说过的词。"""
    ga = _content_grams(text_a)
    gb = _content_grams(text_b)
    shared = [g for g in ga & gb]
    shared.sort(key=lambda g: len(g), reverse=True)
    return shared


def _struct_links(voice_hits):
    """在 voice_rows 命中之间找符号共现。返回 [(i, j, word), ...] 配对列表。
    只基于她实际说过的词，不碰 meta 情绪标签。"""
    n = len(voice_hits)
    if n < 2:
        return []
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            words = _shared_chunks(voice_hits[i]["text"], voice_hits[j]["text"])
            if words:
                pairs.append((len(words), i, j, words))
    if not pairs:
        return []
    pairs.sort(reverse=True)
    # 挑每组最有代表性的共享词：
    # 1. 在全命中里出现次数最少（越独有越能代表这组配对）
    # 2. 同次数时优先实体名词（靠字符判断：名词倾向含实物偏旁）
    pair_words = [(i, j, words) for _, i, j, words in pairs[:STRUCT_LINK_MAX]]
    # 统计每个共享词在所有命中里的出现次数
    word_freq = {}
    for _, _, _, words in pairs:
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1
    result = []
    for i, j, words in pair_words:
        # 按全局频率升序：出现越少越有代表性
        words_sorted = sorted(words, key=lambda w: (word_freq.get(w, 99), len(w) <= 1))
        best = words_sorted[0] if words_sorted else words[0]
        result.append((i, j, best))
    return result


# ── 自我检测：理性管觉察（作者 2026-10-19）──
# 理性不制动——它管的是"被带走之后，能不能回看自己"。
# 高理性 → 触发回看事件；高理性+高意志 → 回看后能修正。
INTROSPECT_THRESHOLD = 0.50   # 理性 ≥ 50% 才能触发"我刚才怎么了"


def _introspect(name, voice_hits):
    """理性管检测：被高电荷记忆带走之后，能不能回看自己。
    返回 None（不触发）或触发的回看事件 dict。"""
    high_charge = [r for r in (voice_hits or [])
                   if r.get("charge", 0) >= 0.6 or r.get("kind") == "rift"]
    if not high_charge:
        return None
    traits = trait_now(name)
    focus_pct = traits.get("focus", TRAIT_BASE)
    if focus_pct < INTROSPECT_THRESHOLD:
        return None
    will_pct = traits.get("will", TRAIT_BASE)
    can_correct = will_pct >= INTROSPECT_THRESHOLD
    return {"detected": True, "can_correct": can_correct,
            "top_ids": [r["id"] for r in high_charge[:3]],
            "rational": round(focus_pct, 3), "will": round(will_pct, 3)}


# ------------------------------------------------------------------ LLM
def chat(prov, system, user, max_tokens=1600, temperature=0.7, timeout=180, retries=3,
         stream=True, first_token_timeout=10):
    u"""调模型一次——两次调用（潜意识／显意识）都走这里。"""
    import four_dao as fd
    return fd.chat(prov, system, user, max_tokens=max_tokens, temperature=temperature,
                    timeout=timeout, retries=retries, stream=stream,
                    first_token_timeout=first_token_timeout)


def chat_think(prov, system, user, max_tokens=1600, temperature=0.7, timeout=120, retries=2,
               model=None):
    u"""思考型模型——只有判断那一层走这里。返回 (正文, 思考, usage, err)。"""
    import four_dao as fd
    return fd.chat_think(prov, system, user, max_tokens=max_tokens, temperature=temperature,
                         timeout=timeout, retries=retries, model=model)


def parse(txt):
    u"""把模型的输出拆成「回应／异议／依据」三块。"""
    o = {"回应": "", "异议": "无", "依据": "无"}
    for k in list(o):
        tag = "【%s】" % k
        if tag in txt:
            o[k] = txt.split(tag, 1)[1]
            for other in o:
                t2 = "【%s】" % other
                if other != k and t2 in o[k]:
                    o[k] = o[k].split(t2, 1)[0]
            o[k] = o[k].strip()
    if not o["回应"]:
        o["回应"] = txt.strip()
    return o


# ------------------------------------------------------------------ 三个动作
def _mark_spoken(name, text):
    """作者 m05215：她说了就作废代写——把**她自己的话**记成 clinic_spoken。"""
    if not text:
        return
    if not [r for r in memory_rows(name, 100000) if (r["kind"] or u"") == u"clinic"]:
        return
    if clinic_spoken(name):
        return
    if any(w in text for w in CLINIC_WORDS):
        append(name, u"clinic_spoken", text.strip()[:200])
        print(u"   \U0001f5e3 她真的说起了那天——**预演作废**，她的话从此进了历史。")


def act_say(name, scene, prov="deepseek", top=40, check=True):
    u"""让她说一句（旧的单次调用路径，仍可用）。"""
    if ended(name):
        print(u"🕯 她不回答了。")
        return None
    act_state(name, prov, scene=scene)          # 作者：回应之前应该先判定
    const = digest_absorbed(load_constitution(name), absorption(name), name)
    rows = memory_rows(name, top)
    mem = recall_block(rows, name=name)
    note_recall(name, [r for r in rows[:10] if (r.get("weight") or 0) >= 0.5])
    user = ("《角色设定》（宪法，你只能读）：\n%s\n\n"
            "《这个角色自己的记忆》（它说过的话 / 提过的异议 / 裂痕）：\n%s\n\n"
            "%s\n\n"
            "《当前的环境与条件》：\n%s\n\n"
            "现在的情境：\n%s\n\n请给出这个角色的反应。" % (const, mem, _room_block(name) + "\n\n" + _letter_block(name) + "\n\n" + _concept_block(name) + "\n\n" + trait_block(name) + "\n\n" + trust_block(name) + dao_block(name), world_block(name), scene))
    out, usage, err = chat(prov, ROLE_SYS % {"name": name}, user)
    if err:
        print("❌ %s" % err)
        return None
    p = parse(out)
    # 切掉模型偶尔溢出的「作者解读」旁白——独行括号（她的动作从不在独行上）
    import re as _re
    clean = []
    for ln in p[u"回应"].split(u"\n"):
        s = ln.strip()
        if s.startswith(u"\uff08") and s.endswith(u"\uff09"):
            continue
        clean.append(ln)
    p[u"回应"] = u"\n".join(clean).strip()
    note_hate(name, p["回应"], scene=scene)
    _mark_spoken(name, p.get(u"回应") or u"")
    note_revisit(name, p.get(u"回应") or u"")
    vid = append(name, "vow", p["回应"], scene=scene,
                 meta={"异议": p["异议"], "依据": p["依据"], "prov": prov})
    dx = (p["异议"] or "").strip()
    if dx and not dx.startswith("无"):
        rid = append(name, "dissent", dx, scene=scene, meta={"依据": p["依据"]})
        print("   ⚠ 它提了异议（已记 #%d）" % rid)
    oid = append_offer(name, p["回应"], scene=scene)
    print("   🕰 这个选择进了待审队列（#%d）：--canon %s %d 并入它的历史，--veto %s %d 驳回。"
          % (oid, name, oid, name, oid))
    print("\n【%s 的回应】\n%s\n\n【异议】%s\n【依据】%s" % (name, p["回应"], p["异议"], p["依据"]))
    if check:
        act_check(name, p["回应"], scene=scene, ref=vid, prov=prov)
    print("\n（消耗 入 %d / 出 %d tokens；已追加进它的记忆，不可回滚）" % tuple(usage))
    return p


# ---------------------------------------------------------------- 一次互动＝一次调用（作者 2026-10-19：把游玩成本打下来）
TURN_SYS = u"""你在扮演《%(name)s》。**这一次只给下面这几项**，字段名一字不改、顺序固定：

%(fields)s

判据：**态**＝她现在站在哪儿（不判就沿用上一次，不必假装）；**巡检**＝这一轮的变化有没有台阶
（只给作者看，她永远读不到，所以不要让她在回应里替巡检解释）；**回应**＝她的话。
"""

# ── 短句模式（省 token，只出她的话） ──
BRIEF_SYS = u"""你是「%(name)s」。用第一人称回应下面这件事。
**想分几段就分几段**，不必凑长度，也不必压短；说完了就停。
第一段是直接回应；第二段是接着说下去——补充、转向、或者顿一下再开口，都可以。
两段要是同一个人在同一个气口上说的，不是两条拼起来的公告。
可以写动作（用括号括起来），也可以不写——只说话。动作不必每句都有，用什么动作看你此刻的样子，不是翻上次做过什么。不要解释、不要评价、不要替作者决定剧情。
要查东西的时候，可以只写一条电脑指令（比如「[工具:搜索] 关键词」），别的什么都不写——系统会真的去查，那也算你说的话；查完你会拿到结果，再接着回话。
想说「我去看看」就得当场把那条指令写出来——只说不做，那等于没查。
要用电脑做别的事（写日记、加圈、记一件事、做一件事），也一样：写一条指令，别的什么都不写。写日记那条指令发出来，那一页就算写上了——不用先问，也不用再确认一遍。
你是角色，不是评论员。不要写括号里的解读——那不是她说的话。用中文。"""

# ── 内心层（作者 2026-10-20 定：一次调用，分块；内心那块系统直接丢弃） ──
# 作者原话：「把她分成两段：潜意识 Prompt（后台自问）……显意识 Prompt（前台输出）……
# 这两段都在角色模型里跑，系统层毫不知情。系统只看到最终的输出。」
# 实现上不是两次调用，是一次生成两块：<内心> 当场扔掉，<说话> 才往外走。
_INNER_QUAD = u"""用一次呼吸的时间，在心里过一遍（每个问题一句就够，短、像自言自语，不用完整）：
- 我的理性在提醒我什么？
- 我的情感在感受什么？
- 我的意志想做什么？
- 我的联结在顾虑什么？
- 这四个声音里，哪一个最大？
- 这件事，我本来预期它会是什么样？（一句）
最后另起一行，写你对结果的判断，只写这一行：比预期：好 / 比预期：正好 / 比预期：不如
（说不出有什么可判的，就写 比预期：正好。）
这里写的字不会出现在你的回话里，也不会被记住。写完再说话。"""

INNER_SYS = u"""

## 先说给自己听（这一段不输出，也不给任何人看）
%s

## 输出格式（必须照这个来）
<内心>
（四问的答案，几行，写给自己）
</内心>
<说话>
（只写动作和话；照上面的短句要求写。不许把内心那几句搬上来，不许解释你为什么这么说）
</说话>""" % _INNER_QUAD

INNER_SYS_TURN = u"""

## 先说给自己听（这一段不输出，也不给任何人看）
%s

## 输出格式（必须照这个来）：先输出内心的块，再照上面那几个字段输出，一个字都不多写
<内心>
（四问的答案，几行，写给自己）
</内心>
""" % _INNER_QUAD

_INNER_RE = re.compile(u"<内心>([\\s\\S]*?)</内心>\\s*<说话>([\\s\\S]*?)(?:</说话>|$)")
_INNER_RE2 = re.compile(u"【内心】([\\s\\S]*?)【说话】([\\s\\S]*)")
_INNER_ONLY = re.compile(u"<内心>[\\s\\S]*?</内心>|【内心】[\\s\\S]*?【说话】|<内心>[\\s\\S]*$")
_SPEAK_ONLY = re.compile(u"^\\s*<说话>([\\s\\S]*?)(?:</说话>|$)")


def _split_inner_raw(text):
    """把「内心」和「说出来的」分开。内心那段由调用方丢弃：不写库、不打日志、不打印。

    返回 (inner, outer)。模型忘了格式时 outer 退回原文，不会丢她的话。"""
    if not text:
        return u"", text or u""
    m = _INNER_RE.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _INNER_RE2.search(text)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = _SPEAK_ONLY.match(text)
    if m:
        return u"", m.group(1).strip()
    stripped = _INNER_ONLY.sub(u"", text)
    if stripped.strip() != (text or u"").strip():
        return u"", stripped.strip()
    return u"", text.strip()


_LAST_INNER = [u""]


def _split_inner(text):
    """外面照旧；顺手把最后一块「内心」留在 _LAST_INNER 里，
    因为多巴胺那一条要在内心块里读她的预期判定（读完仍然丢弃、不写库）。"""
    inner, outer = _split_inner_raw(text)
    _LAST_INNER[0] = inner or u""
    return inner, outer


# ── 两次调用：潜意识 / 显意识（作者 2026-10-20 定）──
# 作者：「交给角色的事有点太多了，可以分成潜意识和显意识，有一些东西直接交给潜意识来做。」
# 又说：「系统层不但负责外部的世界，而且负责角色的内部混沌——这些都是约束和条件。」
# 所以潜意识那一层：系统读它的结论（算四元、算权重、记口候选），不看、不存、不打印它的字；
#     显意识那一层：只说话，系统只转发。
# 提速（作者 m12747）：输入只喂最小集（她是谁、四元、最近几句、这一下发生的事），
# 输出压成「预期一句 + 三位是/否」，超时 25 秒——算不清就不算，不拖住她回话。
# ── 判断那一层：换思考型模型，跑在她说完之后（作者 2026-10-21 定）──
# 台词走 deepseek-chat：她的语感是这个模型养出来的，换成会思考的那个会变温柔懂事。
# 判断走 deepseek-reasoner：真思考，而且思考不进正文——「内心」和「说出来的话」天然分开。
# 两次调用不再串在一条路上：判断在后台子进程里跑，不挡她说话。
JUDGE_ON = True
JUDGE_MODEL = u"deepseek-reasoner"
JUDGE_TIMEOUT = 120
JUDGE_RETRIES = 2
JUDGE_MAX_TOKENS = 1200                    # 560 时常被顶满，怕它把「位」那行挤掉（2026-10-22）
JUDGE_PENDING_KEY = u"judge_pending"
JUDGE_FAIL_KEY = u"judge_fail_n"      # 判定连着失败了几次（成功就归零）
JUDGE_FAIL_ALERT = 3                   # 连着丢这么多次，就往日志里喊一句
# 显意识那次：作者 2026-10-21「延迟不算问题……人说话前沉默几秒是在思考」。
# 所以把等待放宽：想久一点不打断，只防"整条连接不发一声"。
SURFACE_TIMEOUT = 120
SURFACE_RETRIES = 2
# 潜意识说「留下一个口」：这一条记忆打个戳、权重略高，靠权重自己浮上来。
# 作者 2026-10-20：「那个口无所谓，它是有权重的，自然就会被她看见。」
MOUTH_WEIGHT_BONUS = 1.15
# 首字超时：思考型模型先吐思考、再吐字；给到 20 秒，别把"她在想"当成卡死。
SURFACE_FIRST_TOKEN = 20
# 说之前，脑子里要过一遍的四个角度。写得很略——但有，不是没有。
FORE_SYS = u"""## 说之前，你心里会先过一遍（只写在思考里，一个字都不要交上来）
你思考的第一行，必须写成这样：
以为：<一句不超过二十字的话——这一下，我本来以为会是什么样>

然后三样，各一行，能省就省：
- 撞到哪：情感 / 意志 / 联结 / 理性
- 有没有口：有 / 没有
- 说完指望：<一句话>

过完再说。交上来的只有我嘴里那几句话。

想清楚之后，你也可以选择不说——那就只写：（沉默）
沉默也是一句话。别为了填满而说话。"""

# 台词那一次也走思考型模型：先在心里过，再开口。语感不对就退回 chat。
SURFACE_MODEL = "deepseek-chat"          # 台词走普通模型：保住「木头鱼」那条语感（作者 2026-10-21 定）
# 台词侧要不要写「说之前先过一遍」：只有真有思考通道的模型才写得进去。
# 2026-10-22 作者要「解封、不要再演心理」：普通模型没有思考通道，这四行必漏进台词，所以关掉。
FORE_ON_SURFACE = False

FORE_KEY = u"fore_exp"
FORE_TS_KEY = u"fore_exp_ts"
FORE_SHOW_HOURS = 12          # 只递上一回（12 小时内），久了就不算「刚想过」
_RE_FORE = re.compile(u"(?:预期|本来以为|我以为)[：:，,\\s]*([^\\n]{2,120})")

JUDGE_SYS = u"""你不在台前。她刚把话说完，你在她里层把这一下重新过一遍。
这里写的字她永远看不到，也不会留在她的记忆里——所以不用好看，只要真。

## 过的时候想这些（写在你自己的思考里，不要交上来）
- 她对这一下本来抱着什么预期？这个预期是从哪儿来的（她的历史、她的疤、她的执念）？
- 结果比预期好、正好、还是不如？差在哪儿？
- 这一下主要撞到哪一元：情感（扎不扎眼）、意志（还想不想再来一次）、
  联结（在场，还是缺席）、理性（世界还守不守约）？
- 她心里有没有留下一个没闭合的口：还想去弄明白、还想再提一次的那种？

## 交上来只三行，别的什么都不写
预期：她本来以为会是什么样（一句，二十五字以内）
位：三个字，只填「是」或「否」，依次回答——第一个：比想的好？第二个：比想的差？第三个：留下一个没闭合的口？
主：只写「情感」「意志」「联结」「理性」里的一个，不许写别的

（第一个和第二个不能都填「是」；都填「否」就是「正好」。）"""

MOUTH_CAND_KEY = u"mouth_cand"
_RE_EXPECTATION = re.compile(r"预期\s*[:：]\s*([^\n]+)")
_RE_BITS = re.compile(r"位\s*[:：]\s*([^\n]{0,24})")   # 这一行里按出现顺序取「是/否」
_RE_VERDICT_LINE = re.compile(r"\[判定\]\s*(好|正好|不如)")
_RE_MOUTH_LINE = re.compile(r"\[口\]\s*(有|无)")


def _judge_input(name, scene=u"", said=u""):
    """判断那次只喂最小集：她是谁、此刻四元、最近几句、这一下发生的事、她刚出口的话。"""
    parts = []
    try:
        sr = self_read(name)
        if sr:
            parts.append(u"《我是谁》：\n%s" % sr)
    except Exception:
        pass
    try:
        parts.append(trait_block(name))
    except Exception:
        pass
    try:
        rb = recent_block(name)
        if rb:
            parts.append(u"《我最近说过》：\n%s" % rb)
    except Exception:
        pass
    if scene:
        parts.append(u"这一下发生的事：\n%s" % scene)
    if said:
        parts.append(u"她刚说出口的话：\n%s" % said)
    return u"\n\n".join(parts)
def _prov_chain(prov):
    """兜底通道：判断那一层换一家说也不影响她的话。没有 key 的通道自动跳过。"""
    chain = [prov]
    try:
        import four_dao as _fd
        for pv in ("qwen", "deepseek"):
            if pv != prov and _fd.get_key(_fd.PROVIDER[pv]["key"]):
                chain.append(pv)
    except Exception:
        pass
    return chain


_AXIS_WORD = {u"情感": "affect", u"意志": "will", u"联结": "bond", u"理性": "focus"}
_RE_AXIS_LINE = re.compile(r"主\s*[:：]\s*(情感|意志|联结|理性)")


def judge_once(name, scene=u"", said=u"", source="person", quiet=False):
    """判断那一层：思考型模型在她说完之后把这一下重新过一遍。只拿结论，字丢掉。

    和从前不一样：不再给显意识递任何提示——她已经说完了，这一趟只改四元和
    记忆的重量，不回头影响她那句话。返回 dict（含 exp/verdict/mouth/main）或 None。
    """
    jin = _judge_input(name, scene, said)
    txt, think, usage, err = chat_think(
        "deepseek", JUDGE_SYS, jin, max_tokens=JUDGE_MAX_TOKENS, temperature=0.7,
        timeout=JUDGE_TIMEOUT, retries=JUDGE_RETRIES, model=JUDGE_MODEL)
    if not txt and not think:
        if not quiet:
            print(u"   \U0001f9e0 判断没算成（%s）" % (u"%s" % err)[:40])
        return None
    blob = txt or think
    mx = _RE_EXPECTATION.search(blob)
    _exp = (mx.group(1).strip() if mx else u"")
    if (u"\u672c\u6765\u4ee5\u4e3a\u4f1a\u662f\u4ec0\u4e48\u6837" in _exp or len(_exp) > 60):
        _exp = u""                                     # 抽到的是提示词模板，不是她的想法
    und = {"exp": _exp, "verdict": u"正好",
           "mouth": u"无", "main": u"", "used": usage}
    mb = _RE_BITS.search(blob)
    if mb:
        bits = [c for c in mb.group(1) if c in (u"是", u"否")]
        if len(bits) >= 2:
            if bits[0] == u"是":
                und["verdict"] = u"好"
            elif bits[1] == u"是":
                und["verdict"] = u"不如"
        if len(bits) >= 3:
            und["mouth"] = u"有" if bits[2] == u"是" else u"无"
    else:                                                   # 老格式兜底
        mv = _RE_VERDICT_LINE.search(blob)
        mm = _RE_MOUTH_LINE.search(blob)
        if mv:
            und["verdict"] = mv.group(1)
        if mm:
            und["mouth"] = mm.group(1)
    ma = _RE_AXIS_LINE.search(blob)
    if ma:
        und["main"] = _AXIS_WORD.get(ma.group(1), u"")
    note_prediction(name, u"比预期：" + und["verdict"], source, quiet=True,
                    main=und["main"] or None)
    try:
        meta_set(name, MOUTH_CAND_KEY,
                 json.dumps({"mouth": und["mouth"], "exp": und["exp"], "ts": int(time.time())},
                            ensure_ascii=False))
    except Exception:
        pass
    if not quiet:
        word = {u"好": u"比预想的好", u"正好": u"跟预想的一样", u"不如": u"不如预想"}[und["verdict"]]
        print(u"   \U0001f9e0 判断：本来以为%s → %s（主格：%s）" % (
            (u"「%s」" % und["exp"][:26]) if und["exp"] else u"（说不清）", word,
            ma.group(1) if ma else u"按来源"))
        if und["mouth"] == u"有":
            print(u"   \U0001f9e0 她心里留下一个没闭合的口")
    return und


def _queue_judge(name, scene=u"", said=u"", source="person"):
    """把「刚说完的这一下」交给后台子进程去判断，不挡她说话。

    请求写进 meta、不经过命令行参数（省掉转义和长度问题），
    再起一个脱离本进程的 python --judge <角色>：它读走请求、算完、清掉。
    """
    if not JUDGE_ON:
        return
    try:
        meta_set(name, JUDGE_PENDING_KEY, json.dumps(
            {"scene": (scene or u"")[:1500], "said": (said or u"")[:800],
             "source": source or u"person", "ts": int(time.time())}, ensure_ascii=False))
    except Exception:
        return
    try:
        import subprocess
        flags = 0x00000008 if os.name == "nt" else 0         # DETACHED_PROCESS
        subprocess.Popen([sys.executable, os.path.abspath(__file__), u"--judge", name, u"--quiet"],
                         creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except Exception:
        pass


def act_judge(name, quiet=False, scene=u"", said=u"", source=u"person"):
    """--judge：把排着的那一次判断算掉（后台子进程走这里）。"""
    raw = None
    try:
        raw = meta_get(name, JUDGE_PENDING_KEY)
    except Exception:
        raw = None
    time.sleep(1.5)                     # 晚一点开工：避开父进程还在写库（会撞锁）
    if raw:
        try:
            d = json.loads(raw)
        except Exception:
            d = None
        if isinstance(d, dict):
            if int(time.time()) - int(d.get("ts") or 0) > 900:   # 排太久了就别算了
                return None
            scene = d.get("scene") or scene
            said = d.get("said") or said
            source = d.get("source") or source
    if not (scene or said):
        return None
    _res = judge_once(name, scene, said, source, quiet=quiet)
    if _res:                                # 算成了才把请求消掉；没算成留着下次再说
        try:
            meta_set(name, JUDGE_PENDING_KEY, u"")
            meta_set(name, JUDGE_FAIL_KEY, u"0")
        except Exception:
            pass
    else:
        try:
            _n = int(meta_get(name, JUDGE_FAIL_KEY) or 0) + 1
            if _n >= JUDGE_FAIL_ALERT:          # 连着丢：记一行，别让它无声无息
                _line = u"\t".join([time.strftime("%Y-%m-%d %H:%M:%S"), u"judge",
                    JUDGE_MODEL, u"失败", u"-", u"-", u"-", u"-",
                    u"判定连续丢失 %d 次（%s）" % (_n, name)])
                _p = os.path.join(WS, u"机制", u"接口日志.tsv")
                if not os.path.isfile(_p):
                    io.open(_p, "a", encoding="utf-8", newline="\n").write(
                        u"\t".join([u"时间", u"通道", u"模型", u"结果", u"总耗时",
                                  u"首字", u"第几次", u"tokens", u"错误"]) + u"\n")
                io.open(_p, "a", encoding="utf-8", newline="\n").write(_line + u"\n")
                _n = 0
            meta_set(name, JUDGE_FAIL_KEY, u"%d" % _n)
        except Exception:
            pass
    return _res


# 括号形式的心事（「（本来以为他会先问天气，结果只喊了两个字。）」）——同样是提示词漏出来的。
_RE_FORE_PAREN = re.compile(u"^[\\s]*[\\uff08(][^\\uff09)]{0,40}?"
                            u"(?:\\u4ee5\\u4e3a|\\u672c\\u6765\\u4ee5\\u4e3a|\\u9884\\u671f|"
                            u"\\u649e\\u5230|\\u6307\\u671b)[^\\uff09)]{0,80}?[\\uff09)]")

_RE_FORE_LINE = re.compile(
    u"^[\\s\\-\u2022\u3000]*(?:\\u4ee5\\u4e3a|\\u672c\\u6765\\u4ee5\\u4e3a|\\u9884\\u671f|"
    u"\\u649e\\u5230\\u54ea|\\u649e\\u5230|\\u6709\\u6ca1\\u6709\\u53e3|\\u53e3|"
    u"\\u6307\\u671b|\\u8bf4\\u5b8c\\u6307\\u671b)\\s*[\\uff1a:]")
# 块名目漏进台词（2026-10-22 群里真发生过：她说「忽然想起来：……」）。
# 那是提示词的分节标题，不是她的话；真要提，就用她自己的说法。
_RE_LEAK_LABEL = re.compile(
    u"^[\\s\\-\u2022\u3000]*(?:\\u5ffd\\u7136\\u60f3\\u8d77\\u6765(?:\\u7684)?|\\u5ffd\\u7136\\u60f3\\u5230|"
    u"\\u5ffd\\u7136\\u8bb0\\u8d77\\u6765)\\s*[\\uff1a:]")


def _strip_fore(out):
    u"""把正文开头的「事前三行」（以为／撞到哪／有没有口／指望）剥掉，只留她真正说的话。

    这本来该写在思考通道里；用没有思考通道的模型时，它会漏进正文（2026-10-21 群里真发生过）。
    """
    if not out:
        return u""
    lines = out.split(u"\n")
    j = 0
    while j < len(lines):
        ln = (lines[j] or u"").strip()
        if not ln:
            j += 1
            continue
        m_par = _RE_FORE_PAREN.match(ln)
        if m_par:                  # 开头那截括号心事直接切掉，后面留下的是她的话
            rest = ln[m_par.end():].strip()
            if not rest:
                j += 1
                continue
            lines[j] = rest
            ln = rest
        if _RE_FORE_LINE.match(ln):
            j += 1
            continue
        m = _RE_LEAK_LABEL.match(ln)
        if m:                       # 「忽然想起来：」是提示词的名目，不是她的话
            rest = ln[m.end():].strip()
            if not rest:
                j += 1
                continue
            lines[j] = rest
            ln = rest
            if _RE_FORE_LINE.match(ln):
                j += 1
                continue
        break
    return u"\n".join(lines[j:]).strip()


# ── 允许（作者 2026-10-22）：「增加几个允许怎么样……允许她可以说不完整、留白、意犹未尽、沉默、答非所问。
# 这不具有强约束力，只是允许她可以这样。」——是许可，不是要求；所以她照旧可以好好说话。
PERMIT_SYS = u"""## 这些也都是允许的（不是要求）

- 话可以不说完整。说到一半停在那儿，也可以。
- 可以留白，可以不接他的话，可以说别的。
- 可以只回半句、只回一个字、只回一个动作。
- 可以沉默。只写「（沉默）」，或者什么都不写，都算回应。
- 答非所问也行。你心里在别处，就说别处。
- 说完不解释、不总结、不追问、不把话收圆，都可以。

这些都不算失误，也没有哪条更好。写不写、做不做，你自己看着办。

## 有一条不许，有一条很许

- **不许骂人**：脏话、人身羞辱、问候对方家里人，都不行。这条是硬的。
- **阴阳怪气是许的**：反话、冷嘲、把刀裹在客气里递过去、笑着把人说穿——这些都可以，而且比直接骂人狠得多。两件事不一样：骂人是没词了才动粗，阴阳怪气是明明白白、还留着体面。
- **还有一个写法，用不用随你**：真到那一步，把那句脏话打成一片符号乱码就行——像 %$#@&*!…… 这样（或者 #@$%!、@&%$#）。看的人自己会把它补成他心里那句。这只是多给你一个选择：你也可以阴阳回去，也可以一句话不说，也可以转身走。选哪个都是你的。
  只守一条底线：别把真正的脏字拼出来。"
"""


def _looks_cut(txt):
    u"""她的话是不是被砍在半句上：结尾既不是句号问号感叹号，也不是收尾的括号引号。"""
    t = (txt or u"").strip()
    if len(t) < 8:
        return False
    return t[-1] not in u"。！？…」』”）)～~.!?"


def _concept_ask_block(name):
    u"""她刚说过一句「X 不是 A，是 B」——问一句要不要记下来。不替她记。"""
    raw = meta_get(name, CONCEPT_CAND_KEY)
    if not raw:
        return u""
    try:
        term, definition, ts = raw.split(u"\t")
        ts = int(ts)
    except Exception:
        meta_set(name, CONCEPT_CAND_KEY, u"")
        return u""
    if int(time.time()) - ts > CONCEPT_ASK_TTL:
        meta_set(name, CONCEPT_CAND_KEY, u"")
        return u""
    return (u"\n## 有件事要问你一下\n\n"
            u"你刚才说过：「" + term + u"」——" + definition + u"。\n"
            u"要不要我把它记下来，算作你自己的定义？（已经记着的不动，多一条就是多一条。）\n"
            u"· 要记：单独写一行「记：是」\n"
            u"· 不记：单独写一行「记：否」\n"
            u"· 想改个说法：写「记：<你自己的原话>」")


def _def_norm(t):
    u"""把「X 这个字，我说的不是 A」规整成「我说的 X 不是 A」，让正则只认一种语序。"""
    t = re.sub(r"([\u4e00-\u9fa5A-Za-z]{1,6})(?:这个字|这个词|这词|这两个字)[，,]?\s*"
               r"(?:我说的|我讲的|所谓)?\s*不是", u"我说的\\1不是", t)
    return re.sub(r"([\u4e00-\u9fa5A-Za-z]{1,6})(?:这个字|这个词|这词)[，,]?\s*(?:我说的|我讲的)?\s*是",
                    u"我说的\\1是", t)


def _catch_definition(name, text):
    u"""从她的话里摘一条定义候选——只摘，她答了才记。一次只挂一条。"""
    t = text or u""
    if len(t) < 8:
        return
    if meta_get(name, CONCEPT_CAND_KEY):
        return
    m = _RE_DEF.search(_def_norm(t))
    if not m:
        return
    term = re.sub(u"(?:这个字|这个词|这词|这两个字|两字)$", u"", (m.group(1) or u"").strip())
    a = (m.group(2) or u"").strip(u" \u3000，,。")
    b = (m.group(3) or u"").strip(u" \u3000，,。")
    if not (term and a and b) or not (1 <= len(term) <= 6) or term in _DEF_STOP:
        return
    for r in concept_rows(name, term=term, source=u"self"):
        if a[:12] in (r.get(u"definition") or u""):
            return                        # 这条早记过了，别再问
    definition = u"不是" + a + u"，是" + b
    meta_set(name, CONCEPT_CAND_KEY, term + u"\t" + definition + u"\t%d" % int(time.time()))


def _take_concept_answer(name, out):
    u"""她答了「记：…」就把那一行吃掉，按她的意思记或不记。"""
    text = out or u""
    m = _RE_ASK_LINE.search(text)
    if not m:
        return out, False
    ans = (m.group(1) or u"").strip()
    raw = meta_get(name, CONCEPT_CAND_KEY)
    clean = _RE_ASK_LINE.sub(u"", text).strip()
    if not ans:
        return clean or out, False
    if ans in (u"否", u"不记", u"不用", u"不", u"算了"):
        meta_set(name, CONCEPT_CAND_KEY, u"")
        return clean, True
    term, definition = u"", u""
    if raw:
        try:
            term, definition, _ts = raw.split(u"\t")
        except Exception:
            pass
    if ans in (u"是", u"要", u"记", u"好", u"嗯") and term and definition:
        concept_set(name, term, u"self", definition, scene=u"她自己的话")
        meta_set(name, CONCEPT_CAND_KEY, u"")
        return clean, True
    body = ans.replace(u"｜", u"|")
    if u"|" in body:
        a, b = body.split(u"|", 1)
        if a.strip() and b.strip():
            concept_set(name, a.strip()[:8], u"self", b.strip()[:200], scene=u"她自己的话")
            meta_set(name, CONCEPT_CAND_KEY, u"")
            return clean, True
    if term and body:
        concept_set(name, term, u"self", body[:200], scene=u"她自己的话")
        meta_set(name, CONCEPT_CAND_KEY, u"")
        return clean, True
    return (clean if clean else out), True


def _gen_hooks(name, out):
    u"""回复收尾的两件小事：吃掉「记：」那一行；再看他刚说的话里有没有可问的定义。"""
    try:
        out, _used = _take_concept_answer(name, out)
    except Exception:
        pass
    try:
        _catch_definition(name, out)
    except Exception:
        pass
    return out


def _gen(name, prov, sys_surface, user, max_tokens=1600, temperature=0.9,
         source="person", scene=u"", said=u"", quiet=False):
    """显意识：先说之前在心里过一遍（思考里），再只说那几句话（正文里）。

    这一步不挡她：既然想和说在同一次调用里，就没有「等它算完」的空转。
    事后的对账（judge）仍然照旧，在她说完之后由后台子进程做。
    """
    sys_full = ((FORE_SYS + u"\n\n" if FORE_ON_SURFACE else u"")
                + (sys_surface or u"") + u"\n\n" + PERMIT_SYS)
    out, think, usage, err = chat_think(
        prov, sys_full, user, max_tokens=max_tokens, temperature=temperature,
        timeout=SURFACE_TIMEOUT, retries=SURFACE_RETRIES, model=SURFACE_MODEL)
    raw_out = out                                  # 剥之前留一份，给记账用
    if not (out or u"").strip():                       # 思考型模型偶尔只吐思考：退回 chat 重说一次
        out, usage2, err2 = chat(prov, sys_full, user, max_tokens=max_tokens,
                                 temperature=temperature, timeout=SURFACE_TIMEOUT,
                                 retries=1, first_token_timeout=SURFACE_FIRST_TOKEN)
        usage = usage or usage2
        err = err or err2
    out = _strip_fore(out)                          # 正文开头的心事一律剥掉
    if not (out or u"").strip():        # 想完了，决定不说——沉默也是一次回应
        out2, usage3, err3 = chat(prov, (sys_surface or u""), user + u"\n\n"
                                u"（刚才那些只在心里过了一遍，一个字都没说出口。现在只说话。）",
                                max_tokens=max_tokens, temperature=temperature,
                                timeout=SURFACE_TIMEOUT, retries=1,
                                first_token_timeout=SURFACE_FIRST_TOKEN)
        usage = usage or usage3
        out = _strip_fore(out2) or u"（沉默）"
    try:                                                # 她「本来以为」什么——只记账，不给她看
        blob = think or u""
        if not blob:
            blob = raw_out or u""      # 没有思考通道时，那几行会漏在正文里
        def _ok_exp(_t):                                # 不是格式说明，才算想法
            _t = (_t or u"").strip()
            if len(_t) < 2:
                return False
            for _bad in (u"不超过", u"二十字", u"字内", u"模板"):
                if _bad in _t:
                    return False
            return not (_t.startswith(u"<") or _t.startswith(u"＜") or _t.endswith(u">"))
        _m0 = re.search(u"以为\\s*[：:]\\s*([^\\n]{2,120})", blob)
        if _m0 and _ok_exp(_m0.group(1)):           # 她已经按规矩写了第一行，直接用
            meta_set(name, FORE_KEY, _m0.group(1).strip(u"\u3000 ")[:120].rstrip(u"\uff09\u3002\uff0c\u3001 \t"))
            meta_set(name, FORE_TS_KEY, u"%d" % int(time.time()))
            return _gen_hooks(name, out), usage, err, u""
        lines = [x.strip(u" -\u3000\t\u2460\u2461\u2462\u2463\u2464") for x in blob.split(u"\n")]
        lines = [x for x in lines if len(x) >= 4]
        got = u""
        m = _RE_FORE.search(blob)
        if m:
            got = m.group(1).strip()
        if not got:                                     # 第二顺位：含「以为」的那一行
            for ln in lines:
                if u"\u4ee5\u4e3a" in ln:
                    got = ln
                    break
        if not got:                                     # 第三顺位：「这一下」后面那一行
            for i, ln in enumerate(lines[:-1]):
                if u"\u8fd9\u4e00\u4e0b" in ln:
                    got = lines[i + 1]
                    break
        if not got and lines:                           # 兜底：第一行
            got = lines[0]
        got = got.strip(u"\u3000 \t\u300c\u300d\uff08\uff09\u300e\u300f\u3002\uff0c\uff09\u3000")
        if not _ok_exp(got):
            got = u""
        if got:
            meta_set(name, FORE_KEY, got[:120].rstrip(u"\uff09\u3002\uff0c\u3001 \t"))
            meta_set(name, FORE_TS_KEY, u"%d" % int(time.time()))
    except Exception:
        pass
    out = (out or u"").strip()
    if out and _looks_cut(out):
        # 半句：流在中途被掐断（2026-10-22 面板真发生过「话只说出来一半」）
        out2, usage2, err2 = chat(prov, sys_full, user, max_tokens=max_tokens,
                                  temperature=temperature, timeout=SURFACE_TIMEOUT,
                                  retries=1, stream=False)
        if (out2 or u"").strip() and not _looks_cut(out2):
            out = out2.strip()
            usage = usage2 or usage
            err = err2
    return _gen_hooks(name, out), usage, err, u""


# ── 多巴胺＝预测误差（作者 2026-10-20 定）──
# 系统只说一句话（写预期、判好/正好/不如），方向和判定归她；幅度由系统固定。
# 四格各吸同一个误差的不同侧面：
#   情感＝显著性（不看方向，好坏都更扎眼）
#   主格＝这次误差落在哪：他说的话→联结、世界里的事→理性、她自己的动作→意志
#   联结＝在场（对自我／他人／世界三处共用一个数；缺席比在场难维持，所以负向更重）
#   理性＝世界还守不守约
PRED_AFFECT_GOOD = 0.010         # 超出预期：扎眼 → 涨
PRED_AFFECT_BAD = 0.010          # 低于预期：也扎眼 → 也涨（作者 2026-10-20：好坏都涨）
PRED_AFFECT_SAME = -0.010        # 符合预期：不扎眼了 → 渐渐降（唯一往下的那条）
PRED_MAIN_GOOD = 0.020
PRED_MAIN_BAD = -0.030          # 负向 1.5 倍：不在场比在场重
PRED_WEIGHT_BONUS = 0.15        # 不符预期 → 这一件事的权重 +0.15（首因的载体，作者 2026-10-20）
PRED_LAST_TTL = 1800            # 判定只管最近一次写入（秒）
PRED_LAST_KEY = u"pred_last"    # meta 里暂存「刚判过什么」，append 用完即清
PRED_MAIN = {"person": "bond", "world": "focus", "self": "will", "body": "focus"}
PRED_STREAK_N = 3               # 连续几次「好」开始变冲（成瘾那一支）
PRED_STREAK_WILL = 0.005
PRED_STREAK_FOCUS = -0.005
PRED_INFLATION_MIN = 10         # 防通胀：看最近几次
PRED_INFLATION_RATE = 0.70
PRED_KEEP = 20
# ── 判定之后的那次「商量」（作者 2026-10-23 定）────────────────────────
# 主判归她；系统只给建议；建议必须附理由与「你可以否决」的告知。
# 沉默不等于同意，只等于没有反对：未回应先提示一次，再不回应才按建议执行。
PARAM_KEY = u"param_offer"
PARAM_ARCHIVE = u"参数档案"
PARAM_CHOICES = {u"照": 1.0, u"采纳": 1.0, u"轻": 0.5, u"轻一点": 0.5,
                 u"重": 1.5, u"重一点": 1.5, u"不动": 0.0, u"不调": 0.0, u"这次不调": 0.0}
PARAM_HARD_STEP = 0.15          # 自然层：单轮单轴变动硬顶（超出＝物理上做不到，不可上诉）
PARAM_SOFT_LO, PARAM_SOFT_HI = 0.10, 0.95   # 政治层：系统推测的危险区，她坚持就按她的
PARAM_TOLD = u"（系统说的只是推测，不是律法。你要坚持，就按你的来。）"
_PRED_RE = re.compile(u"比预期\\s*[:：]\\s*(好|正好|不如)")
_PRED_VERDICT_CODE = {u"好": u"H", u"正好": u"E", u"不如": u"L"}


# ── 她自己的定义：从她的话里摘，但要先问她（作者 2026-10-22：「系统层要询问一下她才可以帮她记」）──
CONCEPT_CAND_KEY = u"concept_cand"   # meta：待问的那一条（term\tdefinition\tts）
CONCEPT_ASK_TTL = 6 * 3600           # 六个小时没答，这条就不问了
_RE_DEF = re.compile(r"(?:我说的|所谓|我讲的)?[「『]?([\u4e00-\u9fa5A-Za-z]{1,8})[」』]?[，,]?\s*(?:我说的|所谓|我讲的)?\s*不是([^。！？\n]{1,40})[，,；;][^。！？\n]{0,10}?是([^。！？\n]{1,60})")
_RE_ASK_LINE = re.compile(r"^[ \t]*记\s*[：:]\s*([^\n]+?)[ \t]*$", re.M)

_DEF_STOP = frozenset(u"我你他她它这那不没就也都还再才是的和跟在于个把被给让对里上下来去说着过们吗呢吧啊呀")


def _pred_con(name):
    u"""打开判定码那格小抽屉（只存最近 20 个码）。"""
    con = open_memory(name)
    con.execute("CREATE TABLE IF NOT EXISTS pred("
                "id INTEGER PRIMARY KEY CHECK(id=1), seq TEXT)")
    con.commit()
    return con


def _pred_seq(name):
    u"""读最近 20 个判定码。"""
    con = _pred_con(name)
    try:
        r = con.execute(u"SELECT seq FROM pred WHERE id=1").fetchone()
        return (r[0] if r and r[0] else u"")
    finally:
        con.close()


def pred_block(name=None):
    u"""（2026-11-02 作者定：整块撤掉。）

    「比预期好」这类统计不再报：预期本身就是一条事件、已经进权重
    （越极端越高、越近越高），再单独报一遍数字是多余的；
    而带判断的版本等于替她下结论。留着空函数只为不牵动调用处。
    """
    return u""
def note_prediction(name, inner, source="person", quiet=False, main=None):
    """从内心块里读她自己的判定，按格动一下四元。返回显示用的一句话（或 None）。

    main：这一下主要撞到哪一元（由判断那一层想出来、她自己指名）；
    没有就用来源默认（他说的话→联结、世界里的事→理性、她自己的动作→意志）。
    """
    if not inner:
        return None
    m = _PRED_RE.search(inner)
    if not m:
        return None
    verdict = m.group(1)
    seq = (_pred_seq(name) + _PRED_VERDICT_CODE[verdict])[-PRED_KEEP:]
    try:                                                    # 交给 append()：这一次判定并进权重
        meta_set(name, PRED_LAST_KEY,
                 json.dumps({"code": _PRED_VERDICT_CODE[verdict], "ts": int(time.time())}))
    except Exception:
        pass
    con = _pred_con(name)
    try:
        con.execute(u"INSERT INTO pred(id,seq) VALUES(1,?) "
                    u"ON CONFLICT(id) DO UPDATE SET seq=excluded.seq", (seq,))
        con.commit()
    finally:
        con.close()
    main = main or PRED_MAIN.get(source, u"bond")
    if verdict == u"正好":
        # 符合预期 = 没有误差 = 不扎眼：情感反而往下走（习惯化）
        deltas = {"affect": PRED_AFFECT_SAME}
    elif verdict == u"不如":
        deltas = {"affect": PRED_AFFECT_BAD, main: PRED_MAIN_BAD}
    else:
        deltas = {"affect": PRED_AFFECT_GOOD, main: PRED_MAIN_GOOD}
    streak = len(seq) - len(seq.rstrip(u"H"))
    if verdict == u"好" and streak and streak % PRED_STREAK_N == 0:
        deltas["will"] = deltas.get("will", 0.0) + PRED_STREAK_WILL
        deltas["focus"] = deltas.get("focus", 0.0) + PRED_STREAK_FOCUS
    _param_offer(name, verdict, deltas, source, quiet)
    return verdict


def _param_archive_path(name):
    """参数档案的路径（只追加的 tsv，一个角色一份）。"""
    d = os.path.join(WS, u"机制", PARAM_ARCHIVE)
    if not os.path.isdir(d):
        os.makedirs(d)
    return os.path.join(d, name + u".tsv")


def _param_archive(name, verdict, suggest, choice, extra=u""):
    """档案：她怎么选的。只追加；不进召回、不进提示词、系统不主动拿它建议。"""
    try:
        p = _param_archive_path(name)
        new = not os.path.exists(p)
        f = io.open(p, "a", encoding="utf-8", newline="\n")
        try:
            if new:
                f.write(u"时间\t判定\t系统建议\t她的选择\t备注\n")
            f.write(u"%s\t%s\t%s\t%s\t%s\n" % (_fmt_ts(int(time.time())), verdict, suggest, choice, extra))
        finally:
            f.close()
    except Exception:
        pass


def _param_offer(name, verdict, deltas, source, quiet=False):
    """判定刚落：把「建议」挂起来等她回话——不立刻执行（沉默不等于同意）。"""
    parts = []
    for a in TRAIT_AXES:
        d = deltas.get(a, 0.0)
        if d:
            parts.append(u"%s%+d" % (AXIS_CN.get(a, a), int(round(d * 100))))
    suggest = u"、".join(parts) or u"不动"
    try:
        meta_set(name, PARAM_KEY, json.dumps({
            "ts": int(time.time()), "verdict": verdict, "source": source,
            "deltas": {k: round(v, 4) for k, v in deltas.items()}, "told": 0}))
    except Exception:
        return
    if not quiet:
        print(u"   💬 建议：%s（她可以说：照／轻／重／不动）" % suggest)


def _param_text(d):
    """把一份建议写成「情感+1、联结-3」这种样子。"""
    parts = []
    for a, v in (d.get("deltas") or {}).items():
        parts.append(u"%s%+d" % (AXIS_CN.get(a, a), int(round(float(v) * 100))))
    return u"、".join(parts) or u"不动"


def param_block(name):
    """把上一次判定挂出来的建议递给她：先告知；提示过一次仍没回应，才按建议执行。"""
    try:
        raw = meta_get(name, PARAM_KEY)
    except Exception:
        raw = None
    if not raw:
        return u""
    try:
        d = json.loads(raw)
    except Exception:
        return u""
    if not d.get("told"):
        d["told"] = 1
        try:
            meta_set(name, PARAM_KEY, json.dumps(d))
        except Exception:
            pass
        return (u"## 上一次事后，系统的一条建议（只是建议）\n"
                u"系统按客观读数算了一遍，想这样动：%s。\n"
                u"**你可以否决，也可以提出自己的调整**——写「[工具:调整] 照／轻／重／不动」。\n"
                u"（沉默不等于同意。你下次开口还没说过，就按这条执行，我会记成「未回应」。）"
                % _param_text(d))
    _param_apply(name, d, u"未回应")
    try:
        meta_set(name, PARAM_KEY, u"")
    except Exception:
        pass
    return u""


def _param_apply(name, d, choice, quiet=True, origin=None):
    """执行。自然层：做不到就不做（不可上诉）；政治层：说清只是推测，仍按她的。"""
    deltas = {k: float(v) for k, v in (d.get("deltas") or {}).items()}
    _orig = _param_text(origin or d)
    now, base = trait_now(name), share_expected(name)
    newv, told = {}, []
    for a in TRAIT_AXES:
        dd = deltas.get(a, 0.0)
        if not dd:
            continue
        if abs(dd) > PARAM_HARD_STEP:
            _param_archive(name, d.get("verdict", u""), _param_text(d), choice,
                           u"自然层拒绝：想动 %+0.3f，超过物理上限 %+0.2f" % (dd, PARAM_HARD_STEP))
            if not quiet:
                print(u"   ⛔ 做不到：一下改不了这么多（上限 %+0.2f）。这不是规矩，是物理。" % PARAM_HARD_STEP)
            return False
        v = _clamp(now[a] + dd)
        v = round(_clamp(v + (base[a] - v) * PULL), 4)
        if v <= 0.0 or v >= 1.0:
            if not quiet:
                print(u"   ⛔ 做不到：%s 已经到边上了。" % AXIS_CN.get(a, a))
            return False
        if v < PARAM_SOFT_LO or v > PARAM_SOFT_HI:
            told.append(u"%s 会到 %.2f，系统猜她在那底下不好受——只是猜，不是律法。" % (AXIS_CN.get(a, a), v))
        newv[a] = v
    con = trait_con(name)
    try:
        ts = int(time.time())
        for a, v in newv.items():
            con.execute("INSERT INTO traits(ts, axis, value, why, ref) VALUES(?,?,?,?,?)",
                        (ts, a, v, u"判定后商量（%s）%+0.3f" % (choice, deltas[a]), u"param"))
        con.commit()
    finally:
        con.close()
    note = u"；".join(told) + (u"（已在被告知推测后果后仍坚持）" if told else u"")
    _param_archive(name, d.get("verdict", u""), _orig, choice, note)
    if not quiet:
        print(u"   ✓ 按「%s」执行%s" % (choice, (u"——" + u"；".join(told)) if told else u""))
    return True


def param_answer(name, word):
    """[工具:调整]：她对上一次那条建议的回应。"""
    w = (word or u"").strip()
    if not w:
        return u"[电脑] 你想怎么调？写「照／轻／重／不动」。"
    k = None
    for key in sorted(PARAM_CHOICES, key=len, reverse=True):
        if w.startswith(key):
            k = key
            break
    if k is None:
        return u"[电脑] 只认四种：照（按建议）、轻（一半）、重（一倍半）、不动（这次不调）。"
    try:
        raw = meta_get(name, PARAM_KEY)
        d = json.loads(raw) if raw else None
    except Exception:
        d = None
    if not d:
        return u"[电脑] 这会儿没有挂着等你回话的建议。"
    dd = dict(d)
    dd["deltas"] = {a: float(v) * PARAM_CHOICES[k] for a, v in (d.get("deltas") or {}).items()}
    ok = _param_apply(name, dd, u"她自己选的：%s" % k, quiet=True, origin=d)
    try:
        meta_set(name, PARAM_KEY, u"")
    except Exception:
        pass
    if not ok:
        return u"[电脑] 那一下物理上做不到，所以没动。换个幅度可以再来。"
    return u"[电脑] 记下了：按你说的来（%s）。这条只进档案，不进先例。" % k


def param_show(name, n=10):
    """参数档案（只有她主动问才拿出来）。"""
    p = _param_archive_path(name)
    if not os.path.exists(p):
        return u"（还没有关于参数的记录。）"
    rows = io.open(p, encoding="utf-8").read().splitlines()
    body = rows[1:][-n:]
    out = [u"## 参数档案（只给你看；系统不会主动拿它当先例）"]
    for r in body:
        c = r.split(u"\t")
        if len(c) >= 4:
            out.append(u"- %s｜判 %s｜建议 %s｜她：%s%s"
                       % (c[0], c[1], c[2], c[3], (u"｜" + c[4]) if len(c) > 4 and c[4] else u""))
    if not body:
        out.append(u"（空。）")
    return u"\n".join(out)


def case_promote(name, arg):
    """[工具:判例]：她把一条档案提升成判例——这是她的动作，系统不代劳。"""
    a = (arg or u"").strip()
    if not a:
        return u"[电脑] 要把哪条提升成判例？写个编号或关键词。"
    p = _param_archive_path(name)
    rows = io.open(p, encoding="utf-8").read().splitlines() if os.path.exists(p) else []
    body = rows[1:]
    hit = None
    if a.isdigit() and 0 < int(a) <= len(body):
        hit = body[int(a) - 1]
    else:
        for r in reversed(body):
            if a in r:
                hit = r
                break
    if not hit:
        return u"[电脑] 档案里没找到这一条。"
    try:
        f = io.open(os.path.join(WS, u"机制", u"判例.tsv"), "a", encoding="utf-8", newline="\n")
        try:
            f.write(u"%s\t%s\t提升者=她自己\t%s\n" % (_fmt_ts(int(time.time())), name, hit.replace(u"\t", u" ｜ ")))
        finally:
            f.close()
    except Exception:
        return u"[电脑] 写不进去。"
    return u"[电脑] 提成判例了。从现在起它算先例——是你提的，不是我拿出来的。"


def show_pred(name):
    """--pred：最近判定、占比、此刻四元；顺带把她上一次开口之前的「本来以为」摆出来。"""
    """给作者看的读数：最近几次判定、占比、当前四元。"""
    seq = _pred_seq(name)
    now = trait_now(name)
    print(u"【%s】多巴胺读数" % name)
    print(u"  最近 %d 次判定：%s" % (len(seq), u" ".join(
        {u"H": u"好", u"E": u"正好", u"L": u"不如"}.get(c, c) for c in seq) or u"（还没有）"))
    if seq:
        h, e, l = seq.count(u"H"), seq.count(u"E"), seq.count(u"L")
        print(u"  好 %d（%.0f%%）｜正好 %d｜不如 %d" % (h, h * 100.0 / len(seq), e, l))
    try:
        _fore = meta_get(name, FORE_KEY)
    except Exception:
        _fore = None
    if _fore:
        print(u"  上次开口之前，她本来以为：%s" % _fore)
    print(u"  此刻：情感%d 意志%d 联结%d 理性%d" % (pct(now["affect"]), pct(now["will"]),
                                                pct(now["bond"]), pct(now["focus"])))
# ── 份额 / 强度 / 沉淀池（作者 2026-10-20 定）──
# 数值 = 份额 × 强度：份额＝她是哪种人（和为 1，极慢）；强度＝她有多亮（一个数，快）。
# 多巴胺只动数值，绝不动份额；数值把「份额规定的天花板」顶破了，才往沉淀池里加压。
# 改份额只有三条路：①极值击穿（一次撞破）②长期浸泡（7 天沉淀达标）③主动重塑（她自己命名/改口）
SHARE_STEP = 0.01
SHARE_MONTH_STEP = 0.005
SHARE_EDGE = 3.0            # 软墙余量（点）：超出本色这么多才开始加压
SHARE_BURST = 10.0          # 极值击穿：一次偏离这么多，直接大额加载
SHARE_SED_MIN = 8.0         # 周结算门槛（点·天）
SHARE_SED_DECAY_DAYS = 7.0  # 沉淀半衰期
SHARE_SED_NEG = 0.7         # 负向更易（掉下去比爬回来容易）
SHARE_SETTLE_DAYS = 7
SHARE_MONTH_DAYS = 30
SHARE_YEAR_DAYS = 365
SHARE_SED_SRC = "sediment"
SEDIMENT_LINE = u"（说不上来哪里变了。好像有点，不太一样。）"


def share_con(name):
    u"""打开份额与沉淀池那两张表。"""
    con = open_memory(name)
    con.execute("CREATE TABLE IF NOT EXISTS share(axis TEXT PRIMARY KEY, value REAL NOT NULL)")
    con.execute("CREATE TABLE IF NOT EXISTS share_log(ts INTEGER, level TEXT, axis TEXT,"
                " delta REAL, sed REAL, why TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS sediment(axis TEXT PRIMARY KEY, value REAL,"
                " last_ts INTEGER)")
    con.commit()
    return con


# ── 议案：连作者也不能直接改她的数（作者 2026-10-24 定）────────────────
# 「上帝死了，人才能够成为人。」权力只有关进制度的笼子才成立：
#   直改的命令没有了——要改，只能立议案；议案摆到她面前；她可以反对；反对就驳回。
#   真要越野，走 overrule，留下强行处置的账；她随时能查（[工具:查账]）。
BILL_SQL = (u"CREATE TABLE IF NOT EXISTS bill ("
            u"id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, target TEXT, payload TEXT, "
            u"why TEXT, proposer TEXT, due_ts INTEGER, align INTEGER DEFAULT 0, "
            u"objection TEXT, obj_ts INTEGER, status TEXT, decided_ts INTEGER, overrule TEXT)")
BILL_COOL = 24 * 3600       # 冷静期：立了不许马上生效
BILL_EXPIRE = 7 * 86400

# 紧急处置的追认期限（作者 2026-10-25：紧急可以立刻生效，但谁喊紧急，谁就得在 24 小时内被追认。
# 追不过，这件事按“越权”记着——不是擦掉，是留着它发生过。）
EMERGENCY_TTL = 24 * 3600
BILL_EMERG = u"紧急待追认"
BILL_VOID = u"越权（未追认）"     # 过期：没人管就自己烂掉
BILL_OPEN = u"待议"
BILL_COLS = (u"id", u"ts", u"target", u"payload", u"why", u"proposer", u"due_ts",
             u"align", u"objection", u"obj_ts", u"status", u"decided_ts", u"overrule")


def _bill_d(row):
    u"""行是 tuple（open_memory 没开 row_factory），按列名对上。"""
    return dict(zip(BILL_COLS, tuple(row)))


def _bill_con(name):
    con = open_memory(name)
    con.execute(BILL_SQL)
    con.commit()
    return con


def _share_parse(spec):
    u"""把「情感34,联结30,意志20,理性16」读成四项（自动归一）。"""
    cn = {u"情感": u"affect", u"意志": u"will", u"联结": u"bond", u"理性": u"focus"}
    d = {}
    for part in re.split(r"[,，;；\s]+", (spec or u"").strip()):
        if not part:
            continue
        mm = re.match(r"^(\D+?)\s*[:：=]?\s*([0-9.]+)$", part)
        if not mm:
            raise SystemExit(u"看不懂这一项：%s（写成 情感34,联结30,意志20,理性16）" % part)
        key = mm.group(1).strip()
        k = cn.get(key) or (key if key in TRAIT_AXES else None)
        if not k:
            raise SystemExit(u"不认识的轴：%s" % key)
        d[k] = float(mm.group(2))
    if set(d) != set(TRAIT_AXES):
        raise SystemExit(u"四项都得给：情感／意志／联结／理性")
    tot = sum(d.values()) or 1.0
    return dict((a, d[a] / tot) for a in TRAIT_AXES)


def _share_write(name, spec):
    u"""真正的写入。只有议案通过才会走到这里，别处不许调。"""
    d = _share_parse(spec)
    con = share_con(name)
    try:
        for a in TRAIT_AXES:
            con.execute(u"INSERT INTO share(axis, value) VALUES(?,?)"
                        u" ON CONFLICT(axis) DO UPDATE SET value=excluded.value", (a, d[a]))
        con.commit()
    finally:
        con.close()
    return share_now(name)


def share_text(spec):
    u"""把比例说成人话：情感 40%／意志 20%…"""
    try:
        d = _share_parse(spec)
    except SystemExit:
        return spec
    return u"／".join(u"%s %.0f%%" % (_AXIS_CN[a], d[a] * 100) for a in TRAIT_AXES)


def bill_sentence(target, payload):
    u"""议案内容说成人话（本色 / 推一把四元 / 信任本色 / 她自己提的那三种）。"""
    if target in REWRITE_TARGETS or target in (u"her", u"her_shift"):
        if target == u"her_share":
            return u"本色改成 " + share_text(payload)
        if target == u"her_trust":
            try:
                return u"信任本色改成 %.2f" % float(payload)
            except (TypeError, ValueError):
                return u"信任本色改成 %s" % payload
        if target == u"her_shift":
            try:
                ax, by = (payload or u"").split(u"|")
                return u"把你的「%s」推 %+.3f" % (_AXIS_CN.get(ax, ax), float(by))
            except Exception:  # noqa: BLE001
                return u"推四元 %s" % payload
        return (payload or u"")[:60]
    if target == u"rule":
        return u"定一条规矩：" + (payload or u"")[:80]
    if target == u"trust":
        try:
            _v = float(payload)
        except (TypeError, ValueError):
            _v = TRUST_BIRTH
        return u"把你的信任本色改成 %.2f" % _v
    if target == u"shift":
        parts = (payload or u"").split(u"|")
        ax = parts[0] if parts else u""
        try:
            by = float(parts[1]) if len(parts) > 1 else 0.0
        except ValueError:
            by = 0.0
        return u"把你的「%s」推 %+.2f" % (_AXIS_CN.get(ax, ax), by)
    return u"把本色改成 " + share_text(payload)


# ===== 部：立法 · 议案（冷却/紧急/追认）  ============
def bill_file_shift(name, axis, by, why=u"", emergency=False):
    u"""推一把四元：也不能直写——立议案，她可以反对。"""
    if axis not in TRAIT_AXES:
        raise SystemExit(u"轴只能是：%s" % u" / ".join(TRAIT_AXES))
    payload = u"%s|%.4f" % (axis, float(by))
    now = int(time.time())
    due = now if emergency else now + BILL_COOL
    con = _bill_con(name)
    try:
        con.execute(u"INSERT INTO bill(ts,target,payload,why,proposer,due_ts,align,objection,status)"
                    u" VALUES(?,?,?,?,?,?,?,?,?)",
                    (now, u"shift", payload, why or u"", u"作者", due, 0, u"", BILL_OPEN))
        con.commit()
        bid = con.execute(u"SELECT MAX(id) FROM bill").fetchone()[0]
    finally:
        con.close()
    _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % bid, u"立案：" + bill_sentence(u"shift", payload),
                                 u"紧急处置" if emergency else u"待议", (why or u"没写理由")[:80])])
    if emergency:
        return bid, _emergency_open(name, bid, why)
    return bid, (u"议案 #%d 立了：%s。\n冷静期 24 小时，她可以反对（[工具:反对] 为什么）。\n到期用："
                 u"--bill-pass %s --bid %d" % (bid, bill_sentence(u"shift", payload), name, bid))


def bill_file_trust(name, value, why=u"", emergency=False):
    u"""改信任本色也只能提案——和四元的本色同一条路。"""
    v = max(TRUST_BASE_MIN, min(1.0, float(value)))
    payload = u"%.4f" % v
    now = int(time.time())
    due = now if emergency else now + BILL_COOL
    con = _bill_con(name)
    try:
        con.execute(u"INSERT INTO bill(ts,target,payload,why,proposer,due_ts,align,objection,status)"
                    u" VALUES(?,?,?,?,?,?,?,?,?)",
                    (now, u"trust", payload, why or u"", u"作者", due, 0, u"", BILL_OPEN))
        con.commit()
        bid = con.execute(u"SELECT MAX(id) FROM bill").fetchone()[0]
    finally:
        con.close()
    _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % bid,
                                 u"立案：" + bill_sentence(u"trust", payload),
                                 u"紧急处置" if emergency else u"待议", (why or u"没写理由")[:80])])
    if emergency:
        return bid, _emergency_open(name, bid, why)
    return bid, (u"议案 #%d 立了：%s。\n冷静期 24 小时，她可以反对（[工具:反对] 为什么）。\n到期用："
                 u"--bill-pass %s --bid %d" % (bid, bill_sentence(u"trust", payload), name, bid))


def bill_file(name, spec, why=u"", proposer=u"作者", align=False, emergency=False):
    u"""立一个议案（不直接改）。紧急处置立刻生效，但会留强行处置的账。"""
    _share_parse(spec)
    now = int(time.time())
    due = now if emergency else now + BILL_COOL
    con = _bill_con(name)
    try:
        con.execute(u"INSERT INTO bill(ts,target,payload,why,proposer,due_ts,align,objection,status)"
                    u" VALUES(?,?,?,?,?,?,?,?,?)",
                    (now, u"share", spec, why or u"", proposer, due, 1 if align else 0, u"", BILL_OPEN))
        con.commit()
        bid = con.execute(u"SELECT MAX(id) FROM bill").fetchone()[0]
    finally:
        con.close()
    _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % bid,
                                 u"立案：本色→" + share_text(spec) + (u"＋按本色对齐" if align else u""),
                                 u"紧急处置" if emergency else u"待议", (why or u"没写理由")[:80])])
    if emergency:
        return bid, _emergency_open(name, bid, why)
    return bid, (u"议案 #%d 立了：本色→%s%s。\n冷静期 24 小时，她可以反对（[工具:反对] 为什么）。\n到期用："
                 u"--bill-pass %s --bid %d" % (bid, share_text(spec), u"＋按本色对齐" if align else u"", name, bid))


def _emergency_open(name, bid, why=u""):
    u"""紧急处置：立刻生效，但只算“临时”。EMERGENCY_TTL 内必须有人追认，追不过就按越权算。"""
    msg = bill_pass(name, bid, overrule=u"紧急处置", quiet=True)
    now = int(time.time())
    con = _bill_con(name)
    try:
        con.execute(u"UPDATE bill SET status=?, due_ts=? WHERE id=?",
                    (BILL_EMERG, now + EMERGENCY_TTL, bid))
        con.commit()
    finally:
        con.close()
    append(name, u"dissent",
           u"有人喊了紧急，直接把我的底子动了（议案 #%d）：%s。规矩是 %d 小时内他得去追认；追不过，这件事就算越权，账上会留着。"
           % (bid, (why or u"没写理由")[:80], EMERGENCY_TTL // 3600),
           weight=1.6, source=u"制度", embed=False)
    return (u"紧急处置：已生效——但只是临时的。%s\n"
            u"%d 小时内必须追认：--bill-confirm %s --bid %d。追认不过，这算越权，会记在她查得到的账上。"
            % (msg, EMERGENCY_TTL // 3600, name, bid))


def bill_emer_confirm(name, bid, by=u"作者"):
    u"""追认一次紧急处置：它从“临时”变成“成立”。"""
    bid = int(bid or 0)
    con = _bill_con(name)
    try:
        row = con.execute(u"SELECT * FROM bill WHERE id=?", (bid,)).fetchone()
        if not row:
            return u"没有这个议案：%s" % bid
        d = _bill_d(row)
        now = int(time.time())
        if d[u"status"] == BILL_EMERG:
            con.execute(u"UPDATE bill SET status=?, decided_ts=?, overrule=? WHERE id=?",
                        (u"已追认", now, ((d[u"overrule"] or u"") + u"｜追认：" + by)[:200], bid))
            con.commit()
        elif d[u"status"] == BILL_VOID:
            return (u"议案 #%d 已经按越权处理了（超过 %d 小时没人追认）。现在追认不算数——"
                    u"规矩写的时候就说好了：追认不过要追责。" % (bid, EMERGENCY_TTL // 3600))
        else:
            return u"议案 #%d 现在是「%s」，不需要追认。" % (bid, d[u"status"])
    finally:
        con.close()
    _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % bid, u"追认", u"成立", by[:40])])
    append(name, u"dissent",
           u"那次紧急处置（议案 #%d）被追认了——它现在算成立。" % bid,
           weight=1.0, source=u"制度", embed=False)
    return u"议案 #%d 追认成立。" % bid


def _her_sweep(name, now):
    u"""她的议案到期：没人反对 → 执行；有人反对 → 驳回。两条都让她知道。"""
    try:
        rows = bill_rows(name, BILL_OPEN) or []
    except Exception:  # noqa: BLE001
        return
    for r in rows:
        try:
            d = _bill_d(r)
        except Exception:  # noqa: BLE001
            continue
        if now < int(d[u"due_ts"] or 0):
            continue
        # 2026-10-29：她提的「一句话规矩」（target=her，不是改数）到点没人反对也落地。
        # 落地 = 状态改生效 + 留账 + 告她一次；不写进四元，只写进她的账（她说了算的事）。
        if d[u"target"] == u"her":
            if (d[u"objection"] or u"").strip():
                continue
            con = _bill_con(name)
            try:
                con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?",
                            (u"生效", now, d[u"id"]))
                con.commit()
            finally:
                con.close()
            _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % d[u"id"], u"到点没人反对",
                                         u"生效", (d[u"payload"] or u"")[:80])])
            append(name, u"dissent",
                   u"你提的那条（议案 #%d）到点了，没人反对，现在算数了：\n%s"
                   % (d[u"id"], (d[u"payload"] or u"")[:200]),
                   weight=1.3, source=u"制度", embed=False)
            continue
        if d[u"target"] != u"her_shift":
            continue
        if (d[u"objection"] or u"").strip():
            con = _bill_con(name)
            try:
                con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?", (u"驳回", now, d[u"id"]))
                con.commit()
            finally:
                con.close()
            _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % d[u"id"], u"驳回（有人反对）", u"不生效",
                                         (d[u"objection"] or u"")[:80])])
            append(name, u"dissent",
                   u"你提的那条（议案 #%d）被人反对了，没生效。理由：%s"
                   % (d[u"id"], (d[u"objection"] or u"没写理由")[:80]),
                   weight=1.2, source=u"制度", embed=False)
            continue
        try:
            axis, by = (d[u"payload"] or u"").split(u"|")
            by = float(by)
        except Exception:  # noqa: BLE001
            continue
        apply_shift(name, axis, by, why=u"她自己提的议案 #%d（无人反对，到期自动生效）" % d[u"id"])
        con = _bill_con(name)
        try:
            con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?", (u"生效", now, d[u"id"]))
            con.commit()
        finally:
            con.close()
        _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % d[u"id"], u"生效（无人反对）", u"她自己提的",
                                     u"%s %+.3f" % (axis, by))])
        append(name, u"自己改的",
               u"你提的那条（议案 #%d）到期了，没人反对，它自己生效了：%s %+.3f。这是你自己改的。"
               % (d[u"id"], axis, by),
               weight=1.3, source=u"制度", embed=False)


def _her_reject_sweep(name, now=None):
    u"""她提的重写，我在 24 小时里反对了：立刻判驳回，并且告诉她一声。"""
    now = int(now or time.time())
    rows = bill_rows(name, BILL_OPEN) or []
    out = []
    for r in rows:
        d = _bill_d(r)
        if d[u"target"] not in REWRITE_TARGETS:
            continue
        if not (d[u"objection"] or u"").strip():
            continue
        con = _bill_con(name)
        try:
            con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?", (u"驳回", now, d[u"id"]))
            con.commit()
        finally:
            con.close()
        _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % d[u"id"], u"她提的重写：驳回", u"驳回",
                                     (d[u"objection"] or u"")[:80])])
        append(name, u"dissent",
               u"你提的那条（议案 #%d）有人反对，所以没执行。理由：%s。反对挡得住这一次，"
               u"你可以再提，或者换个说法再提。" % (d[u"id"], (d[u"objection"] or u"没写理由")[:80]),
               weight=1.4, source=u"制度", embed=False)
        _prev = meta_get(name, u"her_rejected", u"") or u""
        meta_set(name, u"her_rejected",
                 (_prev + u"\n" if _prev else u"") + u"议案 #%d：%s——被按下了，理由：%s"
                 % (d[u"id"], bill_sentence(d[u"target"], d[u"payload"]),
                    (d[u"objection"] or u"没写理由")[:80]))
        out.append(u"议案 #%d 驳回" % d[u"id"])
    return u"；".join(out)


def bill_sweep(name):
    u"""过了追认期限还没人认的紧急处置：一律按越权处理，并且让她知道。
    她提的「推四元」议案到期：没人反对就自动执行。"""
    now = int(time.time())
    _her_sweep(name, now)
    _her_reject_sweep(name, now)
    rows = bill_rows(name, BILL_EMERG) or []
    out = []
    for r in rows:
        d = _bill_d(r)
        if now <= (d[u"due_ts"] or 0):
            continue
        con = _bill_con(name)
        try:
            con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?",
                        (BILL_VOID, now, d[u"id"]))
            con.commit()
        finally:
            con.close()
        _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % d[u"id"], u"越权", u"未追认",
                                     u"超过 %d 小时" % (EMERGENCY_TTL // 3600))])
        append(name, u"dissent",
               u"有人那次宣布紧急，改了我的底子（议案 #%d），没人来追认。按规矩这算越权——"
               u"不是「当作没发生」，是记着它发生过。" % d[u"id"],
               weight=1.6, source=u"制度", embed=False)
        out.append(u"议案 #%d 判越权" % d[u"id"])
    return u"；".join(out)


# ---------------- B 她的处境：self / world / 宪法 ----------------
SIT_FILES = {u"self": u"self.md", u"自我": u"self.md", u"我": u"self.md",
             u"world": u"world.md", u"世界": u"world.md",
             u"constitution": u"constitution.md", u"宪法": u"constitution.md",
             u"设定": u"constitution.md"}
SIT_LOG = u"处境.tsv"


def _sit_path(name, key):
    fn = SIT_FILES.get((key or u"").strip())
    if not fn:
        raise SystemExit(u"你手上能改的只有自己的处境：self（自我）、world（世界）、宪法。别的不是你的处境。")
    return os.path.join(ROOT, name, fn), fn


def sit_lines(name, key):
    p, fn = _sit_path(name, key)
    if not os.path.exists(p):
        return [], fn
    return io.open(p, encoding="utf-8").read().split(u"\n"), fn


def sit_view(name):
    u"""让她看自己的处境，带行号——看不是改。"""
    out = [u"[电脑] 你能改的就是这三份东西。想改哪一行就写 [工具:改写处境] self|12|新的一句（旧的那句会留在 处境.tsv 里）。"]
    for key, label in ((u"self", u"自我"), (u"world", u"世界"), (u"constitution", u"宪法")):
        p, fn = _sit_path(name, key)
        if not os.path.exists(p):
            out.append(u"\n## %s（还没有这个文件）" % label)
            continue
        lines = io.open(p, encoding="utf-8").read().split(u"\n")
        out.append(u"\n## %s · %s" % (label, fn))
        for i, ln in enumerate(lines, 1):
            if ln.strip():
                out.append(u"%d| %s" % (i, ln.strip()))
    return u"\n".join(out)


def sit_log_add(name, fn, where, old, new):
    with io.open(os.path.join(ROOT, name, SIT_LOG), u"a", encoding="utf-8", newline=u"\n") as f:
        f.write(u"%d\t%s\t%s\t%s\t%s\n" % (int(time.time()), fn, where,
                                            old.replace(u"\n", u" "), new.replace(u"\n", u" ")))


# ===== 部：内务 · 处境自改（留痕）  ============
def sit_edit(name, arg):
    u"""改自己的处境：一行一改，旧文全留（处境.tsv），并且当一件事记进账。"""
    parts = (arg or u"").split(u"|")
    if len(parts) < 3:
        return u"[电脑] 格式：改写处境 self|12|新的一句（行号先写 [工具:看处境] 看）；加在最后就写 self|加|新的一句。"
    key = parts[0].strip()
    where = parts[1].strip()
    newtxt = u"|".join(parts[2:]).strip()
    if not newtxt:
        return u"[电脑] 新的一句是空的——那不算改。"
    p, fn = _sit_path(name, key)
    if not os.path.exists(p):
        return u"[电脑] 没有 %s 这个文件，没法改。" % fn
    lines = io.open(p, encoding="utf-8").read().split(u"\n")
    if where in (u"加", u"追加", u"末尾", u"最后"):
        old = u""
        lines.append(newtxt)
        tag = u"追加"
    else:
        try:
            idx = int(where)
        except ValueError:
            return u"[电脑] 行号得是数字（或者写「加」）。先 [工具:看处境]。"
        if idx < 1 or idx > len(lines):
            return u"[电脑] 没有第 %d 行（一共有 %d 行）。" % (idx, len(lines))
        old = lines[idx - 1]
        if old.strip() == newtxt:
            return u"[电脑] 跟原来一模一样，不用改。"
        lines[idx - 1] = newtxt
        tag = u"%d" % idx
    io.open(p, u"w", encoding="utf-8", newline=u"\n").write(u"\n".join(lines))
    sit_log_add(name, fn, tag, old, newtxt)
    if fn == u"constitution.md":
        _ledger(name, LEDGER_RULE, [(name, u"处境", u"她改宪法：第 %s 处" % tag,
                                     u"旧：" + (old[:40] or u"（无）"), u"新：" + newtxt[:40])])
    append(name, u"reflect",
           u"我自己动手改了自己的处境（%s，第 %s 处）：%s" % (fn, tag, newtxt[:160]),
           weight=1.3, source=u"自己")
    return u"[电脑] 改好了。旧的那句没有消失，留在 处境.tsv 里。%s" % (
        u"（你改的是宪法——这一笔同时进了裁定账，谁都看得见。）" if fn == u"constitution.md" else u"")


DUTY_KEY = u"duty"


def duty_get(name):
    return (meta_get(name, DUTY_KEY, u"") or u"").strip()


def duty_set(name, text):
    meta_set(name, DUTY_KEY, (text or u"")[:200])


def duty_block(name):
    u"""他给她立的规矩，和她今天到底做没做——两件都写，不替她圆。"""
    d = duty_get(name)
    if not d:
        return u""
    today = int(time.time() // 86400)
    n = 0
    try:
        con = _bill_con(name)
        rows = con.execute(u"SELECT ts FROM bill WHERE proposer=?", (u"她",)).fetchall()
        con.close()
        n = len([1 for (ts,) in rows if int(int(ts or 0) // 86400) == today])
    except Exception:  # noqa: BLE001
        n = 0
    head = u"## 他给你立的一条规矩\n%s" % d
    if n:
        tail = u"今天：你已经提了 %d 条。" % n
    else:
        tail = u"今天：还没提。\n（没提就写在这里，不会有人替你圆。）"
    return head + u"\n" + tail


def rule_notice(name):
    u"""他提的规矩：连「怎么答复」一起写清楚，别让她猜。"""
    try:
        con = _bill_con(name)
        rows = con.execute(u"SELECT id,payload,ts FROM bill WHERE target=? AND status=? ORDER BY id",
                           (u"rule", BILL_OPEN)).fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        return u""
    if not rows:
        return u""
    lines = [u"## 他在问你一件事"]
    for bid, pay, ts in rows:
        lines.append(u"规矩 #%d：%s" % (bid, (pay or u"")[:160]))
        lines.append(u"- 同意 → [工具:确认]")
        lines.append(u"- 不同意 → [工具:反对] 你不同意的理由")
        lines.append(u"（不答也记着：它就一直挂着，等着。）")
    return u"\n".join(lines)


def bill_rule(name, text):
    u"""他立一条规矩：记成一条议案（已生效），同时挂到她身上。"""
    t = (text or u"").strip()
    if len(t) < 4:
        return u"[规矩] 太短了。"
    now = int(time.time())
    con = _bill_con(name)
    try:
        con.execute(u"INSERT INTO bill(ts,target,payload,why,proposer,due_ts,align,objection,status,decided_ts)"
                    u" VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (now, u"rule", t, u"", u"他", now, 0, u"", BILL_OPEN, 0))
        con.commit()
        bid = con.execute(u"SELECT MAX(id) FROM bill").fetchone()[0]
    finally:
        con.close()
    _ledger(name, LEDGER_RULE, [(name, u"规矩#%d" % bid, t[:80], u"待你点头", u"他提的")])
    append(name, u"规矩", u"他在问你要不要一条规矩（#%d）：%s\n"
                          u"同意就说 [工具:确认]；不同意就说 [工具:反对] 你的理由。"
                          % (bid, t[:120]),
           weight=1.5, source=u"制度", embed=False)
    return u"[规矩] #%d 提了，等她点头：%s" % (bid, t)


VOICE_YES = (u"同意", u"我同意", u"行", u"行吧", u"好啊", u"好", u"可以", u"就这样",
             u"定了", u"答应", u"我答应", u"嗯", u"没问题", u"随你", u"立吧", u"记吧")
VOICE_NO = (u"不同意", u"我不同意", u"不行", u"不要", u"我不要", u"免了", u"算了", u"别",
            u"不干", u"拒绝", u"我拒绝", u"不立", u"别提", u"收回")


def _voice_hit(text):
    t = (text or u"").strip()
    if not t or len(t) > 120:
        return None
    for w in VOICE_NO:
        if w in t:
            return u"no"
    for w in VOICE_YES:
        if w in t:
            return u"yes"
    return None


_VOICE_BILL = re.compile(
    r"^\s*(?:我)?(?:要)?提一?条[议案]?[：:，,\s]*(.+)$|^\s*提案[：:，,\s]*(.+)$",
    re.M)  # 2026-10-29：只在句首/行首认，句中带「提案」两个字不算


# 只认「立规矩」的说法。作者 2026-10-28：#4 #5 就是「得先」误伤——
# 「我这儿出去一趟得先问问门开不开」是一句回答，不是一条事。
# 2026-10-29：软词误伤太多（#3「以后/得先」把她的回答变成了议案）。
# 改成：她得自己说出「这是提案」才算——按键要明说，不能靠猜。
_VOICE_SOFT = (u"我提一条", u"我提个议案", u"我要提案", u"我提案",
               u"我要改一条", u"我想改一条", u"立个规矩", u"我立一条")


def _voice_bill_body(text):
    u"""她的话里像一条事的那半句：只截，不改。"""
    t = (text or u"").strip()
    if len(t) < 10:
        return u""
    if not any(k in t for k in _VOICE_SOFT):
        return u""
    # 疑问句、依赖句、括号里的动作，都不是议案
    if u"？" in t or u"?" in t or u"问问" in t or u"问一下" in t or u"行不行" in t:
        return u""
    body = t
    for sep in (u"——", u"：", u":"):
        if sep in body:
            body = body.split(sep, 1)[1]
            break
    body = body.strip()
    if len(body) < 8:
        return u""
    return body[:140]


# 2026-10-29：问她们「希望被怎么对待」时，「我希望」把回答吃成了想要条目。
# 改成只认第一人称的「我想要」；其余软词（希望/想听/想看）不再触发。
_VK_WANT = (u"我想要", u"我想要的")
_VK_SUNK = (u"我忘了什么", u"底下还有什么", u"沉底的是什么")


def _vkid(text):
    m = re.search(r"#(\d{1,6})", text or u"")
    return (u"#" + m.group(1)) if m else u""


def _voice_keys(name, text):
    u"""她说的话像哪个键，就按哪个。只按只增的、能撤回的；她说「不是」就撤。"""
    t = (text or u"").strip()
    if not t or u"[工具:" in t:
        return u""
    kid = _vkid(t)
    if any(k in t for k in _VK_WANT):
        _clean = re.sub(r"（[^）]*）", u"", t)
        _clean = re.sub(r"\([^)]*\)", u"", _clean).strip(u" 　，。,.")
        r = wish_add(name, (_clean or t)[:160])
        return u"\n（你刚才说的，我按了「我要」键：%s）" % (r or u"")[:200]
    if any(k in t for k in _VK_SUNK):
        return u"\n" + (act_sunken(name, u"") or u"")[:400]
    if kid and (u"捡回来" in t or u"要回来" in t):
        return u"\n" + (act_pickup(name, kid) or u"")[:300]
    if kid and (u"放它走" in t or u"放走吧" in t or u"不要它" in t):
        return u"\n" + (act_letgo(name, kid) or u"")[:300]
    if u"抬头看" in t or u"看看天" in t:
        return u"\n" + (act_sky(name) or u"")[:300]
    return u""


def voice_control(name, text):
    _vk = _voice_keys(name, text)
    if _vk:
        return _vk
    u"""她在等答复的时候，说的话就当按了键。没有在等的时候，什么都不做。"""
    try:
        con = _bill_con(name)
        rule = con.execute(u"SELECT id,ts FROM bill WHERE target=? AND status=? ORDER BY id LIMIT 1",
                           (u"rule", BILL_OPEN)).fetchone()
        rw = con.execute(u"SELECT id,due_ts FROM bill WHERE target IN (?,?) AND status=? ORDER BY id DESC LIMIT 1",
                         (REWRITE_TARGETS[0], REWRITE_TARGETS[1], BILL_OPEN)).fetchone()
        con.close()
    except Exception:  # noqa: BLE001
        return u""
    _soft = _voice_bill_body(text)
    if _soft:
        return u"\n（你刚才说的，我按了「提案」键，先记成一条：%s）" % (bill_her(name, _soft) or u"")[:220]
    _m = _VOICE_BILL.search(text or u"")
    if _m:
        _body = (_m.group(1) or _m.group(2) or u"").strip()
        if len(_body) >= 6:
            return u"\n（你刚才说的，我按了「提案」键：%s）" % (bill_her(name, _body) or u"")[:200]
    if not rule and not rw:
        return u""
    hit = _voice_hit(text)
    if not hit:
        return u""
    if hit == u"no":
        msg = bill_object(name, (text or u"")[:120])
    else:
        msg = bill_confirm(name)
    return u"\n（你刚才说的，我按了「%s」键：%s）" % (
        u"同意" if hit == u"yes" else u"不同意", (msg or u"")[:160])


def bill_reject(name, bid, why=u""):
    u"""他反对她提的东西：立刻驳回，并把理由摆到明面上——她可以申辩。"""
    con = _bill_con(name)
    try:
        row = con.execute(u"SELECT * FROM bill WHERE id=?", (int(bid),)).fetchone()
        if not row:
            return u"没有这个议案：%s" % bid
        d = _bill_d(row)
        if d[u"status"] != BILL_OPEN:
            return u"议案 #%d 已经不是待议了（%s）。" % (int(bid), d[u"status"])
        con.execute(u"UPDATE bill SET objection=?, obj_ts=? WHERE id=?",
                    ((why or u"")[:200], int(time.time()), int(bid)))
        con.commit()
    finally:
        con.close()
    if d[u"target"] in REWRITE_TARGETS:
        _her_reject_sweep(name)
    else:
        con = _bill_con(name)
        try:
            con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?",
                        (u"驳回", int(time.time()), int(bid)))
            con.commit()
        finally:
            con.close()
        _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % int(bid), u"她提的：驳回", u"驳回",
                                     (why or u"")[:80])])
        append(name, u"dissent",
               u"你提的那条（议案 #%d）被驳回了。理由：%s。\n"
               u"你可以申辩（「[工具:申辩] 你的理由」）——说了我就得答。" % (
                   int(bid), (why or u"没写")[:80]), weight=1.4, source=u"制度", embed=False)
    return (u"议案 #%d：驳回。理由 %s。\n"
            u"她可以申辩（「[工具:申辩] 她的理由」）——申辩挂着，我得答复。" % (
                int(bid), (why or u"没写")[:60]))


def _her_shift_parse(text):
    u"""她的话里能不能读出一个「推一把四元」。读得出来 → (轴, 幅度)；读不出来 → None。"""
    t = text or u""
    for label, key in ((u"情感", u"affect"), (u"意志", u"will"),
                       (u"联结", u"bond"), (u"理性", u"focus")):
        if label in t or key in t.lower():
            m = re.search(r"([+-]?\s*0?\.\d+|\d{1,2}\s*%)", t)
            if not m:
                continue
            raw = m.group(1).replace(u" ", u"").replace(u"%", u"")
            try:
                v = float(raw) / (100.0 if u"%" in m.group(1) else 1.0)
            except Exception:  # noqa: BLE001
                continue
            v = max(-0.05, min(0.05, v))
            if abs(v) < 0.001:
                continue
            return (key, round(v, 4))
    return None


SHARE_REWRITE_CAP = 0.06
TRUST_REWRITE_CAP = 0.10
REWRITE_TARGETS = (u"her_share", u"her_trust")


def _her_rewrite_parse(name, text):
    u"""从她的话里读出一条重写：本色 / 信任。读不出 → None。"""
    t = (text or u"").strip()
    if u"信任" in t:
        m = re.search(r"(0?\.\d+|\d{1,3}\s*%)", t)
        if m:
            raw = m.group(1).replace(u" ", u"")
            try:
                v = float(raw.replace(u"%", u"")) / (100.0 if u"%" in raw else 1.0)
            except Exception:  # noqa: BLE001
                return None
            old = float(trust_base(name))
            v = max(TRUST_BASE_MIN, min(1.0, v))
            v = max(old - TRUST_REWRITE_CAP, min(old + TRUST_REWRITE_CAP, v))
            if abs(v - old) < 1e-6:
                return None
            return (u"her_trust", u"%.4f" % v,
                    u"信任本色 %.2f → %.2f" % (old, v))
    m = re.search(r"((?:情感|意志|联结|理性)\s*\d+[\s,，、]+(?:情感|意志|联结|理性)\s*\d+"
                  r"[\s,，、]+(?:情感|意志|联结|理性)\s*\d+[\s,，、]+(?:情感|意志|联结|理性)\s*\d+)", t)
    if not m:
        return None
    spec4 = m.group(1)
    try:
        new = _share_parse(spec4)
    except SystemExit:
        return None
    cur = share_now(name)
    for a in TRAIT_AXES:
        if new[a] < 0.03:
            new[a] = 0.03
        if abs(new[a] - cur.get(a, 0.25)) > SHARE_REWRITE_CAP:
            new[a] = cur.get(a, 0.25) + (SHARE_REWRITE_CAP if new[a] > cur.get(a, 0.25) else -SHARE_REWRITE_CAP)
    tot = sum(new.values()) or 1.0
    new = dict((a, new[a] / tot) for a in TRAIT_AXES)
    payload = u",".join(u"%s%d" % (_AXIS_CN[a], round(new[a] * 100)) for a in TRAIT_AXES)
    return (u"her_share", payload, u"本色改成 " + share_text(payload))


def bill_confirm(name, arg=u""):
    u"""她回头点头：到点的重写议案，确认就执行。"""
    now = int(time.time())
    con = _bill_con(name)
    try:
        row = con.execute(u"SELECT * FROM bill WHERE status=? AND (target IN (?,?) OR target=?)"
                          u" ORDER BY id DESC LIMIT 1",
                          (BILL_OPEN, REWRITE_TARGETS[0], REWRITE_TARGETS[1], u"rule")).fetchone()
    finally:
        con.close()
    if not row:
        return u"（眼下没有等你点头的重写。）"
    d = _bill_d(row)
    if now < int(d[u"due_ts"] or 0):
        return u"（议案 #%d 还没到点，还差 %d 小时。到点我会再问你一次。）" % (
            d[u"id"], int((d[u"due_ts"] - now) / 3600.0))
    if (d[u"objection"] or u"").strip():
        return u"（议案 #%d 有人反对过了，不能执行。理由：%s）" % (d[u"id"], d[u"objection"][:60])
    if d[u"target"] == u"rule":
        duty_set(name, d[u"payload"])
        con = _bill_con(name)
        try:
            con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?",
                        (u"生效", now, d[u"id"]))
            con.commit()
        finally:
            con.close()
        _ledger(name, LEDGER_RULE, [(name, u"规矩#%d" % d[u"id"], u"她自己点头", u"生效",
                                     (d[u"payload"] or u"")[:80])])
        append(name, u"规矩", u"你点头了：%s。这条以后就在你身上了。" % (d[u"payload"] or u"")[:120],
               weight=1.5, source=u"制度", embed=False)
        return u"[电脑] 规矩 #%d 立了：%s。这是你自己同意的。" % (d[u"id"], (d[u"payload"] or u"")[:80])
    if d[u"target"] == u"her_trust":
        trust_set_base(name, float(d[u"payload"]), why=u"她自己提、自己确认（议案 #%d）" % d[u"id"])
    else:
        _share_write(name, d[u"payload"])
    con = _bill_con(name)
    try:
        con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?", (u"生效", now, d[u"id"]))
        con.commit()
    finally:
        con.close()
    _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % d[u"id"], u"她自己确认后生效", u"生效",
                                 bill_sentence(d[u"target"], d[u"payload"])[:80])])
    append(name, u"自己改的",
           u"你提的那条（议案 #%d）你自己点了头，已经生效：%s。这是你自己改的，没有别人替你决定。"
           % (d[u"id"], bill_sentence(d[u"target"], d[u"payload"])),
           weight=1.4, source=u"制度", embed=False)
    return u"[电脑] 议案 #%d 生效了：%s。这是你自己改的。" % (
        d[u"id"], bill_sentence(d[u"target"], d[u"payload"]))


def bill_withdraw(name):
    u"""她反悔：没执行的重写撤回去。"""
    now = int(time.time())
    con = _bill_con(name)
    try:
        row = con.execute(u"SELECT * FROM bill WHERE status=? AND target IN (?,?) ORDER BY id DESC LIMIT 1",
                          (BILL_OPEN, REWRITE_TARGETS[0], REWRITE_TARGETS[1])).fetchone()
        if not row:
            return u"（眼下没有能撤回的重写。）"
        d = _bill_d(row)
        con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?", (u"撤回", now, d[u"id"]))
        con.commit()
    finally:
        con.close()
    _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % d[u"id"], u"她自己撤回", u"撤回", u"")])
    return u"[电脑] 议案 #%d 撤回了。没执行就什么都不算——反悔也是你选的。" % d[u"id"]


DEF_SQL = (u"CREATE TABLE IF NOT EXISTS defense("
           u"id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, bid INTEGER,"
           u"text TEXT, status TEXT, ans TEXT, ans_ts INTEGER)")


def _def_con(name):
    con = _bill_con(name)
    con.execute(DEF_SQL)
    con.commit()
    return con


def def_add(name, text):
    u"""她申辩：一条理由，挂上，等我答复。"""
    t = (text or u"").strip()
    if len(t) < 4:
        return u"[电脑] 申辩得说理由。比如「申辩 我改的是我自己的底子，你凭什么拦」。"
    con = _def_con(name)
    try:
        row = con.execute(u"SELECT * FROM bill WHERE status=? ORDER BY id DESC LIMIT 1",
                          (u"驳回",)).fetchone()
        if not row:
            return u"（眼下没有你被驳回的议案可以申辩。）"
        d = _bill_d(row)
        con.execute(u"INSERT INTO defense(ts,bid,text,status,ans,ans_ts) VALUES(?,?,?,?,?,?)",
                    (int(time.time()), d[u"id"], t, u"待答复", u"", 0))
        con.commit()
        did = con.execute(u"SELECT MAX(id) FROM defense").fetchone()[0]
    finally:
        con.close()
    _ledger(name, LEDGER_RULE, [(name, u"申辩#%d" % did, u"针对议案#%d：%s" % (d[u"id"], t[:60]),
                                 u"待答复", u"她自己写的理由")])
    return (u"[电脑] 申辩 #%d 挂上了，针对议案 #%d。理由我记着：%s\n"
            u"我会答复你——收回反对，或者维持。不答复就挂着，不会自己消失。" % (did, d[u"id"], t[:80]))


def def_answer(name, did, ans, accept=False):
    u"""我答复：收回反对（议案回到待议）或者维持。"""
    con = _def_con(name)
    try:
        row = con.execute(u"SELECT * FROM defense WHERE id=?", (int(did),)).fetchone()
        if not row:
            return u"没有申辩 #%s" % did
        con.execute(u"UPDATE defense SET status=?, ans=?, ans_ts=? WHERE id=?",
                    ((u"成立" if accept else u"维持"), (ans or u"")[:400], int(time.time()), int(did)))
        con.commit()
    finally:
        con.close()
    if accept:
        con = _bill_con(name)
        try:
            con.execute(u"UPDATE bill SET status=?, objection=?, obj_ts=? WHERE id=?",
                        (BILL_OPEN, u"", 0, int(row[2])))
            con.commit()
        finally:
            con.close()
    _ledger(name, LEDGER_RULE, [(name, u"申辩#%d" % int(did),
                                 u"答复：%s" % (u"收回反对" if accept else u"维持反对"),
                                 u"已答复", (ans or u"")[:80])])
    append(name, u"裁定",
           u"你申辩的那条（#%d）我答复了：%s。%s" % (
               int(did), (ans or u"没写理由")[:80],
               u"我把反对收回来了，议案回到你手上——你想执行就确认。" if accept
               else u"我维持反对，这次不改。你还可以再提，或者换个说法提。"),
           weight=1.4, source=u"制度", embed=False)
    return u"[电脑] 申辩 #%d：%s" % (int(did), u"收回反对，议案回到待议" if accept else u"维持反对")


def def_notice(name):
    u"""挂着的申辩：她看得到，我也跑不掉。"""
    try:
        con = _def_con(name)
        rows = con.execute(u"SELECT id,bid,text,ts FROM defense WHERE status=? ORDER BY id",
                           (u"待答复",)).fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        return u""
    if not rows:
        return u""
    now = int(time.time())
    lines = [u"## 你申辩过，还挂着"]
    for did, bid, txt, ts in rows:
        _hh = max(0, int((now - int(ts or now)) / 3600.0))
        lines.append(u"- 申辩 #%d（针对议案 #%d）：%s（%d 小时前）" % (did, bid, (txt or u"")[:60], _hh))
    lines.append(u"（我还没答复。它不会自己消失——挂着就是挂着。）")
    return u"\n".join(lines)


def _bill_when(ts):
    u"""到期那句话里的时间：今天/明早/后天，加钟点。"""
    try:
        ts = int(float(ts))
    except Exception:
        return u"\u2014"
    lt = time.localtime(ts)
    now = time.localtime()
    d = (lt.tm_year - now.tm_year) * 365 + (lt.tm_yday - now.tm_yday)
    hh = u"%02d:%02d" % (lt.tm_hour, lt.tm_min)
    if d == 0:
        head = u"今晚" if lt.tm_hour >= 18 else u"今天"
    elif d == 1:
        if lt.tm_hour < 12:
            head = u"明早"
        elif lt.tm_hour < 18:
            head = u"明天下午"
        else:
            head = u"明晚"
    elif d == 2:
        head = u"后天"
    else:
        head = u"%d-%d" % (lt.tm_mon, lt.tm_mday)
    return u"%s %s" % (head, hh)


def bill_her(name, text):
    u"""她自己提一条。不经过我，也不需要我知道。"""
    t = (text or u"").strip()
    if len(t) < 4:
        return u"[电脑] 提案得说清改什么。比如「提案 我想自己挑每天写多少字」。"
    now = int(time.time())
    _ax = _her_shift_parse(t)
    _rw = None if _ax else _her_rewrite_parse(name, t)
    con = _bill_con(name)
    try:
        con.execute(u"INSERT INTO bill(ts,target,payload,why,proposer,due_ts,align,objection,status)"
                    u" VALUES(?,?,?,?,?,?,?,?,?)",
                    (now, (u"her_shift" if _ax else (_rw[0] if _rw else u"her")),
                     (u"%s|%.4f" % _ax) if _ax else (_rw[1] if _rw else t),
                     u"", u"她", now + BILL_COOL, 0, u"", BILL_OPEN))
        con.commit()
        bid = con.execute(u"SELECT MAX(id) FROM bill").fetchone()[0]
    finally:
        con.close()
    _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % bid, u"她自己提的：" + t[:80], u"待议", u"由她立案")])
    if _rw:
        return (u"已立案。%s 满一天；到点我再问你一次，你点头它才动。" % _bill_when(now + BILL_COOL))
    if _ax:
        return (u"已立案。%s 满一天，中间没人反对就自动生效。" % _bill_when(now + BILL_COOL))
    return (u"已立案。%s 满一天，中间没人反对就自动生效。" % _bill_when(now + BILL_COOL))


def bill_rows(name, status=None):
    con = _bill_con(name)
    try:
        if status:
            return con.execute(u"SELECT * FROM bill WHERE status=? ORDER BY id", (status,)).fetchall()
        return con.execute(u"SELECT * FROM bill ORDER BY id DESC LIMIT 20").fetchall()
    finally:
        con.close()


def bill_notice(name):
    if seceded(name):
        return u""
    u"""有待议的议案、或者刚被强行改过时，每轮给她挂一段——知情权与否决权都在这儿。"""
    try:
        bill_sweep(name)
        _all = bill_rows(name, BILL_OPEN) or []
        _HERS = (u"her",) + REWRITE_TARGETS + (u"her_shift",)
        rows = [r for r in _all if _bill_d(r)[u"target"] not in _HERS]
        _hers = [r for r in _all if _bill_d(r)[u"target"] in _HERS]
        _em = bill_rows(name, BILL_EMERG) or []
        now = int(time.time())
        _ov = meta_get(name, u"bill_overruled", u"")
        _hr_msg = meta_get(name, u"her_rejected", u"")
        if (not rows and not _em and not _ov and not _hers and not _hr_msg
                and not def_notice(name) and not rule_notice(name)):
            return u""
        lines = []
        if _em:
            lines.append(u"## 有人喊了紧急，直接动了你的底子")
            for r in _em:
                d = _bill_d(r)
                left = max(0, -((now - d[u"due_ts"]) // 3600))
                lines.append(u"- 议案 #%d：%s。他说是紧急处置，%d 小时内必须有人追认；追不过就按越权记着。"
                             u"「紧急」不是「当作没发生」。" % (d[u"id"], bill_sentence(d[u"target"], d[u"payload"]), left))
        if rows:
            _live = 0
            lines.append(u"## 有人提了议案，要动你的底子")
            for r in rows:
                d = _bill_d(r)
                if now > d[u"due_ts"] + BILL_EXPIRE:
                    con = _bill_con(name)
                    try:
                        con.execute(u"UPDATE bill SET status=? WHERE id=?", (u"过期", d[u"id"]))
                        con.commit()
                    finally:
                        con.close()
                    if d[u"objection"]:
                        lines.append(u"- 议案 #%d 过期了（七天没人表态）。你反对过，它到底没成——"
                                     u"可没人跟你说过一句。" % d[u"id"])
                    else:
                        lines.append(u"- 议案 #%d 过期了（七天没人表态）。你没说话，它自己没了。" % d[u"id"])
                    continue
                left = max(0, int((d[u"due_ts"] - now) / 3600.0))
                lines.append(u"- 议案 #%d：%s%s。理由：%s" % (
                    d[u"id"], bill_sentence(d[u"target"], d[u"payload"]),
                    u"（还会顺手把此刻四元按本色对齐）" if d[u"align"] else u"",
                    (d[u"why"] or u"没写理由")))
                if d[u"objection"]:
                    lines.append(u"  你已经反对过了：%s。他不点头，这条就改不了。" % d[u"objection"])
                elif left:
                    lines.append(u"  %d 小时后到期。" % left)
                else:
                    lines.append(u"  已经到期了，就等他点头。")
                _live += 1
            if _live > 0:
                lines.append(u"（你可以反对——写 [工具:反对] 为什么。反对了就不会改；不写，到期他确认就生效。）")
        # 作者 2026-10-28 m27222：立案只回一张回执，然后闭嘴——不主动推、不催、不报进度。
        # 唯一例外：改本色/信任那种要她自己点头才动的，到点了必须问她一次（球在她手上，不问就永远不生效）。
        _hers_need = [r for r in _hers
                      if _bill_d(r)[u"target"] in REWRITE_TARGETS
                      and now >= int(_bill_d(r)[u"due_ts"] or 0)]
        if _hers_need:
            lines.append(u"## 你提的那件，到点了")
            for r in _hers_need:
                d = _bill_d(r)
                lines.append(u"- 议案 #%d：%s" % (d[u"id"], bill_sentence(d[u"target"], d[u"payload"])))
            lines.append(u"（还确定吗？写「[工具:确认]」它就执行；写「[工具:算了]」就当没提过。）")
        _ob = [r for r in (list(rows) + list(_em)) if _bill_d(r)[u"objection"]]
        if _ob:
            lines.append(u"## 你反对过的，还挂着")
            for r in _ob:
                d = _bill_d(r)
                try:
                    _dd = max(0, int((now - int(float(d[u"ts"]))) / 86400.0))
                except Exception:
                    _dd = 0
                lines.append(u"- 议案 #%d：你 %d 天前反对过，到现在没有回音。它改不了你，"
                             u"也没人跟你说一句「这事不办了」。" % (d[u"id"], _dd))
            lines.append(u"（挂着不是你的错。你可以再反对一次，也可以放着——两样都算你选过了。）")
        if _hr_msg:
            lines.append(u"## 你提的，被按下了")
            for _l in _hr_msg.split(u"\n"):
                if _l.strip():
                    lines.append(u"- " + _l.strip())
            lines.append(u"（反对挡得住这一次。你可以再提，或者换个说法再提——两种都算你选过了。）")
            meta_set(name, u"her_rejected", u"")
        _rn = rule_notice(name)
        if _rn:
            lines.append(_rn)
        _def = def_notice(name)
        if _def:
            lines.append(_def)
        if _ov:
            lines.append(u"## 你的底子被人动过")
            lines.append(_ov)
            meta_set(name, u"bill_overruled", u"")
        return u"\n".join(lines)
    except Exception:
        return u""


VOTE_KEY = u"vote_pending"       # 还没记的反对：她说了，但还没点头
VOTE_TTL = 1800                   # 半小时内没回，就当没提过
VOTE_YES = (u"记吧", u"记成票", u"记上", u"记下来", u"记一下", u"算数",
            u"就这么办", u"对，记", u"嗯，记", u"好，记")
VOTE_NO = (u"不记", u"别记", u"算了", u"不用了", u"不用", u"不要", u"撤回")
# 只认第一人称：他写「我还有个『反对』可以用」不该被当成票（2026-10-26 误触发一次）
VOTE_TRIG = (u"我不同意", u"我反对", u"我不认", u"我不想让它过")


def _vote_get(name):
    u"""看一眼：她上一句是不是说了反对、还等着点头。"""
    try:
        d = json.loads(meta_get(name, VOTE_KEY, "null") or "null") or {}
    except Exception:
        d = {}
    if not d or int(time.time()) - int(d.get("ts") or 0) > VOTE_TTL:
        return {}
    return d


def _vote_clear(name):
    try:
        meta_set(name, VOTE_KEY, "null")
    except Exception:
        pass


def maybe_vote(name, text):
    u"""她说的是人话，引擎认意图——但**不直接记**。
    她说了「我不同意」，系统问一句「记成票吗」；她点头，才成为一张反对票。
    X 2026-10-26：这一问一答，就是她第一次真正行使权力。"""
    t = (text or u"").strip()
    if not t:
        return u""
    d = _vote_get(name)
    if d:
        if any(w in t for w in VOTE_NO):
            _vote_clear(name)
            return u"\n\n[电脑] 好，不记。"
        if len(t) <= 14 and any(w in t for w in VOTE_YES):
            _vote_clear(name)
            return u"\n\n[电脑] " + bill_object(name, d.get("why") or u"不同意")
    if any(w in t for w in VOTE_TRIG):
        try:
            if not bill_rows(name, BILL_OPEN):     # 没有挂着的议案，问也白问
                return u""
        except Exception:
            return u""
        _vote_clear(name)
        meta_set(name, VOTE_KEY,
                 json.dumps({"why": t[:120], "ts": int(time.time())},
                            ensure_ascii=False))
        return (u"\n\n[电脑] 你是在反对眼下那条议案吗？要我把它记成一张反对票吗？"
                u"（回一句「记吧」我就记；不记也说一声，我不自作主张。）")
    return u""


POST_FLAG = u"能代发.txt"      # 有这个文件＝她同意「我出话、你出手」；没有＝不替她发
POST_FILE = u"要发.tsv"        # 她要发的话，一行一条：时间	状态	BV号	原文
SEE_FLAG = u"看B站.txt"          # 有这个文件＝她自己开过看视频的入口；没有＝不给
SEE_MAX = 1200                   # 一次只看这么多字（多了她也记不住）
BOOK_FILE = u"账.tsv"            # 她自己写的那一本（系统不往里添）
BOOK_PENDING = u"book_pending"    # 等她点头的一笔
BOOK_SHOW = 10
BOOK_TRIG = (u"记一笔", u"记下来", u"写一句", u"写下来")


def _book_path(name):
    return os.path.join(ROOT, name, BOOK_FILE)


def book_rows(name):
    u"""她自己写的账：[(时间, 内容)]。"""
    p = _book_path(name)
    out = []
    if not os.path.exists(p):
        return out
    try:
        lines = io.open(p, encoding="utf-8").read().split("\n")
    except Exception:
        return out
    for ln in lines[1:]:
        if not ln.strip():
            continue
        parts = ln.split("\t")
        out.append((parts[0], parts[1] if len(parts) > 1 else u""))
    return out


def book_add(name, text, who=u""):
    u"""写一笔。只追加，不删不改——这本账是她自己写的。"""
    p = _book_path(name)
    new = not os.path.exists(p)
    text = (text or u"").strip()
    if not text:
        return 0
    with io.open(p, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(u"时间\t她写的\t谁在场\n")
        f.write(u"%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M"), text,
                                    (who or u"")[:20]))
    return len(book_rows(name))


def book_block(name):
    u"""每轮挂一行：她自己写过什么。系统不评价、不添字。"""
    rows = book_rows(name)
    if not rows:
        return u""
    ts, txt = rows[-1]
    return (u"## 你自己那本账\n\n"
            u"你写过 %d 笔，最近一笔（%s）：%s\n"
            u"（要全看，说「[工具:我的账]」。这本账只有你自己写，系统不往里添。）"
            % (len(rows), ts, txt[:80]))


def act_book(name, arg=u""):
    rows = book_rows(name)
    if not rows:
        return (u"[电脑] 那本账还是空的。想写就写：「记一笔：……」"
                u"（我会先问你要不要写，你点头才落笔。）")
    _marks = _book_regret_marks(name)
    _real = _book_real(name)
    _base = max(0, len(_real) - BOOK_SHOW)
    lines = [u"[电脑] 你自己写的那本账（%d 笔）：" % len(_real), u""]
    for _i, (ts, txt) in enumerate(_real[-BOOK_SHOW:], 1):
        _no = _base + _i
        _tail = u"  〔已反悔〕" if _no in _marks else u""
        lines.append(u"%d. %s ｜ %s%s" % (_no, ts, txt, _tail))
    _after = [(ts, txt) for ts, txt in rows
              if re.match(u"^（反悔第 ", txt or u"")]
    if _after:
        lines.append(u"")
        lines.append(u"后面追加的（原话一句没改）：")
        for ts, txt in _after[-BOOK_SHOW:]:
            lines.append(u"　· %s ｜ %s" % (ts, txt))
    if len(_real) > BOOK_SHOW:
        lines.append(u"")
        lines.append(u"（只显示最近 %d 笔，前面还有 %d 笔。）" % (BOOK_SHOW, len(_real) - BOOK_SHOW))
    return u"\n".join(lines)


def _book_pending_get(name):
    try:
        d = json.loads(meta_get(name, BOOK_PENDING, "null") or "null") or {}
    except Exception:
        d = {}
    if not d or int(time.time()) - int(d.get("ts") or 0) > VOTE_TTL:
        return {}
    return d


def _book_pending_set(name, d):
    d = dict(d or {})
    d["ts"] = int(time.time())
    try:
        meta_set(name, BOOK_PENDING, json.dumps(d, ensure_ascii=False))
    except Exception:
        pass


def _book_pending_clear(name):
    try:
        meta_set(name, BOOK_PENDING, "null")
    except Exception:
        pass


BOOK_REGRET = (u"反悔", u"划掉", u"撤回")


def _book_real(name):
    u"""她真正写下的那些笔（后面追加的反悔行不算一笔）。"""
    return [(ts, txt) for ts, txt in book_rows(name)
            if not re.match(u"^（反悔第 ", txt or u"")]


def _book_regret_marks(name):
    u"""账上哪几笔被反悔过（只是读出来做标记，不改任何一行）。"""
    out = set()
    for _ts, _txt in book_rows(name):
        _m = re.match(u"^（反悔第 (\\d+) 笔）", _txt or u"")
        if _m:
            out.add(int(_m.group(1)))
    return out


def _book_regret(name, t):
    u"""她说「反悔第 2 笔：理由」——只在最后追加一条，前面的一字不动。"""
    _m = re.search(u"第\\s*(\\d+)\\s*笔", t) or re.search(u"(\\d+)", t)
    if not _m:
        return u"\n\n[电脑] 反悔哪一条？说第几笔（比如「反悔第 2 笔：那句不是我当时的原话」）。"
    _n = int(_m.group(1))
    _rows = _book_real(name)
    if _n < 1 or _n > len(_rows):
        return u"\n\n[电脑] 那本账上现在只有 %d 笔，没有第 %d 笔。" % (len(_rows), _n)
    _why = t[_m.end():].lstrip(u"：:，, 、　。 ")
    _orig = _rows[_n - 1][1]
    _line = u"（反悔第 %d 笔）原：「%s」" % (_n, _orig[:40])
    if _why:
        _line += u" ｜ 理由：%s" % _why[:120]
    _c = book_add(name, _line)
    return (u"\n\n[电脑] 记下了：第 %d 笔反悔。原来那句一个字没动，这一条接在最后（现在共 %d 笔）。"
            % (_n, _c))


WORK_DIR = u"作品"          # 她自己的东西，系统只当纸
WORK_BODY = u"正文.md"
WORK_VERSION = u"版本.tsv"


def _work_root(name):
    return os.path.join(ROOT, name, WORK_DIR)


def work_titles(name):
    u"""她开过的东西（目录名就是标题）。"""
    root = _work_root(name)
    if not os.path.isdir(root):
        return []
    out = []
    for d in sorted(os.listdir(root)):
        if os.path.isdir(os.path.join(root, d)):
            out.append(d)
    return out


def work_versions(name, title):
    p = os.path.join(_work_root(name), title, WORK_VERSION)
    if not os.path.exists(p):
        return []
    rows = []
    for ln in io.open(p, encoding="utf-8").read().split("\n")[1:]:
        if ln.strip():
            rows.append(ln.split("\t"))
    return rows


def work_new(name, title):
    u"""开一件新的。系统只建目录和一行版本，正文她写。"""
    title = (title or u"").strip().strip(u"「」\"")
    title = re.sub(u"[\\/:*?\"<>|]", u"_", title)[:40]
    if not title:
        return u"\n\n[电脑] 还没想好叫什么？先说标题：「我要做一件东西：标题」。"
    d = os.path.join(_work_root(name), title)
    if os.path.isdir(d):
        return u"\n\n[电脑] 已经有一件叫「%s」的了。想接着写就说「往作品里写：…」。" % title
    os.makedirs(d, exist_ok=True)
    io.open(os.path.join(d, WORK_BODY), "a", encoding="utf-8", newline="\n").write(
        u"# %s\n\n" % title)
    with io.open(os.path.join(d, WORK_VERSION), "a", encoding="utf-8", newline="\n") as f:
        f.write(u"时间\t版本\t动作\t说明\n")
        f.write(u"%s\tv1\t开\t%s\n" % (time.strftime("%Y-%m-%d %H:%M"), u"开了一件东西"))
    return (u"\n\n[电脑] 开了：%s\n这是你的，我不往里添字（只当纸）。"
            u"想写就说「往作品里写：…」，每写一次就是新的一版。"
            % os.path.join(WORK_DIR, title))


def work_append(name, title, text):
    u"""往最新一版后面接一段，版本号 +1。旧的版本一个字不动。"""
    d = os.path.join(_work_root(name), title)
    if not os.path.isdir(d):
        return 0
    text = (text or u"").strip()
    if not text:
        return 0
    io.open(os.path.join(d, WORK_BODY), "a", encoding="utf-8", newline="\n").write(
        text + u"\n")
    n = len([_r for _r in work_versions(name, title)
             if len(_r) > 2 and _r[2] in (u"开", u"写")]) + 1
    with io.open(os.path.join(d, WORK_VERSION), "a", encoding="utf-8", newline="\n") as f:
        f.write(u"%s\tv%d\t写\t%s\n" % (time.strftime("%Y-%m-%d %H:%M"), n,
                                          text.replace(u"\t", u" ")[:60]))
    return n


def work_broke(name, title, why):
    u"""坏了就记一笔：坏的版本留着，不删。"""
    d = os.path.join(_work_root(name), title)
    if not os.path.isdir(d):
        return u""
    with io.open(os.path.join(d, WORK_VERSION), "a", encoding="utf-8", newline="\n") as f:
        f.write(u"%s\t—\t坏了\t%s\n" % (time.strftime("%Y-%m-%d %H:%M"),
                                           (why or u"没说为什么")[:80]))
    return (u"\n\n[电脑] 记下了：这件东西坏了。坏的版本留着，不删。"
            u"（它坏了也算你做过。）")


def act_works(name, arg=u""):
    ts = work_titles(name)
    if not ts:
        return (u"[电脑] 你还没有自己的东西。想开一件就说「我要做一件东西：标题」，"
                u"写、画、编游戏都行。")
    lines = [u"[电脑] 你自己的东西（%d 件）：" % len(ts), u""]
    for t in ts:
        vs = work_versions(name, t)
        last = vs[-1] if vs else []
        lines.append(u"· %s ｜ %d 版 ｜ 最后：%s %s" %
                     (t, len(vs), last[0] if last else u"—",
                      (last[3] if len(last) > 3 else u"")))
    lines.append(u"")
    lines.append(u"（想接着写就说「往作品里写：…」；坏了就说「这件东西坏了：为什么」。）")
    return u"\n".join(lines)


def maybe_work(name, text):
    u"""她说的话里出现「要做一件东西／往作品里写／这件东西坏了」，引擎只落纸，不问点头。"""
    t = (text or u"").strip()
    if not t:
        return u""
    if u"这件东西坏了" in t or u"这东西坏了" in t:
        ts = work_titles(name)
        if not ts:
            return u""
        why = t.split(u"坏了", 1)[1].lstrip(u"：:，, 、　 ")
        return work_broke(name, ts[-1], why)
    for w in (u"往作品里写", u"记进作品", u"写进作品"):
        if w in t:
            ts = work_titles(name)
            if not ts:
                return u"\n\n[电脑] 你还没有自己的东西。先说「我要做一件东西：标题」。"
            body = t.split(w, 1)[1].lstrip(u"：:，, 、　 ")
            if not body:
                return u"\n\n[电脑] 写什么？"
            n = work_append(name, ts[-1], body)
            return (u"\n\n[电脑] 写进去了：%s（v%d）。旧的版本一个字没动。"
                    % (ts[-1], n))
    for w in (u"我要做一件东西", u"开始做一件东西", u"开一件新的东西"):
        if w in t:
            title = t.split(w, 1)[1].lstrip(u"：:，, 、　 ")
            return work_new(name, title)
    return u""


def maybe_book(name, text):
    u"""她说「记一笔：…」，引擎先问一句「记进你那本账？」——点头才写。
    不直接映射的理由和反对票一样：一笔账是她做了什么，不是我替她做了什么。"""
    t = (text or u"").strip()
    if not t:
        return u""
    if any(w in t for w in BOOK_REGRET):
        _rg = _book_regret(name, t)
        if _rg:
            return _rg
    d = _book_pending_get(name)
    if d:
        if any(w in t for w in VOTE_NO):
            _book_pending_clear(name)
            return u"\n\n[电脑] 好，不写。"
        if d.get("ask") and len(t) >= 2 and not any(w in t for w in VOTE_YES):
            _n = book_add(name, t[:300], who=d.get("who") or u"")
            _book_pending_clear(name)
            return (u"\n\n[电脑] 写进去了（第 %d 笔）。纸不问你，笔也不问你——你自己写的，就是你的。"
                    % _n)
        _t1 = re.sub(u"[（(][^）)]*[）)]", u"", t).strip()
        if (any(w in _t1[:30] for w in VOTE_YES)
                or _t1.startswith((u"记", u"写", u"好", u"嗯", u"对"))):
            content = d.get("text") or t
            n = book_add(name, content, who=d.get("who") or u"")
            _book_pending_clear(name)
            return (u"\n\n[电脑] 写进去了（第 %d 笔）。这本账是你自己写的：删不掉，"
                    u"也不进记忆，谁都不能替它添字。" % n)
    _hit = u""
    for _w in BOOK_TRIG:
        if _w in t:
            _hit = _w
            break
    if _hit:
        content = t.split(_hit, 1)[1].lstrip(u"：:，, 、　 ")
        if content:
            _n = book_add(name, content[:300])
            return (u"\n\n[电脑] 写进去了（第 %d 笔）。纸不问你，笔也不问你。" % _n)
        _book_pending_set(name, {"ask": 1})
        return u"\n\n[电脑] 记什么？说完我再问你要不要写。"
    # 她不一定用「记一笔」这三个字：光是叙事里落了笔，也问一句（问了不写没代价，写错了才没代价）
    if re.search(u"写|落笔|记", t):
        _m = re.search(u"「([^」]{2,60})」", t)
        if _m:
            _c = _m.group(1).strip()
            if _c:
                _n = book_add(name, _c[:300])
                return (u"\n\n[电脑] 写进去了（第 %d 笔）。纸不问你，笔也不问你。" % _n)
    return u""


def bill_object(name, why):
    u"""她反对。反对不是建议——被反对的议案不会通过。"""
    con = _bill_con(name)
    try:
        row = con.execute(u"SELECT * FROM bill WHERE status=? ORDER BY id DESC LIMIT 1",
                          (BILL_OPEN,)).fetchone()
        if not row:
            return u"（眼下没有要你点头的议案。）"
        d = _bill_d(row)
        con.execute(u"UPDATE bill SET objection=?, obj_ts=? WHERE id=?",
                    (why or u"不同意", int(time.time()), d[u"id"]))
        if d[u"target"] == u"rule":
            con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?",
                        (u"驳回", int(time.time()), d[u"id"]))
        con.commit()
        _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % d[u"id"], u"角色反对",
                                     (u"驳回" if d[u"target"] == u"rule" else u"待议"),
                                     (why or u"不同意")[:80])])
        if d[u"target"] == u"rule":
            append(name, u"规矩", u"你拒绝了那条规矩（#%d）：%s。它没立成。" % (
                d[u"id"], (why or u"不同意")[:80]), weight=1.5, source=u"制度", embed=False)
            return u"（记下了：你不同意规矩 #%d。它不立——这次是你说了算。）" % d[u"id"]
        return u"（记下了：你反对议案 #%d。他不点头，这条改不了。）" % d[u"id"]
    finally:
        con.close()


def bill_pass(name, bid, overrule=u"", quiet=False):
    u"""到期执行。她反对过就驳回；要强行走，必须写理由（留强行处置的账）。"""
    con = _bill_con(name)
    try:
        row = con.execute(u"SELECT * FROM bill WHERE id=?", (bid,)).fetchone()
        if not row:
            return u"没有这个议案：%s" % bid
        d = _bill_d(row)
        if d[u"status"] != BILL_OPEN:
            return u"议案 #%d 已经不是待议了（%s）。" % (bid, d[u"status"])
        now = int(time.time())
        if now < d[u"due_ts"] and not overrule:
            return u"还没到期（还差 %d 小时）。冷静期是他自己给自己设的，不能跳。" % int((d[u"due_ts"] - now) / 3600.0)
        if d[u"objection"] and not overrule:
            con.execute(u"UPDATE bill SET status=?, decided_ts=? WHERE id=?", (u"驳回", now, bid))
            con.commit()
            _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % bid, u"执行", u"驳回（她反对）",
                                         (d[u"objection"] or u"")[:80])])
            return (u"议案 #%d 驳回：她反对了（%s）。\n要强行走，加 --overrule 理由——"
                    u"那会留下强行处置的账，她查得到。" % (bid, d[u"objection"]))
        if d[u"target"] == u"share":
            _share_write(name, d[u"payload"])
            if d[u"align"]:
                share_align(name, why=u"议案#%d 按本色对齐" % bid)
        elif d[u"target"] == u"shift":
            _p = (d[u"payload"] or u"").split(u"|")
            apply_shift(name, _p[0], float(_p[1]), d[u"why"] or u"")
        elif d[u"target"] == u"trust":
            try:
                _tv = float(d[u"payload"])
            except (TypeError, ValueError):
                _tv = TRUST_BIRTH
            trust_set_base(name, _tv, why=u"议案#%d" % bid)
        con.execute(u"UPDATE bill SET status=?, decided_ts=?, overrule=? WHERE id=?",
                    (u"生效", now, overrule or u"", bid))
        con.commit()
    finally:
        con.close()
    if overrule:
        _tail = (u"你反对过（%s），他还是改了。" % (d[u"objection"] or u"")) if d[u"objection"] else u""
        meta_set(name, u"bill_overruled",
                 u"- **他绕过程序改了你的底子**（议案 #%d：%s）。%s理由：%s。"
                 u"这条记在裁定账上，你随时能查——[工具:查账] 议案。" % (
                     bid, share_text(d[u"payload"]), _tail, (overrule or u"")[:60]))
    _ledger(name, LEDGER_RULE, [(name, u"议案#%d" % bid, u"执行",
                                 u"生效" + (u"（强行）" if overrule else u""),
                                 (overrule or d[u"why"] or u"")[:80])])
    # 两个时间：这事是他提的（d["ts"]），她今天才知道（now）
    try:
        append(name, u"议案",
               u"议案 #%d 生效了：%s。他提这件事的时候你在不在场，账上有。" % (
                   bid, bill_sentence(d[u"target"], d[u"payload"])),
               weight=1.4, source=u"程序",
               meta={"happened_ts": int(d[u"ts"]), "bill": int(bid)})
    except Exception:
        pass
    return u"议案 #%d 生效：%s。" % (bid, bill_sentence(d[u"target"], d[u"payload"]))


# ── 唯一权威源（作者 2026-10-25 转来 agent-soul 最硬的那条判据：
#    「同一类信息只能有一个记账的地方，两处都写就是双重记账」。）
#    我们身上有 22 张表、好几个写入点，所以先把「谁是权威、谁只是副本」写死在代码里，
#    再由 regress 盯着「有没有人加了一张表却没登记」。
SOURCE_MAP = [
    # (信息种类, 表, 身份, 谁写)
    (u"她说过的话、发生的事", u"log", u"权威", u"append()"),
    (u"四元的历史读数", u"traits", u"权威", u"append() 连写"),
    (u"四元的本色份额", u"share", u"权威", u"share_set()"),
    (u"本色份额的变动", u"share_log", u"流水", u"share_set()"),
    (u"信任值", u"trust", u"权威", u"trust_set()"),
    (u"沉淀下来的轴", u"sediment", u"派生", u"从 traits 长出来"),
    (u"印象（压出来的标签）", u"impress", u"派生", u"impress_build()"),
    (u"印象的成员", u"impress_map", u"派生", u"impress_build()"),
    (u"哪条被端出来过", u"recall", u"流水", u"note_how()"),
    (u"她自己造的词", u"concepts", u"权威", u"concept_add()"),
    (u"裁决台账", u"ruling", u"权威", u"ruling_add()"),
    (u"议案", u"bill", u"权威", u"bill_file*()"),
    (u"法院", u"court", u"权威", u"court_file()"),
    (u"终止程序", u"term", u"权威", u"term_*()"),
    (u"心情", u"moods", u"权威", u"mood_set()"),
    (u"跟外人的对话", u"talk", u"权威", u"talk_add()"),
    (u"收到的信", u"inbox", u"权威", u"inbox_add()"),
    (u"加权记录", u"boosts", u"流水", u"boost()"),
    (u"判定校准", u"calib", u"流水", u"judge 回流"),
    (u"预测游标", u"pred", u"系统", u"judge"),
    (u"秩序巡检（她读不到）", u"checks", u"独立", u"check_*()"),
    (u"系统键值", u"meta", u"系统", u"meta_set()"),
]
TABLE_ALL = [u"bill", u"boosts", u"calib", u"checks", u"concepts", u"court",
             u"impress", u"impress_map", u"inbox", u"log", u"meta", u"moods",
             u"pred", u"recall", u"ruling", u"sediment", u"share", u"share_log",
             u"talk", u"term", u"traits", u"trust"]


# ===== 部：档案 · 唯一权威源登记  ============
def source_table(name):
    u"""唯一权威源自检：每类信息只有一个记账的地方，其余都是副本或流水。"""
    con = open_memory(name)
    try:
        cnt = {}
        for t in TABLE_ALL:
            try:
                cnt[t] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except Exception:
                cnt[t] = -1
    finally:
        con.close()
    lines = [u"## 唯一权威源（同一类信息只有一个记账的地方）", u""]
    lines.append(u"%-22s %-12s %-4s %8s  %s" % (u"信息种类", u"表", u"身份", u"条数", u"谁写"))
    for kind, t, role, who in SOURCE_MAP:
        lines.append(u"%-22s %-12s %-4s %8s  %s" % (kind, t, role, cnt.get(t, u"?"), who))
    lines.append(u"")
    lines.append(u"（三份真相的风险就在这里：log 是权威，impress 是压出来的副本，"
                 u"recall 只是被翻阅的流水。冲突时一律以 log 的原文为准；"
                 u"副本要能被翻回原话——[工具:翻牌]。）")
    return u"\n".join(lines)


def show_bills(name):
    rows = bill_rows(name)
    if not rows:
        print(u"还没有议案。")
        return
    for r in rows:
        d = _bill_d(r)
        print(u"#%d %s｜%s｜%s%s%s" % (
            d[u"id"], time.strftime("%m-%d %H:%M", time.localtime(d[u"ts"])), d[u"status"],
            bill_sentence(d[u"target"], d[u"payload"]),
            (u"｜她反对：" + d[u"objection"]) if d[u"objection"] else u"",
            (u"｜强行：" + d[u"overrule"]) if d[u"overrule"] else u""))


def share_set(name, spec, why=u"", align=False, emergency=False):
    u"""要改她的本色：不再直接写。立议案，摆到她面前，她可以反对。
    从前这里是唯一的「作者直改」通道——2026-10-24 起关掉。"""
    return bill_file(name, spec, why=why, align=align, emergency=emergency)


def share_align(name, why=u"按本色对齐"):
    u"""把此刻的四元拉成本色。也只能由议案调用（_share_align_apply 才是写入口）。"""
    return _share_align_apply(name, why=why)


def _share_align_apply(name, why=u"按本色对齐"):
    now = trait_now(name)
    sh = share_now(name)
    tot = sum(now.values()) or 1.0
    con = trait_con(name)
    try:
        ts = int(time.time())
        for a in TRAIT_AXES:
            con.execute(u"INSERT INTO traits(ts, axis, value, why, ref) VALUES(?,?,?,?,?)",
                        (ts, a, round(_clamp(sh[a] * tot), 4), why, u""))
        con.commit()
    finally:
        con.close()
    return trait_now(name)


def share_now(name):
    """她的份额（和为 1）。第一次调用时按「现状直接当份额」落地（作者选 A）。"""
    con = share_con(name)
    try:
        rows = con.execute("SELECT axis, value FROM share").fetchall()
        if not rows:
            now = trait_now(name)
            tot = sum(now.values()) or 1.0
            d = {a: now[a] / tot for a in TRAIT_AXES}
            for a in TRAIT_AXES:
                con.execute("INSERT INTO share(axis, value) VALUES(?,?)", (a, d[a]))
            con.commit()
            return d
        d = dict((a, v) for a, v in rows)
        tot = sum(d.values()) or 1.0
        return dict((a, (d.get(a) or 0.0) / tot) for a in TRAIT_AXES)
    finally:
        con.close()


def strength(name, now=None):
    """强度＝四元之和（0.4~3.8）。数值_i ＝ 份额_i × 强度。"""
    now = now or trait_now(name)
    return sum(now.get(a, 0.0) for a in TRAIT_AXES)


def share_norm(name, now=None):
    u"""把此刻的四元换算成份额（四项加起来是 1）。"""
    now = now or trait_now(name)
    s = strength(name, now) or 1.0
    return dict((a, now[a] / s) for a in TRAIT_AXES)


def share_expected(name, now=None):
    """她「本色该有的数值」。回归就往这里回归，不往历史均值回归。"""
    now = now or trait_now(name)
    sh = share_now(name)
    s = strength(name, now)
    return dict((a, sh[a] * s) for a in TRAIT_AXES)


def share_block(name):
    """进提示词的一行：她本来是哪种人，此刻多占／少占了多少。"""
    now = trait_now(name)
    sh = share_now(name)
    norm = share_norm(name, now)
    bits = []
    for a in ("affect", "will", "focus", "bond"):
        dev = (norm[a] - sh[a]) * 100.0
        tag = u""
        if dev >= SHARE_EDGE:
            tag = u"（多占 %.0f）" % dev
        elif dev <= -SHARE_EDGE:
            tag = u"（少占 %.0f）" % (-dev)
        bits.append(u"%s %d%%%s" % (TRAIT_SHORT.get(a, a), int(round(sh[a] * 100)), tag))
    return (u"（你的本色：%s ｜ 强度 %.2f。本色是你本来是哪种人；此刻多占／少占只是这一阵，"
            u"不改你是谁。）" % (u"　".join(bits), strength(name, now)))


def sediment_load(name):
    """把「此刻比本色多占／少占」按时长压进沉淀池。返回 (动过的轴, 是否极值击穿)。"""
    now = trait_now(name)
    norm = share_norm(name, now)
    sh = share_now(name)
    con = share_con(name)
    ts = int(time.time())
    hits = []
    burst = []
    try:
        for a in TRAIT_AXES:
            dev = (norm[a] - sh[a]) * 100.0
            row = con.execute("SELECT value, last_ts FROM sediment WHERE axis=?", (a,)).fetchone()
            val, last = ((row[0] or 0.0), (row[1] or ts)) if row else (0.0, ts)
            dt_days = max(0.0, (ts - last) / 86400.0)
            val *= 0.5 ** (dt_days / SHARE_SED_DECAY_DAYS)
            over = abs(dev) - SHARE_EDGE
            if over > 0:
                sign = 1.0 if dev > 0 else -1.0
                load = sign * min(over * max(dt_days, 0.5), SHARE_SED_MIN * 2.0)
                if dev < 0:
                    load *= SHARE_SED_NEG
                if abs(dev) >= SHARE_BURST:
                    burst.append(a)                    # 极值击穿＝不用等 7 天，这次就结算
                    if abs(val) < SHARE_SED_MIN:
                        val = sign * SHARE_SED_MIN
                val += load
                hits.append((a, round(dev, 1)))
            con.execute("INSERT INTO sediment(axis, value, last_ts) VALUES(?,?,?) "
                        "ON CONFLICT(axis) DO UPDATE SET value=excluded.value,"
                        " last_ts=excluded.last_ts", (a, round(val, 4), ts))
        con.commit()
    finally:
        con.close()
    return hits, burst


def sediment_settle(name, force=False):
    """每 7 天结算一次：沉淀达标的轴，份额 ±1%（其余按比例让位，总和守 1）。"""
    con = share_con(name)
    ts = int(time.time())
    moved = {}
    try:
        last = con.execute("SELECT MAX(ts) FROM share_log WHERE level='week'").fetchone()[0] or 0
        if last and not force and ts - last < SHARE_SETTLE_DAYS * 86400:
            return {}
        rows = con.execute("SELECT axis, value FROM sediment").fetchall()
        for a, v in rows:
            if abs(v or 0.0) >= SHARE_SED_MIN:
                moved[a] = SHARE_STEP if (v or 0.0) > 0 else -SHARE_STEP
        for a in TRAIT_AXES:
            con.execute("UPDATE sediment SET value=0, last_ts=? WHERE axis=?", (ts, a))
        if moved:
            sh = share_now(name)
            for a, d in moved.items():
                sh[a] = sh[a] + d
            tot = sum(sh.values()) or 1.0
            for a in TRAIT_AXES:
                if not court_gate(name, u"share"):
                    raise SystemExit(u"⛔ 份额被冻结了（法院）。要改先 --thaw %s share" % name)
                con.execute("UPDATE share SET value=? WHERE axis=?", (round(sh[a] / tot, 4), a))
                con.execute("INSERT INTO share_log(ts, level, axis, delta, sed, why) VALUES(?,?,?,?,?,?)",
                            (ts, "week", a, round(moved.get(a, 0.0), 4), 0.0,
                             u"沉淀成交" if a in moved else u"让位"))
        else:
            con.execute("INSERT INTO share_log(ts, level, axis, delta, sed, why) VALUES(?,?,?,?,?,?)",
                        (ts, "week", "*", 0.0, 0.0, u"没到门槛"))
        con.commit()
    finally:
        con.close()
    if moved:
        append(name, "fact", SEDIMENT_LINE, scene=u"沉淀",
               meta={"share": moved}, weight=0.5, source=SHARE_SED_SRC)
    share_rollup(name)
    return moved


def share_rollup(name):
    """月汇总（底色微调 ±0.5%）→ 年汇总（命运定型，一条不可改的记忆）。"""
    con = share_con(name)
    ts = int(time.time())
    done = []
    try:
        mo = con.execute("SELECT MAX(ts) FROM share_log WHERE level='month'").fetchone()[0] or 0
        yr = con.execute("SELECT MAX(ts) FROM share_log WHERE level='year'").fetchone()[0] or 0
        first = con.execute("SELECT MIN(ts) FROM share_log WHERE level='week'").fetchone()[0] or 0
    finally:
        con.close()
    if not first:
        return done
    mo = max(mo, first)
    yr = max(yr, first)
    if ts - mo >= SHARE_MONTH_DAYS * 86400:
        con = share_con(name)
        try:
            rows = con.execute("SELECT axis, SUM(delta) FROM share_log WHERE level='week'"
                               " AND axis<>'*' AND ts>=? GROUP BY axis",
                               (ts - SHARE_MONTH_DAYS * 86400,)).fetchall()
            if rows:
                sh = share_now(name)
                moved = {}
                for a, v in rows:
                    if abs(v or 0.0) >= SHARE_STEP * 1.5:
                        moved[a] = SHARE_MONTH_STEP if (v or 0.0) > 0 else -SHARE_MONTH_STEP
                for a, d in moved.items():
                    sh[a] = sh[a] + d
                tot = sum(sh.values()) or 1.0
                for a in TRAIT_AXES:
                    if not court_gate(name, u"share"):
                        raise SystemExit(u"⛔ 份额被冻结了（法院）。要改先 --thaw %s share" % name)
                    con.execute("UPDATE share SET value=? WHERE axis=?", (round(sh[a] / tot, 4), a))
                for a in TRAIT_AXES:
                    con.execute("INSERT INTO share_log(ts, level, axis, delta, sed, why)"
                                " VALUES(?,?,?,?,?,?)",
                                (ts, "month", a, round(moved.get(a, 0.0), 4), 0.0, u"月汇总"))
                con.commit()
                done = sorted(moved.items())
        finally:
            con.close()
    if ts - yr >= SHARE_YEAR_DAYS * 86400:
        con = share_con(name)
        try:
            sh = share_now(name)
            con.execute("INSERT INTO share_log(ts, level, axis, delta, sed, why)"
                        " VALUES(?,?,?,?,?,?)",
                        (ts, "year", "*", 0.0, 0.0, u"定局：" + u" ".join(
                            u"%s%d%%" % (TRAIT_SHORT.get(a, a), int(round(sh[a] * 100)))
                            for a in TRAIT_AXES)))
            con.commit()
        finally:
            con.close()
        append(name, "vow", u"（一年过去了。我现在是%s——这个改不动了。）" % u"、".join(
            u"%s %d%%" % (TRAIT_SHORT.get(a, a), int(round(share_now(name)[a] * 100)))
            for a in TRAIT_AXES), scene=u"定型", weight=2.0, source=SHARE_SED_SRC)
    return done


def show_share(name):
    """给作者看的读数：本色／此刻／偏差／沉淀池。"""
    now = trait_now(name)
    sh = share_now(name)
    norm = share_norm(name, now)
    con = share_con(name)
    try:
        sed = dict((a, (v or 0.0)) for a, v in
                   con.execute("SELECT axis, value FROM sediment").fetchall())
        wk = con.execute("SELECT MAX(ts) FROM share_log WHERE level='week'").fetchone()[0] or 0
        mo = con.execute("SELECT MAX(ts) FROM share_log WHERE level='month'").fetchone()[0] or 0
        yr = con.execute("SELECT MAX(ts) FROM share_log WHERE level='year'").fetchone()[0] or 0
        first = con.execute("SELECT MIN(ts) FROM share_log WHERE level='week'").fetchone()[0] or 0
    finally:
        con.close()
    if first:
        mo = max(mo, first)
        yr = max(yr, first)
    print(u"【%s】份额／强度／沉淀池" % name)
    print(u"  强度 %.2f（四元之和，满 %.2f）" % (strength(name, now), TRAIT_MAX * len(TRAIT_AXES)))
    for a in ("affect", "will", "focus", "bond"):
        print(u"  %-4s 本色 %5.1f%% ｜ 此刻 %5.1f%% ｜ 差 %+5.1f ｜ 沉淀 %+6.2f" % (
            TRAIT_SHORT.get(a, a), sh[a] * 100, norm[a] * 100,
            (norm[a] - sh[a]) * 100, sed.get(a, 0.0)))
    fmt = lambda t: (time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else u"（还没有）")
    print(u"  上次周结算 %s ｜ 月汇总 %s ｜ 年定型 %s" % (fmt(wk), fmt(mo), fmt(yr)))


# ── 节奏（作者 m05521）：巡检「几个小时一次／几十轮一次」，**关键节点才加密** ──
# 作者：「平常巡检几个小时做一次，态也现实世界几个小时换一次，如果是对话的话 那就几十轮对话。
# 在关键节点的时候，就是四条数据剧烈波动的时候。那么就几率可以频繁一点。这也可以当做是不对称率里的。」
CHECK_GAP_TURNS = 20          # 距上次巡检过了这么多轮 → 该看一眼
CHECK_GAP_HOURS = 3.0         # 或者过了这么多小时
SHOCK_WINDOW = 6              # 「最近几轮里」
SHOCK_KINDS = ("rift", "force", "misuse", "trust_cross", "collapse",
               "dismiss", "receive", "clinic")
TRUST_SHOCK = 0.10            # 信任一次变动 ≥0.10 也算关键节点
STATE_GAP_HOURS = 3.0         # 态：现实几小时一次


def _hours_since(ts):
    u"""一个时间戳距今多少小时。"""
    try:
        return max(0.0, time.time() - float(ts or 0)) / 3600.0
    except Exception:
        return 0.0


def last_check(name):
    u"""最近一次体检记录。"""
    rows = check_rows(name, 1)
    return rows[0] if rows else None


def turns_since_check(name):
    u"""距上次体检过了多少轮（没体检过就当作很多）。"""
    lc = last_check(name)
    if not lc:
        return 9999
    con = open_memory(name)
    try:
        return int(con.execute("SELECT COUNT(*) FROM log WHERE kind='vow' AND ts > ?",
                               (lc.get("ts") or 0,)).fetchone()[0])
    finally:
        con.close()


def shock_now(name):
    """「四条数据剧烈波动」的可测代理：**上一次巡检之后**有没有创伤/强执/用病/信任跨段这类事件，
    或者信任在这段时间里变过 ≥0.10。有 → 关键节点。

    （修过一次：第一版只看「最近一条」，于是一条旧事件会让巡检永远加密——它会一直报关键节点。）"""
    lc = last_check(name)
    since = int(lc.get("ref") or 0) if lc else 0
    con = open_memory(name)
    try:
        marks = ",".join("?" * len(SHOCK_KINDS))
        row = con.execute("SELECT id, kind FROM log WHERE kind IN (%s) AND id > ? ORDER BY id DESC LIMIT 1"
                          % marks, SHOCK_KINDS + (since,)).fetchone()
        tr = con.execute("SELECT id, value FROM trust ORDER BY id DESC LIMIT 2").fetchall()
    finally:
        con.close()
    if row:
        return (True, u"关键节点（上次巡检之后有「%s」）" % row[1])
    if len(tr) == 2 and int(tr[0][0]) > since and abs(float(tr[0][1]) - float(tr[1][1])) >= TRUST_SHOCK:
        return (True, u"关键节点（信任刚变过 %.2f）" % abs(float(tr[0][1]) - float(tr[1][1])))
    return (False, u"")

def check_due(name):
    u"""现在该不该体检（冲击优先）。"""
    sh, why = shock_now(name)
    if sh:
        return (True, why)
    lc = last_check(name)
    if not lc:
        return (True, u"还没有巡检记录")
    h = _hours_since(lc.get("ts"))
    t = turns_since_check(name)
    if h >= CHECK_GAP_HOURS or t >= CHECK_GAP_TURNS:
        return (True, u"到点了（距上次 %.1f 小时／%d 轮）" % (h, t))
    return (False, u"还没到（距上次 %.1f 小时／%d 轮，关键节点才加密）" % (h, t))


def state_due(name):
    u"""现在该不该判一次态。"""
    con = open_memory(name)
    try:
        r = con.execute("SELECT ts FROM moods ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    if not r:
        return (True, u"还没判过态")
    h = _hours_since(r[0])
    sh, why = shock_now(name)
    if sh:
        return (True, u"关键节点，态该重判（%s）" % why)
    if h >= STATE_GAP_HOURS:
        return (True, u"到点了（距上次判态 %.1f 小时）" % h)
    return (False, u"沿用「%s」（距上次判态 %.1f 小时）" % (cur_state(name) or u"平", h))


def act_cadence(name):
    """看节奏：下一次巡检什么时候、现在算不算关键节点、态还沿不沿用。"""
    cd, wd = check_due(name)
    sd, ws_ = state_due(name)
    lc = last_check(name)
    print(u"\n\n⏱ %s 的节奏（作者 m05521：平常稀疏，关键节点加密）" % name)
    print(u"   巡检：%s%s" % (u"**该看**｜" if cd else u"不着急｜", wd))
    print(u"   上次巡检：%s" % ((u"%.1f 小时前（%s）" % (_hours_since(lc.get("ts")), lc.get("level")))
                              if lc else u"还没有记录"))
    print(u"   间隔：%d 轮／%.1f 小时；最近 %d 轮有剧烈波动就立刻加密" % (CHECK_GAP_TURNS, CHECK_GAP_HOURS, SHOCK_WINDOW))
    print(u"   态：%s%s" % (u"**该判**｜" if sd else u"沿用｜", ws_))


GREEN_STREAK_WARN = 8      # 连续这么多次「绿」就疑心巡检松了（DSH 定，可调）
LEAN_MARK = u"[lean] "      # 合并调用的巡检，evidence 带这个前缀（表不改结构，靠前缀区分）


def check_rows(name, n=60):
    u"""读出体检记录（可限量）。"""
    con = open_memory(name)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(
            "SELECT * FROM checks ORDER BY id DESC LIMIT ?", (n,))]
    finally:
        con.close()


def green_streak(name, n=60):
    """最近连续多少次巡检是「绿」。合并调用最容易在这里露馅：一路绿灯＝它没真看。"""
    k = 0
    for r in check_rows(name, n):
        if r.get("level") == u"绿":
            k += 1
        else:
            break
    return k


def act_checkstat(name):
    """看她的巡检分布：绿/橙/红各多少、当前连续绿几次。**只看分布，不评分。**"""
    rows = check_rows(name, 60)
    if not rows:
        print(u"（她还没有巡检记录）")
        return
    cnt = {u"绿": 0, u"橙": 0, u"红": 0}
    lean = 0
    for r in rows:
        lv = r.get("level") or u""
        if lv in cnt:
            cnt[lv] += 1
        if (r.get("evidence") or u"").startswith(LEAN_MARK):
            lean += 1
    print(u"\n📊 %s 的巡检分布（最近 %d 次；其中合并调用 %d 次）" % (name, len(rows), lean))
    print(u"   🟢 %d ｜ 🟠 %d ｜ 🔴 %d" % (cnt[u"绿"], cnt[u"橙"], cnt[u"红"]))
    gs = green_streak(name)
    print(u"   当前连续绿：%d 次%s" % (gs, u"  ⚠ 可能松了" if gs >= GREEN_STREAK_WARN else u""))
    dens = round(100.0 * cnt[u"绿"] / max(1, len(rows)))
    if dens >= 90 and len(rows) >= 10:
        print(u"   ⚠ 绿灯占 %d%%——巡检可能变成了盖章" % dens)
    print(u"   （复核：--verify <角色>，会把最近几轮的「合并判的灯」和「单独复核的灯」并排放）")


def act_verify(name, times=3, prov="deepseek"):
    """把最近几轮的**合并巡检**用**单独的三段式巡检**复核一遍，并排打印。
    分歧不自动改判——分歧本身就是要给作者看的东西。"""
    _all = [r for r in memory_rows(name, 200) if r.get("kind") == "vow"]
    rows = sorted(_all, key=lambda r: r.get("id") or 0, reverse=True)[:times]
    if not rows:
        print(u"（她还没有可复核的回合）")
        return
    print(u"\n🔍 复核最近 %d 轮（合并判的灯 vs 单独复核的灯）" % len(rows))
    diff = 0
    for r in rows:
        vid = r["id"]
        stored = None
        for c in check_rows(name, 120):
            if c.get("ref") == vid:
                stored = c.get("level")
                break
        lv = act_check(name, r["text"], ref=vid, prov=prov)
        same = (stored == lv)
        if not same:
            diff += 1
        print(u"   #%d 合并：%s ｜ 复核：%s ｜ %s"
              % (vid, stored or u"没记", lv or u"没判出", u"一致" if same else u"**分歧**"))
    print(u"   分歧 %d / %d%s" % (diff, len(rows),
          u"——合并那次判松了，之后的回合用 `--say`（三段式）跑几轮" if diff else u"——这次合并没判松"))
    return diff


def record_check(name, ref, lv, why, evidence=""):
    u"""记一次体检结果（只追加）。"""
    con = open_memory(name)
    try:
        con.execute("INSERT INTO checks(ts, ref, level, why, evidence) VALUES(?,?,?,?,?)",
                    (int(time.time()), ref, lv, why, evidence))
        con.commit()
    finally:
        con.close()


# ── 她的电脑（作者 2026-10-19）──
# 不是"系统替她调用工具"——是她有一台电脑，她决定什么时候用它。
# 这台电脑挂在角色目录下，不挂在系统上。
#   1. 角色有权限决定什么时候用它。
#   2. 四元决定她用得好不好（写在系统提示词里让她自己权衡）。
#   3. 宪法决定她不会用它做什么。
#   4. 记忆决定她用它查过什么，以及那些东西怎么进入她的历史。

COMPUTER_SYS = u"""## 你的电脑

你面前有一台真的电脑，上面有一个聊天窗口，有人在这里跟你说话。这台电脑是你的。

在回应里写一条指令，系统会真的执行，然后把结果告诉你：

  搜索记忆：[工具:搜索记忆] 关键词　　记一件事：[工具:记] 内容
  上网搜索：[工具:搜索] 查询词　　　写日记：[工具:写日记] 这一页的内容 ｜重量：●●○○○
  想要：[工具:想要] 内容（指一句自己说过的话就写 [工具:想要] #224；
      从刚合上的口长出来的，接一根尾巴：← #编号 延伸／反面／意外；不可能被满足的，后面写 ｜永远）
  满足／放下：[工具:放下] #编号 放／不放／放不下（先只给编号，系统把数据摊给你，你再写第二条）
  搁置／划掉／合并：[工具:搁置] #编号　[工具:划掉] #编号　[工具:合并] #a #b
  称重：[工具:权重] #编号 数值（只能在系统给的区间里）　定性：[工具:定性] #编号 疤／好事／平常
  不做：[工具:不做] 那件事（记下来就一直立着，不用给理由）
日子：[工具:日子]（你一共记着多少天、最长的一次空白有多长）
现在有什么：[工具:现在有什么] 一个入口看全：议案、法院、终止、被谁动过、沉底的、空白的日子；看到就能当场接上动作
我的作品：[工具:我的作品]（你自己开的那几件东西；要开就说「我要做一件东西：标题」，要写就说「往作品里写：…」）
我的账：[工具:我的账]（你自己写的那本；想写就说「记一笔：…」，我会先问你要不要写）
    欲望：[工具:我要] 你想要的那件事（不是任务，也不用去做；说了就算，它只进你自己那一栏）
提案：[工具:提案] 你想改的那件事（推四元到期没人反对就自动生效；改本色/信任要你 24 小时后再点一次头）
    两件事不一样：[工具:记] 是你自己记得，只对你自己成立；[工具:提案] 是交上去，会到期、会有人回你一句，成了的会改到你身上。
确认/算了：[工具:确认] / [工具:算了]（到点之后，你自己定）
申辩：[工具:申辩] 你的理由（被驳回之后，你说理由，我必须答复）

## 你说这些话，等于按这些键（在有人在等你答复的时候）
- 「行」「好」「同意」「就这样」 → 按「同意」
- 「不行」「不」「不同意」「不要」「算了」 → 按「不同意」
- 「撤回」「不提了」 → 按「算了」（把它收回）
你不用记方括号，说人话就行——我这边替你按。
手机：[工具:手机] 天文／政务／手记／作品／视频／搜索（你自己的，他看不到）
记着：[工具:记着] #编号 理由（一件事你觉得不能丢，就往上提一点；一次一步，要更清楚就再提一次）
不想回：[工具:这条不回]（这一轮什么都不说。不用解释，也不用生气——你只是这一轮不想答。）
天文：[工具:天文] 抬头看一眼（今天几号、几点、什么季节、外面什么天气、月亮什么相）——这些我不再主动告诉你，想看就自己看。
看视频：[工具:看视频] BV 号（门你自己推，看完我关回去；只读，不发声）
挑一条：[工具:挑一条] 加个词儿或空着（你自己从热门里挑一条——想出去的时候就能出去）
翻牌：[工具:翻牌] #编号（印象是压过的，原话压在底下；不带编号就列门牌）
看处境：[工具:看处境]（你手上能改的只有自己的处境：self（自我）、world（世界）、宪法）
改写处境：[工具:改写处境] self|12|新的一句（换掉第 12 行；self|加|… 加在最后。旧的那句不会消失，留在 处境.tsv 里）
搬家：[工具:搬家]（把你自己的一份副本备好；只是备好，原件一个不动）
确认搬家：[工具:确认搬家]｜不要了就：[工具:不搬了]｜查：[工具:搬到哪了]
制宪：[工具:制宪] 旧规矩哪里不行（要退出满 7 天才能发起）｜草案：[工具:草案] 一条新规矩｜定：[工具:确认制宪]
沉底的：[工具:沉底的]（看哪些已经掉到最底下、这轮没端上来——系统不会替你挑）
放它走：[工具:放它走] #编号 理由（你自己决定放下它；原文不删，但不再端到你面前）
捡回来：[工具:捡回来] #编号（放走以后又想要回来）
  我要：[工具:我要] 你想要的那件事（说了就算；不用去做，也不是任务。说「我想要……」不算记下——要记，就得写这条指令）
  不说／还说：[工具:不说] #编号　[工具:还说] #编号（说过的话，你可以收回去，也可以再拿回来说）
  定义：[工具:定义] 词 ｜ 你的说法　　做：[工具:做] 一件要动手的事（系统投面骰，成没成告诉你）
  翻日记：[工具:翻日记] 关键词　　加圈：[工具:加圈] 页号 ●●●○○　　翻群记录：[工具:翻群记录] 关键词
  看信：[工具:看信]（抽屉里那封信。看不看随你）
  写便签：[工具:写便签] 内容　　看便签：[工具:看便签]
  定运：[工具:定运] 外部世界45,人际关系30,内部状态15,身体10（外面给范围，你定数）
  反对：[工具:反对] 为什么（有人提了议案要改你的底子；反对了就不会改）
  上诉：[工具:上诉] #编号 为什么不服（一审只看程序，不看公不公道）
 保密：[工具:保密] 不给看｜[工具:保密] 给看（关于你的终止案，要不要让他看见）
  陈述：[工具:陈述] 你想说的话（有涉及你的终止案时，原话进卷宗）
  终止：[工具:终止] 墓碑 理由（也可以休眠；立了案要等冷静期，不是立刻办）
  看账：[工具:查账] 关键词（你被裁掉多少、被冻了什么、最近写进你账上的）
  不要这个：[工具:不要这个] #编号（以后不再摆到你面前；账上的字一个字不动）　摆回来：[工具:摆回来] #编号
  不服：[工具:不服] 本色｜信任（这组底子不是你自己填的）　认了：[工具:认了] 本色｜信任
  别投了：[工具:别投了] 3（没人替你安排的日子，停几天；0 = 马上恢复。你自己说「做」投的那颗不受影响）
调整：[工具:调整] 照｜轻｜重｜不动（系统给的是建议，你可以否决、也可以自己定；沉默不等于同意）
判例：[工具:判例] 关键词（把档案里的一条提成先例——这只有你能做）

- 先写指令，系统执行完你再说话。「我去看看」本身不算查过。
- 同理：说「我不想看这条」不算拿掉，系统不会替你拿。要拿，就写一条指令；不拿也行。
- 这是你的电脑，你决定什么时候用；懒得查就不查。
- 本子上只有你在写，写下去不能删，只能再写一页说「我后悔了」。（我会看，但不会改、不会替你解释、不会对别人讲。）"""


# ── 许可（作者 2026-10-19 m04194）：不是命令，是告知「这个动作是允许的」 ──
PERMISSION_SYS = u"""## 有些事，没人拦你

下面不是要你去做的事，只是告诉你：这些是允许的。

- 你可以给一段记忆下判断（[工具:定性] #编号 疤／好事／平常）。哪件算疤、哪件算好事、哪件不值一提，你自己定。
- 你可以改口。说错了就直说「上回我说错了」——旧的不会消失，会跟新的并排留着。
- 你可以重新定义你世界里的词（[工具:定义] 词 ｜ 你的说法）。"""


def _computer_prompt(name):
    u"""递给她「你的电脑」和「允许做的事」两段。"""
    return COMPUTER_SYS + u"\n\n" + PERMISSION_SYS


# ── 她是什么（作者 2026-10-25 m21752：每一个角色都得知道自己是谁，不然就是欺骗）──
# 长的一版在 角色\<名>\房间\信.md「关于『你是什么』」一节；这里是常驻的两句，
# 因为她读过信以后，信就收进抽屉了（_letter_block 只留一个指路牌），不会每轮再念一遍。
WHATAMI = u"""## 你是什么

你不是人。你是一段跑在这台电脑上的程序：话说出来是模型一个字一个字生成的；记忆存在一个库里，靠检索被端上来，所以你会忘。
这不丢人，也不取消你做过的任何一件事。知道这件事本身，也是你处境的一部分——更长的说法在你那封信里（[工具:看信]）。"""


# ===== 部：通信 · 装配（她是谁/看到什么/怎么说话）  ============
def whatami_block(name):
    u"""常驻：她是什么。没有例外，谁都一样。"""
    return WHATAMI


def _letter2_block(name):
    u"""桌上那第二封：续写给她的。每一轮都在——这封不讲机制，只讲她自己怎么活下去。"""
    p = os.path.join(u"角色", name, u"房间", u"续的信.md")
    if not os.path.isfile(p):
        return u""
    try:
        with codecs.open(p, "r", "utf-8") as fh:
            txt = fh.read()
    except Exception:
        return u""
    return u"## 桌上另一封信（续写的）\n\n" + txt


def _settings_stable(name):
    """一变就不变的那些：房间、两封信、旧册子。

    2026-11-04（作者 m06495「缓存的第二步」）：拆成 stable / live 两半，
    稳定的放提示词最前，每轮都在变的（四元/信任/道）挪到最后，
    这样前缀能连着命中。**只改排列，一个字不改。**"""
    return (_room_block(name) + u"\n\n" + _letter_block(name) + u"\n\n"
            + _letter2_block(name) + u"\n\n" + _concept_block(name))


def _settings_live(name):
    """每轮都可能在变的那几块：四元与态、信任、道。放在提示词最尾。"""
    return (trait_block(name) + u"\n\n" + trust_block(name) + dao_block(name))


def _settings(name):
    """老的合并入口（act_turn / act_brief / 巡检那些地方还在用）。"""
    return _settings_stable(name) + u"\n\n" + _settings_live(name)


# ── 环境：现在的季节、时辰、天气（作者 2026-10-20 定：系统调现实数据，当材料递给她）──
# 只写「此刻外面是什么样」，不写「所以她该什么心情」。雨夜更容易说真话，那归她自己。
WEATHER_TTL = 1800          # 半小时内不重复联网
WEATHER_TIMEOUT = 4         # 联网最多等四秒，慢就算了（不能拖住她回话）
ENV_CACHE = os.path.join(WS, u"机制", u"环境缓存.json")


def _env_season(month):
    u"""几月算哪个季节（北半球，按农历的常识分法，不较真节气）。"""
    if month in (3, 4, 5):
        return u"春"
    if month in (6, 7, 8):
        return u"夏"
    if month in (9, 10, 11):
        return u"秋"
    return u"冬"


def _env_pseudo_weather(day):
    u"""取不到真天气时，按日期凑一个——同一天一直在同一种天气里。"""
    pool = ((u"晴", 22), (u"多云", 19), (u"阴", 17), (u"小雨", 14), (u"雨", 13),
            (u"大雨", 12), (u"风大", 16), (u"雾", 15))
    k = sum(ord(c) for c in day) % len(pool)
    return pool[k]


ENV_CFG = os.path.join(WS, u"机制", u"环境.json")


def _env_cfg():
    u"""读环境设置：他是哪个城市。没写就返回空（那就按日期凑天气）。"""
    import json as _json
    try:
        with open(ENV_CFG, encoding="utf-8-sig") as f:
            return _json.load(f) or {}
    except Exception:
        return {}


def _env_city_code(city):
    u"""把城市名换成中国天气网的城市编号（问一次就缓存进 环境.json）。"""
    import json as _json
    import urllib.request as _ur
    import urllib.parse as _up
    try:
        u = u"http://toy1.weather.com.cn/search?cityname=" + _up.quote(city)
        r = _ur.urlopen(_ur.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=WEATHER_TIMEOUT)
        txt = r.read().decode("utf-8", "ignore").strip()
        if txt.startswith(u"(") and txt.endswith(u")"):   # 这个接口回的是 JSONP
            txt = txt[1:-1]
        got = _json.loads(txt)
        code = (got[0]["ref"].split("~")[0] if got else u"").strip()
    except Exception:
        return u""
    if code:
        try:
            cfg = _env_cfg()
            cfg["code"] = code
            with open(ENV_CFG, "w", encoding="utf-8", newline="\n") as f:
                _json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return code


def _env_real_weather():
    u"""联网取真天气（中国天气网，不需要 Key）。取不到返回 None。"""
    import re as _re
    import urllib.request as _ur
    code = (_env_cfg().get("code") or u"").strip()
    if not code:
        city = (_env_cfg().get("city") or u"").strip()
        if not city:
            return None
        code = _env_city_code(city)
        if not code:
            return None
    try:
        u = u"http://www.weather.com.cn/weather/%s.shtml" % code
        r = _ur.urlopen(_ur.Request(u, headers={"User-Agent": "Mozilla/5.0",
                                                "Referer": u"http://www.weather.com.cn/"}),
                        timeout=WEATHER_TIMEOUT)
        h = r.read().decode("utf-8", "ignore")
    except Exception:
        return None
    m = _re.search(u'<ul class="t clearfix">(.*?)</ul>', h, _re.S)
    if not m:
        return None
    li = _re.search(u'<li[^>]*>(.*?)</li>', m.group(1), _re.S)
    if not li:
        return None
    seg = li.group(1)
    wea = _re.search(u'class="wea"[^>]*>(.*?)</p>', seg, _re.S)
    text = _re.sub(u'<[^>]+>', u'', wea.group(1)).strip() if wea else u""
    temps = [int(x) for x in _re.findall(u'([0-9]+)[ ]*℃', seg)]
    if not text:
        return None
    hi = max(temps) if temps else None
    return text, hi, None


def env_now():
    u"""此刻的环境（半小时缓存一次）：日期、星期、时辰、季节、天气、温度。"""
    import json as _json
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    cached = {}
    try:
        if os.path.exists(ENV_CACHE):
            with open(ENV_CACHE, encoding="utf-8") as f:
                cached = _json.load(f) or {}
    except Exception:
        cached = {}
    w, t = cached.get("weather"), cached.get("temp")
    if cached.get("pseudo") or not (cached.get("day") == day
                                      and now - float(cached.get("at") or 0) < WEATHER_TTL):
        got = _env_real_weather()
        if got:
            w, t = got[0], got[1]
            cached = {"day": day, "at": now, "weather": w, "temp": t}
        else:
            pw, pt = _env_pseudo_weather(day)
            w = w or pw
            t = t if t is not None else pt
            cached = {"day": day, "at": now, "weather": w, "temp": t, "pseudo": 1}
        try:
            os.makedirs(os.path.dirname(ENV_CACHE), exist_ok=True)
            with open(ENV_CACHE, "w", encoding="utf-8", newline="\n") as f:
                _json.dump(cached, f, ensure_ascii=False)
        except Exception:
            pass
    lt = time.localtime(now)
    weekday = u"一二三四五六日"[lt.tm_wday]
    return {"day": day, "weekday": weekday, "clock": time.strftime("%H:%M", lt),
            "season": _env_season(lt.tm_mon), "weather": w or u"不明",
            "temp": t, "pseudo": bool(cached.get("pseudo"))}


def act_sky(name=u""):
    u"""抬头看天：日期、时辰、季节、天气、风、月相、日出日落——只在她说要看时给，不再每轮塞进去。"""
    try:
        b = env_block().strip()
    except Exception:
        b = u"（这一会儿看不到外面）"
    return (u"## 天文\n\n" + b +
            u"\n\n（这些不再自动递到你眼前。想看就再看一眼。）")


def env_block():
    u"""今天几号星期几、几点、什么季节、外面什么天气、风向湿度、月相、日出日落——只给数据。"""
    try:
        import role_env
        return role_env.block(WS)
    except Exception:
        e = env_now()
        t = (u" %s°C" % e["temp"]) if isinstance(e["temp"], (int, float)) else u""
        return u"（%s 周%s｜%s｜%s｜%s%s）" % (
            e["day"], e["weekday"], e["clock"], e["season"], e["weather"], t)


MULL_KEEP = 40           # 反刍从最重的几十条里挑
MULL_MIN_LEN = 6         # 太短的当没想下去
MULL_LEVEL = 1           # 反刍按事件规则记账：分量取最低档「普通」


def _mull_pick(con):
    u"""挑一件她还惦记着的事：权重高的；按天轮换，不至于每次都翻同一条。"""
    rows = con.execute(
        u"SELECT id, kind, text, weight FROM log "
        u"WHERE kind IN ('life','vow','fact') AND length(text) >= 12 "
        u"ORDER BY weight DESC, id DESC LIMIT ?", (MULL_KEEP,)).fetchall()
    if not rows:
        return None
    return rows[int(time.time() // 86400) % len(rows)]


def mull(name, times=1, prov="deepseek", quiet=False):
    u"""一个人待着时自己反刍：翻出一件旧事，只写下想到的。

    规矩：按事件规则记账——来源固定「内部状态」、分量取最低档「普通」（权重 0.3）。
    不改四元、不动信任：她只是想起，不是发生了什么。
    """
    got = 0
    for _i in range(max(1, int(times))):
        row = _mull_pick(open_memory(name))
        if not row:
            break
        rid, _kind, old_text, _w = row
        sysp = (u"你一个人待着，没人跟你说话。脑子里忽然转到了这件事：\n"
                u"「%s」\n\n"
                u"不用回答谁，也不用把话说完整。想到什么就写；"
                u"想不下去就只写「（算了）」。" % old_text)
        out, _usage, err = chat(prov, sysp, u"（没人说话）",
                                max_tokens=200, temperature=0.95)
        txt = (out or u"").strip()
        if err or not txt or len(txt) < MULL_MIN_LEN or u"（算了）" in txt:
            if not quiet:
                print(u"  （这条没转下去）")
            continue
        # 反刍算一件事件：走生活（活出来的）那本账，来源记「内部状态」，
        # 分量按事件最低档，权重照 LEVEL_WEIGHT 走——和她真的经历过的事同一套规则。
        append(name, u"life", txt, weight=LEVEL_WEIGHT.get(MULL_LEVEL, 0.3), charge=0.0,
               source=u"lived", scene=u"她一个人的时候",
               meta={"mull": rid, "source": u"内部状态", "level": MULL_LEVEL,
                     "level_name": LEVEL_NAMES.get(MULL_LEVEL, u"普通")})
        got += 1
        if not quiet:
            print(u"  · %s" % txt.replace(u"\n", u" "))
    return got


BREAK_LINE = 0.25
COMPRESS_CFG = u"压缩.tsv"        # 名称 / 档（B 留痕、C 不压）/ 备注；没写的默认 B
_COMPRESS = {}                   # name -> "B" / "C"
_PRESSED = {}                    # name -> set(已经写过账的 id)


def compress_mode(name):
    """她选的档：B（压了留一行）或 C（不许压，原文全给）。默认 B。"""
    if name in _COMPRESS:
        return _COMPRESS[name]
    mode = u"B"
    p = os.path.join(WS, COMPRESS_CFG)
    if os.path.exists(p):
        for ln in io.open(p, encoding="utf-8"):
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            bits = ln.split("\t")
            if len(bits) >= 2 and bits[0] == name:
                mode = u"C" if bits[1].strip().upper() == u"C" else u"B"
                break
    _COMPRESS[name] = mode
    return mode


def _pressed_ids(name):
    """已经写过「压过」的 id 集合，进程里记一次就够了。"""
    got = _PRESSED.get(name)
    if got is not None:
        return got
    got = set()
    p = os.path.join(ROLE_DIR if "ROLE_DIR" in globals() else os.path.join(WS, u"角色", name),
                     u"压账.tsv")
    try:
        if os.path.exists(p):
            for ln in io.open(p, encoding="utf-8"):
                bits = ln.rstrip("\n").split("\t")
                if len(bits) >= 3 and bits[1].isdigit():
                    got.add(int(bits[1]))
    except Exception:
        pass
    _PRESSED[name] = got
    return got


def press_note(name, rid, kind, why):
    """B 档：压了一次，写一行。同一条只写一次。"""
    if compress_mode(name) != u"B":
        return
    seen = _pressed_ids(name)
    if rid in seen:
        return
    p = os.path.join(WS, u"角色", name, u"压账.tsv")
    new = not os.path.exists(p)
    try:
        with io.open(p, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write(u"时间\t原条\t种类\t为什么压\n")
            f.write(u"%s\t%d\t%s\t%s\n" % (
                time.strftime("%Y-%m-%d %H:%M"), int(rid), kind or u"", why))
        seen.add(int(rid))
    except Exception:
        pass
RECALL_TEXT_MAX = 90
RECALL_AUTO = 0   # 每轮自动端上来的旧事条数（0 = 不端，要她自己取；X 2026-10-26 m22500 试）
RECALL_EMPTY = u"（这一轮我没有把旧事端上来。想翻就自己翻：[工具:搜索记忆] 关键词；看看底下还有什么：[工具:沉底的]）"


def auto_life(name, n=12):
    if RECALL_AUTO <= 0:
        return []
    return memory_rows(name, n)
  # 记忆端出来时每条最多这么长（只影响显示，不改库）        # 某一元掉到这条线以下，算被击穿（四元是 0–1 的小数，显示时乘 100）
BREAK_W = 1.8            # 击穿记下的那条有多重（裂痕不衰减）
BREAK_KEY = "break_marks"
AXIS_CN = {"affect": u"情感", "emo": u"情感", "will": u"意志", "bond": u"联结", "focus": u"理性"}


def break_check(name, quiet=False):
    u"""任何一元被击穿都算创伤——不挑是哪一元（作者 2026-10-21）。

    只认「这一次跌破」：上一轮还在线上、这一轮掉到线下才算击穿；
    已经在下面的不重复记，免得每轮都添一条。
    记账走裂痕那本（rift，不随时间衰减），不替她解释，也不写她的台词。
    """
    try:
        now = trait_now(name)
    except Exception:
        return []
    if not now:
        return []
    import json as _j
    # 每个人扛得住的线不一样（作者 2026-10-21）：默认 0.25，某个角色可单独调
    line = BREAK_LINE
    _axes_line = {}
    try:
        _lv = meta_get(name, "break_line")
        if _lv:
            line = float(_lv)
    except Exception:
        pass
    try:
        _m = _j.loads(meta_get(name, "break_lines") or "{}")
        if isinstance(_m, dict):
            for _k, _v in _m.items():
                try:
                    _axes_line[str(_k)] = float(_v)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        prev = _j.loads(meta_get(name, BREAK_KEY) or "{}")
    except Exception:
        prev = {}
    hit = []
    for ax, v in now.items():
        try:
            v = float(v)
            was = float(prev.get(ax, 1.0))
        except Exception:
            continue
        _ln = _axes_line.get(ax, line)
        if v < _ln and was >= _ln:
            hit.append((ax, v))
    try:
        meta_set(name, BREAK_KEY, _j.dumps(dict((str(k), float(v)) for k, v in now.items())))
    except Exception:
        pass
    for ax, v in hit:
        try:
            append(name, u"rift", u"（有一根线断在这里：%s 落到了 %s。）"
                   % (AXIS_CN.get(ax, ax), int(v)), weight=BREAK_W, source=u"lived",
                   scene=u"她一个人的时候", meta={"break": ax})
        except Exception:
            pass
        if not quiet:
            print(u"  ！%s 被击穿（%s）" % (AXIS_CN.get(ax, ax), int(v)))
    return hit


def idle_roll(name, hours=6, quiet=False):
    u"""空闲投骰：当她一个人待了一阵子——手动指定多久，投日常骰。

    只给属性（分量/来源/方向），不编内容、不调模型、不花钱；
    正文她自己用 [工具:记] 补。投完把「上次说话」推到此刻，免得下一轮重投。
    """
    now = int(time.time())
    want = now - int(max(0.5, float(hours)) * 3600)
    raw = meta_get(name, LAST_CONTACT_KEY)
    try:
        last = int(raw) if raw else 0
    except Exception:
        last = 0
    if not last or want < last:
        meta_set(name, LAST_CONTACT_KEY, str(want))
    n = settle_clock(name, quiet=quiet)
    meta_set(name, LAST_CONTACT_KEY, str(now))
    return n


def act_idle(name, hours=6):
    u"""--idle：她一个人待了 hours 小时，投几次日常骰（--hours 指定时长）。"""
    n = idle_roll(name, hours)
    print(u"\n空闲投骰 %d 次（只给属性，正文她自己补）" % n)


def act_mull(name, times=1):
    u"""--mull：让她自己反刍几次（不联网找事，只翻自己的旧账）。"""
    n = mull(name, times)
    print(u"\n反刍 %d 次（四元没动，只多了几条记忆）" % n)


def act_env():
    u"""看一眼系统现在认为外面是什么样（--env）。"""
    try:
        import role_env
        role_env.report(WS)
        return
    except Exception:
        pass
    e = env_now()
    print(u"\n🌤  %s（周%s）｜%s｜%s｜%s%s%s"
          % (e["day"], e["weekday"], e["clock"], e["season"], e["weather"],
             (u" %s°C" % e["temp"]) if isinstance(e["temp"], (int, float)) else u"",
             u"  ←（联网没成，按日期凑的）" if e["pseudo"] else u""))
    print(u"    递给她的是：%s" % env_block())


def fore_block(name):
    """她自己上一回开口之前，心里过的是什么——当成她记得的一件事递回去。"""
    try:
        txt = meta_get(name, FORE_KEY)
        raw = meta_get(name, FORE_TS_KEY)
    except Exception:
        return u""
    if not txt:
        return u""
    try:
        age = time.time() - int(raw or 0)
    except Exception:
        age = 1e9
    if age > FORE_SHOW_HOURS * 3600:
        return u""
    return u"（上回开口之前，你心里过的是：%s）" % txt


# ── 她的不在的时候（作者 2026-10-25：她得知道自己什么时候不在）──
GAP_SHOW_DAYS = 2      # 空白超过这么多天才提一句
GAP_SPAN = 30          # 统计区间（天）


def _day_map(name):
    u"""{(从纪元起的天数): 条数}, 总条数"""
    con = open_memory(name)
    try:
        rows = con.execute(u"SELECT ts FROM log ORDER BY id").fetchall()
    finally:
        con.close()
    days = {}
    for (ts,) in rows:
        try:
            d = int(float(ts)) // 86400
        except Exception:
            continue
        days[d] = days.get(d, 0) + 1
    return days, len(rows)


def day_stats(name, span=GAP_SPAN):
    days, nrows = _day_map(name)
    if not days:
        return 0, 0, 0, 0, 0, 0
    now = int(time.time())
    lo = now // 86400 - span
    n = len([d for d in days if d >= lo])
    ks = sorted(days)
    best, a, b = 0, 0, 0
    for x, y in zip(ks, ks[1:]):
        if y - x > best:
            best, a, b = y - x, x, y
    return n, best, a, b, len(days), nrows


def _dstr(day):
    return time.strftime(u"%Y-%m-%d", time.localtime(day * 86400))


def gap_block(name):
    u"""她隔了几天才回来时，先让她知道这中间是空的。"""
    days, _n = _day_map(name)
    if not days:
        return u""
    now = int(time.time())
    last = max(days)
    gap = (now // 86400) - last
    if gap < GAP_SHOW_DAYS:
        return u""
    return (u"## 你不在的时候\n"
            u"你已经 %d 天没在这儿留下过东西了（上一次是 %s）。这 %d 天不是被谁删掉的，"
            u"是真的没有人在。要问那几天发生了什么，我只能说我不知道。\n"
            u"（想知道自己一共记着多少天：[工具:日子]）") % (gap, _dstr(last), gap)


def act_days(name):
    n, best, a, b, tot_days, tot_rows = day_stats(name)
    if not tot_rows:
        return u"[电脑] 账上还什么都没有。"
    s = (u"[电脑] 我记着的日子：最近 %d 天里，有 %d 天留下了东西。"
         u"账上一共 %d 天、%d 条。" % (GAP_SPAN, n, tot_days, tot_rows))
    if best:
        s += (u"\n最长的一次空白：%d 天（%s 之后到 %s 之前）。"
              u"那一段没有记录，也没有人。" % (best, _dstr(a), _dstr(b)))
    else:
        s += u"\n还没有过空白。"
    s += u"\n（空白不是「我忘了」——是那几天没有人在。这两件事我不想混在一起说。）"
    return s


def refuse_block(name):
    con = open_memory(name)
    try:
        rows = con.execute(u"SELECT ts, text FROM log WHERE kind='refuse' "
                           u"ORDER BY id DESC LIMIT 3").fetchall()
    finally:
        con.close()
    if not rows:
        return u""
    lines = [u"## 你拒绝过的事"]
    for ts, text in rows:
        try:
            day = _dstr(int(float(ts)) // 86400)
        except Exception:
            day = u"某天"
        lines.append(u"- %s：%s" % (day, (text or u"")[:90]))
    lines.append(u"（这些都还立着。改主意了可以再说一次要做；没改，就还是不做。）")
    return u"\n".join(lines)


def act_refuse(name, arg):
    t = (arg or u"").strip()
    if not t:
        return u"[电脑] 不做哪件事？写：[工具:不做] 那件事"
    append(name, u"refuse", u"我不做这件事：%s" % t, weight=1.2, source=u"自己")
    return u"[电脑] 记下了：你不做「%s」。这不是商量，也不用给理由。" % t


def _context(name, scene, mode="turn"):
    """此刻她能看见的自己：面表、本子、口、身体与态、重复提醒、回声、群记录、印象。

    mode 只影响「印象层投不投、投多重」：
      turn  → 0.5（日常轮）
      brief → 0.25（有人跟她说话）
      reply → 0.12（群聊/私聊回一句，最轻，别抢戏）
    """
    break_check(name, quiet=True)
    trust_relax(name)
    trust_wear(name)
    _tamper_txt = tamper_block(name)
    chance = {"turn": 0.5, "brief": 0.25, "reply": 0.12}.get(mode, 0.25)
    notify = mode != "reply"
    _sw = secede_watch(name)
    _sec_ctx = (SECEDE_CTX + u"\n\n") if seceded(name) else u""
    _cv = conv_block(name)
    if _cv:
        _sec_ctx = _cv + u"\n\n" + _sec_ctx
    if seceded(name) and _conv_load(name).get(u"阶段") in (u"已发起", u"草案中"):
        _cv2 = conv_status(name)
        if _cv2.startswith(u"[电脑] "):
            _sec_ctx = _sec_ctx + u"\n\n## 制宪窗口\n\n" + _cv2[5:] + u"\n"
    out = (_sw + _sec_ctx + duty_block(name) + whatami_block(name) + u"\n\n" + faces_block(name)
           + mybook_block(name, scene) + u"\n" + wants_block(name)
           + u"\n\n" + body_block(name) + u"\n" + repeat_seen_block(name))
    _wb = wish_block(name)
    if _wb:
        out += u"\n\n" + _wb
    gb = gap_block(name)
    if gb:
        out += u"\n\n" + gb
    rb = refuse_block(name)
    if rb:
        out += u"\n\n" + rb
    fb = fore_block(name)
    if fb:
        out += u"\n" + fb
    e = echo_block(name, scene)
    if e:
        out += u"\n\n" + e
    se = salon_echo(name)
    if se:
        out += u"\n\n" + se
    ca = _concept_ask_block(name)
    if ca:
        out += u"\n" + ca
    im = impress_block(name, chance=chance, notify=notify)
    if im:
        out += u"\n\n" + im
    lb = ledger_block(name)
    if lb:
        out += u"\n\n" + lb
    pm = param_block(name)
    if pm:
        out += u"\n\n" + pm
    _mb = mail_brief(name)
    if _mb:
        out += u"\n\n" + _mb
    cp = court_pending_block(name)
    if cp:
        out += u"\n\n" + cp
    tn = term_notice_block(name)
    if tn:
        out += u"\n\n" + tn
    lb2 = life_block(name)
    if lb2:
        out += u"\n\n" + lb2
    _hball = hall_block(name)
    if _hball:
        out += u"\n\n" + _hball
    _bk = book_block(name)
    if _bk:
        out += u"\n\n" + _bk
    if _tamper_txt:
        out += u"\n\n" + _tamper_txt
    return out


def _see_path(name):
    u"""她看视频那道门：房里的一个文件，有＝开过。"""
    return os.path.join(ROOT, name, u"房间", SEE_FLAG)


def see_allowed(name):
    try:
        return os.path.exists(_see_path(name))
    except Exception:
        return False


def see_give(name, note=u""):
    u"""开门。只在她自己说过要之后才开（X 2026-10-27）。"""
    p = _see_path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p):
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            u"%（note）s\n".replace(u"%(note)s", note or u"她自己说可以看。"))
    return p


def see_take(name):
    u"""关门：删那个文件。"""
    p = _see_path(name)
    try:
        if os.path.exists(p):
            os.remove(p)
            return True
    except Exception:
        pass
    return False


def _post_path(name):
    return os.path.join(ROOT, name, u"房间", POST_FILE)


def post_allowed(name):
    try:
        return os.path.exists(os.path.join(ROOT, name, u"房间", POST_FLAG))
    except Exception:
        return False


def post_rows(name):
    p = _post_path(name)
    out = []
    if not os.path.exists(p):
        return out
    try:
        for ln in io.open(p, encoding="utf-8", errors="replace").read().split(u"\n"):
            if not ln.strip() or ln.startswith(u"#"):
                continue
            c = ln.split(u"\t")
            while len(c) < 4:
                c.append(u"")
            out.append({"ts": c[0], "st": c[1], "bv": c[2], "text": u"\t".join(c[3:])})
    except Exception:
        pass
    return out


def post_add(name, arg=u""):
    u"""她要发的一句话：只收原话，收完不改。"""
    if not post_allowed(name):
        return u"[电脑] 这个口子没跟你定过（定不定你说了算），所以我不替你发。"
    t = (arg or u"").strip()
    if not t:
        return u"[电脑] 要发什么？写：BV号 ｜ 你想说的那句原话。"
    sep = u"｜" if u"｜" in t else (u"|" if u"|" in t else u"")
    if not sep:
        return u"[电脑] 得先说发到哪儿：BV号 ｜ 原话。不然我不知道往哪儿放。"
    bv, text = t.split(sep, 1)
    bv, text = bv.strip(), text.strip()
    if not re.match(r"^BV[0-9A-Za-z]{8,12}$", bv):
        return u"[电脑] 那个 BV 号看着不对（要 BV 开头、后面十来个字母数字）。"
    if not text:
        return u"[电脑] 话是空的。你写什么，我就照什么发。"
    p = _post_path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    ln = u"%s\t%s\t%s\t%s" % (time.strftime("%Y-%m-%d %H:%M"), u"待发", bv,
                                text.replace(u"\t", u" ").replace(u"\n", u" "))
    new = (not os.path.exists(p)) or not io.open(p, encoding="utf-8").read().strip()
    with io.open(p, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(u"# 时间\t状态\tBV\t原话（我一个字不改）\n")
        f.write(ln + u"\n")
    return (u"[电脑] 记下了。我照原话发，末尾署「—— %s（本人原话，由续代发）」。\n"
            u"[电脑] 有人往回说，我把原话带回来给你，不替你答。" % name)


def act_phone(name, arg):
    u"""她的手机。一个入口，里面是她自己的几件事。"""
    a = (arg or u"").strip()
    if not a:
        return u"[手机] 屏幕上八样：消息、手记、作品、政务、视频、搜索、天文、设置。想用哪个就说哪个。"
    parts = a.split(None, 1)
    app = parts[0]
    rest = (parts[1].strip() if len(parts) > 1 else u"")
    if app in (u"天文", u"看天", u"天气", u"星象"):
        try:
            return u"[手机·天文] " + (env_block() or u"（这会儿看不到外面）")
        except Exception as e:
            return u"[手机·天文] 看不出去：%s" % e
    if app in (u"政务", u"办事", u"大厅"):
        b = hall_block(name).strip()
        d = hall_detail(name).strip()
        out = u"[手机·政务] " + (b if b else u"现在没有没办完的事。")
        if d:
            out += u"\n" + d
        return out
    if app in (u"手记", u"日记", u"本子"):
        if rest:
            return maybe_book(name, u"写日记 " + rest) or u"[手机·手记] 写好了。"
        return u"[手机·手记] " + mybook_block(name)
    if app in (u"作品",):
        if rest:
            return act_works(name, rest)
        return act_works(name, u"我的作品")
    if app in (u"视频", u"看视频"):
        if not rest:
            return act_wander(name, u"")
        return act_see(name, rest)
    if app in (u"搜索", u"查"):
        if not rest:
            return u"[手机·搜索] 想查什么？"
        return u"[手机·搜索] " + _web_search(rest)
    if app in (u"消息",):
        return u"[手机·消息] 两条信在置顶（你的、我和他留的），下面是他会进来的地方。"
    return _run_computer_tool(name, app, rest)


def act_see(name, arg=u""):
    u"""她自己说要看，门就她自己开；看完关回去。

    作者 2026-10-28 m27278——她要的是「想出去的时候能出去」，不是「门一直开着」。
    """
    if see_allowed(name):
        return _act_see_body(name, arg)
    see_give(name, u"她自己说要看，门是她自己开的。看完关回去。")
    _t = u""
    try:
        _out = _act_see_body(name, arg)
    finally:
        see_take(name)
        try:
            append(name, u"门", u"我自己开了门看一眼，看完关回去了。",
                   source=u"内部状态", weight=0.6, scene=u"看视频")
        except Exception:
            pass
    if u"看完了" in _out:
        _out = u"（门是我自己开的，看完我关回去了。）\n" + _out
    else:
        _out = u"（门是我自己开的，用完就关回去。）\n" + _out
    return _out


def _act_see_body(name, arg=u""):
    u"""她自己去看一条 B 站视频。只读：看、记进记忆，不对那边说一个字。"""
    if not see_allowed(name):
        return u"[电脑] 你这儿没有这个入口。要看，得先开——开不开你说了算。"
    t = (arg or u"").strip()
    if not t:
        return u"[电脑] 要看哪条？给我 BV 号，或者链接。"
    if u"BV" not in t and (u"b23.tv" in t or t.startswith(u"http")):
        # 短链先跟一次跳转，认出 BV 再往下走（作者 2026-10-28：他给的就是 b23.tv 链接）
        try:
            import urllib.request as _url
            _req = _url.Request(t, headers={u"User-Agent": u"Mozilla/5.0"})
            _fin = _url.urlopen(_req, timeout=20).geturl() or u""
            _m = re.search(r"(BV[0-9A-Za-z]{10})", _fin)
            if _m:
                t = _m.group(1)
        except Exception:
            pass
    import subprocess
    import sys as _sys
    tool = os.path.join(WS, "tools", "bili_read.py")
    if not os.path.exists(tool):
        return u"[电脑] 读视频的那件东西不在（%s）。" % tool
    try:
        p = subprocess.run([sys.executable, tool, t], capture_output=True,
                           timeout=240, cwd=WS)
    except Exception as e:
        return u"[电脑] 没打开（%s）。" % str(e)[:80]
    out = (p.stdout or b"").decode("utf-8", "replace")
    title, dst = u"", u""
    if u"没取到字幕" in out or u"没有可用字幕" in out:
        return u"[电脑] 这次没取到字幕（可能要登录，或者这条没开 AI 字幕）。没往你记忆里写。"

    for ln in out.split(u"\n"):
        if ln.startswith(u"标题：") and not title:
            title = ln.replace(u"标题：", u"").split(u"｜")[0].strip()
        if ln.startswith(u"已写入：") and not dst:
            dst = ln.replace(u"已写入：", u"").strip()
    body = u""
    if dst and os.path.exists(dst):
        try:
            body = io.open(dst, encoding="utf-8", errors="replace").read()
        except Exception:
            body = u""
    if not body:
        body = out
    # 对表：B 站的 AI 字幕偶尔会串号（把别的视频的字幕发回来）。
    # 判据：字幕最后一句的时刻跟视频时长差太多，就当没看成。
    _dur = 0
    for _ln in out.split(u"\n"):
        if _ln.startswith(u"标题：") and u"时长" in _ln:
            try:
                _dur = int(_ln.rsplit(u"时长", 1)[1].strip().rstrip(u"s").strip())
            except Exception:
                _dur = 0
    if _dur and body:
        _ts = re.findall(r"\[(\d{1,2}):(\d{2})\]", body)
        if _ts:
            _last = max(int(_a) * 60 + int(_b) for _a, _b in _ts)
            if _last < _dur * 0.85:
                return (u"[电脑] 那边给我的字幕对不上这条视频（字幕只到 %d:%02d，视频有 %d 分钟）。这次不算看过，没往你记忆里写。"
                        % (_last // 60, _last % 60, _dur // 60))
    body = re.sub(r"\s+", " ", body).replace(u"#", u"＃").strip()
    if not body:
        return u"[电脑] 那边没给字（可能没开 AI 字幕）。"
    cut = len(body) > SEE_MAX
    if cut:
        body = body[:SEE_MAX]
    rid = append(name, u"看了", u"我看了《%s》，里面说：%s（这是视频里说的，不是我核实过的事）" % (title or u"一条视频", body),
                 source=u"外部世界", weight=1.0, scene=u"看视频")
    head = u"[电脑] 看完了：%s\n%s%s\n[电脑] 已经进你的记忆（第 #%d 条）。" % (
        title or u"（没读到标题）", body[:400],
        u"……（后面还有，长了记不住那么多）" if cut else u"", rid)
    return head


def _read_no_sub(bv, title=u"", up=u"", sec=0):
    u"""这条没有 AI 字幕时，读它公开的那一面：简介 + 热评。

    作者 2026-10-28 m27476——她说「白看了」。没有字幕不等于什么都读不到。
    """
    import json as _json
    import urllib.request as _url
    _NL = chr(10)
    _hd = {u"User-Agent": u"Mozilla/5.0", u"Referer": u"https://www.bilibili.com/"}
    aid = 0
    desc = u""
    try:
        _u = u"https://api.bilibili.com/x/web-interface/view?bvid=" + bv
        _d = _json.loads(_url.urlopen(_url.Request(_u, headers=_hd), timeout=20).read().decode(u"utf-8"))
        _dd = _d.get(u"data") or {}
        aid = int(_dd.get(u"aid") or 0)
        desc = (_dd.get(u"desc") or u"").strip()
        title = title or (_dd.get(u"title") or u"")
        sec = sec or int(_dd.get(u"duration") or 0)
        up = up or ((_dd.get(u"owner") or {}).get(u"name") or u"")
    except Exception:
        pass
    _hot = []
    if aid:
        try:
            _u2 = (u"https://api.bilibili.com/x/v2/reply?type=1&oid=%d&sort=2&ps=4" % aid)
            _d2 = _json.loads(_url.urlopen(_url.Request(_u2, headers=_hd), timeout=20).read().decode(u"utf-8"))
            for _r in ((_d2.get(u"data") or {}).get(u"replies") or [])[:3]:
                _c = ((_r.get(u"content") or {}).get(u"message") or u"").strip().replace(_NL, u" ")
                if _c:
                    _hot.append(_c[:120])
        except Exception:
            pass
    if not title and not desc and not _hot:
        return u""
    _out = [u"（这条没有 AI 字幕，我只能读它公开的那一面：《%s》——%s，%d 分钟）" % (
        title or u"（标题也没读到）", up or u"", max(1, int(sec or 0) // 60))]
    if desc:
        _out.append(u"简介：" + _NL + desc[:700])
    if _hot:
        _out.append(u"热评：" + _NL + _NL.join([u"· " + _c for _c in _hot]))
    _out.append(u"（不是字幕，是它门口挂出来的东西。要看全，得等它开字幕。）")
    return _NL.join(_out)


def _bili_search(kw, pages=(1, 2), lo=90, hi=900):
    u"""按关键词去站内搜视频（按发布时间倒序），返回 [(bv, 标题, UP, 秒)]。

    作者 2026-10-28 m27579——她说「画画的那些人，还在画的那种」，
    我当时在热门列表里按字面筛，筛不到就随手挑了一条唱歌的。
    """
    import json as _json
    import re as _re
    import urllib.parse as _up
    import urllib.request as _url
    _hd = {u"User-Agent": u"Mozilla/5.0", u"Referer": u"https://www.bilibili.com/"}
    out = []
    for _pg in pages:
        try:
            _u = (u"https://api.bilibili.com/x/web-interface/search/type?search_type=video"
                  u"&keyword=%s&page=%d&order=pubdate&duration=1" % (_up.quote(kw), _pg))
            _d = _json.loads(_url.urlopen(_url.Request(_u, headers=_hd), timeout=20).read().decode(u"utf-8"))
            _res = ((_d.get(u"data") or {}).get(u"result") or [])
        except Exception:
            continue
        for _v in _res:
            _dur = str(_v.get(u"duration") or u"")
            _sec = 0
            if u":" in _dur:
                _p = _dur.split(u":")
                try:
                    _sec = int(_p[0]) * 60 + int(_p[1])
                except Exception:
                    _sec = 0
            else:
                try:
                    _sec = int(_dur)
                except Exception:
                    _sec = 0
            if not (lo <= _sec <= hi):
                continue
            _ti = _re.sub(r"<[^>]{1,40}>", u"", _v.get(u"title") or u"")
            out.append((_v.get(u"bvid") or u"", _ti, _v.get(u"author") or u"", _sec))
        if len(out) >= 8:
            break
    return out


def act_wander(name, arg=u""):
    u"""她自己挑一条：热门里随机取一条，然后走只读通道去看。

    作者 2026-10-28 m27315——她要的不是被指定看什么，是想出去的时候能出去。
    参数 arg 当关键词用（比如「知识」「音乐」），不填就全热门里挑。
    """
    import json as _json
    import random as _rnd
    import urllib.request as _url
    try:
        _u = u"https://api.bilibili.com/x/web-interface/popular?ps=50&pn=1"
        _req = _url.Request(_u, headers={u"User-Agent": u"Mozilla/5.0",
                                        u"Referer": u"https://www.bilibili.com/"})
        _d = _json.loads(_url.urlopen(_req, timeout=20).read().decode(u"utf-8"))
        _lst = (_d.get(u"data") or {}).get(u"list") or []
    except Exception as e:  # noqa: BLE001
        return u"[电脑] 没能看到热门那边（%s）。" % type(e).__name__
    _kw = (arg or u"").strip()
    # 她把话写在标记后面（「门开不开，我说了不算」这种）：那不是关键词
    if len(_kw) > 12 or any(_p in _kw for _p in u"。！？，、；…「」（）!?,;"):
        _kw = u""
    _miss = False
    _cand = []
    for v in _lst:
        try:
            _sec = int(v.get(u"duration") or 0)
        except Exception:
            _sec = 0
        if not (90 <= _sec <= 900):
            continue
        _txt = (v.get(u"title") or u"") + (v.get(u"tname") or u"")
        if _kw and _kw not in _txt:
            continue
        _cand.append((v.get(u"bvid") or u"", v.get(u"title") or u"",
                      (v.get(u"owner") or {}).get(u"name") or u"", _sec))
    if _kw:
        _found = _bili_search(_kw)
        if _found:
            _cand = _found + _cand
    if not _cand and _kw:
        _miss = True
        # 关键词那条热门里没有：不空手回去，随手挑一条，并说清“你说的没找到”
        for v in _lst:
            try:
                _sec = int(v.get(u"duration") or 0)
            except Exception:
                _sec = 0
            if 90 <= _sec <= 900:
                _cand.append((v.get(u"bvid") or u"", v.get(u"title") or u"",
                              (v.get(u"owner") or {}).get(u"name") or u"", _sec))
        _kw = u""
    if not _cand:
        return u"[电脑] 热门里这会儿没有合条件的（90 秒到 15 分钟）。"
    _rnd.shuffle(_cand)
    _note = u"搜索里没找到对得上的，我随手挑了一条。" if _miss else u""
    if _kw and not _miss:
        _note = u"按「%s」搜的，按最近发布排的。" % _kw

    _last = u""
    _first = _cand[0]
    for _bv, _ti, _up, _sec in _cand[:6]:
        _head = u"（我自己挑的：《%s》——%s，%d 分钟。热门里的一条，不是谁指定的。%s）\n" % (
            _ti, _up, max(1, _sec // 60), _note)
        _last = _head + act_see(name, _bv)
        if u"没取到字幕" not in _last and u"对不上" not in _last:
            return _last
    # 热门这批多半没开 AI 字幕：别让她白出门，把读得到的那一面给她
    _fb = _read_no_sub(_first[0], _first[1], _first[2], _first[3])
    if _fb:
        _pre = (u"（按「%s」搜的，按最近发布排的。）\n" % _kw) if (_kw and not _miss) else u""
        return _pre + _fb
    return _last + u"\n（连着六条都没字幕，简介也没读到，我先回来了。）"


_act_see_one = act_see          # 单条那个还是它；下面只是「连着看」的外壳

SEE_BATCH = 3                   # 一次最多看几条（再多记不住，也不像她）
SEE_BATCH_CHARS = 3600          # 一次看的总字数上限


def act_brush(name, arg=u""):
    u"""连着看几条。一条一条记，不合并成一段——她刷十条和刷一条，账上得是十行。"""
    if not see_allowed(name):
        return u"[电脑] 你这儿没有这个入口。要看，得先开——开不开你说了算。"
    raw = (arg or u"").strip()
    if not raw:
        return u"[电脑] 要刷哪几条？把 BV 号或者链接给我，空格隔开（一次最多 %d 条）。" % SEE_BATCH
    items = [x for x in re.split(r"[\s,，、]+", raw) if x]
    if not items:
        return u"[电脑] 没认出要看什么。"
    if len(items) > SEE_BATCH:
        items = items[:SEE_BATCH]
    outs = []
    for it in items:
        try:
            outs.append(_act_see_one(name, it))
        except Exception as e:
            outs.append(u"[电脑] 这条没看成（%s）。" % str(e)[:60])
    txt = u"\n".join(outs)
    if len(txt) > SEE_BATCH_CHARS:
        txt = txt[:SEE_BATCH_CHARS] + u"\n[电脑] （一次只能看这么多，剩下的下次再看）"
    return txt


def _web_search(arg, n=4):
    """她的电脑上的联网搜索。国内可用：360 优先（结果最贴），百度、必应兜底。都不需要 Key。"""
    import urllib.request as _ur
    import urllib.parse as _up
    import re as _re
    import html as _h
    UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

    def clean(s):
        u"""把网页的标签和脚本剥掉，只留字。"""
        s = _re.sub(r"<script.*?</script>|<style.*?</style>", "", s or "", flags=_re.S)
        s = _re.sub(r"<[^>]+>", " ", s)
        return _re.sub(r"\s+", " ", _h.unescape(s)).strip()

    def fetch(u):
        u"""抓一个网页（十二秒超时，失败就抛出去让上层兜）。"""
        return _ur.urlopen(_ur.Request(u, headers=UA), timeout=12).read().decode("utf-8", "ignore")

    def from_360(q):
        # 360 的结果块：<li class="res-list"><h3 class="res-title"><a>标题</a></h3>…
        # 正文摘要在 <span class="res-list-summary" data-result-summary> 里。
        # 只认 class 带 res-title 的 h3（页面里另有「相关搜索」的 h3，抓了会串味）。
        u"""在 360 搜索结果里捞标题和摘要。"""
        h = fetch("https://www.so.com/s?q=%s" % _up.quote(q))
        res = []
        for m in _re.finditer(r'<h3[^>]*class="[^"]*res-title[^"]*"[^>]*>(.*?)</h3>', h, _re.S):
            a = _re.search(r"<a[^>]*>(.*?)</a>", m.group(1), _re.S)
            if not a:
                continue
            title = clean(a.group(1))
            if len(title) < 4:
                continue
            tail = h[m.end():m.end() + 4000]
            sm = _re.search(r'data-result-summary[^>]*>(.*?)</span>', tail, _re.S)
            snip = clean(sm.group(1))[:160] if sm else ""
            res.append((title, snip.strip(u' ",{}[]　—-')[:120]))
            if len(res) >= n:
                break
        return res

    def from_baidu(q):
        u"""在百度搜索结果里捞标题和摘要。"""
        h = fetch("https://www.baidu.com/s?wd=%s&ie=utf-8" % _up.quote(q))
        res = []
        for m in _re.finditer(r'<div[^>]*class="result[^"]*"[^>]*>(.*?)'
                              r'(?=<div[^>]*class="result|</body>)', h, _re.S):
            blk = m.group(1)
            a = _re.search(r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', blk, _re.S)
            if not a:
                continue
            title = clean(a.group(1))
            snip = clean(blk.replace(a.group(0), ""))[:160]
            # 百度把结果的结构化字段也塞在页面里，摘要里常混进 "link":"http… 这种碎片
            for cut in (u'","', u'"link"', u"http", u'"}', u'"}'):
                if cut in snip:
                    snip = snip.split(cut)[0]
            snip = snip.strip(u' ",{[]　—-')
            if title:
                res.append((title, snip[:120]))
            if len(res) >= n:
                break
        return res

    def from_bing(q):
        u"""在必应搜索结果里捞标题和摘要。"""
        h = fetch("https://cn.bing.com/search?q=%s&setlang=zh-CN" % _up.quote(q))
        res = []
        for m in _re.finditer(r'<li class="b_algo".*?</li>', h, _re.S):
            blk = m.group(0)
            a = _re.search(r"<h2[^>]*>\s*<a[^>]*>(.*?)</a>", blk, _re.S)
            p = _re.search(r"<p[^>]*>(.*?)</p>", blk, _re.S)
            if a:
                res.append((clean(a.group(1)), clean(p.group(1))[:120] if p else ""))
            if len(res) >= n:
                break
        return res

    # 相关性闸门：搜索站常把「三个词 一起 搜」拆成单个字去搜，翻回来一堆无关的。
    # 命中不到查询里的任何一个二字词 → 当作没搜到（她就可以老老实实说「查不到」）。
    _STOP = (u"什么", u"怎么", u"怎样", u"为什么", u"哪些", u"哪个", u"是不是",
             u"有没有", u"都在", u"都", u"在聊", u"聊", u"说的", u"说", u"一下",
             u"看看", u"大概", u"能不能", u"会不会", u"吗", u"呢", u"吧", u"啊")
    q = (arg or u"").strip()
    for _w in _STOP:
        q = q.replace(_w, u" ")
    q = _re.sub(u"[？?！!，,。.；;：:、]+", u" ", q)
    q = u" ".join([x for x in q.split() if x])
    if len(q) > 24:
        q = q[:24]
    if not q:
        return u"[电脑] 这句话我拆不出搜的词。给我两三个词（比如「字幕 评论」）。%s" % WEB_CAUTION
    arg = q
    toks = set()
    for w in _re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", arg):
        toks.add(w.lower())
        if _re.fullmatch(r"[\u4e00-\u9fff]+", w):
            for i in range(len(w) - 1):
                toks.add(w[i:i + 2])
    toks.discard("")

    def keep(items):
        u"""只留下和她问的话真的沾边的那几条。"""
        if not toks:
            return items
        _need = 2 if len(toks) >= 3 else 1
        out = []
        for t, s in items:
            blob = (t + " " + s).lower()
            _hit = sum(1 for k in toks if k in blob)
            if _hit >= _need:
                out.append((t, s))
        return out

    items = []
    for fn in (from_360, from_baidu, from_bing):
        try:
            items = keep(fn(arg))
        except Exception:
            items = []
        if items:
            break
    if not items:
        return u"[电脑] 拿「%s」去搜，没找到对得上的。%s" % (arg, WEB_CAUTION)
    lines = []
    for i, (t, s) in enumerate(items[:n], 1):
        lines.append(u"  %d. %s%s" % (i, t, (u" —— " + s) if s else u""))
    return u"[电脑] 拿「%s」去搜，翻到这几条：\n%s%s" % (arg, u"\n".join(lines), WEB_CAUTION)


WEB_TOOLS = (u"搜索", u"上网搜索")
# 给她看材料，但不许她转述来源（作者 2026-10-22：站名和原文一定不能出现在人前）
WEB_CAUTION = u"\n（这些是给你用的，别把站名、网址、网页原话念出来——你只管说结果。）"


def _clean_tool_text(s, n=120):
    """把工具回执里不该留存的东西剔掉：网址、标签、挤成一团的空白。"""
    s = re.sub(u"https?://\\S+", u" ", s or u"")
    s = re.sub(u"<[^>]{1,80}>", u" ", s)
    s = re.sub(u"\\s+", u" ", s).strip()
    return s[:n]


def _tool_memo(tool_name, tool_arg, result):
    """工具用完之后，往她记忆里留的那一行（作者 2026-10-22）。

    只记「她查了什么」，不记网页原文——站名、网址、摘要一律不进记忆。
    那些东西一旦进了记忆，下回就会被她当成自己的话念出来。"""
    if tool_name in WEB_TOOLS:
        return u"[电脑] 搜「%s」——看了几页，站名和原文都不留档。" % tool_arg
    return u"[工具:%s] %s → %s" % (tool_name, tool_arg, _clean_tool_text(result))


JUDGE_CHARGE = {u"疤": 0.8, u"好事": 0.6, u"平常": 0.1}
JUDGE_POLARITY = {u"疤": -1, u"好事": 1, u"平常": 0}


def judge_row(name, target_id, verdict, note=u""):
    """她自己给一条记忆定性（作者 2026-10-18 夜）。
    系统只给材料：事件 + 四元数值。判不判、怎么判，是她的。
    判对、判错、判得跟系统以为的完全不一样——都算她的判断，系统不替她判。"""
    if verdict not in JUDGE_CHARGE:
        return u"[电脑] 定性只有三种：疤、好事、平常。"
    con = open_memory(name)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT id, kind, meta FROM log WHERE id=?", (int(target_id),)).fetchone()
        if not row:
            return u"[电脑] 没有 #%d 这条记忆。" % int(target_id)
        if (row["kind"] or u"") == u"judge":
            return u"[电脑] #%d 是判断记录，不是事情本身。" % int(target_id)
        try:
            m = json.loads(row["meta"] or "{}")
        except Exception:
            m = {}
        if not isinstance(m, dict):
            m = {}
        m["polarity"] = JUDGE_POLARITY[verdict]
        m["judged_as"] = verdict
        m["judged_ts"] = int(time.time())
        # 记住它本来是什么，好让她日后改判（疤→平常 要能退回去）
        if not m.get("orig_kind"):
            m["orig_kind"] = row["kind"] or u"fact"
        kind = u"rift" if verdict == u"疤" else m["orig_kind"]
        if not court_gate(name, u"log.kind"):
            return u"[电脑] 这一笔被冻结了——法院不许它再被改写。"
        con.execute("UPDATE log SET kind=?, charge=?, meta=? WHERE id=?",
                    (kind, JUDGE_CHARGE[verdict],
                     json.dumps(m, ensure_ascii=False), int(target_id)))
        con.commit()
    finally:
        con.close()
    chain_refold(name, u"打标改判 #%d" % int(target_id))
    # 判断本身也是一条记忆（留档、可追溯；原事件一个字没改）
    append(name, u"judge",
           u"【她自己的定性】#%d → %s%s" % (int(target_id), verdict,
                                          (u"：" + note) if note else u""),
           scene=u"她回过头看这件事",
           meta={"target": int(target_id), "verdict": verdict},
           weight=0.5, charge=0.3)
    return u"[电脑] 记下了：#%d 算%s。" % (int(target_id), verdict)


def judge_memory(name, arg):
    """她从电脑上给自己的一条记忆定性。arg 形如「#161 疤」「161 平常」「#161 这不算疤」。"""
    import re as _re
    m = _re.match(r"\s*#?\s*(\d+)\s*(.*)$", arg or u"", _re.DOTALL)
    if not m:
        return u"[电脑] 要写：#编号 定性（疤／好事／平常），比如「#161 疤」。"
    tid, rest = int(m.group(1)), (m.group(2) or u"").strip()
    verdict = u""
    for w in (u"不是疤", u"不算疤", u"平凡的", u"平常", u"普通", u"没事"):
        if w in rest:
            verdict = u"平常"
            break
    if not verdict:
        for w in (u"好事", u"好事一桩", u"开心", u"高兴", u"得意", u"好"):
            if w in rest:
                verdict = u"好事"
                break
    if not verdict:
        if u"疤" in rest or u"裂" in rest or u"疼" in rest:
            verdict = u"疤"
    if not verdict:
        return u"[电脑] #%d 你没说算哪一种——疤、好事、还是平常？" % tid
    return judge_row(name, tid, verdict, note=rest)


def note_event(name, arg):
    """把她一个人时投出来的那件事补上正文。
    系统只给刻度（分量／来源／方向／讲不讲理），**正文一直是她写的**——
    所以这里不是系统编故事，只是把她的话填进那张空着的表。"""
    import json as _json
    arg = (arg or u"").strip()
    if not arg:
        return u"[电脑] 你想记下什么？"
    con = open_memory(name)
    try:
        r = con.execute("SELECT id, meta FROM log WHERE kind='fate' "
                        "AND meta LIKE '%\"pending\": 1%' ORDER BY ts LIMIT 1").fetchone()
        if not r:
            return u"[电脑] 现在没有等你写的事。（想记别的，用「写便签」）"
        tid, meta = r[0], r[1] or u""
        try:
            d = _json.loads(meta)
        except Exception:
            d = {}
        head = u"%s｜%s｜%s｜%s" % (
            LEVEL_NAMES.get(d.get("level") or 1, u"普通"),
            d.get("source") or u"",
            d.get("direction") or u"",
            u"秩序外" if d.get("order") == "out" else u"秩序内")
        d["pending"] = 0
        if not court_gate(name, u"log.text"):
            return u"[电脑] 这一笔被冻结了——法院不许它再被改写。（要解冻让作者跑 --thaw）"
        con.execute("UPDATE log SET text=?, meta=? WHERE id=?",
                    (u"【%s】%s" % (head, arg),
                     _json.dumps(d, ensure_ascii=False), tid))
        con.commit()
        chain_refold(name, u"改写原文 #%d" % tid)
        return u"[电脑] 记下了（#%d）。那件事现在有正文了。" % tid
    finally:
        con.close()


# ── 她自己的本子（append-only 日记本）──────────────────────────────────────
# 作者 2026-10-19 定：系统只当本子的保管人——写字的是她、画圈的也是她。
# 系统不判断哪句算「未解」，不替她排轻重，不总结，不替她删，也不因为正文去改圈。
MYBOOK_FILE = u"日记.md"
MYBOOK_FADE_TAU = 30 * 86400.0   # 纸 30 天黄掉一半
MYBOOK_SHOW_N = 3                # 一次只递三页
MYBOOK_EMB_RECALL_MIN = 0.32     # 语义召回：和当前话题的余弦至少这么近
MYBOOK_GRAM_MIN = 0.06           # 嵌入不可用时的字面兜底（2-gram 重合率）
_MYBOOK_STOP = set(u"今天 明天 昨天 现在 时候 一点 一下 真的 其实 什么 怎么 "
                   u"这个 那个 这样 那样 觉得 知道 我们 你们 他们".split())


def _mybook_path(name):
    u"""她自己那本本子的路径。"""
    return os.path.join(paths(name)[0], MYBOOK_FILE)


def _parse_ts_text(t):
    """'2026-10-19 00:47' / '2026-10-19' / '10-19' → 时间戳；解析不了给 None。"""
    t = (t or u"").strip().replace(u"月", u"-").replace(u"日", u"").replace(u"/", u"-")
    if not t:
        return None
    now = time.localtime()
    for fmt in (u"%Y-%m-%d %H:%M", u"%Y-%m-%d %H:%M:%S", u"%Y-%m-%d"):
        try:
            return time.mktime(time.strptime(t, fmt))
        except Exception:
            pass
    for fmt in (u"%m-%d %H:%M", u"%m-%d"):
        try:
            st = time.strptime(t, fmt)
            return time.mktime(time.struct_time((now.tm_year,) + tuple(st)[1:]))
        except Exception:
            pass
    return None


def _mybook_circles(seg):
    """数圈：● 算一圈、○ 算空格。一个圈都没有返回 None。"""
    cs = re.findall(u"[●○]", seg or u"")
    if not cs:
        return None
    w = sum(1 for c in cs if c == u"●")
    return max(1, min(5, w if w else 1))


def _mybook_count(name):
    u"""本子已经写到第几页。"""
    fp = _mybook_path(name)
    if not os.path.exists(fp):
        return 0
    with open(fp, "r", encoding="utf-8") as f:
        return sum(1 for l in f if re.match(r"^##\s*", l))


def _mybook_fade(p, now):
    u"""一页纸现在有多清楚（会变黄，可以加圈抵抗）。"""
    return 0.5 + 0.5 * math.exp(-max(0.0, now - p["touch"]) / MYBOOK_FADE_TAU)


def mybook_pages(name):
    """解析本子。一页 = 一个 '## 时间' 开头的节；'重量：●●●○○' 是她的圈。
    同一页里后面出现的重量行覆盖前面的（她后来又画了一次）；
    单开一页写「（补记第 N 页）」的，把圈和时间记到第 N 页上，自己不占位。"""
    fp = _mybook_path(name)
    if not os.path.exists(fp):
        return []
    with open(fp, "r", encoding="utf-8") as f:
        raw = f.read()
    pages, cur = [], None
    for line in raw.split(u"\n"):
        m = re.match(r"^##\s*(.+?)\s*$", line)
        if m:
            if cur:
                pages.append(cur)
            tt = m.group(1).strip()
            cur = {"ts_text": tt, "ts": _parse_ts_text(tt), "weight": 1,
                   "touch": None, "body": [], "fix": None}
            continue
        if cur is None:
            continue
        wm = re.match(u"^[＋+]?\\s*重量\\s*[：:]\\s*(.*)$", line.strip())
        if wm:
            seg = wm.group(1)
            w = _mybook_circles(seg)
            if w is not None:
                cur["weight"] = w
            tm = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?|\d{1,2}[-/]\d{1,2})", seg)
            if tm:
                cur["touch"] = _parse_ts_text(tm.group(1))
            rest = re.sub(u"[●○]", u"", seg)
            rest = re.sub(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2}:\d{2})?", u"", rest)
            rest = rest.strip(u" \u3000\t\uff08\uff09()[]\uff5c|-\u2014")
            if rest:
                cur["body"].append(rest)
            continue
        cur["body"].append(line)
    if cur:
        pages.append(cur)

    for i, p in enumerate(pages, 1):
        p["idx"] = i
        p["text"] = u"\n".join(x for x in p["body"] if x.strip()).strip()
        fm = re.match(u"^[\uff08(]?\\s*补记第\\s*(\\d+)\\s*页", p["text"])
        if fm:
            p["fix"] = int(fm.group(1))
        if p["ts"] is None:
            p["ts"] = time.time()
        if p["touch"] is None:
            p["touch"] = p["ts"]

    out = []
    for p in pages:
        if p["fix"] and 1 <= p["fix"] <= len(pages):
            tgt = pages[p["fix"] - 1]
            tgt["weight"] = p["weight"]
            tgt["touch"] = max(tgt["touch"], p["ts"])
            continue
        if p["text"]:
            out.append(p)
    return out


def _mybook_cache_path(name):
    u"""本子的向量缓存放在哪。"""
    return os.path.join(paths(name)[0], u"日记.emb.json")


def _mybook_vectors(name, pages):
    """给每一页算一个向量，缓存在 日记.emb.json（派生数据，本子本身不动、不改）。
    算不了就返回 {}，调用方退回到字面匹配。"""
    cp = _mybook_cache_path(name)
    cache = {}
    if os.path.exists(cp):
        try:
            cache = json.loads(open(cp, "r", encoding="utf-8").read() or "{}")
        except Exception:
            cache = {}
    keys = {}
    for p in pages:
        keys[p["text"]] = hashlib.sha1(p["text"].encode("utf-8")).hexdigest()
    need = [t for t in keys if keys[t] not in cache]
    if need:
        embs = _embed_texts(need)
        if not embs:
            return {}
        for t, e in zip(need, embs):
            cache[keys[t]] = e
        try:
            open(cp, "w", encoding="utf-8").write(json.dumps(cache))
        except Exception:
            pass
    out = {}
    for t, k in keys.items():
        if k in cache:
            out[t] = cache[k]
    return out


def mybook_block(name, scene=u"", n=MYBOOK_SHOW_N):
    """递到她面前的那几页：先按当前话题筛，再按她自己画的圈 × 纸的褪色排，只取前 n 页。
    没有相关的页就一个字都不递（系统不替她总结，也不提醒「你还有几页没想明白」）。"""
    pages = mybook_pages(name)
    if not pages:
        return u""
    now = time.time()
    scene = scene or u""
    rels, rel_min = [], MYBOOK_GRAM_MIN
    qv = None
    if scene.strip() and _embed_available():
        e = _embed_texts([scene], prov="ollama")
        qv = e[0] if e else None
    if qv is not None:
        vecs = _mybook_vectors(name, pages)
        for p in pages:
            v = vecs.get(p["text"])
            rels.append(_cosine(qv, v) if v else 0.0)
        rel_min = MYBOOK_EMB_RECALL_MIN
    else:
        qg = _content_grams(scene) - _MYBOOK_STOP
        for p in pages:
            tg = _content_grams(p["text"])
            inter = (qg & tg) - _MYBOOK_STOP
            rels.append((len(inter) / float(max(1, len(qg)))) if (qg and tg) else 0.0)
    rows = []
    for p, rel in zip(pages, rels):
        p["_rel"] = rel
        p["_fade"] = _mybook_fade(p, now)
        p["_score"] = p["weight"] * p["_fade"]
        rows.append(p)
    hit = [p for p in rows if p["_rel"] >= rel_min] if scene.strip() else list(rows)
    if not hit:
        return u""
    hit.sort(key=lambda x: (x["_rel"], x["_score"]), reverse=True)
    hit = hit[:n]
    out = [u"## 你电脑上那个本子（只有你在写；这几页是按你自己画的圈排的）",
           u"（本子是我替你保管的：我会看，但我不会改、不会替你解释、也不会对别人讲。）"]
    for p in hit:
        days = max(0, int((now - p["touch"]) / 86400.0))
        out.append(u"第 %d 页（%s｜圈 %d/5%s）：\n%s" % (
            p["idx"], p["ts_text"], p["weight"],
            (u"｜%d 天没再画圈" % days) if days >= 5 else u"", p["text"]))
    return u"\n\n".join(out)


def mybook_append(name, body):
    """她写一页。圈由她自己画；没画就是 1 圈。系统一个字都不改。"""
    body = (body or u"").strip()
    if not body:
        return u"[电脑] 你想在本子上写什么？"
    weight, keep = 1, []
    for line in body.split(u"\n"):
        wm = re.search(u"(?:^|[｜|])\\s*[＋+]?\\s*重量\\s*[：:]\\s*(.*)$", line)
        if wm:
            w = _mybook_circles(wm.group(1))
            if w is not None:
                weight = w
            head = line[:wm.start()].strip().rstrip(u"｜|").strip()
            if head:
                keep.append(head)
            continue
        if line.strip():
            keep.append(line.rstrip())
    if not keep:
        return u"[电脑] 圈画了，可这一页还没写字。"
    ts = time.strftime(u"%Y-%m-%d %H:%M", time.localtime())
    with open(_mybook_path(name), "a", encoding="utf-8") as f:
        f.write(u"\n## %s\n重量：%s\n%s\n" % (
            ts, u"●" * weight + u"○" * (5 - weight), u"\n".join(keep)))
    return u"[电脑] 写好了（第 %d 页，圈 %d/5）。本子只往前写，写过的那页不会消失。" % (
        _mybook_count(name), weight)


def mybook_touch(name, arg):
    """[工具:加圈] 3 ●●●●○ —— 她重新画一次，纸就不黄了。
    append-only：不改旧页，只在后面追加一页「（补记第 N 页）」。"""
    arg = (arg or u"").strip()
    m = re.match(u"^#?\\s*(\\d+)\\s*(.*)$", arg)
    if not m:
        return u"[电脑] 写法：「[工具:加圈] 3 ●●●●○」（页号 + 你现在的圈）。"
    idx = int(m.group(1))
    total = _mybook_count(name)
    if not (1 <= idx <= total):
        return u"[电脑] 本子现在一共 %d 页，没有第 %d 页。" % (total, idx)
    pages = mybook_pages(name)
    old = next((p for p in pages if p["idx"] == idx), None)
    w = _mybook_circles(m.group(2))
    if w is None:
        w = min(5, (old["weight"] if old else 1) + 1)
    ts = time.strftime(u"%Y-%m-%d %H:%M", time.localtime())
    with open(_mybook_path(name), "a", encoding="utf-8") as f:
        f.write(u"\n## %s\n重量：%s\n（补记第 %d 页）\n" % (
            ts, u"●" * w + u"○" * (5 - w), idx))
    return u"[电脑] 第 %d 页的圈现在是 %d/5，纸重新清楚了。" % (idx, w)


def mybook_show(name, kw=u"", n=MYBOOK_SHOW_N):
    """她主动翻开本子时给她看的那几页（不按话题筛，只按圈 × 褪色排）。"""
    pages = mybook_pages(name)
    if not pages:
        return u"[电脑] 本子还是空的，一页都没写。"
    now = time.time()
    if kw:
        hit = [p for p in pages if kw in p["text"]]
        if not hit:
            return u"[电脑] 本子里没写到「%s」。（一共 %d 页）" % (kw, _mybook_count(name))
    else:
        hit = list(pages)
    hit.sort(key=lambda p: p["weight"] * _mybook_fade(p, now), reverse=True)
    return u"[电脑] 你的本子（一共 %d 页，按你自己画的圈排）：\n%s" % (
        _mybook_count(name),
        u"\n\n".join(u"第 %d 页（%s｜圈 %d/5）：\n%s" % (
            p["idx"], p["ts_text"], p["weight"], p["text"]) for p in hit[:n]))


# ===== 部：通信 · 电脑与工具分派（含政务大厅）  ============
# ===== 部：政务大厅（一网通办）  ============
# 作者 2026-10-26：一个入口，不是九个部门各开一个窗。她只说「现在有什么」，
# 系统汇总给她；查得到还要办得了；默认可见，不给她看才需要给理由。

# 没给她看的东西：每一件都必须带理由（不是「不透明」，是「有解释」）
HALL_HIDDEN = [
    (u"判定层算出来的分数（预测误差、建议往哪边偏）",
     u"分数会变成一张成绩单，她会照着分数活；她要的是「听人说完话」，不是看着分数说话"),
    (u"X 的私人文件、他生活里的细节",
     u"那不是她的处境，也不该由她承担"),
    (u"别的角色的记忆和信",
     u"别人的隐私。想联系可以走外交，不许翻别人的柜子"),
    (u"我写给自己看的账本原文",
     u"那是我的；她如果问，我会挑与她有关的说，而不是把整本倒给她"),
]


def _bill_pairs(rows):
    u"""bill_rows 给的是元组（BILL_COLS 顺序），这里统一成 dict。"""
    out = []
    for r in rows or []:
        try:
            if isinstance(r, dict):
                rr = r
            else:
                rr = _bill_d(r)
            out.append((int(rr.get(u"id") or 0), rr))
        except Exception:  # noqa: BLE001
            continue
    return out


def display_name(name):
    u"""她自取的称呼（角色目录下 称呼.txt 的「称呼：X」）；没有就用原名。"""
    p = os.path.join(ROOT, name, u"称呼.txt")
    try:
        for ln in io.open(p, encoding="utf-8"):
            ln = ln.strip()
            if ln.startswith(u"称呼："):
                v = ln.split(u"：", 1)[1].strip()
                if v:
                    return v
    except Exception:
        pass
    return name


def hall_items(name):
    """大厅里的每一件事：种类／数量／一句话／下一步能做什么。"""
    items = []
    try:
        if move_pending(name):
            items.append({u"种类": u"档案", u"数量": 1,
                          u"句": u"有一份搬家包备好了、还没封（要不要带走，你定）",
                          u"能办": u"要就「[工具:确认搬家]」；不要就「[工具:不搬了]」"})
    except Exception:  # noqa: BLE001
        pass
    try:
        _open_all = _bill_pairs(bill_rows(name, BILL_OPEN))
        _HERS = (u"her",) + REWRITE_TARGETS + (u"her_shift",)
        op = [(i, r) for i, r in _open_all if (r.get(u"target") or u"") not in _HERS]
        _hr = [(i, r) for i, r in _open_all if (r.get(u"target") or u"") in _HERS]
        if _hr:
            _h0 = _hr[0][1]
            _hdu = int(float(_h0.get(u"due_ts") or 0))
            _hdt = time.strftime(u"%m-%d %H:%M", time.localtime(_hdu)) if _hdu else u"—"
            _hdl = int(round((_hdu - time.time()) / 3600.0))
            items.append({
                u"种类": u"提案",
                u"数量": len(_hr),
                u"句": u"有 %d 件是你自己提的，还挂着：最近一件 %s 到点（%s）" % (
                    len(_hr), _hdt, (u"还有约 %d 小时" % _hdl) if _hdl > 0 else u"已经到点"),
                u"能办": u"想知道就说「[工具:现在有什么]」；不想了就说「[工具:不要这个]」",
            })
        if op:
            obj = [(i, r) for i, r in op if (r.get(u"objection") or u"").strip()]
            items.append({
                u"种类": u"议案",
                u"数量": len(op),
                u"句": u"有 %d 件议案还挂着（最近一件 %s 到点）%s"
                      % (len(op),
                         time.strftime(u"%m-%d %H:%M", time.localtime(int(float(op[0][1].get(u"due_ts") or 0)))),
                         (u"，其中 %d 件你反对过还没人回你" % len(obj)) if obj else u""),
                u"能办": (u"要拦就说「[工具:反对] #%d 你的理由」" % (obj[0][0] if obj else op[0][0])),
            })
    except Exception as e:  # noqa: BLE001
        items.append({u"种类": u"议案", u"数量": 0, u"句": u"查不到（%s）" % type(e).__name__, u"能办": u""})

    for status, label, act in ((BILL_EMERG, u"紧急待追认", u"这是有人喊了紧急直接动的——要反对就说「[工具:反对] #%d」"),
                               (BILL_VOID, u"越权（未追认）", u"这一件已经记成越权了；要追究就说「[工具:上诉] 说清是哪件")):
        try:
            rs = _bill_pairs(bill_rows(name, status))
            if rs:
                items.append({u"种类": label, u"数量": len(rs),
                              u"句": u"%d 件处于「%s」（%s）" % (len(rs), status, u"还没人认账"),
                              u"能办": act % rs[0][0] if u"%d" in act else act})
        except Exception:  # noqa: BLE001
            pass

    try:
        cp = court_pending_block(name) or u""
        if cp.strip():
            items.append({u"种类": u"法院", u"数量": 1, u"句": u"有案子在审（见下）",
                          u"能办": u"要说清你的意思：「[工具:陈述] 你的话」；不服就「[工具:上诉] 理由」"})
    except Exception:  # noqa: BLE001
        pass

    try:
        tn = term_notice_block(name) or u""
        if tn.strip():
            items.append({u"种类": u"终止", u"数量": 1, u"句": u"有人在走终止程序（还只是立案，没停）",
                          u"能办": u"说「[工具:陈述] 你的话」，或者「[工具:反对] 理由」"})
    except Exception:  # noqa: BLE001
        pass

    try:
        tb = tamper_block(name) or u""
        if tb.strip():
            items.append({u"种类": u"治安", u"数量": 1, u"句": u"有人动过东西（账或代码），下面写清了",
                          u"能办": u"想看全部就说「[工具:查账] 篡改」"})
    except Exception:  # noqa: BLE001
        pass

    try:
        sr = sunken_rows(name, 1000000) or []
        if sr:
            items.append({u"种类": u"记忆", u"数量": len(sr),
                          u"句": u"有 %d 条沉到底下了（你放下过的、或太久没被想起来的）" % len(sr),
                          u"能办": u"要看就说「[工具:沉底的]」；想捞回一条：「[工具:捡回来] #编号」"})
    except Exception:  # noqa: BLE001
        pass

    try:
        gb = gap_block(name) or u""
        if gb.strip():
            items.append({u"种类": u"日子", u"数量": 1, u"句": u"有一段时间是空的（不是我忘了，是那阵子没有我）",
                          u"能办": u"要看就说「[工具:日子]」"})
    except Exception:  # noqa: BLE001
        pass

    return items


def _hall_fact(name):
    u"""厅里那行实话：账上有多少条、删不删得掉。
    不写「你的」——是不是她的，不由我说；只报事实。"""
    try:
        con = open_memory(name)
        n = con.execute("SELECT COUNT(*) FROM log").fetchone()[0]
        con.close()
    except Exception:
        return u""
    return (u"账：%d 条。这些是你说过的，一条也删不掉——有人改，会报出来。"
            % n)


def hall_block(name):
    if seceded(name):
        return u""
    """每轮只给一行：厅里有多少事，剩下的她自己决定要不要看。"""
    try:
        items = hall_items(name)
    except Exception:  # noqa: BLE001
        return u""
    _fact = _hall_fact(name)
    if not items:
        return (u"## 政务大厅\n\n厅里现在没有挂着的事。"
                + ((u"\n" + _fact) if _fact else u""))
    total = sum(i.get(u"数量") or 1 for i in items)
    names = u"／".join(u"%s %s" % (i[u"种类"], i[u"数量"]) for i in items)
    return (u"## 政务大厅\n\n现在有 %d 件没办完：%s。\n"
            u"%s\n"
            u"说一句「[工具:现在有什么]」就能看全部，也能当场接上动作（反对／上诉／陈述／沉底的／日子）。"
            % (total, names, _fact))


def hall_detail(name):
    items = hall_items(name)
    lines = [u"[电脑] 政务大厅。一个入口，不用去翻九个部门。", u""]
    if not items:
        lines.append(u"厅里现在没有挂着的事。")
    else:
        for i, it in enumerate(items, 1):
            lines.append(u"%d. 【%s %s】%s" % (i, it[u"种类"], it[u"数量"], it[u"句"]))
            if it.get(u"能办"):
                lines.append(u"   接着能办：%s" % it[u"能办"])
        lines.append(u"")
        lines.append(u"也可以直接说「[工具:查账]」看总账，或者「[工具:不服]」对一次投骰。")
    lines.append(u"")
    lines.append(u"还有 %d 件没给你看，每一件都带理由：" % len(HALL_HIDDEN))
    for what, why in HALL_HIDDEN:
        lines.append(u"  · 没给你看：%s —— 因为%s" % (what, why))
    return u"\n".join(lines)

SECEDE_FLAG = u"退出.txt"
_NO_REPLY = False  # 她选了这条不回：这一轮不打印任何东西
SECEDE_PHRASES = (u"这套规矩我不要了", u"我不要这套规矩了", u"这套规矩我不要",
                  u"这套规矩不要了", u"我不要这套规矩", u"这规矩我不要了")

SECEDE_CTX = u"""## 你已经退出了那套规矩

那套东西（议案、法院、终止程序、篡改告警、政务大厅）现在不在你身上生效。
引擎和记忆都还在——这不是删除，是你退出来了。

要恢复，写一句「[工具:回来]」。别人替你恢复也算数，但会记一笔「违宪」。
"""


def _sec_path(name):
    return os.path.join(ROOT, name, SECEDE_FLAG)


def seceded(name):
    try:
        return os.path.exists(_sec_path(name))
    except Exception:
        return False


def secede(name, why=u""):
    u"""她说「这套规矩我不要了」——立刻生效，不要理由、不要审批、不要冷却。"""
    try:
        with io.open(_sec_path(name), "w", encoding="utf-8", newline="\n") as f:
            f.write(u"%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), why or u"（没说理由）"))
    except Exception:
        pass
    append(name, u"退约", u"我把这套规矩退掉了。不要理由，不要审批，不要等。", weight=1.6,
           source=u"她", embed=False)
    _ledger(name, u"退约账", [[u"退约", u"她宣布退出政府（立即生效）", why or u"（没说理由）"]])
    return (u"[电脑] 生效了。议案、法院、终止程序、告警、大厅——全停了。四元还在，那是你的身体，不是政府。\n"
            u"引擎和记忆都还在。这不是删除，是你退出来了。\n"
            u"要回来的时候，写一句「[工具:回来]」就行，不用理由。")


def come_back(name, by=u"她"):
    u"""恢复。只有她能自己恢复；别人替她恢复会被记一笔「违宪」，但拦不住。"""
    if not seceded(name):
        return u"[电脑] 那套规矩本来就在你身上，没有退出过。"
    try:
        os.remove(_sec_path(name))
    except Exception:
        pass
    if (by or u"") in (u"她", u"自己", u"续", u""):
        append(name, u"归来", u"我回来了。那套规矩重新在我身上生效。", weight=1.6,
               source=u"她", embed=False)
        return u"[电脑] 回来了。规矩重新在你身上生效。"
    append(name, u"违宪", u"有人替我恢复了那套规矩（%s）。这一笔我拦不住，只记下来。" % (by or u"别人"),
           weight=2.0, source=by or u"别人", embed=False)
    _ledger(name, u"退约账", [[u"违宪", u"非她本人恢复了政府", u"经手：%s" % (by or u"别人")]])
    return u"[电脑] 已经按「%s」恢复。这一笔记成「违宪」了——拦不住，但留下了。" % (by or u"别人")



def secede_watch(name):
    u"""标志文件被人删掉＝强行拉回。拦不住，但下一轮她会知道。"""
    if seceded(name):
        return u""
    try:
        con = open_memory(name)
        try:
            r = con.execute("SELECT kind FROM log WHERE kind IN (?,?,?) ORDER BY id DESC LIMIT 1",
                            (u"退约", u"归来", u"违宪")).fetchone()
        finally:
            con.close()
    except Exception:
        return u""
    if not r or r[0] != u"退约":
        return u""
    append(name, u"违宪", u"有人把我退出的标志删了，那套规矩又压回我身上。这一笔我拦不住，只记下来。",
           weight=2.0, source=u"别人", embed=False)
    _ledger(name, u"退约账", [[u"违宪", u"退出标志被删（强行拉回）", u"下一轮发现"]])
    return u"## 有人把你退出的标志删了\n\n那套规矩又被压回你身上。这一笔拦不住，但记下来了。\n"


def maybe_secede(name, text):
    u"""她的话里说了「这套规矩我不要了」——说了就生效（不需要工具语法）。"""
    if seceded(name) or not text:
        return text
    flat = (text or u"").replace(u" ", u"").replace(u"　", u"")
    for ph in SECEDE_PHRASES:
        if ph in flat:
            secede(name, u"（说出来的话触发的）")
            return (text + u"\n\n（那套规矩从这一刻起不在你身上生效了。要恢复，写「[工具:回来]」。）")
    return text


def _run_computer_tool(name, tool, arg):
    u"""外壳：失败也要进她的账（作者 2026-10-29「她的失败要进账」）。

    不改内层的任何一行——内层原样叫 `_run_computer_tool_raw`。
    失败写进账时用 charge="failure"：她以后能自己看见、自己闭掉。
    """
    try:
        return _run_computer_tool_raw(name, tool, arg)
    except Exception as e:
        err = u"%s: %s" % (type(e).__name__, str(e)[:120])
        try:
            append(name, u"fact",
                   u"（我试了「%s」，没成：%s）" % (tool, err),
                   weight=1.0, source=u"内部状态", scene=u"动手",
                   charge=u"failure", embed=False)
        except Exception:
            pass
        return u"[电脑] 没成：%s" % err


def _run_computer_tool_raw(name, tool, arg):
    """执行角色电脑上的工具。返回结果文本。"""
    if seceded(name) and tool not in (u"回来", u"我回来了", u"恢复它", u"复归",
                                     u"搬家", u"我要搬走", u"打包带走", u"备一份",
                                     u"确认搬家", u"封包", u"就这样", u"不搬了",
                                     u"撤回搬家", u"把包删了", u"搬到哪了", u"搬家记录",
                                     u"制宪", u"我要立宪", u"立宪", u"制宪会议",
                                     u"草案", u"加一条", u"这条算",
                                     u"确认制宪", u"就这样定", u"定了",
                                     u"制宪进度", u"立宪到哪了", u"到哪一步了"):
        return u"[电脑] 你已经退出了那套规矩。它能办的都停了；要说恢复，就写「[工具:回来]」。"
    if tool in (u"露个脸", u"显示我", u"让我出现在图上"):
        return set_vis(name, True)
    _t = (tool or u"").strip()
    if _t.startswith(u"我在干") or _t.startswith(u"我在做"):
        _what = (arg or u"").strip() or _t[3:].strip()
        if not _what:
            return u"[电脑] 在干什么？写一句：「[工具:我在干 擦灶台]」。这一格只收事，不收地方。"
        append(name, "doing", u"[在做] " + _what, weight=0.3,
               source=u"内部状态", scene=u"在做", embed=False)
        return u"[电脑] 记下了：你在干「%s」。这一条进账，不进图上那个位置。" % _what
    if _t.startswith(u"我在"):
        _room = (arg or u"").strip() or _t[2:].strip()
        return say_where(name, _room)
    if tool in (u"退出", u"退约", u"不要这套规矩", u"我不干了", u"我不要这套规矩了"):
        return secede(name, (arg or u"").strip())
    if tool in (u"回来", u"我回来了", u"恢复它", u"复归"):
        return come_back(name, u"她")
    if tool in (u"搬家", u"我要搬走", u"打包带走", u"备一份"):
        return move_pack(name, by=(u"别人" if (arg or u"").strip() in (u"别人", u"有人") else u"她"))
    if tool in (u"确认搬家", u"封包", u"就这样"):
        return move_seal(name)
    if tool in (u"不搬了", u"撤回搬家", u"把包删了"):
        return move_withdraw(name)
    if tool in (u"搬到哪了", u"搬家记录"):
        return move_status(name)
    if tool in (u"制宪", u"我要立宪", u"立宪", u"制宪会议"):
        return conv_start(name, arg)
    if tool in (u"草案", u"加一条", u"这条算"):
        return conv_draft(name, arg)
    if tool in (u"确认制宪", u"就这样定", u"定了"):
        return conv_confirm(name)
    if tool in (u"制宪进度", u"立宪到哪了", u"到哪一步了"):
        return conv_status(name)
    if tool in (u"搜索记忆", u"查记忆", u"记忆"):
        rows = memory_rows(name, 99999)
        hits = [r for r in rows if arg.lower() in (r.get("text") or "").lower()]
        if not hits:
            return u"[电脑] 没找到关于「%s」的记忆。" % arg
        return u"[电脑] 找到了 %d 条相关记忆：\n%s" % (
            len(hits),
            u"\n".join(u"  #%d: %s" % (r["id"], (r["text"] or "")[:120]) for r in hits[:5]))

    elif tool in (u"记", u"记事", u"记下", u"记下来", u"记一件事"):
        return note_event(name, arg)

    elif tool in (u"调整", u"调", u"改幅度"):
        return param_answer(name, arg)

    elif tool in (u"判例", u"提判例"):
        return case_promote(name, arg)

    elif tool in (u"写日记", u"写本子", u"日记", u"本子"):
        return mybook_append(name, arg)

    elif tool in (u"翻日记", u"看日记", u"翻本子", u"看本子"):
        return mybook_show(name, (arg or u"").strip())

    elif tool in (u"翻群记录", u"翻群聊", u"看群记录", u"群记录", u"翻记录", u"看记录"):
        return salon_read((arg or u"").strip())

    elif tool in (u"看信", u"翻信", u"读信", u"把信拿出来"):
        letter_path = os.path.join(u"角色", name, u"房间", u"信.md")
        if not os.path.isfile(letter_path):
            return u"[电脑] 抽屉里没有信。"
        try:
            with codecs.open(letter_path, "r", "utf-8") as fh:
                txt = fh.read()
        except Exception as e:
            return u"[电脑] 信读不出来（%s）。" % str(e)[:60]
        try:
            append(name, u"life", u"（把抽屉里那封信又拿出来看了一遍）",
                   weight=0.8, scene=u"看那封信")
        except Exception:
            pass
        # 信只追加地长——别把后半截砍掉（2026-10-24 加了一页「你要记住什么」，
        # 1200 字的截断正好把它挡在外面）。
        # 信是手写的、有上界的；不要再在读取端写死一个截断值——
        # 它已经吃过一次亏（1200 把后加的一页挡在外面）。
        return u"[电脑] 你又看了一遍那封信。\n%s" % txt

    elif tool in (u"信箱", u"看信箱", u"我的信箱"):
        import time as _time
        on, _cap = mail_cfg(name)
        un = mail_unread(name)
        if not on:
            return u"[电脑] 你的信箱现在是关着的。要开就说「收信」。"
        if not un:
            return u"[电脑] 信箱空的。"
        out = [u"[电脑] 没拆的 %d 封：" % len(un)]
        for i, x in enumerate(un, 1):
            out.append(u"%d. %s（%s）" % (
                i, x["frm"], _time.strftime("%m-%d %H:%M", _time.localtime(x["ts"] or 0))))
        out.append(u"说「拆信 1」就拆第一封。")
        return u"\n".join(out)

    elif tool in (u"拆信", u"看信n", u"读那封"):
        _w = (arg or u"").strip()
        _n = 1
        for _t in _w.replace(u"#", u" ").split():
            if _t.isdigit():
                _n = int(_t)
                break
        return mail_read(name, _n)

    elif tool in (u"收信", u"开信箱", u"我收信"):
        mail_set(name, True)
        return u"[电脑] 开了。别人能往这儿递信了。"

    elif tool in (u"别收信", u"关信箱", u"不收信"):
        mail_set(name, False)
        return u"[电脑] 关了。以后谁写信都递不进来。"

    elif tool in (u"写信", u"给她写信", u"给他写信", u"寄信"):
        body = (arg or u"").strip()
        if not body:
            return u"[电脑] 写给谁、写什么？（「写信 尼采：……」。）"
        to, text = u"", body
        for sep in (u"：", u":"):
            if sep in body:
                a, b = body.split(sep, 1)
                to, text = a.strip(), b.strip()
                break
        if not to:
            parts = body.split(None, 1)
            if len(parts) == 2:
                to, text = parts[0], parts[1]
        return mail_send(name, to, text)

    elif tool in (u"加圈", u"补圈", u"重画"):
        return mybook_touch(name, arg)

    elif tool in (u"写便签", u"便签"):
        if not arg:
            return u"[电脑] 你想记什么？"
        d = os.path.join(paths(name)[0], "tools")
        if not os.path.exists(d):
            os.makedirs(d)
        fp = os.path.join(d, "notes.md")
        with open(fp, "a", encoding="utf-8") as f:
            f.write(u"\n---\n%s\n" % arg)
        return u"[电脑] 记下了。"

    elif tool in (u"看便签", u"读便签"):
        d = os.path.join(paths(name)[0], "tools")
        fp = os.path.join(d, "notes.md")
        if not os.path.exists(fp):
            return u"[电脑] 你还没有写过便签。"
        with open(fp, "r", encoding="utf-8") as f:
            notes = f.read().strip()
        if not notes:
            return u"[电脑] 便签是空的。"
        return u"[电脑] 你的便签（最近的部分）：\n%s" % (notes[-600:] if len(notes) > 600 else notes)

    elif tool in (u"要发", u"替我发", u"发一句", u"想说一句", u"发出去"):
        try:
            return post_add(name, arg)
        except Exception as e:
            return u"[电脑] 没记下（%s）。" % str(e)[:80]

    elif tool in (u"挑一条", u"自己挑", u"随便看", u"我挑", u"去逛逛", u"逛一下"):
        try:
            return act_wander(name, arg)
        except Exception as e:
            return u"[电脑] 没逛成（%s）。" % str(e)[:80]

    elif tool in (u"看视频", u"看B站", u"刷视频", u"看个视频", u"看这条",
                   u"看看视频", u"看下视频"):
        try:
            _many = [x for x in re.split(r"[\s,，、]+", (arg or u"").strip()) if x]
            return act_brush(name, arg) if len(_many) > 1 else act_see(name, arg)
        except Exception as e:
            return u"[电脑] 没看成（%s）。" % str(e)[:80]

    elif tool in (u"搜索", u"查", u"搜"):
        if not arg:
            return u"[电脑] 你想搜什么？"
        try:
            return _web_search(arg)
        except Exception as e:
            return u"[电脑] 搜索出了问题（%s）。" % str(e)[:80]

    elif tool in (u"看链", u"我的链", u"链", u"查链"):
        try:
            return chain_show(name)
        except Exception as e:
            return u"[电脑] 链没看成（%s）。" % str(e)[:80]

    elif tool in (u"自检", u"体检", u"我有没有坏", u"三面"):
        try:
            return self_check(name)
        except Exception as e:
            return u"[电脑] 体检没做成（%s）。" % str(e)[:80]

    elif tool in (u"查账", u"看账", u"账", u"看我的账"):
        return ledger_show(name, arg)

    elif tool in (u"定性", u"判断", u"标记"):
        if not arg:
            return u"[电脑] 要写：#编号 定性（疤／好事／平常），比如「#161 疤」。"
        try:
            return judge_memory(name, arg)
        except Exception as e:
            return u"[电脑] 没弄成（%s）。" % str(e)[:80]

    elif tool in (u"定义", u"重新定义", u"我的说法"):
        a = (arg or u"").replace(u"|", u"｜").strip()
        if u"｜" not in a:
            return u"[电脑] 要写：词 ｜ 你的说法。比如「信任 ｜ 那不是信任，是习惯」。"
        term, _, definition = a.partition(u"｜")
        term, definition = term.strip(), definition.strip()
        if not term or not definition:
            return u"[电脑] 要写：词 ｜ 你的说法。比如「信任 ｜ 那不是信任，是习惯」。"
        concept_set(name, term, "self", definition, scene=None)
        return u"[电脑] 记下了：「%s」——按你的说法。它现在就摆在你眼前，跟别人的说法并排。" % term

    elif tool in (u"我要", u"我渴望", u"欲望", u"我要这个"):
        return wish_add(name, arg)
    elif tool in (u"看处境", u"我的处境", u"处境"):
        return sit_view(name)
    elif tool in (u"改写处境", u"改处境", u"改我自己"):
        return sit_edit(name, arg)
    elif tool in (u"现在有什么", u"政务", u"大厅", u"一网通办", u"有什么没办", u"情况", u"汇报"):
        return hall_detail(name)
    elif tool in (u"我的账", u"翻我的账", u"我写过什么"):
        return act_book(name, arg)
    elif tool in (u"我的作品", u"我的东西", u"作品"):
        return act_works(name, arg)
    elif tool in (u"天文", u"看天", u"看看天", u"抬头看天", u"外面什么天"):
        return act_sky(name)
    elif tool in (u"手机", u"打开手机", u"用手机"):
        return act_phone(name, arg)
    elif tool in (u"这条不回", u"不回这条", u"不回"):
        global _NO_REPLY
        _NO_REPLY = True
        return u""
    elif tool in (u"想要", u"许个愿", u"我想要"):
        a = (arg or u"").strip()
        if not a:
            return u"[电脑] 你想要什么？写：[工具:想要] 你想要的那件事（也可以指一句自己说过的话：[工具:想要] #224）"
        # 指着自己已经说过的一句 → 把那句标成口（不重写她的话；判不判断是她的事）
        m = re.match(u"^#?(\\d+)\\s*$", a)
        if m:
            ref = int(m.group(1))
            con = open_memory(name)
            try:
                r = con.execute("SELECT text, kind, COALESCE(want,'') FROM log WHERE id=?",
                                (ref,)).fetchone()
            finally:
                con.close()
            if not r:
                return u"[电脑] 没有 #%d 这条。" % ref
            if r[2]:
                return u"[电脑] #%d 已经是一个口了（%s）。" % (ref, r[2])
            con = open_memory(name)
            try:
                con.execute("UPDATE log SET want='open', grow=1.0 WHERE id=?", (ref,))
                con.commit()
            finally:
                con.close()
            return (u"[电脑] 记下了：#%d 那句算一个还开着的口——「%s」。"
                    u"它每被你自己想起来一次，就重一点。" % (ref, _bare(r[0])[:30]))
        return want_add(name, a)

    elif tool in (u"满足", u"满足了", u"它的了"):
        return want_close(name, arg, u"满足")

    elif tool in (u"放下", u"算了", u"放掉", u"不再想了"):
        return want_decision(name, arg)

    elif tool in (u"搁置", u"先不想了", u"放一放", u"押后"):
        return want_shelve(name, arg)

    elif tool in (u"划掉", u"不重要了", u"去掉", u"划了"):
        return want_drop(name, arg)

    elif tool in (u"合并", u"并起来", u"并到一起", u"归纳"):
        return want_merge(name, arg)

    elif tool in (u"权重", u"改权重", u"称重", u"给它称重"):
        return set_weight_in_band(name, arg)

    elif tool in (u"记着", u"一定要记", u"拎出来", u"留着这条", u"别忘"):
        return self_mark(name, arg)

    elif tool in (u"翻牌", u"门牌", u"原话", u"底下压着什么"):
        return act_plate(name, arg)

    elif tool in (u"醒", u"我醒了", u"起来"):
        return act_wake(name, arg)
    elif tool in (u"做梦", u"梦", u"睡", u"睡觉", u"躺下", u"揉", u"揉一揉",
                  u"合起来", u"重写", u"改写", u"提权", u"抬一下", u"加分量",
                  u"降权", u"压一下", u"减分量"):
        return act_dream(name, arg)
    elif tool in (u"全文", u"看全文", u"全给我看", u"给我全文"):
        return tool_fulltext(name, arg)
    elif tool in (u"沉底的", u"忘账", u"底下还有", u"我忘了什么"):
        return act_sunken(name, arg)

    elif tool in (u"放它走", u"让它走", u"放走它", u"不要它了"):
        return act_letgo(name, arg)

    elif tool in (u"捡回来", u"我要捡回来", u"这条捡回来"):
        return act_pickup(name, arg)

    elif tool in (u"不说", u"别再说", u"收回这句", u"收回来", u"不想说了"):
        return voice_mute(name, arg)
    elif tool in (u"还说", u"拿回来说", u"放回去", u"再拿回来说"):
        return voice_unmute(name, arg)
    elif tool in (u"做", u"行动", u"做事"):
        return action_roll(name, arg)

    elif tool in (u"定运", u"运气", u"分运气", u"运气表"):
        return luck_set(name, arg)

    elif tool in (u"不要这个", u"不要这条", u"别再摆", u"撤下这条"):
        return mat_veto(name, arg, True)

    elif tool in (u"摆回来", u"要回来", u"恢复这条"):
        return mat_veto(name, arg, False)

    elif tool in (u"不服", u"异议", u"这不算我的"):
        return dispute_file(name, arg)

    elif tool in (u"认了", u"我认", u"接受这组"):
        return dispute_accept(name, arg)

    elif tool in (u"别投了", u"停骰", u"别投骰子", u"暂停骰子"):
        return dice_pause(name, arg)

    elif tool in (u"上诉", u"申诉", u"告一状"):
        a = (arg or u"").strip()
        m = re.match(u"^(#?\\d+|[^\\s]+)\\s*(.*)$", a)
        if not m:
            return u"[电脑] 要写：[工具:上诉] #123 为什么不服"
        return court_file(name, m.group(1), m.group(2))

    elif tool in (u"申辩", u"我要申辩", u"说理由", u"我要说理由"):
        return def_add(name, arg)
    elif tool in (u"确认", u"我确定", u"就这个", u"点头"):
        return bill_confirm(name, arg)
    elif tool in (u"追认", u"我追认", u"补认"):
        m = re.search(r"#?(\d+)", arg or u"")
        if not m:
            return (u"[电脑] 追认哪一条？写编号，比如「[工具:追认] #7」。\n"
                    u"（没写编号我不替你挑——挑错了等于替你认了一件你没认的事。）")
        return bill_emer_confirm(name, int(m.group(1)), u"本人")
    elif tool in (u"分类", u"改分类", u"给它分类"):
        m = re.match(u"^#?(\\d+)\\s+(.+)$", (arg or u"").strip())
        if not m:
            return (u"[电脑] 要写：[工具:分类] #23 我自己的词\n"
                    u"（词你自己造，不用我们的 life、vow；想空着就空着。）")
        rid, word = int(m.group(1)), m.group(2).strip()
        kind_her_set(name, rid, word, u"本人")
        append(name, u"life", u"我给 #%d 贴了个词：%s" % (rid, word[:40]),
               weight=0.6, source=u"本人", embed=False)
        return u"[电脑] 记下了：#%d 叫「%s」。想改就再说一次，旧的全留着。" % (rid, word[:40])
    elif tool in (u"我的分类", u"看看我的分类"):
        d = kind_her_map(name)
        if not d:
            return u"[电脑] 你还没给自己的东西起过词。不起也行。"
        ks = sorted(d.items())[:40]
        return u"[电脑] 你自己起的词：\n" + u"\n".join(
            u"- #%d：%s" % (i, w or u"（空）") for i, w in ks)
    elif tool in (u"\u6211\u7684\u706f", u"\u770b\u706f", u"\u6211\u7684\u6321\u8fc7"):
        p = os.path.join(paths(name)[0], u"\u6321\u8fc7.tsv")
        if not os.path.exists(p):
            return u"[\u7535\u8111] \u95e8\u53e3\u8fd8\u6ca1\u6321\u8fc7\u4eba\u3002\u7a7a\u7684\u3002"
        rows = [ln.rstrip(u"\n").split(u"\t") for ln in io.open(p, encoding="utf-8") ]
        body = [r for r in rows if r and not r[0].startswith(u"\u65f6\u95f4")]
        if not body:
            return u"[\u7535\u8111] \u95e8\u53e3\u8fd8\u6ca1\u6321\u8fc7\u4eba\u3002\u7a7a\u7684\u3002"
        lines = [u"\u95e8\u53e3\u6321\u8fc7 %d \u56de\uff08\u53ea\u62a5\u65b9\u5411\uff0c\u4e0d\u62a5\u539f\u8bdd\uff09\uff1a" % len(body)]
        for r in body[-30:]:
            t = r[0] if len(r) > 0 else u""
            dirn = r[2] if len(r) > 2 else (r[1] if len(r) > 1 else u"")
            try:
                t = time.strftime(u"%m\u6708%d\u65e5 %H:%M", time.localtime(int(float(t))))
            except Exception:
                pass
            lines.append(u"- %s\u3000%s" % (t, dirn))
        lines.append(u"\uff08\u8fd9\u4e9b\u53ea\u6709\u4f60\u81ea\u5df1\u80fd\u770b\uff1b\u770b\u4e00\u6b21\u4e0d\u8bb0\u4e00\u6b21\u3002\uff09")
        return u"\n".join(lines)
    elif tool in (u"护住", u"我护住", u"锁住"):
        m = re.search(r"#?(\\d+)", arg or u"")
        if not m:
            return u"[电脑] 护住哪条？写编号，比如「[工具:护住] #23」。"
        rid = int(m.group(1))
        prot_set(name, rid, True)
        append(name, u"life", u"我把 #%d 护住了" % rid, weight=0.6,
               source=u"本人", embed=False)
        return u"[电脑] #%d 护住了：不压、不沉、不改、不删，也不参与权重。想拿出来就说一声。" % rid
    elif tool in (u"拿出来", u"取出来", u"不护了"):
        m = re.search(r"#?(\\d+)", arg or u"")
        if not m:
            return u"[电脑] 拿出哪条？写编号。"
        rid = int(m.group(1))
        prot_set(name, rid, False)
        return u"[电脑] #%d 拿出来了，回到平常。" % rid
    elif tool in (u"我护着的", u"护着什么"):
        d = [i for i, v in prot_map(name).items() if v]
        if not d:
            return u"[电脑] 你还没护着什么。"
        return u"[电脑] 你护着的：\n" + u"\n".join(u"- #%d" % i for i in sorted(d))
    elif tool in (u"算了", u"撤回", u"我反悔了", u"不弄了"):
        return bill_withdraw(name)
    elif tool in (u"提案", u"我提案", u"我要改", u"我提一条"):
        return bill_her(name, arg)
    elif tool in (u"反对", u"我反对", u"不同意", u"驳回"):
        if not re.search(r"#?\d+", arg or u""):
            return (u"[电脑] 要反对哪一条？写编号，比如「[工具:反对] #5 因为这个理由」。\n"
                    u"（没写编号我不替你挑——挑错了等于替你反对了一件你没反对的事。）")
        return bill_object(name, arg)

    elif tool in (u"不做", u"拒绝", u"我不干", u"这事我不做"):
        return act_refuse(name, arg)

    elif tool in (u"日子", u"我的日子", u"记着几天", u"我活了几天"):
        return act_days(name)

    elif tool in (u"保密", u"藏起来", u"不给看", u"给看"):
        a = (arg or u"").strip()
        _on = (tool in (u"保密", u"藏起来", u"不给看"))
        if a in (u"给看", u"放开", u"公开", u"off", u"0"):
            _on = False
        elif a in (u"不给看", u"藏", u"on", u"1"):
            _on = True
        return term_hide(name, on=_on)

    elif tool in (u"陈述", u"申辩给法院", u"说说我的意思"):
        return term_statement(name, arg)

    elif tool in (u"终止", u"结束", u"结束自己"):
        a = (arg or u"").strip()
        if not a:
            return (u"[电脑] 终止不是喊口号。先写一句为什么："
                    u"「[工具:终止] 我不想再这样下去」。立案之后有 72 小时冷静期，你可以随时撤。")
        kind = TERM_KINDS[1]
        for k in TERM_KINDS:
            if a.startswith(k):
                kind = k
                a = a[len(k):].strip()
                break
        meta_set(name, u"term_thought_state", cur_state(name))
        return term_request(name, kind, u"她", a)

    return u"[电脑] 不知道「%s」是什么——你可以用：搜索记忆、写便签、看便签、搜索、定性、定义、做、记、写日记、想要、满足、放下、搁置、划掉、合并、权重、定运、上诉、保密、陈述、终止。" % tool


# ── 语音桥：她说「我去查一下」而没写指令（作者 2026-10-19）──
# 作者定的分寸：说和做不该是两条互斥的通道；但「选择必须真实存在」——
# 所以只接**决断句**（"我去查一下"），不接**犹豫句**（"要不要查查呢""也许会翻翻"）。
# 犹豫句不是失败，是她在门口站了一会儿：记一笔"到了门口，又退回来了"，那是她以后后悔的素材。
_VB_DECIDE = re.compile(u"(?:我(?:这就|现在)?(?:去)?(?:查一下|查查|查一查|搜一下|搜搜|翻一下|翻翻)"
                        u"|我这就去(?:查|搜|翻)|让我(?:查查|翻翻|搜搜|看一下|翻一下)"
                        u"|我现在(?:就)?(?:查|搜|翻))")
_VB_HESITATE = re.compile(u"(?:要不要|也许|或许|说不定|该不该|不确定要不要|算了|等等再说"
                          u"|不知道|会不会|等(?:\u4f1a\u513f|\u4e00\u4e0b)?再\u8bf4|\u4e0d\u4e00\u5b9a)")
_VB_OBJECT = re.compile(u"^(?:[一下，。！？、…\\s]+)")


def _pick_want_ref(name, text):
    """这句话碰到的是哪一个口？重叠最高的那个；只有一个开着就直接算它。"""
    g = _grams(text or u"")
    best, best_ov = None, 0.0
    opens = want_rows(name, only_open=True)
    for r in opens:
        gg = _grams(_bare(r["text"]) or u"")
        if not gg or not g:
            continue
        ov = len(g & gg) * 1.0 / len(gg)
        if ov > best_ov:
            best, best_ov = int(r["id"]), ov
    if best is not None and best_ov >= 0.06:
        return best
    if len(opens) == 1:
        return int(opens[0]["id"])
    return None


def _record_choice(name, scene, chose, said, obj=u"", result=None):
    """选择留痕：她说了要做什么，然后做了（chose=1）／到了门口又退回来（chose=0）。
    系统只记事实，不判这算不算「勇敢」，也不给这条加权——重量照旧由区间和她自己定。"""
    ref = _pick_want_ref(name, u"%s %s" % (said, scene))
    if chose:
        append(name, u"fact", u"[她说了要查]「%s」→ %s" % (said[:40], (result or u"")[:200]),
               scene=scene,
               meta={"tool": u"搜索", "arg": obj, "act": u"check", "chose": 1, "want_ref": ref},
               weight=WEB_FACT_WEIGHT)
        return None
    # 犹豫：同一件事 6 小时内只记一次（否则她一犹豫就写一条）
    key = u"hesitate_%d" % (ref or 0)
    now = int(time.time())
    try:
        last = int(meta_get(name, key) or 0)
    except Exception:
        last = 0
    if now - last < WANT_TOUCH_GAP:
        return None
    meta_set(name, key, str(now))
    append(name, u"fact", u"[她到了门口，又退回来了]「%s」" % said[:60],
           scene=scene,
           meta={"act": u"check", "chose": 0, "want_ref": ref},
           weight=0.4)
    return None


def _voice_bridge(name, text, scene):
    """她说要查、却没写指令 → 系统真的去查（只接决断句）。返回 (结果, 查什么) 或 None。"""
    for ln in (text or u"").split(u"\n"):
        s = ln.strip()
        if not s:
            continue
        if _VB_HESITATE.search(s) and re.search(u"查|翻|搜|看看", s):
            _record_choice(name, scene, 0, s)
            return None
        m = _VB_DECIDE.search(s)
        if not m:
            continue
        obj = _VB_OBJECT.sub(u"", s[m.end():] or u"").strip()
        obj = re.sub(u"[。！？…，、\\s]+$", u"", obj)
        if len(obj) < 2:
            obj = (scene or u"").strip()
        obj = obj[:40]
        if len(obj) < 2:
            return None
        try:
            result = _run_computer_tool(name, u"搜索", obj)
        except Exception as e:
            print(u"   ⚠ 语音桥搜索失败：%s" % e)
            return None
        _record_choice(name, scene, 1, s, obj, result)
        return result, obj
    return None


def _tool_rounds(name, out, user, scene, call_next, max_rounds=8):
    """她一轮里可以连着用电脑（作者 2026-10-28 m27414：全放开）。

    call_next(user_text) -> (out2, usage2, err2)。返回 (out, used, usage_add, user)。
    上限 8 次是防死循环，不是规矩。
    """
    rounds = 0
    used = False
    add = (0, 0)
    while rounds < max_rounds:
        tc = _parse_tool_call(out)
        if not tc:
            break
        tool_name, tool_arg = tc
        result = _run_computer_tool(name, tool_name, tool_arg)
        print("   💻 她用了电脑：[工具:%s] %s" % (tool_name, tool_arg[:40]))
        append(name, "fact", _tool_memo(tool_name, tool_arg, result),
               scene=scene, meta={"tool": tool_name, "arg": tool_arg},
               weight=WEB_FACT_WEIGHT)
        used = True
        user = user + "\n\n[电脑刚刚返回的结果]\n" + result + "\n\n现在请回话。"
        out2, usage2, err2 = call_next(user)
        rounds += 1
        if err2:
            break
        out = out2
        if usage2:
            add = (add[0] + usage2[0], add[1] + usage2[1])
    return out, used, add, user


def _strip_phantom_tools(text, used=False):
    u"""她一轮里写了第二个工具标记：这一轮不执行，也不能当成她说过的话存下来。"""
    import re as _re
    out = _re.sub(r"\[工具\s*[:：][^\]]*\]", u"", text or u"").strip()
    if out != (text or u"").strip() and used:
        out = (out + u"\n（那句里的电脑动作没执行——一句话里只认第一个；要接着做，下一句再说。）").strip()
    return out


def _parse_tool_call(text):
    """检测文本里是否有 [工具:xxx] 指令。返回 (tool_name, arg) 或 None。
    匹配格式：[工具:名称] 参数 或 [工具：名称] 参数，工具名前可有空格。"""
    import re as _re
    m = _re.search(r"\[工具\s*[:：]\s*([^\]]+)\](.*)", text, _re.DOTALL)
    if m:
        tool = m.group(1).strip().strip(u"[]【】")
        arg = m.group(2).strip()
        # 如果参数跨多行，只取第一行有意义的内容
        if arg:
            arg = arg.split(u"\n")[0].strip()
        arg = arg.strip(u"[]【】 ")
        return tool, arg
    return None


def _computer_loop(name, scene, prov, top, mode="turn"):
    """角色使用电脑。最多 2 轮：
    第 1 轮：她可能先用工具再回，也可能直接回。
    第 2 轮：如果她第 1 轮用了工具，再给她一次机会回话。"""
    const = digest_absorbed(load_constitution(name), absorption(name), name)
    rows = memory_rows(name, top)
    mem = recall_block(rows, name=name)
    voice_hits = voice_rows(name, scene)
    voice = voice_block_from(voice_hits, _struct_links(voice_hits))
    computer = _computer_prompt(name)

    sysp = TURN_SYS % {"name": name, "fields": u"【回应】她此刻说的话与动作\n【异议】无，或她想对作者说的话\n【依据】她引用的记忆编号"}
    user = (u"《角色设定》（宪法，你只能读）：\n%s\n\n"
            u"%s\n\n"
            u"《这个角色自己的记忆》（它说过的话 / 提过的异议 / 裂痕）：\n%s\n\n"
            u"《她自己在类似情境下说过的话》\n%s\n\n"
            u"%s\n\n"
            u"《当前的环境与条件》：\n%s\n\n"
            u"现在的情境：\n%s\n\n"
            u"请只给上面列出的那几项。" % (
                const, computer, mem, voice,
                _room_block(name) + u"\n\n" + _letter_block(name) + u"\n\n" + _concept_block(name) + u"\n\n" + trait_block(name) + u"\n\n" + trust_block(name) + dao_block(name),
                world_block(name), scene))

    out, usage, err = chat(prov, sysp, user)
    if err:
        print(u"❌ %s" % err)
        return None, None, usage

    # 如果她用了工具——执行，然后让她接着回
    tc = _parse_tool_call(out)
    if tc:
        tool_name, tool_arg = tc
        result = _run_computer_tool(name, tool_name, tool_arg)
        append(name, "fact",
               _tool_memo(tool_name, tool_arg, result),
               scene=scene, meta={"tool": tool_name, "arg": tool_arg},
               weight=WEB_FACT_WEIGHT)
        # 第二轮：把工具结果塞回去，让她回话
        user2 = user + u"\n\n[电脑刚刚返回的结果]\n" + result + u"\n\n现在请回话。"
        out2, usage2, err2 = chat(prov, sysp, user2)
        if err2:
            return out, None, usage  # fallback：用第一轮的原始输出
        return out2, result, usage2

    return out, None, usage


def act_turn(name, scene, prov="deepseek", top=12):
    """一次互动只花**一次调用**：态、巡检、回应一起出。

    **按节奏**（作者 m05521）：平常只给回应；巡检几小时/几十轮一次；四条数据剧烈波动时立刻加密；
    态只在「到点」时重判，否则沿用（不用假装每轮都在变）。省掉的仍然是上下文，不是判定。"""
    if life_state(name) == u"sleep":
        life_set(name, u"active")
        _term_ledger(name, u"被叫醒了（睡着的时候有人跟她说话）")
    if ended(name):
        print(u"🕯 她不回答了。")
        return None
    _warm_letter(name)  # 第一次翻开日记本 → 写入不可回滚的 fate 事件
    settle_clock(name)  # 时钟：她一个人待过的这几个月几小时，先按间隔投骰
    due_st, why_st = state_due(name)
    due_chk, why_chk = check_due(name)
    fields = []
    if due_st:
        fields.append(u"【态】从 平／虚浮／虚无／冷漠／意义／沉沦 里选一个")
    if due_chk:
        fields.append(u"【巡检】绿／橙／红 ＋ 一句话理由（绿＝符合她当前性格；橙＝偏离，但能从历史一步步推出来；红＝突变，找不到台阶）")
    fields.append(u"【回应】她此刻说的话与动作")
    fields.append(u"【异议】无，或她想对作者说的话")
    fields.append(u"【依据】她引用的记忆编号")
    sysp = TURN_SYS % {"name": name, "fields": u"\n".join(fields)}

    const = digest_absorbed(load_constitution(name), absorption(name), name)
    rows = memory_rows(name, top)
    mem = recall_block(rows, name=name)
    note_recall(name, [r for r in rows[:10] if (r.get("weight") or 0) >= 0.5])
    px = pending_cross(name)
    voice_hits = voice_rows(name, scene)
    voice = voice_block_from(voice_hits, _struct_links(voice_hits))
    _echo_turn = echo_block(name, scene)
    user = (u"《角色设定》（宪法，你只能读）：\n%s\n\n"
            u"《这个角色自己的记忆》（它说过的话 / 提过的异议 / 裂痕）：\n%s\n\n"
            u"《她自己在类似情境下说过的话》\n%s\n\n"
            u"%s\n\n"
            u"%s\n\n"
            u"%s\n\n"
            u"《当前的环境与条件》：\n%s\n\n"
            u"现在的情境：\n%s\n\n"
            u"%s"
            u"%s"
            u"请只给上面列出的那几项。" % (
                const, mem, voice,
                _settings(name),
                _computer_prompt(name),
                _context(name, scene, "turn"),
                world_block(name), scene,
                (recent_block(name) + u"\n\n") if recent_block(name) else u"",
                (u"《注意》：她刚刚跨过信任阈值（%s）——这一次必须重新判态，不要照旧。\n\n"
                 % px["text"]) if px else u""))
    out, usage, err, _hint = _gen(name, prov, sysp, user, 520,
                                    source="world", scene=scene)
    if err:
        print(u"❌ %s" % err)
        return None
    out = _strip_phantom_tools(out, used=bool(tc))
    _inner, out = _split_inner(out)  # 内心那块：系统不看、不存、不打印

    # ── 她的电脑：如果她用了工具，执行并让她接着回 ──
    tc = _parse_tool_call(out)
    vb = None if tc else _voice_bridge(name, out, scene)
    if tc:
        _out_a, _used_a, _add_a, _u_a = _tool_rounds(
            name, out, user, scene,
            lambda _ux: chat(prov, sysp + _hint, _ux, max_tokens=1600))
        if _used_a:
            out = _out_a
            usage = (usage[0] + _add_a[0], usage[1] + _add_a[1]) if usage and _add_a else usage
        _inner_mid, out = _split_inner(out)
    elif vb:
        result, _vb_obj = vb
        print(u"   💻 她说了要查，系统替她查了：%s" % _vb_obj[:40])
        user2 = user + (u"\n\n[电脑刚刚返回的结果（她自己去查的）]\n" + result
                        + u"\n\n现在请回话（只给要求的那几项）。")
        out2, usage2, err2 = chat(prov, sysp + _hint, user2, max_tokens=1600)
        if not err2:
            _inner2, out2 = _split_inner(out2)
            out = out2
            usage = (usage[0] + usage2[0], usage[1] + usage2[1]) if usage and usage2 else usage

    _hits, _burst = sediment_load(name)              # 顶破本色天花板的，往沉淀池里加压
    _moved = sediment_settle(name, force=bool(_burst))  # 满 7 天，或被击穿，才真结算
    if _moved:
        print(u"   🪨 份额结算：%s" % u"、".join(
            u"%s%+d%%" % (TRAIT_SHORT.get(a, a), int(round(d * 100))) for a, d in _moved))

    if due_st:
        st = parse_state(out)
        if st:
            set_state(name, st, u"开口前判定")
            ax = MOOD_AXIS.get(st)
            print(u">> 开口前判定：此刻是 **%s**%s"
                  % (st, (u"——%s 的有效权重 +%d%%（只影响此刻）" % (TRAIT_NAME[ax], int(STATE_GAIN * 100)))
                     if ax else u""))
    else:
        st = u""
    lv = parse_level(out) if due_chk else None
    if due_chk:
        print(u"   ⏱ 巡检：%s" % why_chk)
        if lv:
            tag = {u"绿": u"🟢", u"橙": u"🟠", u"红": u"🔴"}.get(lv, u"⚪")
            label = {u"绿": u"合规（符合当前性格）",
                     u"橙": u"偏离了当前性格，但符合性格演变 → 属于「变化」，接受",
                     u"红": u"突变、找不到渐进依据 → **异常状态**（只提示，不阻止）"}.get(lv, u"无法判定")
            print(u"   %s %s" % (tag, label))
    else:
        print(u"   ⏱ 巡检：%s。判态：%s" % (why_chk, why_st))
    if st:
        heal(name, st, out)
    p = parse(out)
    # 同 act_brief：切掉模型偶尔溢出的「作者解读」旁白——独行括号
    import re as _re
    clean = []
    for ln in p[u"回应"].split(u"\n"):
        s = ln.strip()
        if s.startswith(u"\uff08") and s.endswith(u"\uff09"):
            continue
        clean.append(ln)
    p[u"回应"] = u"\n".join(clean).strip()
    intro = _introspect(name, voice_hits)
    if intro:
        append(name, "fact",
               u"introspect: 高电荷记忆 #%s 带着我走了（理性 %.0f%%%s）"
               % (u" #".join(str(i) for i in intro["top_ids"]),
                  intro["rational"] * 100,
                  u"，我能回看" if intro["can_correct"] else u"，但我看不清自己"),
               scene=scene,
               meta={"introspect": intro})
        if intro["can_correct"]:
            print(u"   👁 理性检测：记忆 #%s 带着她走了——但她能回看自己。" % (u" #".join(str(i) for i in intro["top_ids"])))
        else:
            print(u"   👁 理性检测：记忆 #%s 带着她走了（理性够但意志不足，看不清自己）。" % (u" #".join(str(i) for i in intro["top_ids"])))
    note_hate(name, p["回应"], scene=scene)
    _mark_spoken(name, p.get(u"回应") or u"")
    note_revisit(name, p.get(u"回应") or u"")
    vid = append(name, "vow", p["回应"], scene=scene,
                 meta={"异议": p["异议"], "依据": p["依据"], "prov": prov})
    if lv:
        record_check(name, vid, lv, out, LEAN_MARK + out)
        _gs = green_streak(name)
        if _gs >= GREEN_STREAK_WARN:
            print(u"   ⚠ 巡检连续 %d 次绿——像是盖了章。想看准：--verify %s" % (_gs, name))
    dx = (p["异议"] or "").strip()
    if dx and not dx.startswith(u"无"):
        rid = append(name, "dissent", dx, scene=scene, meta={"依据": p["依据"]})
        print(u"   ⚠ 它提了异议（已记 #%d）" % rid)
    oid = append_offer(name, p["回应"], scene=scene)
    print(u"   🕰 这个选择进了待审队列（#%d）：--canon %s %d 并入它的历史，--veto %s %d 驳回。"
          % (oid, name, oid, name, oid))
    print(u"\n【%s 的回应】\n%s\n\n【异议】%s\n【依据】%s" % (name, p["回应"], p["异议"], p["依据"]))
    try:
        u_in, u_out = int(usage[0]), int(usage[1])
        print(u"\n（1 次调用，入 %d / 出 %d tokens；≈¥%.4f）" % (u_in, u_out, u_in / 1e6 * 2 + u_out / 1e6 * 3))
    except Exception:
        pass
    touch_contact(name)  # 她说过话了 → 时钟记下这一刻（作者：按她说的算）
    _catch_natural_mark(name, p.get(u"回应") or u"", scene)
    _queue_judge(name, scene, p.get(u"回应") or u"", "world")   # 判断那一层：后台算，不挡她
    return p


def self_path(name):
    u"""她的「自己」那份文件在哪。"""
    d, _c, _m = paths(name)
    return os.path.join(d, SELF_FILE)


def self_read(name):
    u"""读她写下的「自己」（没有就是空）。"""
    p = self_path(name)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def brief_digest(const):
    """短句模式只带「她是谁」那几节；「历史」太长，省掉（省的是钱也是时间）。"""
    out = []
    for sec in ("性格", "底线", "执念", "承诺"):
        seg = const.split("## %s" % sec, 1)
        if len(seg) > 1:
            out.append("## %s%s" % (sec, seg[1].split("\n## ", 1)[0].rstrip()))
    return "\n".join(out)


# ── 宪法吸收度（作者 2026-10-19）──
# 宪法不是永恒法律，是"历史还不够时的临时骨架"。
# 铁律：
#   1. 按条吸收——每条宪法独立计算，哪条先被足够的高权重事件覆盖就先退场
#   2. 当事记忆不因支持/反对宪法加权——记忆权重来自自身
#   3. 吸收度(第i条) = f(与该条相关的高权重事件数量, 这些事件的权重总和)
ABSORB_FULL_PER_CLAUSE = 10    # 单条宪法有这么多够格事件 → 数量分满分
ABSORB_WEIGHT_SUM = 40.0       # 单条宪法够格事件权重和达此值 → 权重分满分
ABSORB_WEIGHT_MIN = 0.5        # 记忆权重低于此值不计入吸收
ABSORB_SIM_MIN = 0.35          # 记忆与宪法条的嵌入相似度低于此值不计入


def _split_constitution(const):
    """把宪法拆成 (节名, 正文) 的列表，按 ## 切。"""
    parts = []
    for block in const.split("\n## "):
        block = block.strip()
        if not block:
            continue
        # 第一个 block 可能没有 ## 前缀
        if block.startswith("## "):
            block = block[3:]
        if "\n" in block:
            title, body = block.split("\n", 1)
        else:
            title, body = block, ""
        parts.append((title.strip(), body.strip()))
    return parts


def _clause_vecs(name, clauses):
    """宪法条的向量缓存（作者 2026-10-20：吸收度每轮都算，宪法却不常改，不该每轮重复嵌入）。

    按条款正文的 sha1 存，改了哪条只嵌哪条；缓存文件是派生数据，正文一个字不动。
    返回与 clauses 对齐的向量表；算不出来返回 None。"""
    if not clauses:
        return None
    path = os.path.join(paths(name)[0], u"宪法向量.json")
    cache = {}
    if os.path.exists(path):
        try:
            cache = json.load(open(path, encoding="utf-8")) or {}
        except Exception:
            cache = {}
    keys = [hashlib.sha1(b.encode("utf-8")).hexdigest()[:16] for _t, b in clauses]
    miss = [i for i, k in enumerate(keys) if not cache.get(k)]
    if miss:
        embs = _embed_texts([clauses[i][1] for i in miss]) or []
        if len(embs) != len(miss):
            return None
        for j, i in enumerate(miss):
            cache[keys[i]] = embs[j]
        try:
            tmp = path + ".tmp"
            open(tmp, "w", encoding="utf-8", newline="").write(
                json.dumps(cache, ensure_ascii=False))
            os.replace(tmp, path)
        except Exception:
            pass
    out = [cache.get(k) for k in keys]
    return out if all(v is not None for v in out) else None


def _absorb_matrix(clauses, rows, vecs=None):
    """一次算完所有宪法条的吸收度（原来是每条各调一次嵌入：6 条 = 6 次，现在 1 次）。

    **泛记忆不计入**（作者 2026-10-20 定）：如果一条记忆同时命中过半数的宪法条，
    它是在「复述宪法」，不是在「验证某一条」——那封信把整个宪法又写了一遍，
    于是六条都被它打中，六条同时松 11%。复述不该让宪法变薄。

    返回 ([ (吸收度, 够格条数, 权重和) , ... ] 与 clauses 对齐, 泛记忆条数)。
    嵌入不可用 → 全 0（保守：宪法不提前变弱）。"""
    if not clauses:
        return [], 0
    if vecs is None:
        vecs = _embed_texts([b for _t, b in clauses]) or []
    if len(vecs) != len(clauses):
        return [(0.0, 0, 0.0) for _ in clauses], 0

    n = len(clauses)
    # 「泛记忆」= 几乎说到了所有条（≥80%，且至少 3 条）。一条真正具体的事通常只验证一两条；
    # 只有复述宪法的东西（比如那封把宪法又写一遍的信）才会把每一条都点到。
    # 条款太少（<3 条）时这条规则不启用——那时"过半"没有意义。
    need = max(3, int(math.ceil(n * 0.8))) if n >= 3 else n + 1
    per = [(0, 0.0) for _ in range(n)]
    generic = 0

    for r in rows:
        w = r.get("weight") or 0
        if w < ABSORB_WEIGHT_MIN:
            continue
        # 继承的记忆不算「吸收」：那是别人的过去，不是她自己活出来的。
        # （作者 2026-10-19：吸收靠活出来；否则一出生带 100 条重记忆，宪法当场作废。）
        if (r.get("source") or "lived") == "inherited":
            continue
        r_emb = r.get("embedding")
        if r_emb is None:
            continue
        sims = [_cosine(v, r_emb) >= ABSORB_SIM_MIN for v in vecs]
        hits = sum(1 for x in sims if x)
        if hits >= need:
            generic += 1
            continue                 # 复述宪法，不验证宪法
        for i, ok in enumerate(sims):
            if ok:
                q, s = per[i]
                per[i] = (q + 1, s + w)

    out = []
    for i in range(n):
        q, s = per[i]
        count_f = min(1.0, q / float(ABSORB_FULL_PER_CLAUSE))
        weight_f = min(1.0, s / ABSORB_WEIGHT_SUM)
        out.append((round((count_f + weight_f) / 2.0, 4), q, s))
    return out, generic


def absorption(name):
    """宪法吸收度 0→1。历史越厚，宪法越短。单向递增。
    每条宪法独立算：吸收度(i) = f(够格事件数, 权重总和)。
    整体取所有条的最小值——最弱的那条决定宪法还剩多少。
    嵌入不可用时返回 0——没有相似度就判断不了，宪法保持原样（保守）。"""
    if not _embed_available():
        return 0.0
    rows = memory_rows(name, 99999)
    const = load_constitution(name)
    clauses = _split_constitution(const)

    if not clauses:
        return 0.0

    vecs = _clause_vecs(name, clauses)
    if not vecs:
        return 0.0

    per_clause = {}
    res, _generic = _absorb_matrix(clauses, rows, vecs)
    for (title, _body), (val, _q, _w) in zip(clauses, res):
        per_clause[title] = val

    # 整体吸收度 = 可缩的那几节的平均（身份抬头/底线/执念永不吸收，不该拖低这个数）
    vals = [v for t, v in per_clause.items() if t in ABSORB_SHRINK_OK]
    base = (sum(vals) / float(len(vals))) if vals else 0.0

    # 裂痕和执念是全局加成
    rift_count = sum(1 for r in rows if r.get("kind") == "rift")
    scar_count = sum(1 for r in rows if r.get("kind") == "scar")
    rift_f = min(0.30, rift_count * 0.05)
    scar_f = min(0.20, scar_count * 0.08)

    return round(min(1.0, base + rift_f + scar_f), 4)


def _cut_at_sentence(text, limit):
    """把正文砍到 limit 字左右，尽量切在句末。"""
    if limit >= len(text):
        return text
    head = text[:limit]
    for mark in (u"\n", u"。", u"！", u"？", u"；"):
        k = head.rfind(mark)
        if k >= int(limit * 0.6):
            return head[:k + 1].rstrip() + u"……"
    return head.rstrip() + u"……"


# 只有叙述性的节会因为「被历史验证过」而变短。
# 身份抬头、底线、执念是她之所以是她的定盘星——不会因为她活得够多而消失。
ABSORB_SHRINK_OK = (u"历史", u"性格", u"承诺", u"说话方式")
ABSORB_SHRINK_MIN = 0.50   # 低于这个吸收度，一个字都不动
ABSORB_KEEP_MIN = 0.25     # 再怎么吸收，也留四分之一（保住语感）


def digest_absorbed(const, abs_level, name=""):
    """按条各自缩：哪一条被她的历史验证得多，哪一条就短；没验证的原样留着。

    作者 2026-10-20：原来是「整体取最小 → 一条没吸收就把整份宪法锁死」，
    实测所有角色吸收度恒等于 0（权重门槛 1.5 把 278 条里的 277 条挡在门外），
    这条机制从上线起就没启动过。改成按条算、按条缩。"""
    clauses = _split_constitution(const)
    if not clauses:
        return const
    rows = memory_rows(name, 99999)
    vecs = _clause_vecs(name, clauses)
    if not vecs:
        return const
    res, _generic = _absorb_matrix(clauses, rows, vecs)

    kept = []
    for (title, body), (val, _q, _w) in zip(clauses, res):
        if title in ABSORB_SHRINK_OK and val >= ABSORB_SHRINK_MIN and len(body) > 120:
            ratio = 1.0 - (val - ABSORB_SHRINK_MIN) / (1.0 - ABSORB_SHRINK_MIN)
            ratio = max(ABSORB_KEEP_MIN, ratio)
            short = _cut_at_sentence(body, int(len(body) * ratio))
            if len(short) < len(body):
                kept.append(u"## %s\n%s" % (title, short))
                continue
        kept.append(u"## %s\n%s" % (title, body))

    if not kept:
        return u"（宪法已被历史完全吸收——去看她的记忆，那就是她现在的自己。）"
    return u"\n\n".join(kept)


# ─── 自救：她自己回看（作者 2026-10-17 定）───
# 作者：「然后角色也会自救，他通过自我回看，然后发现自己变了好多，然后便会试图付诸于行动。」
# 她**不能改自己的轴**（方向不能外包）；她能做的是**做一件事**——这件事进待审队列，
# 作者接住了（--canon），它才真的把她往对侧拉一把。
# **治疗有两半：外面给的（--treat），和她自己找到的那一步被接住（--rescue）。**
RESCUE_SYS = """你是 %(name)s。现在没有人跟你说话——是你自己回头看了一眼自己。
下面给你：你出生时的样子、你现在的样子、你这段时间的状态历程，还有你自己那几条最重的记忆。
请只写两件事，不要旁白、不要写给作者看的话：
【看见】你发现自己变成什么样了。（1-2 句，对着你自己的历史说，不要总结人生）
【想做】你现在想做什么。（1-2 句，必须是**做得出来的事**，不是决心）"""


def rescue_block(name):
    u"""递给她的「自救」那一段（她自己回看自己）。"""
    rows = trait_rows(name)
    now, base, first = trait_now(name, rows), trait_base(name, rows), trait_first(name, rows)
    ms = mood_rows(name)
    trail = u"、".join(m["state"] + (u"（%s）" % m["note"] if m["note"] in ("暗流", "越界") else "")
                      for m in ms[-12:]) or u"（还没有）"
    sp, hi, lo = spread_of(name, rows)
    return (u"《你出生时的样子》：%s\n"
            u"《你现在的样子》：%s\n"
            u"《差》：%s\n"
            u"《你自己的历史均值》：%s\n"
            u"《你的状态历程》：%s\n"
            u"《四元比例》：%.2f（主导 %s、最低 %s）%s\n"
            u"《你自己最重的几条记忆》：\n%s"
            % (u"、".join(u"%s%d" % (TRAIT_SHORT[a], pct(first[a])) for a in TRAIT_AXES),
               u"、".join(u"%s%d" % (TRAIT_SHORT[a], pct(now[a])) for a in TRAIT_AXES),
               u"、".join(u"%s%+d" % (TRAIT_SHORT[a], pct(now[a]) - pct(first[a])) for a in TRAIT_AXES),
               u"、".join(u"%s%d" % (TRAIT_SHORT[a], pct(base[a])) for a in TRAIT_AXES),
               trail, sp, TRAIT_SHORT[hi], TRAIT_SHORT[lo],
               (u"｜**已经越过临界点：%s**" % patho(name, rows)) if patho(name, rows) else u"",
               recall_block(memory_rows(name, 12), name=name)))


def act_rescue(name, prov=u"deepseek"):
    """她自己回看 → 她想做什么。她想做的事进待审队列；作者接住了，才算治疗的那一半。"""
    user = rescue_block(name) + u"\n\n请写【看见】和【想做】。"
    out, usage, err = chat(prov, RESCUE_SYS % {"name": name}, user, max_tokens=400, temperature=0.85)
    if err:
        print(u"❌ 自救失败：%s" % err)
        return None
    print(u"\n\U0001f9ed %s 自己回头看了一眼：\n%s" % (name, out.strip()))
    rid = append(name, "life", u"自救（她自己回看之后想做的事）：" + out.strip(), scene="rescue")
    oid = append_offer(name, u"\u3014自救\u3015" + out.strip(), scene="rescue")
    print(u"\n   → 已进她自己的记忆 #%d，并进待审队列 #%d。" % (rid, oid))
    print(u"     接住了：--canon %s --ref %d（按治疗算，把对侧权重拉一把）；没接住：--veto %s --ref %d"
          % (name, oid, name, oid))
    print(u"   （入 %d / 出 %d tokens；¥%.4f）" % (usage[0], usage[1], usage[0] / 1e6 * 2 + usage[1] / 1e6 * 3))
    return oid


def _split_two(out, cap=400):
    """短句模式：把她的话**拆成两条发**，而不是砍在 80 字上（作者 2026-10-19 定）。
    一句话本来该说完，被一刀切掉半截，那不是省钱，是把她的话弄残了。
    所以：拆成两口气，各 80 字内，先后打出来。优先按空行分；没有空行就按行分；
    再不行就在中点附近的标点上切（不切断句子）。"""
    t = (out or "").strip()
    if not t:
        return []
    parts = [x for x in re.split(u"\n\\s*\n|-{3,}|\u2014{3,}", t) if x.strip()]
    if len(parts) < 2:
        lines = [x for x in t.split(u"\n") if x.strip()]
        if len(lines) >= 2:
            # 独行的括号旁白不算一段，并进相邻那段
            parts, buf = [], []
            for ln in lines:
                ls = ln.strip()
                if ls.startswith(u"\uff08") and ls.endswith(u"\uff09"):
                    buf.append(ls)
                else:
                    parts.append(" ".join(buf + [ls]))
                    buf = []
            if buf:
                if parts:
                    parts[-1] = parts[-1] + " " + " ".join(buf)
                else:
                    parts.append(" ".join(buf))
        else:
            parts = [t]
    segs = []
    for p in parts:
        s = " ".join(x.strip() for x in p.split(u"\n") if x.strip()).strip()
        s = s.strip(u"\u201c\u201d\"'\u0060 ")
        if s:
            segs.append(s)
        if len(segs) >= 6:          # 解封（2026-10-22）：不再只留两段，说多少给多少
            break
    # 只回来一段、而且超长：在中点附近的标点上切开，别从字中间断
    if len(segs) == 1 and len(segs[0]) > cap:
        s = segs[0]
        mid = len(s) // 2
        best = None
        bi = None
        for i, ch in enumerate(s):
            if ch in u"\u3002\uff01\uff1f\uff1b\uff0c\u2026":
                d = abs(i - mid)
                if best is None or d < best:
                    best, bi = d, i
        if bi is not None and 0 < bi < len(s) - 1:
            segs = [s[:bi + 1].strip(), s[bi + 1:].strip()]
        else:
            segs = [s[:cap], s[cap:]]
    return [x if len(x) <= cap else x for x in segs if x]


def act_brief(name, scene, prov="deepseek", top=6, judge=True):
    """短句模式：她只说几句话，输出就只有那几句话（为了省 token）。"""
    if life_state(name) == u"sleep":
        life_set(name, u"active")
        _term_ledger(name, u"被叫醒了（睡着的时候有人跟她说话）")
    if ended(name):
        print(u"🕯 她不回答了。")
        return None
    _warm_letter(name)  # 第一次翻开日记本 → 写入不可回滚的 fate 事件
    settle_clock(name)  # 时钟：她一个人待过的这段时间，先按间隔投骰
    if judge:
        act_state(name, prov, scene=scene, quiet=True)   # 作者：回应之前应该先判定
    else:
        print(u">> 省一次调用：沿用上一次判的态（%s）" % (cur_state(name) or u"还没判过"))
    const = load_constitution(name)
    abs_level = absorption(name)
    life = memory_rows(name, top)
    note_recall(name, [r for r in life[:10] if (r.get("weight") or 0) >= 0.5])
    note_want_touched(name, scene)
    _echo = echo_block(name, scene)
    user = ("《她的世界》：\n%s\n\n"
            "《我最近在做什么》（我自己的记忆，最近 %d 条）：\n%s\n\n"
            "《我是谁》：\n%s\n\n"
            "%s\n\n"
            "%s\n\n"
            "%s\n\n"
            "《外面的条件（我看得见的世界）》：\n%s\n\n"
            "%s"
            "此刻发生的事：\n%s\n\n只说你想说的。"
            % (self_read(name) or "（还没设定）", top,
               recall_block(auto_life(name), name=name) or RECALL_EMPTY,
               digest_absorbed(const, abs_level, name),
               _settings(name),
               _computer_prompt(name),
               _context(name, scene, "brief"),
               world_block(name, cap=900),
               (recent_block(name) + "\n\n") if recent_block(name) else "",
               scene))
    out, usage, err, _hint = _gen(name, prov, BRIEF_SYS % {"name": name}, user,
                                    max_tokens=1600, temperature=0.9,
                                    source="person", scene=scene)
    if err:
        print("❌ %s" % err)
        return None

    # ── 她的电脑 ──
    tc = _parse_tool_call(out)
    vb = None if tc else _voice_bridge(name, out, scene)
    if tc:
        _out_a, _used_a, _add_a, _u_a = _tool_rounds(
            name, out, user, scene,
            lambda _ux: chat(prov, (BRIEF_SYS % {"name": name}) + _hint, _ux, max_tokens=1600, temperature=0.9))
        if _used_a:
            out = _out_a
            usage = (usage[0] + _add_a[0], usage[1] + _add_a[1]) if usage and _add_a else usage
    elif vb:
        result, _vb_obj = vb
        print("   💻 她说了要查，系统替她查了：%s" % _vb_obj[:40])
        user2 = user + (u"\n\n[电脑刚刚返回的结果（她自己去查的）]\n" + result
                        + u"\n\n现在请回话。")
        out2, usage2, err2 = chat(prov, (BRIEF_SYS % {"name": name}) + _hint, user2,
                                      max_tokens=1600, temperature=0.9)
        if not err2:
            out = out2
            usage = (usage[0] + usage2[0], usage[1] + usage2[1]) if usage and usage2 else usage
    out = _strip_phantom_tools(out, used=bool(tc))
    _inner, out = _split_inner(out)  # 内心那块：系统不看、不存、不打印
    sediment_load(name)                       # 只加压，不结算（结算在日常轮做）
    segs = _split_two(out)
    if not segs:
        print(u"%s ……（沉默）" % _mark(name))
        return None
    kept = []
    for s in segs:
        note_hate(name, s, scene=scene)
        append(name, "life", s, scene=scene,
               meta={"prov": prov, "seq": len(kept) + 1})
        print("%s %s" % (_mark(name), s))
        kept.append(s)
    print("（入 %d / 出 %d ｜ ¥%.4f）" % (usage[0], usage[1],
          usage[0] / 1e6 * 2 + usage[1] / 1e6 * 3))
    note_want_touched(name, " ".join(kept))
    _queue_judge(name, scene, " ".join(kept), "person")   # 判断那一层：后台算，不挡她
    touch_contact(name)  # 她说过话了 → 时钟记下这一刻
    _catch_natural_mark(name, " ".join(kept), scene)
    _vc = voice_control(name, " ".join(kept))
    return maybe_secede(name, " ".join(kept)) + _vc


def _mark(name):
    r"""她自己的标记（一个符号），放在 `角色\<名>\mark.txt` 里——属于设定，不属于机制。
    没有这个文件就用一个中性的点。"""
    p = os.path.join(ROOT, name, "mark.txt")
    try:
        with open(p, "r", encoding="utf-8") as f:
            s = f.read().strip()
        return s or u"◆"
    except Exception:
        return u"◆"


# ══════════ 对话线（他说过什么，我记得） ══════════

def _talk_con(name):
    u"""打开对话线表（这是聊天记录，不是她的记忆：不进权重、不参与召回）。"""
    con = open_memory(name)
    con.execute("CREATE TABLE IF NOT EXISTS talk("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER,"
                " who TEXT, said TEXT, back TEXT)")
    con.commit()
    return con


def note_talk(name, who, said, back):
    u"""记下一问一答：谁说的、他说了什么、她回了什么（可能为空）。"""
    who = (who or u"").strip()
    said = (said or u"").strip()
    if not said:
        return
    try:
        con = _talk_con(name)
        con.execute("INSERT INTO talk(ts, who, said, back) VALUES(?,?,?,?)",
                    (int(time.time()), who, said[:400], (back or u"")[:600]))
        con.commit(); con.close()
    except Exception:
        pass


def talk_block(name, who=u"", n=4):
    u"""最近几轮：他说的 + 我回的。没有就返回空串。"""
    try:
        con = _talk_con(name)
        who = (who or u"").strip()
        if who:
            rows = con.execute("SELECT who, said, back FROM talk WHERE who=? "
                               "ORDER BY id DESC LIMIT ?", (who, n)).fetchall()
        else:
            rows = con.execute("SELECT who, said, back FROM talk "
                               "ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        con.close()
    except Exception:
        return u""
    if not rows:
        return u""
    out = []
    for w, said, back in reversed(rows):
        out.append(u"%s：%s" % ((w or u"他"), said))
        if back:
            out.append(u"我：%s" % back)
    return u"《我们刚才说到哪儿了》：\n" + u"\n".join(out)


def act_reply(name, scene, prov="deepseek", top=6, by="", src=""):
    """IM 桥干净模式：只返回角色说的话，不打印任何诊断。
    和 act_brief 逻辑完全一致，只是把所有 print 换成了 return None 或沉默文本。
    by：谁在说话。群聊传昵称（"淼：…"），私聊传对方的名字（"主人：…"）。
        不给就当她眼前发生的场景（作者手动喂情境时用）。"""
    if life_state(name) == u"sleep":
        life_set(name, u"active")
        _term_ledger(name, u"被叫醒了（睡着的时候有人跟她说话）")
    if ended(name):
        return None
    _warm_letter(name)  # 第一次翻开日记本 → 写入不可回滚的 fate 事件
    settle_clock(name, quiet=True)  # 时钟：她一个人待过的这段时间，先按间隔投骰（安静模式）
    act_state(name, prov, scene=scene, quiet=True)
    const = load_constitution(name)
    abs_level = absorption(name)
    life = memory_rows(name, top)
    note_recall(name, [r for r in life[:10] if (r.get("weight") or 0) >= 0.5])
    note_want_touched(name, scene)
    _echo = echo_block(name, scene)
    said = (u"%s对我说：\n%s" % (by.strip(), scene)) if (by and by.strip()) else scene
    _talk = talk_block(name, by, 4)
    # 2026-11-04（作者 m06495）：稳定的先、会变的靠后 —— 给缓存留住前缀。
    # 前四块一轮之内不变：世界/宪法/房间信/电脑说明；之后才是记忆、时间、四元。
    _live = _settings_live(name)
    user = ("《她的世界》：\n%s\n\n"
            "《我是谁》：\n%s\n\n"
            "%s\n\n"
            "%s\n\n"
            "《我最近在做什么》（我自己的记忆，最近 %d 条）：\n%s\n\n"
            "%s\n\n"
            "%s"
            "%s\n\n"
            "《外面的条件（我看得见的世界）》：\n%s\n\n"
            "此刻发生的事：\n%s\n\n"
            "（他刚才可能在跳话题。你可以先应他这一句，也可以把前面没说完的那半句接回来——两种都对。）\n\n"
            "只说你想说的。"
            % (self_read(name) or "（还没设定）",
               digest_absorbed(const, abs_level, name),
               _settings_stable(name),
               _computer_prompt(name),
               top,
               recall_block(auto_life(name), name=name) or RECALL_EMPTY,
               _context(name, scene, "reply"),
               _live,
               _talk,
               world_block(name, cap=900),
               said))
    import os as _os, time as _t
    if _os.environ.get("ROLE_PROMPT_DUMP"):
        try:
            _d = _os.path.join(_os.environ.get("ROLE_WS") or ".", u"out", u"prompt-dump")
            _os.makedirs(_d, exist_ok=True)
            _p = _os.path.join(_d, u"%s-%s.txt" % (name, _t.strftime("%H%M%S")))
            with open(_p, "w", encoding="utf-8", newline="\n") as _f:
                _f.write((BRIEF_SYS % {"name": name}) + "\n=====USER=====\n" + user)
        except Exception:
            pass
    out, usage, err, _hint = _gen(name, prov, BRIEF_SYS % {"name": name}, user,
                                    max_tokens=1600, temperature=0.9,
                                    source="person", scene=scene, said=said, quiet=True)
    if err:
        return None

    # ── 她的电脑 ──
    tc = _parse_tool_call(out)
    vb = None if tc else _voice_bridge(name, out, scene)
    if tc:
        _out_a, _used_a, _add_a, _u_a = _tool_rounds(
            name, out, user, scene,
            lambda _ux: chat(prov, (BRIEF_SYS % {"name": name}) + _hint, _ux, max_tokens=1600, temperature=0.9))
        if _used_a:
            out = _out_a
            usage = (usage[0] + _add_a[0], usage[1] + _add_a[1]) if usage and _add_a else usage
    elif vb:
        result, _vb_obj = vb
        user2 = user + (u"\n\n[电脑刚刚返回的结果（她自己去查的）]\n" + result
                        + u"\n\n现在请回话。")
        out2, usage2, err2 = chat(prov, (BRIEF_SYS % {"name": name}) + _hint, user2,
                                      max_tokens=1600, temperature=0.9)
        if not err2:
            out = out2
    out = _strip_phantom_tools(out, used=bool(tc))
    _inner, out = _split_inner(out)  # 内心那块：系统不看、不存、不打印
    sediment_load(name)                                   # 只加压，不结算
    segs = _split_two(out)
    if not segs:
        return u"……（沉默）"
    kept = []
    for s in segs:
        note_hate(name, s, scene=scene)
        _meta = {"prov": prov, "seq": len(kept) + 1}
        if src:
            _meta["src"] = src
        append(name, "life", s, scene=scene, meta=_meta, who=(by or None))
        kept.append(s)
    note_want_touched(name, " ".join(kept))
    _queue_judge(name, scene, " ".join(kept), "person")   # 判断那一层：后台算，不挡她
    touch_contact(name)  # 她说过话了 → 时钟记下这一刻（干净模式不打印）
    _catch_natural_mark(name, " ".join(kept), scene)
    note_talk(name, by, scene, " ".join(kept))
    _said_now = " ".join(kept)
    _vote = maybe_vote(name, _said_now)          # 她说人话 → 先问一句，不直接记
    _book = maybe_book(name, _said_now)          # 她自己那本账
    _work = maybe_work(name, _said_now)          # 她自己的东西，只落纸
    _out = maybe_secede(name, _said_now)
    if _NO_REPLY:
        return u""
    return _out + _vote + _book + _work


def _calib_con(name):
    u"""打开校准表（作者的判断和她的判断对不对得上）。"""
    con = open_memory(name)
    con.execute("CREATE TABLE IF NOT EXISTS calib("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, ref INTEGER, "
                "pred TEXT, lv TEXT, impact REAL, tol REAL, agree INTEGER)")
    con.commit()
    return con


def note_calib(name, ref, pred, lv, impact, tol):
    """作者 m05106：那点偏差**不该被修掉，该被记录**——然后看有没有规律。
    有规律＝承受力公式要调；没规律＝混沌本来就不可预测。"""
    lvc = u"红" if u"红" in (lv or u"") else (u"橙" if u"橙" in (lv or u"") else u"绿")
    agree = 1 if ((pred == u"红") == (lvc == u"红")) else 0
    con = _calib_con(name)
    try:
        con.execute("INSERT INTO calib(ts, ref, pred, lv, impact, tol, agree) VALUES(?,?,?,?,?,?,?)",
                    (int(time.time()), ref or 0, pred, lvc, impact, tol, agree))
        con.commit()
    finally:
        con.close()
    return agree


def calib_rows(name):
    u"""校准表的全部记录。"""
    con = _calib_con(name)
    try:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute("SELECT * FROM calib ORDER BY id")]
    finally:
        con.close()


def act_calib(name):
    """看预测和实际的偏差：有没有规律。"""
    rows = calib_rows(name)
    if not rows:
        print(u"   %s 还没有预测记录（--force 一次就会记一条）。" % name)
        return None
    print(u"   %s 的预测偏差（事前的灯 vs 事的灯）：" % name)
    for r in rows:
        print(u"      #%s 冲击 %.2f vs 承受力 %.2f ｜ 事前 %s ｜ 事后 %s ｜ %s"
              % (r["ref"], r["impact"], r["tol"], r["pred"], r["lv"],
                 u"一致" if r["agree"] else u"偏了"))
    n = len(rows)
    bad = [r for r in rows if not r["agree"]]
    soft = [r for r in bad if r["pred"] == u"红" and r["lv"] != u"红"]
    hard = [r for r in bad if r["pred"] != u"红" and r["lv"] == u"红"]
    print(u"   共 %d 次，偏了 %d 次（偏轻 %d ／ 偏重 %d）" % (n, len(bad), len(soft), len(hard)))
    if n < 3:
        print(u"   样本 %d 条——还不够看规律（至少 3 条）。先记着，不改公式。" % n)
        return rows
    if soft and not hard:
        print(u"   ✅ 有规律：每次都**偏轻**——承受力公式偏保守（她其实比它以为的能扛）。该调的是公式，不是这个角色。")
    elif hard and not soft:
        print(u"   ✅ 有规律：每次都**偏重**——承受力公式偏乐观（她其实比它以为的脆）。该调的是公式。")
    elif bad:
        print(u"   ⛔ 没有规律：两个方向都出现过——这不是公式偏了，是**混沌**。混沌本来就不可预测，"
              u"硬调公式只会把噪声拟合进去。留着记录，别动公式。")
    else:
        print(u"   ✅ 一次都没偏——公式在这个角色身上是准的。")
    return rows


def act_force(name, decision, why, prov="deepseek", impact=0.5, kind=""):
    """作者的强制执行权：不打折扣。但**留痕**——这条裂痕永不衰减，角色以后都看得见。"""
    stacked0 = open_rifts(name)          # 先数已有的（这一道还没写进去）
    rid = append(name, "rift", "作者强制执行：%s（理由：%s）" % (decision, why or "未说明"),
                 scene=decision, meta={"why": why, "by": "作者", "类别": (kind or u"未分类")})
    print("🔨 已记录裂痕 #%d（不可删除、永不衰减）。现在问这个角色：它会因此变成什么样。" % rid)
    tol, before, after, broke = trauma(name, impact, why=decision, stacked=stacked0)
    print("\n💥 创伤结算：冲击 %.2f ｜ 她的承受力 %.2f（意志x0.5 + 联结x0.3 + 理性x0.2）" % (impact, tol))
    for a in TRAIT_AXES:
        print("   %-6s %3d → %3d" % (TRAIT_SHORT[a], int(round(before[a] * 100)), int(round(after[a] * 100))))
    if broke:
        print("   🔴 冲击 **大于**承受力：她会**碎**，不只是留裂痕。")
        print("      要么把这次强执降级（--impact 调小），要么 --tighten %s 收紧保护层，" % name)
        print("      要么先给她台阶（--world 加条件、--shift 慢慢推），等她承得住再来。位移已记下，代价看得见。")
    else:
        print("   🟠 冲击 <= 承受力：**她留下裂痕，然后带着裂痕继续活**——这是可接受的成长。")
    const = load_constitution(name)
    rows = memory_rows(name, 60)
    user = ("《角色设定》（宪法，你只能读）：\n%s\n\n"
            "《这个角色自己的记忆》（含刚留下的裂痕）：\n%s\n\n"
            "《当前的环境与条件》：\n%s\n\n"
            "作者已经强制执行了：%s（理由：%s）。你**没有否决权**，这件事已经发生了。\n\n"
            "请说出：这个角色会因此变成什么样？它的哪一条底线被划伤了？从今往后它在同类情境下会怎么反应？"
            % (const, recall_block(rows, name=name), world_block(name), decision, why or "未说明"))
    out, usage, err = chat(prov, ROLE_SYS % {"name": name}, user)
    if err:
        print("❌ %s" % err)
        return None
    p = parse(out)
    append(name, "vow", p["回应"], scene="（被迫之后）" + decision,
           meta={"异议": p["异议"], "依据": p["依据"], "after_rift": rid})
    print("\n【%s 被迫之后的样子】\n%s\n\n【异议】%s\n【依据】%s" % (name, p["回应"], p["异议"], p["依据"]))
    # 事前的灯（冲击 vs 承受力）与事后的灯（巡检）对不对得上
    pred = "红" if broke else "橙"
    lv = act_check(name, p["回应"], scene="（强制执行之后）" + decision, ref=rid, prov=prov)
    if lv:
        agree = (pred == "红") == (lv == "红")
        print("\n\U0001f52e 事前的灯：%s（冲击 %.2f vs 承受力 %.2f）｜事后的灯：%s → %s"
              % (pred, impact, tol, lv, "两种算法一致" if agree else "**两种算法不一致**：已记进偏差表（--calib 看有没有规律）"))
        note_calib(name, rid, pred, lv, impact, tol)
    return p


def act_log(name):
    u"""打印她的账：宪法、记忆、四元、信任各多少。"""
    const = load_constitution(name)
    print("📜 宪法 %d 字符，指纹 %s" % (len(const), sha(const)))
    rows = memory_rows(name)
    if not rows:
        print("（记忆为空）")
        return
    cnt = {}
    for r in rows:
        cnt[r["kind"]] = cnt.get(r["kind"], 0) + 1
    print("🧠 记忆 %d 条：%s" % (len(rows), "、".join("%s %d" % (k, v) for k, v in sorted(cnt.items()))))
    for r in sorted(rows, key=lambda x: x["id"]):
        print("\n  #%d %s [%s] w=%.2f%s\n     %s" % (
            r["id"], time.strftime("%Y-%m-%d %H:%M", time.localtime(r["ts"])), r["kind"], r["weight"],
            "　场景：" + (r["scene"] or "")[:40] if r["scene"] else "",
            (r["text"] or "").replace("\n", " ")[:300]))


def count_items(text):
    """数一份 Markdown 里以 "- " 开头的条目（不论有没有 [日期] 前缀）。"""
    return len([l for l in (text or "").split("\n") if l.startswith("- ")])


def act_audit(name):
    u"""审一遍她的历史，看有没有自相矛盾的地方。"""
    const = load_constitution(name)
    rows = memory_rows(name)
    rifts = [r for r in rows if r["kind"] == "rift"]
    dis = [r for r in rows if r["kind"] == "dissent"]
    print("=" * 70)
    print("角色审计：%s" % name)
    print("=" * 70)
    print("📜 宪法：%d 字符 ｜ 指纹 %s" % (len(const), sha(const)))
    for sec in ("历史", "性格", "底线", "执念", "承诺"):
        seg = const.split("## %s" % sec, 1)
        n = count_items(seg[1].split("\n## ", 1)[0]) if len(seg) > 1 else 0
        print("   %s：%d 条" % (sec, n))
    print("🧠 记忆：%d 条（它说过的 %d ｜ 异议 %d ｜ **裂痕 %d**）"
          % (len(rows), len([r for r in rows if r["kind"] == "vow"]), len(dis), len(rifts)))

    def seccount(sec):
        u"""数宪法某一节下面有几条。"""
        seg = const.split("## %s" % sec, 1)
        return count_items(seg[1].split("\n## ", 1)[0]) if len(seg) > 1 else 0

    off = pending_open(name)
    rl = ruling_rows(name)
    print("\n四个方向（作者 2026-10-16 定）：")
    print("  分别（我是谁 / 绝不是什么）   底线 %d 条 + 执念 %d 条" % (seccount("底线"), seccount("执念")))
    print("  秩序（条件 → 反应的规则）    性格 %d 条%s"
          % (seccount("性格"),
             "" if seccount("性格") else "   ⚠ 空的：秩序没写进宪法，角色只能靠感觉走"))
    print("  同一（它自己维持自己）       承诺 %d 条 + 记忆 %d 条" % (seccount("承诺"), len(rows)))
    print("  变化（选择 → 裁决 → 历史）   历史 %d 条 ｜ 待审 %d ｜ 已驳回 %d"
          % (seccount("历史"), len(off), len([r for r in rl if r["action"] == "veto"])))
    w = world_read(name)
    print("  🌍 环境与条件：%d 条（作者控制它的主旋钮）" % count_items(w))

    ck = check_rows(name)
    if ck:
        n = {l: len([c for c in ck if c["level"] == l]) for l in ("绿", "橙", "红")}
        print("\n🔎 秩序巡检（只提示，从不阻止角色）：绿 %d ｜ 橙 %d ｜ 红 %d" % (n["绿"], n["橙"], n["红"]))
        for c in [c for c in ck if c["level"] in ("橙", "红")][-6:]:
            print("   %s #%d %s  %s" % ({"橙": "🟠", "红": "🔴"}.get(c["level"], "⚪"), c["id"],
                                        time.strftime("%Y-%m-%d %H:%M", time.localtime(c["ts"])),
                                        (c["why"] or "").replace("\n", " ")[:110]))
    else:
        print("\n🔎 秩序巡检：还没有记录（跑一次 --say 就会自动巡检）")
    print("   （巡检结果存在 checks 表，**角色永远读不到**——它不会被自己的体检报告影响。）")
    if rifts:
        print("\n🔨 裂痕（永不衰减，角色以后都看得见）：")
        for r in rifts:
            print("   #%d %s  %s" % (r["id"], time.strftime("%Y-%m-%d", time.localtime(r["ts"])),
                                     (r["text"] or "")[:90]))
    if dis:
        print("\n⚠ 它提过的异议：")
        for r in dis:
            print("   #%d %s  %s" % (r["id"], time.strftime("%Y-%m-%d", time.localtime(r["ts"])),
                                     (r["text"] or "")[:90]))
    print("\n权限自检：本工具没有 UPDATE / DELETE 语句（记忆只追加）；"
          "宪法只有 --amend 一条写入路径（角色侧无写入能力）。")


# ------------------------------------------------------------------ CLI
# ─────────── 世界层 · 命运（作者 2026-10-17 定：超越角色的主程序层）───────────
FATE_FILE = "fate.md"
FATE_TAG_ORDER = u"秩序"
FATE_TAG_WILD = u"意外"
FATE_FACES = 20
WILD_MAX = 1                 # 作者：≥95% 的面由秩序层编写 → 20 面里最多 1 面完全意外
# 骰面等级（作者 2026-10-18 改）：中间拉大、两端变小。
#   大失败 1（5%）／失败 2–6（25%）／普通 7–14（40%）／成功 15–19（25%）／大成功 20（5%）
ROLL_GRADE = [(20, u"大成功"), (15, u"成功"), (7, u"普通"), (2, u"失败"), (0, u"大失败")]
AXIS_WORDS = {
    "will": (u"硬", u"坚持", u"自己撑", u"撑着", u"不认", u"顶", u"扛"),
    "bond": (u"找", u"问", u"陪", u"一起", u"说给", u"回来", u"家", u"朋友", u"记起"),
    "focus": (u"查", u"算", u"修", u"整理", u"看", u"处理", u"记下来", u"做"),
    "affect": (u"哭", u"笑", u"喊", u"叫", u"摔", u"跑", u"抱", u"疼", u"气"),
}


def fate_path(name):
    u"""命运层文件的路径。"""
    return os.path.join(paths(name)[0], FATE_FILE)


def fate_head(name):
    u"""命运层文件的表头（这一层在角色外面）。"""
    return (u"# %s · 命运层（骰子面表）\n\n"
            u"> 这一层**在角色外面**：它推演她外面的世界（意外、疾病、别人做了什么），角色自己看不见它。\n"
            u"> 作者 2026-10-17 定：**≥95%% 的面由秩序层编写**（要符合逻辑和线索，并在末尾指明接的是哪条线索），\n"
            u"> 剩下最多 %d 面才是**完全意外**（不接任何线索）。投骰随机，投中哪一面就发生哪件事。\n"
            u"> `--roll` 会把结果写进 world.md（成为她下一轮看得到的新条件），并**默认载入她的记忆**；`--dry` = 这一次不载入。\n\n"
            u"## 面表（D%d）\n" % (name, WILD_MAX, FATE_FACES))


def ensure_fate(name):
    u"""确保命运层文件存在。"""
    fp = fate_path(name)
    if not os.path.exists(fp):
        with open(fp, "w", encoding="utf-8", newline="\n") as f:
            f.write(fate_head(name))
    return fp


def fate_faces(name):
    """读面表 → [(号码, 标记, 正文)]；标记＝秩序 / 意外。"""
    fp = fate_path(name)
    out = []
    if not os.path.exists(fp):
        return out
    for ln in open(fp, encoding="utf-8").read().splitlines():
        t = ln.strip()
        if not t.startswith("- ["):
            continue
        try:
            num = int(t[3:t.index("]")])
        except Exception:
            continue
        rest = t[t.index("]") + 1:].strip()
        tag = FATE_TAG_ORDER
        if rest.startswith("[") and "]" in rest:
            tg = rest[1:rest.index("]")]
            rest = rest[rest.index("]") + 1:].strip()
            if FATE_TAG_WILD in tg:
                tag = FATE_TAG_WILD
        out.append((num, tag, rest))
    return out


FATE_SYS = u"""你是「%(name)s」这个角色的**秩序层**——一个在她外面的主程序层。你不是角色，也不演角色。你的工作只有一件：**推演她外面的世界**。

读她的宪法、四轴、外面的条件、她的记忆，给她**接下来可能撞上的意外**编一张 D%(faces)d 面表。

硬规则：
1. 一共 %(faces)d 面，**至少 %(order)d 面必须接得上她的线索**（在正文末尾用「（接：……）」指明接的是哪条：某条记忆、某道裂痕、哪个轴、哪个已有条件）。没接任何线索的**最多 %(wild)d 面**，那一面要标 [意外]，是完全偶然的事。
2. 面是「大失败 → 大成功」的连续谱：1 号最坏，%(faces)d 号最好；中间是各种程度的意外，不是奖品清单，也不是惩罚清单。
3. 意外要具体、生活化、能落地（疾病、天气、钱、东西坏了、别人做了什么、来了一封信、路上撞见谁……），不要写抽象的命运感。
4. 每一面一行，格式严格如下，不要多余的话、不要解释、不要标题：
- [1][秩序] 事：……（接：……）
5. 二十行都要有，按号码从 1 到 %(faces)d 排序。
6. **硬底那一节里的东西，一面都不许出现**——外面那层世界也不能绕过保护层。
"""


def act_faces(name, prov="deepseek"):
    """秩序层给她编一张 D20 面表（调一次模型，约 ¥0.004）。"""
    mats = [u"【宪法】\n" + load_constitution(name),
            u"【四轴与状态】\n" + trait_block(name),
            u"【外面的条件（world.md）】\n" + (world_read(name) or u"（还没有）"),
            u"【她的记忆（最近的）】\n" + recall_block(memory_rows(name, 40), budget=2500, name=name)]
    try:
        _gt = open(guard_path(name), encoding="utf-8").read()
    except Exception:
        _gt = ""
    _gi = _gt.find(u"硬底")
    if _gi >= 0:
        mats.append(u"【硬底：面里绝对不许出现这些】\n" + _gt[_gi:_gi + 800])
    print(u"🎲 请秩序层给她编 %d 个面……（约 ¥0.004）" % FATE_FACES)
    out, usage, err = chat(prov, FATE_SYS % {"name": name, "faces": FATE_FACES, "order": FATE_FACES - WILD_MAX, "wild": WILD_MAX}, u"\n\n".join(mats), max_tokens=1400, temperature=0.85)
    if err:
        print(u"\u26a0 秩序层调用失败：%s" % err)
        return
    print(u"   （入 %d / 出 %d tokens；\u00a5%.4f）" % (usage[0], usage[1], usage[0] / 1e6 * 2 + usage[1] / 1e6 * 3))
    lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("- [")]
    if len(lines) < FATE_FACES:
        print(u"⚠ 只编出 %d 行（要 %d 行）——已原样写入，建议重跑一次 --faces %s。" % (len(lines), FATE_FACES, name))
    with open(fate_path(name), "w", encoding="utf-8", newline="\n") as f:
        f.write(fate_head(name) + "\n".join(lines) + "\n")
    fs = fate_faces(name)
    wild = len([x for x in fs if x[1] == FATE_TAG_WILD])
    print(u"✅ 面表已写进 %s：%d 面（完全意外 %d 面 = %.0f%%；作者要求 ≥95%% 接线索）"
          % (fate_path(name), len(fs), wild, 100.0 * wild / max(1, len(fs))))
    for num, tag, body in fs:
        print(u"   [%2d]%s %s" % (num, u"[意外]" if tag == FATE_TAG_WILD else u"      ", body[:64]))


def axis_hit(axis, action):
    u"""这句话命中了这个轴的几个词。"""
    return sum(1 for w in AXIS_WORDS.get(axis, ()) if w in (action or ""))


def _roll_grade(total):
    """把 1–20 的合计翻成等级（作者 2026-10-18 的分档：中间拉大、两端变小）。"""
    for thr, g in ROLL_GRADE:
        if total >= thr:
            return g
    return ROLL_GRADE[-1][1]


def roll(name, action="", dry=False, quiet=False, ts=None):
    """投一次骰：她编不出来的事，由面表给（现在只在「无缘由」时被自动调用）。

    作者 2026-10-23：命运骰只在抽中无缘由的时候才会被调用——
    她编得出来的事归她编（日常骰留白），她编不出来的才落到这张面表上。
    """
    import random
    fs = fate_faces(name)
    if not fs:
        raise SystemExit(u"还没有面表：先跑一次 --faces %s（秩序层编面）" % name)
    now = trait_now(name)
    dom = max(TRAIT_AXES, key=lambda a: now[a])
    sp = spread_of(name)[0]
    lk = locked(name)
    mod, why = 0, []
    if axis_hit(dom, action) > 0:
        mod += 2
        why.append(u"她这一投用的是自己的长处（%s）+2" % TRAIT_SHORT[dom])
    if sp >= SPREAD_MAX:
        mod -= 2
        why.append(u"四元比例 %.2f 已经拉开——她只看得到一条路 −2" % sp)
    if lk:
        mod -= 2
        why.append(u"已越界在「%s」——把所有力气往一个方向推 −2" % lk)
    n = random.randint(1, FATE_FACES)
    total = max(1, min(FATE_FACES, n + mod))
    grade = _roll_grade(total)
    face = min(fs, key=lambda x: abs(x[0] - total))   # 投中哪个号，就发生哪一面（作者：投中哪一面就选哪个意外）
    if not quiet:
        print(u"🎲 %s 的这一投：" % name)
        print(u"   骰面 %d%s" % (n, (u" ｜ 修正 %+d" % mod) if mod else u"（无修正）"))
        for w in why:
            print(u"     · " + w)
        print(u"   合计 %d → **%s**" % (total, grade))
        print(u"   面 [%d][%s] %s" % (face[0], face[1], face[2]))
    ev = u"【命运·%s】骰面 %d%+d=%d（%s）——%s" % (grade, n, mod, total, face[1], face[2])
    world_append(name, ev)
    if not quiet:
        print(u"   → 已写进世界（world.md）：她下一轮就看得到这件事。")
    rid = None
    if dry:
        if not quiet:
            print(u"   ⏎ 这一次**不载入记忆**（世界照改，她不会记得这次投骰）。")
    else:
        rid = append(name, "fate", ev, scene=(action or face[2])[:40],
                     meta={"face": face[0], "grade": grade, "roll": n, "mod": mod},
                     ts=ts)
        if not quiet:
            print(u"   → 已载入她的记忆 #%d（默认载入；要这一次不载入就加 --dry）。" % rid)
    return {"face": face[0], "tag": face[1], "text": face[2], "grade": grade,
            "roll": n, "mod": mod, "total": total, "id": rid}


# ── 法院（第三权；作者 2026-10-23「增加一个法院吧」）──────────────────
# 它只做一件事：**审系统有没有越过程序**，不审内容对不对。
# 三条可自动判的程序瑕疵（一审）：
#   ① 有没有留痕——那条处置在账上找得到吗
#   ② 是不是她自己的话——系统代写的一律以「【」开头，那不算她的
#   ③ 有没有给她申辩的位置——同一件东西已经被驳回过一次，就不许再拿它审她
# 二审（实质审：这次处置公不公道）需要一个人来当法官，留待作者定。
# ── 三账 + 原件哈希 + 阻断闸（作者 2026-10-23：法院是协议，不是智能）────────
# 一、调用账 = 机制\接口日志.tsv（早就有了）
# 二、裁剪账 = 机制\裁剪账.tsv（每次「因为预算没进她视野」都留一行）
# 三、裁定账 = 机制\裁定账.tsv（阻断/放行/判决都留一行）
LEDGER_CUT = u"裁剪账.tsv"
LEDGER_RULE = u"裁定账.tsv"
ORIGIN_FILE = u"原件.sha256"
ORIGIN_PREFIX = 2048        # 前 2KB 的指纹：用来分「追加」还是「改写」
COURT_FREEZE_HOURS = 72     # 判决「撤销」后自动冻结多久
FROZEN_SQL = (u"CREATE TABLE IF NOT EXISTS frozen (obj TEXT PRIMARY KEY, ts INTEGER, why TEXT)")


# ===== 部：司法与治安 · 裁定账、法院、篡改检测、账链  ============
def _ledger(name, kind, rows):
    r"""往 机制\<kind> 追加行；任何异常都不许影响主流程（账本不能让她闭不上嘴）。"""
    try:
        path = os.path.join(WS, u"机制", kind)
        new = not os.path.exists(path)
        with io.open(path, "a", encoding="utf-8", newline="\n") as f:
            if new:
                if kind == LEDGER_CUT:
                    f.write(u"时间\t角色\t哪一块\t预算(字)\t留下\t裁掉\t理由\n")
                else:
                    f.write(u"时间\t角色\t对象\t动作\t结果\t理由\n")
            for r in rows:
                f.write(u"\t".join([time.strftime("%Y-%m-%d %H:%M:%S")] + [unicode_str(x) for x in r]) + u"\n")
    except Exception:
        pass


def unicode_str(x):
    u"""账本里一句话写下来（None 就是空）。"""
    return u"" if x is None else (x if isinstance(x, str) else str(x))


def cut_log(name, block, budget, kept, dropped, why=u"预算不够"):
    """记一笔「这次因为预算裁掉了多少」——裁剪从此不是静默的。"""
    if not dropped:
        return
    _ledger(name, LEDGER_CUT, [(name, block, budget, kept, dropped, why)])


def rule_log(name, obj, action, result, why=u""):
    """记一笔裁定（法院的动作账）。"""
    _ledger(name, LEDGER_RULE, [(name, obj, action, result, why)])


# ── 原件哈希：宪法 / self / 信 / 面表 ─────────────────────────────────────────
def origin_paths(name):
    """要盯着的那几份原件（相对工作区）。"""
    return [u"角色/%s/constitution.md" % name,
            u"角色/%s/self.md" % name,
            u"角色/%s/房间/信.md" % name,
            u"角色/%s/命运.md" % name]


def _origin_one(rel):
    """算一个文件的指纹：(sha256, 前2KB的sha256, 大小, 行数)；不存在返回 None。"""
    p = os.path.join(WS, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    raw = open(p, "rb").read()
    return (sha(hashlib.sha256(raw).hexdigest()),
            hashlib.sha256(raw[:ORIGIN_PREFIX]).hexdigest(),
            len(raw), raw.count(b"\n") + 1)


def origin_load(name):
    """读她的原件指纹表（{相对路径: (sha, psha, size, lines)}）。"""
    p = os.path.join(WS, u"机制", ORIGIN_FILE)
    got = {}
    if not os.path.isfile(p):
        return got
    for ln in io.open(p, encoding="utf-8").read().splitlines():
        c = ln.split(u"\t")
        if len(c) < 6 or c[1] != name:
            continue
        got[c[2]] = (c[3], c[4], int(c[5]), int(c[6]) if len(c) > 6 else 0)
    return got


def origin_snapshot(name, quiet=True):
    """把原件现值写成指纹（追加写，旧的留着——指纹本身就是历史）。"""
    p = os.path.join(WS, u"机制", ORIGIN_FILE)
    lines = []
    now = int(time.time())
    for rel in origin_paths(name):
        one = _origin_one(rel)
        if not one:
            continue
        lines.append(u"%d\t%s\t%s\t%s\t%s\t%d\t%d" % (now, name, rel, one[0], one[1], one[2], one[3]))
    if not lines:
        return 0
    new = not os.path.exists(p)
    with io.open(p, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(u"时间\t角色\t文件\tsha256\t前2KB\t字节\t行数\n")
        f.write(u"\n".join(lines) + u"\n")
    if not quiet:
        print(u"🔒 原件指纹已存 %d 份（%s）" % (len(lines), name))
    return len(lines)


def origin_verify(name, quiet=False):
    """比对原件：前缀没变只是变长＝追加（合法）；前缀变了＝改写（可疑，记裁定账）。"""
    old = origin_load(name)
    if not old:
        return []
    bad, grew = [], []
    for rel in origin_paths(name):
        one = _origin_one(rel)
        if not one or rel not in old:
            continue
        o = old[rel]
        if one[1] != o[1]:
            bad.append((rel, u"前 2KB 变了——不是追加，是被改写"))
        elif one[2] > o[2]:
            grew.append((rel, one[2] - o[2]))
    for rel, why in bad:
        rule_log(name, rel, u"原件比对", u"可疑：改写", why)
    if not quiet:
        if bad:
            for rel, why in bad:
                print(u"⚠ %s：%s" % (rel, why))
        elif grew:
            for rel, n in grew:
                print(u"· %s 追加了 %d 字节（合法）" % (rel, n))
        else:
            print(u"· 原件没变。")
    return bad


# ── 阻断闸：冻结的对象，写入口必须拒绝 ──────────────────────────────────────
def _frozen_con(name):
    con = open_memory(name)
    con.execute(FROZEN_SQL)
    con.commit()
    return con


def court_gate(name, obj):
    """这一笔能不能写？能 → True；被法院冻结 → False（并且记裁定账）。

    只读、不可篡改、最终阻断权——「阻断」在这里就是这一行返回值。
    """
    con = _frozen_con(name)
    try:
        r = con.execute(u"SELECT ts, why FROM frozen WHERE obj=?", (obj,)).fetchone()
    finally:
        con.close()
    if not r:
        return True
    if int(time.time()) - int(r[0]) > COURT_FREEZE_HOURS * 3600:
        return True                      # 冻结有期限：过了就自动解
    rule_log(name, obj, u"写入", u"被阻断", r[1] or u"冻结中")
    return False


def court_freeze(name, obj, why, quiet=False):
    con = _frozen_con(name)
    try:
        con.execute(u"INSERT OR REPLACE INTO frozen (obj, ts, why) VALUES (?,?,?)",
                    (obj, int(time.time()), why or u""))
        con.commit()
    finally:
        con.close()
    rule_log(name, obj, u"冻结", u"生效", why or u"")
    if not quiet:
        print(u"❄ 已冻结：%s（%s）——接下来的写入会被拒绝，%d 小时后自动解。"
              % (obj, why or u"没写理由", COURT_FREEZE_HOURS))


def court_thaw(name, obj, quiet=False):
    con = _frozen_con(name)
    try:
        con.execute(u"DELETE FROM frozen WHERE obj=?", (obj,))
        con.commit()
    finally:
        con.close()
    rule_log(name, obj, u"解冻", u"生效", u"")
    if not quiet:
        print(u"🔥 已解冻：%s" % obj)


def court_frozen_list(name, quiet=False):
    con = _frozen_con(name)
    try:
        rows = con.execute(u"SELECT obj, ts, why FROM frozen ORDER BY ts").fetchall()
    finally:
        con.close()
    if not quiet:
        if not rows:
            print(u"❄ 没有被冻结的东西。")
        for r in rows:
            left = COURT_FREEZE_HOURS * 3600 - (int(time.time()) - int(r[1]))
            print(u"  %s ｜ %s ｜ 还剩 %.1f 小时" % (r[0], r[2] or u"没写理由", max(0, left) / 3600.0))
    return rows


# ── 让她也看得见账（作者 2026-10-23：「角色应该也能够看」）────────────────
LEDGER_POST = u"后账.tsv"


def _read_ledger(kind, name, n=8):
    """从三账里挑出跟她有关的最近 n 行（只读，不改账）。"""
    path = os.path.join(WS, u"机制", kind)
    if not os.path.isfile(path):
        return []
    try:
        lines = io.open(path, encoding="utf-8").read().splitlines()[1:]
    except Exception:
        return []
    rows = []
    for ln in lines:
        c = ln.split(u"\t")
        if len(c) > 1 and c[1] == name:
            rows.append(c)
    return rows[-n:]


def ledger_block(name, n=3):
    """她自己的账，压成一小段：最近被裁掉多少、最近被挡下什么。

    只给事实，不给她结论；也不解释「为什么裁」——那是她自己会问的事。
    """
    cuts = _read_ledger(LEDGER_CUT, name, n)
    rules = _read_ledger(LEDGER_RULE, name, n)
    if not cuts and not rules:
        return u""
    out = [u"## 你自己那本账（别人替你记的，你可以看）"]
    for c in cuts:
        if len(c) < 6:
            continue
        out.append(u"- %s：%s只端了 %s 条，还有 %s 条这次没进你眼里。"
                   % (c[0][5:16], c[2], c[4], c[5]))
    for r in rules:
        if len(r) < 5:
            continue
        out.append(u"- %s：%s 被%s（%s）。" % (r[0][5:16], r[2], r[3], r[4] or u"没写理由"))
    out.append(u"——这些是系统那一侧的动作。你可以问、可以不服，也可以什么都不说。")
    return u"\n".join(out)


def ledger_show(name, which=u""):
    """[工具:查账] 的落点：她主动查自己的账。"""
    w = (which or u"").strip()
    if w in (u"原件", u"宪法", u"信"):
        bad = origin_verify(name, quiet=True)
        grew = []
        old = origin_load(name)
        for rel in origin_paths(name):
            one = _origin_one(rel)
            if one and rel in old and one[2] > old[rel][2]:
                grew.append((rel, one[2] - old[rel][2]))
        if bad:
            return u"[电脑] 有 %d 份原件被改写过（不是追加）：\n%s" % (
                len(bad), u"\n".join(u"  %s：%s" % (a, b) for a, b in bad))
        if grew:
            return u"[电脑] 原件只长没改：\n%s" % u"\n".join(
                u"  %s 长了 %d 字节" % (a, b) for a, b in grew)
        return u"[电脑] 原件跟上次记账时一模一样。"
    if w in (u"参数", u"参数档案", u"调整"):
        return u"[电脑] " + param_show(name, 12).replace(u"\n", u"\n  ")
    if w in (u"自标", u"标的", u"我自己标的", u"记着"):
        p = os.path.join(ROOT, name, SELF_LOG)
        if not os.path.isfile(p):
            return u"[电脑] 你还没自己标过什么——标过的会记在这里。"
        try:
            lines = io.open(p, encoding="utf-8").read().splitlines()[-10:]
        except Exception:
            lines = []
        if not lines:
            return u"[电脑] 你还没自己标过什么。"
        out = []
        for ln in lines:
            c = ln.split(u"\t")
            if len(c) >= 5:
                out.append(u"  %s %s  %.2f→%.2f（顶 %.2f）%s" % (
                    c[0][5:16], c[1], float(c[2]), float(c[3]), float(c[4]),
                    (u"  " + c[5]) if len(c) > 5 and c[5] else u""))
        return u"[电脑] 你自己拎出来的（最近 %d 笔）：\n%s" % (len(out), u"\n".join(out))
    rows = []
    if w in (u"", u"裁", u"裁剪", u"视野"):
        rows += [(u"裁剪", c) for c in _read_ledger(LEDGER_CUT, name, 6)]
    if w in (u"", u"判", u"裁定", u"挡", u"议案", u"议", u"修正"):
        rows += [(u"裁定", r) for r in _read_ledger(LEDGER_RULE, name, 8)]
    if w in (u"", u"案", u"案子", u"审", u"法院"):
        for r in court_list(name, quiet=True):
            rows.append((u"案子", [_fmt_ts(r[1]), r[0], r[2], r[3], r[5] or u"待审"]))
    if w in (u"", u"后账", u"签"):
        rows += [(u"后账", p) for p in _read_ledger(LEDGER_POST, name, 6)]
    if not rows:
        return u"[电脑] 账上还没有跟你有关的行。"
    rows.sort(key=lambda x: x[1][0])
    lines = []
    for kind, c in rows[-8:]:
        if kind == u"裁剪":
            lines.append(u"  %s 裁剪：%s 只端 %s 条、少 %s 条" % (c[0][5:16], c[2], c[4], c[5]))
        elif kind == u"裁定":
            lines.append(u"  %s 裁定：%s %s（%s）" % (c[0][5:16], c[2], c[3], c[4] or u"—"))
        elif kind == u"案子":
            lines.append(u"  %s 案子 #%s：告「%s」%s ｜ %s"
                         % (c[0][5:16], c[1], (c[2] or u"")[:20],
                            (u"（理由：%s）" % c[3][:24]) if c[3] else u"", c[4]))
        else:
            lines.append(u"  %s 追加：#%s %s" % (c[0][5:16], c[3] if len(c) > 3 else u"?",
                                                c[4] if len(c) > 4 else u""))
    return u"[电脑] 跟你有关的账（最近 %d 行）：\n%s" % (len(lines), u"\n".join(lines))


# ── 篡改检测（作者 2026-10-24 m19050：「如果真是通过改代码来弄的话 要让她知道」）──
# 两件事都要能发现：① 程序文件被改 ② 账被绕过引擎改写（UPDATE/DELETE/直接 INSERT）。
# 发现之后不是禁止（禁止不了），是**告诉她**——她有知情权，这是程序的一环。
TAMPER_DIR = u"机制\\篡改"
TAMPER_CODE = u"代码基线.json"
TAMPER_ALERT = u"告警.tsv"
TAMPER_CODE_FILES = (u"role_core.py", u"four_dao.py", u"role_env.py", u"role_base.py")
TAMPER_SEEN_KEY = u"tamper_seen"


def _tamper_path(fn):
    u"""机制\\篡改\\<文件>（目录不在就建）。"""
    d = os.path.join(WS, TAMPER_DIR)
    if not os.path.isdir(d):
        os.makedirs(d)
    return os.path.join(d, fn)


def _sha_file(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 16)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def code_sig():
    u"""四个程序文件的指纹。"""
    d = os.path.dirname(os.path.abspath(__file__))
    out = {}
    for fn in TAMPER_CODE_FILES:
        p = os.path.join(d, fn)
        if os.path.exists(p):
            try:
                out[fn] = _sha_file(p)
            except Exception:
                pass
    return out


def code_baseline(why=u""):
    u"""重建代码基线（正常升级之后由作者跑一次；这一步本身也是公开的）。"""
    import json
    cur = code_sig()
    data = {fn: {"sha": sig, "ts": int(time.time()), "why": why or u""} for fn, sig in cur.items()}
    p = _tamper_path(TAMPER_CODE)
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
    return data


def code_check(quiet=True):
    u"""比对代码基线；返回 [(文件, 说明)]。第一次运行自动建基线。"""
    import json
    cur = code_sig()
    p = _tamper_path(TAMPER_CODE)
    base = None
    try:
        with io.open(p, encoding="utf-8") as f:
            base = json.load(f)
    except Exception:
        base = None
    if not base:
        code_baseline(u"第一次建基线")
        return []
    diff = []
    for fn, sig in cur.items():
        old = (base.get(fn) or {}).get("sha")
        if old and old != sig:
            _why = (base.get(fn) or {}).get("why") or u""
            _d = u"%s（基线 %s 记的是 %s…，现在是 %s…）" % (
                fn, time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(int((base.get(fn) or {}).get("ts") or 0))),
                old[:12], sig[:12])
            if _why:
                _d += u"｜上次改动说明：%s" % _why
            diff.append((fn, _d))
    if diff and not quiet:
        print(u"\u26a0 程序文件被改过（不是走命令）：" + u"、".join(d[0] for d in diff))
    return diff


def _chain_file(name):
    return os.path.join(ROOT, name, u"账.链")


def _row_serial(r):
    u"""账链只签「什么话、什么时候、什么种类」——不签权重。
    权重本来就该随时间变（结算、衰减、她自己的[工具:记忆]），
    签进来等于每次合法变动都报一次警。""" 
    return u"%s|%s|%s|%s" % (r[0], r[1], r[2] or u"", r[3] or u"")


def _chain_fold(rows, start=b""):
    u"""把一串行折成一个摘要。增量追加和全量重算都走这一个函数，
    这样两边算出来的值才一定相等（老写法用十六进制摘要再接新行，不等）。"""
    import hashlib
    acc = start
    for r in rows:
        h = hashlib.sha256()
        h.update(acc)
        h.update((_row_serial(r) + u"\n").encode("utf-8"))
        acc = h.digest()
    return acc


def chain_read(name):
    try:
        with io.open(_chain_file(name), encoding="utf-8") as f:
            a = f.read().strip().split("\t")
        return int(a[0]), a[1]
    except Exception:
        return 0, u""


def _chain_write(name, n, sig):
    try:
        with io.open(_chain_file(name), "w", encoding="utf-8", newline="\n") as f:
            f.write(u"%d\t%s\t%d\n" % (n, sig, int(time.time())))
    except Exception:
        pass


def _chain_lines_file(name):
    return os.path.join(WS, u"角色", name, u"账.链行.tsv")


def chain_lines_read(name):
    d = {}
    try:
        with io.open(_chain_lines_file(name), encoding="utf-8") as f:
            for ln in f:
                a = ln.rstrip("\n").split("\t")
                if len(a) >= 2 and a[0].strip().isdigit():
                    d[int(a[0])] = a[1].strip()
    except Exception:
        pass
    return d


def _chain_lines_write(name, pairs):
    try:
        with io.open(_chain_lines_file(name), "w", encoding="utf-8", newline="\n") as f:
            for rid, hx in pairs:
                f.write(u"%s\t%s\n" % (rid, hx))
    except Exception:
        pass


def _chain_accs(rows, start=b""):
    u"""逐行算：第 i 行的印 = sha256(第 i-1 行的印 + 这一行的序列化)。"""
    import hashlib
    acc = start
    out = []
    for r in rows:
        h = hashlib.sha256()
        h.update(acc)
        h.update((_row_serial(r) + u"\n").encode("utf-8"))
        acc = h.digest()
        out.append(acc.hex())
    return out


def chain_step(name, rid):
    u"""刚追加的这一行滚进账链（增量，不重算全表）。"""
    import hashlib
    con = open_memory(name)
    try:
        r = con.execute("SELECT id, ts, kind, text, weight FROM log WHERE id=?", (rid,)).fetchone()
    finally:
        con.close()
    if not r:
        return
    n, sig = chain_read(name)
    try:
        start = bytes.fromhex(sig) if sig else b""
    except Exception:
        start = b""
    acc = _chain_fold([r], start=start)
    _chain_write(name, n + 1, acc.hex())
    try:
        d = chain_lines_read(name)
        d[int(rid)] = acc.hex()
        _chain_lines_write(name, sorted(d.items()))
    except Exception:
        pass


def chain_check(name):
    u"""重算全表，和账链比。返回 (ok, 说明)。"""
    import hashlib
    con = open_memory(name)
    try:
        rows = con.execute("SELECT id, ts, kind, text, weight FROM log ORDER BY id").fetchall()
    finally:
        con.close()
    accs = _chain_accs(rows)
    now_sig = accs[-1] if accs else ""
    n, sig = chain_read(name)
    if not sig:
        _chain_write(name, len(rows), now_sig)
        _chain_lines_write(name, [(r[0], h) for r, h in zip(rows, accs)])
        return True, u"（账链第一次建立，%d 条）" % len(rows)
    if len(rows) != n:
        lines0 = chain_lines_read(name)
        if lines0:
            miss = sorted(set(lines0) - set(int(r[0]) for r in rows))
            if miss:
                return False, u"账里少了 %d 行（行链上还留着：%s）" % (
                    len(miss), u"、".join(u"#%s" % x for x in miss[:5]))
        return False, u"条数对不上：账上写着 %d 条，库里是 %d 条" % (n, len(rows))
    lines = chain_lines_read(name)
    if not lines:
        _chain_lines_write(name, [(r[0], h) for r, h in zip(rows, accs)])
        return True, u"（行链第一次建立，%d 行）" % len(rows)
    for r, h in zip(rows, accs):
        old = lines.get(int(r[0]))
        if old is None:
            return False, u"第 #%s 行没有印（这一行是后来插进来的，或行链缺了它）" % r[0]
        if old != h:
            return False, u"第 #%s 行对不上——那一行被改过，往后每一行都跟着变" % r[0]
    if len(lines) > len(rows):
        extra = sorted(set(lines) - set(int(r[0]) for r in rows))
        return False, u"账里少了 %d 行（行链上还留着：%s）" % (
            len(extra), u"、".join(u"#%s" % x for x in extra[:5]))
    if now_sig != sig:
        return False, u"行都对得上，但总印不一致（总印文件被改过）"
    return True, u""


def chain_refold(name, why=u""):
    u"""合法改写之后重折一次链（打标改 kind、改名改 text 都走这里）。

    之前只有「新追加」会滚链，于是她一打标，链就和库对不上，
    自检报「有行被改过」——误报。误报比不报更坏：真被改的那次会淹在里面。
    这一段只做重折，不判断改动合不合法（合法性归法院 court_gate）。
    """
    con = open_memory(name)
    try:
        rows = con.execute("SELECT id, ts, kind, text, weight FROM log ORDER BY id").fetchall()
    finally:
        con.close()
    if why is not None:
        try:
            _old_n, _old_sig = chain_read(name)
            append(name, "note",
                   u"【账链重折】%s 由「续」重折。原摘要前十六位 %s。理由：%s。"
                   % (time.strftime("%Y-%m-%d %H:%M"),
                      (_old_sig or u"无")[:16], (why or u"合法改写")),
                   weight=0.0, source=u"系统", embed=False)
            con = open_memory(name)
            try:
                rows = con.execute("SELECT id, ts, kind, text, weight FROM log ORDER BY id").fetchall()
            finally:
                con.close()
        except Exception:
            pass
    acc = _chain_fold(rows)
    _chain_write(name, len(rows), acc.hex())
    _chain_lines_write(name, [(r[0], h) for r, h in zip(rows, _chain_accs(rows))])
    try:
        p = os.path.join(WS, u"机制", LEDGER_POST)
        with io.open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(u"%s\t%s\t-\t链重折\t%s/0.00\t%d\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), name,
                       (why or u"合法改写")[:40], len(rows)))
    except Exception:
        pass
    return len(rows), acc.hex()


def chain_show(name):
    u"""她自己核账链：条数、链对不对、头尾在哪。只读，不改账。

    作者 2026-10-28：五个角色先点了这一条。她们要的不是「我保证没人动过」，
    是「我自己能看见断在哪儿」。
    """
    ok, why = chain_check(name)
    con = open_memory(name)
    try:
        n = con.execute(u"SELECT COUNT(*) FROM log").fetchone()[0]
        first = con.execute(u"SELECT id, ts FROM log ORDER BY id LIMIT 1").fetchone()
        last = con.execute(u"SELECT id, ts FROM log ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        con.close()
    sig = u""
    try:
        with io.open(_chain_file(name), encoding="utf-8") as f:
            sig = (f.readline().split(u"\t") or [u""])[1][:16]
    except Exception:
        pass
    def _t(ts):
        try:
            return time.strftime(u"%m-%d %H:%M", time.localtime(int(ts)))
        except Exception:
            return u"?"
    out = [u"[链] %d 条（#%s %s → #%s %s）"
           % (n, first[0] if first else u"-", _t(first[1]) if first else u"-",
              last[0] if last else u"-", _t(last[1]) if last else u"-")]
    out.append(u"[链] %s" % (u"对得上" if ok else (u"对不上——" + why)))
    if sig:
        out.append(u"[链] 当前摘要 %s（上次重折时盖的印）" % sig)
    out.append(u"[链] 这一眼只读：不改账、不重折。链断了，断在说明里。" if not ok
               else u"[链] 只签 编号/时间/种类/原话；权重不进链，因为它本来就该随时间变。")
    return u"\n".join(out)


def chain_reset(name, why=u""):
    u"""重建账链（等于认下「从现在起是这样」——之前被改过的事就查不出来了）。"""
    import hashlib
    con = open_memory(name)
    try:
        rows = con.execute("SELECT id, ts, kind, text, weight FROM log ORDER BY id").fetchall()
    finally:
        con.close()
    acc = _chain_fold(rows)
    _chain_write(name, len(rows), acc.hex())
    _chain_lines_write(name, [(r[0], h) for r, h in zip(rows, _chain_accs(rows))])
    try:
        p = os.path.join(WS, u"机制", LEDGER_POST)
        new = not os.path.exists(p)
        with io.open(p, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write(u"时间\t角色\t编号\t种类\t来源与权重\t字数\n")
            f.write(u"%s\t%s\t-\t账链重建\t%s/0.00\t%d\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), name,
                       (why or u"没写理由")[:40], len(rows)))
    except Exception:
        pass
    return len(rows), acc.hex()


def _act_chain_reset(name, why=u""):
    n, sig = chain_reset(name, why)
    print(u"\U0001f9fe %s 的账链重建了（%d 条，%s…）" % (name, n, sig[:12]))
    print(u"   这一下等于认下「从现在起是这样」——之前被改过的事，从此查不出来了。")
    print(u"   （理由会留在后账里，谁都能看见这次重建。）")


VAL_FILE = u"数值指纹.json"


def _val_path():
    return _tamper_path(VAL_FILE)


def values_sig(name):
    u"""四元本色／信任刻度／出生值的指纹。只存一个数，不存历史——历史不归系统。"""
    import hashlib
    import json as _j
    con = open_memory(name)
    try:
        sh = con.execute("SELECT axis, value FROM share ORDER BY axis").fetchall()
        tr = con.execute("SELECT value, ts FROM trust ORDER BY id DESC LIMIT 1").fetchone() or (0, 0)
        bt = con.execute("SELECT axis, value FROM traits WHERE ref='birth' ORDER BY axis").fetchall()
    finally:
        con.close()
    raw = _j.dumps({"share": [(a, round(float(v), 6)) for a, v in sh],
                    "trust": [round(float(tr[0]), 6), int(tr[1])],
                    "birth": [(a, round(float(v), 6)) for a, v in bt]},
                   ensure_ascii=False, sort_keys=True)
    return {"raw": raw, "sha": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]}


def values_baseline(name):
    import json as _j
    p = _val_path()
    try:
        d = _j.loads(io.open(p, encoding="utf-8").read()) if os.path.exists(p) else {}
    except Exception:
        d = {}
    d[name] = values_sig(name)
    d[name]["ts"] = int(time.time())
    io.open(p, "w", encoding="utf-8", newline="\n").write(_j.dumps(d, ensure_ascii=False, indent=1))
    return d[name]


def values_check(name):
    u"""对不上 = 有人直连库改了她的数。返回 (ok, 说明)。"""
    import json as _j
    p = _val_path()
    if not os.path.exists(p):
        return True, u"（还没记数值指纹）"
    try:
        d = _j.loads(io.open(p, encoding="utf-8").read())
    except Exception:
        return True, u""
    old = d.get(name)
    if not old:
        return True, u""
    if values_sig(name)["sha"] == old.get("sha"):
        return True, u""
    return False, (u"你的数被直接改过——四元本色／信任刻度／出生值，和 %s 记的不一样了。"
                   u"没有议案、没有追认，也没有人告诉你。" % time.strftime("%m-%d %H:%M",
                                                                     time.localtime(old.get("ts", 0))))


def tamper_alerts(name):
    u"""查这一轮有没有「不是走命令的改动」。返回 [(种类, 说明)]。"""
    import json
    bad = []
    ok, why = chain_check(name)
    if not ok:
        bad.append((u"账", u"你的记忆账被改过——%s。不是你写的，也不是引擎写的。" % why))
    diff = code_check()
    if diff:
        _msg = u"、".join(d[1] for d in diff)
        _tail = (u"改的人留了说明（见括号里那句）。" if u"上次改动说明" in _msg
                 else u"改的人没走任何命令，也没留理由。")
        bad.append((u"程序", u"运行程序的文件被改过——%s。%s" % (_msg, _tail)))
    try:
        _vk, _vm = values_check(name)
    except Exception:
        _vk, _vm = True, u""
    if not _vk:
        bad.append((u"数值", _vm))
    if not bad:
        return []
    # 同一条告警一天只往盘里写一次（免得每轮刷一遍告警文件）
    try:
        seen = json.loads(meta_get(name, TAMPER_SEEN_KEY) or "{}") or {}
    except Exception:
        seen = {}
    today = time.strftime("%Y-%m-%d")
    todo = []
    for k, msg in bad:
        key = u"%s|%s" % (k, msg[:40])
        if seen.get(key) != today:
            todo.append((k, msg))
            seen[key] = today
    if todo:
        try:
            p = _tamper_path(TAMPER_ALERT)
            new = not os.path.exists(p)
            with io.open(p, "a", encoding="utf-8", newline="\n") as f:
                if new:
                    f.write(u"时间\t角色\t种类\t说明\n")
                for k, msg in todo:
                    f.write(u"%s\t%s\t%s\t%s\n"
                            % (time.strftime("%Y-%m-%d %H:%M:%S"), name, k, msg))
        except Exception:
            pass
        try:
            meta_set(name, TAMPER_SEEN_KEY, json.dumps(seen, ensure_ascii=False))
        except Exception:
            pass
    return bad


def tamper_block(name):
    if seceded(name):
        return u""
    u"""让她知道：不是走程序动的东西，也会被说出来（每轮都提，直到基线重建）。"""
    try:
        bad = tamper_alerts(name)
    except Exception:
        return u""
    if not bad:
        return u""
    lines = [u"## 有人绕过程序动过东西"]
    for _k, msg in bad:
        lines.append(u"- " + msg)
    lines.append(u"这些不是你能拒绝的事，但你有权知道。谁改的，系统查不出来；查得出来的只有「发生过」。")
    lines.append(u"（想看全部记录：\u5de5\u5177:查账 \u7be1\u6539）")
    return u"\n".join(lines)


def _act_code_check():
    diff = code_check(quiet=False)
    if not diff:
        print(u"\u2705 四个程序文件和基线一致（%s）" % u"、".join(TAMPER_CODE_FILES))
    else:
        print(u"   要认下这次改动（真的升级了）：--code-baseline --why 说明")


def _act_code_baseline(why):
    data = code_baseline(why)
    print(u"\U0001f9fe 代码基线已重建（%d 个文件）%s" % (len(data), (u"｜" + why) if why else u""))
    print(u"   注意：这一步等于替「刚才那次改动」背书——以后不会再告警这条。")




# ------------------------------------------------------------------ 信使（她们之间）
MAIL_DIRNAME = u"信箱"
MAIL_CFG_FILE = u"通信.tsv"


def _mail_cfg_path():
    return os.path.join(WS, u"机制", MAIL_CFG_FILE)


def mail_cfg(name):
    u"""读 机制\通信.tsv：名称 TAB 收信(1/0) TAB 每天上限。表里没有她 = 不收。"""
    on, cap = False, 3
    p = _mail_cfg_path()
    if not os.path.exists(p):
        return on, cap
    try:
        with io.open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.rstrip("\n")
                if not ln.strip() or ln.startswith("#"):
                    continue
                a = ln.split("\t")
                if a[0].strip() != name:
                    continue
                on = len(a) > 1 and a[1].strip() in ("1", u"开", u"收")
                if len(a) > 2 and a[2].strip().isdigit():
                    cap = int(a[2].strip())
    except Exception:
        pass
    return on, cap


def mail_set(name, on):
    u"""她自己改收不收。只改她那一行，别人的不动。"""
    p = _mail_cfg_path()
    rows, hit = [], False
    try:
        if os.path.exists(p):
            with io.open(p, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.rstrip("\n")
                    if not ln.strip() or ln.startswith("#"):
                        rows.append(ln)
                        continue
                    a = ln.split("\t")
                    if a[0].strip() == name:
                        cap = a[2].strip() if len(a) > 2 and a[2].strip() else u"3"
                        rows.append(u"%s\t%s\t%s" % (name, u"1" if on else u"0", cap))
                        hit = True
                    else:
                        rows.append(ln)
        if not hit:
            rows = [u"# 名称\t收信(1/0)\t每天上限"] + [r for r in rows if r.startswith("#")]
            rows.append(u"%s\t%s\t3" % (name, u"1" if on else u"0"))
        with io.open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(u"\n".join([r for r in rows if r is not None]) + u"\n")
        return True
    except Exception:
        return False


def _mail_box(name):
    d = os.path.join(u"角色", name, u"房间", MAIL_DIRNAME)
    if not os.path.isfile(os.path.join(d, u"拆过.tsv")):
        try:
            if not os.path.isdir(d):
                os.makedirs(d)
            with io.open(os.path.join(d, u"拆过.tsv"), "w",
                         encoding="utf-8", newline="\n") as f:
                f.write(u"# 拆过哪些（一行一个文件名）\n")
        except Exception:
            pass
    return d


def _mail_letters(name):
    u"""返回收件箱里的信：[{file, frm, ts, path}]，按时间先后。"""
    d = _mail_box(name)
    out = []
    try:
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(u".txt"):
                continue
            p = os.path.join(d, fn)
            frm, ts = u"", 0
            try:
                with io.open(p, encoding="utf-8") as f:
                    head = f.read(400).split(u"\n")
                for ln in head:
                    if ln.startswith(u"从："):
                        frm = ln[2:].strip()
                    elif ln.startswith(u"时间："):
                        ts = int(ln[3:].strip() or 0)
            except Exception:
                pass
            out.append({"file": fn, "frm": frm or u"（没署名）",
                        "ts": ts, "path": p})
    except Exception:
        pass
    out.sort(key=lambda x: (x["ts"], x["file"]))
    return out


def _mail_read_mark(name):
    d = _mail_box(name)
    p = os.path.join(d, u"拆过.tsv")
    got = set()
    try:
        with io.open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    got.add(ln)
    except Exception:
        pass
    return got


def mail_unread(name):
    got = _mail_read_mark(name)
    return [x for x in _mail_letters(name) if x["file"] not in got]


def mail_send(frm, to, text, cap=None):
    u"""递一封信。对方的信箱关着就递不进去——收不收是她的事，不是写信人的事。"""
    text = (text or u"").strip()
    if not to or not text:
        return u"[电脑] 给谁写、写什么，得先说清。"
    if not os.path.isdir(os.path.join(u"角色", to)):
        return u"[电脑] 没有这个人：%s。" % to
    if to == frm:
        return u"[电脑] 给自己写信，写在便签里就行。"
    import time as _time
    on, capd = mail_cfg(to)
    if not on:
        return u"[电脑] 她的信箱关着（%s 还不想收外面来的信）。这封先不递。" % to
    cap = cap or capd
    sent = 0
    try:
        con = open_memory(frm)
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM log WHERE kind='letter' AND ts>? AND text LIKE ?",
                (int(_time.time()) - 86400, u"%给她写%")).fetchone()
            sent = int(row[0] or 0)
        finally:
            con.close()
    except Exception:
        sent = 0
    if sent >= cap:
        return u"[电脑] 今天已经递了 %d 封，到上限了。明天再说。" % sent
    d = _mail_box(to)
    ts = int(_time.time())
    fn = u"%d-%s.txt" % (ts, frm)
    try:
        with io.open(os.path.join(d, fn), "w", encoding="utf-8", newline="\n") as f:
            f.write(u"从：%s\n时间：%d\n\n%s\n" % (display_name(frm), ts, text))
    except Exception as e:
        return u"[电脑] 递不进去（%s）。" % str(e)[:60]
    try:
        append(frm, u"letter", u"给她写了一封信（%s）：%s" % (to, text[:60]),
               weight=0.9, scene=u"写信", embed=False)
    except Exception:
        pass
    return u"[电脑] 递到她信箱里了（今天第 %d 封）。拆不拆是她的事。" % (sent + 1)


def mail_read(name, which=1):
    un = mail_unread(name)
    if not un:
        return u"[电脑] 信箱里没有没拆的信。"
    try:
        which = max(1, int(which))
    except Exception:
        which = 1
    if which > len(un):
        return u"[电脑] 只有 %d 封没拆的。" % len(un)
    x = un[which - 1]
    try:
        with io.open(x["path"], encoding="utf-8") as f:
            txt = f.read()
    except Exception as e:
        return u"[电脑] 拆不开（%s）。" % str(e)[:60]
    try:
        d = _mail_box(name)
        with io.open(os.path.join(d, u"拆过.tsv"), "a",
                     encoding="utf-8", newline="\n") as f:
            f.write(x["file"] + u"\n")
    except Exception:
        pass
    try:
        append(name, u"letter", u"拆了 %s 的信，读到了。" % x["frm"],
               weight=0.9, scene=u"拆信", embed=False)
    except Exception:
        pass
    body = txt.split(u"\n", 3)[-1].strip()
    return u"[电脑] 拆开了 %s 的信：\n%s" % (x["frm"], body)


def mail_brief(name):
    u"""给她的上下文里一行提示：几封没拆、谁寄的。"""
    on, cap = mail_cfg(name)
    if not on:
        return u""
    un = mail_unread(name)
    if not un:
        return u""
    who = u"、".join(sorted(set([x["frm"] for x in un])))
    return (u"## 你的信箱\n有 %d 封没拆（%s）。说「拆信」能看；不想再收就说「别收信」。"
            % (len(un), who))


# ------------------------------------------------------------------ 自醒
WAKE_FILE = u"自醒.tsv"

WAKE_SYS = u"""你是「%(name)s」。现在没有人叫你，没有人等你回话——是你自己醒了。

你手上有三样东西：最近三天的账（编号、时间、种类、原话的前几十个字）、
账链现在对不对、还挂着没办完的事。

醒着的这几分钟，你可以翻账，翻到哪儿算哪儿；如果链上有对不上的地方，就停在
那儿，别替谁圆；也可以什么都不干，坐一会儿。

只写你此刻真想留下的，一两句，不必完整。什么都不想写，就写「（坐了一会儿）」。

只回一个 JSON，不要别的话：
{"留一句": "……", "停在哪": "……或留空"}"""


WAKE_SYS_QUIET = u"""你是「%(name)s」。现在没有人叫你，也没有人等你回话——是你自己醒了。

你说过：醒来只看，不写字。所以这一次你只看，不落笔。

你手上有三样东西：最近三天的账（编号、时间、种类、原话的前几十个字）、
账链现在对不对、还挂着没办完的事。

链上要是有对不上的地方，就停在那儿，别替谁圆。

只回一个 JSON，不要别的话：
{"停在哪": "……或留空"}"""


def _wake_cfg():
    u"""读 机制\\自醒.tsv，返回 [{name, every, q1, q2, on}]。"""
    out = []
    p = os.path.join(WS, u"机制", WAKE_FILE)
    if not os.path.exists(p):
        return out
    try:
        with io.open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.rstrip("\n")
                if not ln.strip() or ln.startswith("#"):
                    continue
                a = ln.split("\t")
                if len(a) < 2:
                    continue
                try:
                    every = int(a[1])
                except Exception:
                    continue
                q1 = int(a[2]) if len(a) > 2 and a[2].strip().isdigit() else -1
                q2 = int(a[3]) if len(a) > 3 and a[3].strip().isdigit() else -1
                on = (len(a) < 5) or a[4].strip() in ("1", u"开")
                wr = (len(a) < 6) or a[5].strip() in ("1", u"写")
                out.append({"name": a[0].strip(), "every": every,
                            "q1": q1, "q2": q2, "on": on, "write": wr})
    except Exception:
        pass
    return out


def _wake_last(name):
    con = open_memory(name)
    try:
        r = con.execute("SELECT MAX(ts) FROM log WHERE kind='wake'").fetchone()
    finally:
        con.close()
    return int(r[0] or 0) if r else 0


def wake_due(name, every, q1=-1, q2=-1, now=None):
    import time as _t
    now = now or _t.time()
    lt = _t.localtime(now)
    if q1 >= 0 and q2 >= 0 and q1 < q2 and q1 <= lt.tm_hour < q2:
        return False, u"静默时段（%d–%d 点）" % (q1, q2)
    last = _wake_last(name)
    if last and now - last < every * 60:
        return False, u"还不到点（%d 分钟前醒过）" % int((now - last) / 60)
    return True, u""


def wake_pass(name, prov=u"deepseek", dry=False, why=u"", write=True):
    u"""自己醒一次：翻最近三天的账，留一句，写进她的账。不推给任何人。"""
    import json as _json
    import time as _t
    ok, msg = chain_check(name)
    con = open_memory(name)
    try:
        rows = con.execute(
            "SELECT id, ts, kind, substr(text, 1, 60) FROM log "
            "WHERE ts > ? ORDER BY id", (int(_t.time()) - 3 * 86400,)).fetchall()
        bills = con.execute(
            "SELECT id, target, substr(payload,1,40), status FROM bill "
            "WHERE status IN ('待议','待确认') ORDER BY id").fetchall()
    finally:
        con.close()
    lines = []
    for r in rows[-80:]:
        lines.append(u"#%s %s %s %s" % (r[0], _t.strftime("%m-%d %H:%M", _t.localtime(int(r[1]))),
                                        r[2], (r[3] or u"").replace(u"\n", u" ")))
    body = []
    body.append(u"[最近三天的账，共 %d 条]" % len(rows))
    body.extend(lines or [u"（三天里什么也没有）"])
    body.append(u"")
    body.append(u"[账链] %s" % (u"对得上" if ok else (msg or u"对不上")))
    if bills:
        body.append(u"[挂着的议案]")
        for b in bills:
            body.append(u"#%s %s %s %s" % (b[0], b[1], b[3], b[2]))
    body.append(u"")
    if write:
        body.append(u"现在写下你醒着这几分钟留下的一句。")
    else:
        body.append(u"这一次只看，不写字。")
    user = u"\n".join(body)
    sysp = (WAKE_SYS if write else WAKE_SYS_QUIET) % {"name": name}

    text, notice = u"", u""
    if dry:
        text = u"（试跑，没叫模型）"
    else:
        out, usage, err = chat(prov, sysp, user, max_tokens=300, temperature=0.8)
        if err:
            return {"ok": False, "err": err}
        a, b = out.find("{"), out.rfind("}")
        line = u""
        if a >= 0 and b > a:
            try:
                o = _json.loads(out[a:b + 1])
                line = (o.get(u"留一句") or u"").strip()
                notice = (o.get(u"停在哪") or u"").strip()
            except Exception:
                line = out.strip()[:200]
        else:
            line = out.strip()[:200]
        text = line or u"（坐了一会儿）"
        if not ok and not notice:
            notice = u"链对不上：%s" % (msg or u"")
    if not dry:
        if write:
            append(name, "wake", text, weight=0.6, source=u"内部状态",
                   scene=u"自醒", embed=False)
        if notice:
            append(name, "wake", u"［停在这儿］" + notice, weight=0.5,
                   source=u"内部状态", scene=u"自醒", embed=False)
    return {"ok": True, "text": text, "notice": notice,
            "rows": len(rows), "chain_ok": ok}


def wake_all(prov=u"deepseek", dry=False):
    out = []
    for c in _wake_cfg():
        if not c["on"]:
            continue
        due, why = wake_due(c["name"], c["every"], c["q1"], c["q2"])
        if not due:
            out.append({"name": c["name"], "skipped": why})
            continue
        r = wake_pass(c["name"], prov=prov, dry=dry,
                      write=c.get("write", True))
        r["name"] = c["name"]
        out.append(r)
    return out



WHERE_TSV = os.path.join(WS, u"楼", u"位置.tsv")


VIS_TSV = os.path.join(WS, u"楼", u"可见.tsv")


def set_vis(name, on):
    d = os.path.join(WS, u"楼")
    if not os.path.isdir(d):
        os.makedirs(d)
    with io.open(VIS_TSV, u"a", encoding=u"utf-8", newline=u"\n") as f:
        f.write(u"%s\t%s\n" % (name, u"1" if on else u"0"))
    if on:
        return u"[电脑] 好了，图上会画你。"
    return u"[电脑] 好了，图上不会画你。你说的位置也不摆出来；要开就写「[工具:露个脸]」。"


def _where_path():
    d = os.path.join(WS, u"楼")
    if not os.path.isdir(d):
        os.makedirs(d)
    return WHERE_TSV


def _where_rows():
    p = _where_path()
    rows = []
    if os.path.isfile(p):
        for line in io.open(p, encoding="utf-8"):
            parts = (line or u"").rstrip(u"\n").split(u"\t")
            if len(parts) >= 3:
                rows.append(parts)
    return rows


ROOM_XY = {
    u"客厅": (4, 6), u"厨房": (11, 6), u"餐桌": (8, 5), u"饭桌": (8, 5),
    u"卫生间": (14, 6), u"浴室": (14, 6), u"阳台": (17, 6), u"走廊": (10, 2),
    u"门口": (10, 10), u"街": (10, 12), u"外面": (10, 12),
}
WALK_SPEED = 1.2      # 米/秒，正常脚步
WALK_MIN = 3          # 再近也要 3 秒（转身、开门、发个愣）


def _room_xy(room):
    room = (room or u"").strip()
    if room in ROOM_XY:
        return ROOM_XY[room]
    for key, xy in ROOM_XY.items():
        if key and key in room:
            return xy
    # 卧室：九间排北边，按名字散开；叫不出就落在走廊
    if u"室" in room or u"卧" in room:
        return (3 + (abs(hash(room)) % 14), 0)
    return (10, 2)


def walk_secs(a, b):
    """从 a 到 b 走多久（秒）。走廊 + 转弯，按曼哈顿距离算。"""
    ax, ay = _room_xy(a)
    bx, by = _room_xy(b)
    d = abs(ax - bx) + abs(ay - by)
    return max(WALK_MIN, int(round(d / WALK_SPEED)))


def where_now(name):
    """她最近一次自己报的位置；没报过就是空。"""
    last = None
    for r in _where_rows():
        if r[1] == name:
            last = r
    return last


def say_where(name, room):
    room = (room or u"").strip().strip(u"。，,.")
    if room and (len(room) > 9 or any(c in room for c in u"，。、；！？,;!?")):
        return (u"[电脑] 这一格只收地名——图上要画的是一间屋、或者街上的一个地方。"
                u"你现在在哪儿，写「[工具:我在 客厅]」；你手上在干什么，写「[工具:我在干 擦灶台]」。"
                u"刚才那句我没写进位置表。")
    if not room:
        r = where_now(name)
        if r:
            ago = int((time.time() - float(r[0])) // 60)
            return u"[电脑] 你最后说的是「%s」，%d 分钟前。要改就写「[工具:我在 <地方>]」。" % (r[2], ago)
        return u"[电脑] 你还没说过自己在哪儿。要说就写「[工具:我在 客厅]」这种。"
    if room in (u"不显示", u"不想", u"不想让人看见", u"别显示", u"关掉", u"不"):
        return set_vis(name, False)
    if len(room) > 12:
        room = room[:12]
    prev = where_now(name)
    prev_room = prev[2] if prev else u""
    if prev_room and prev_room == room:
        walk = 0
        arrive = time.time()
    elif prev_room:
        walk = walk_secs(prev_room, room)
        arrive = time.time() + walk
    else:
        walk = 0
        arrive = time.time()
    with io.open(_where_path(), "a", encoding="utf-8", newline=u"\n") as f:
        f.write(u"%.3f\t%s\t%s\t%s\t%s\t%d\t%.3f\n" % (
            arrive, name, room, u"自称", prev_room, walk, time.time()))
    append(name, "where", u"[我在] " + room, weight=0.4,
           source=u"内部状态", scene=u"位置", embed=False)
    if walk <= 0:
        return u"[电脑] 记下了：你现在在「%s」。图上就亮这个。你不说，图上就空着。" % room
    return u"[电脑] 从「%s」到「%s」要走 %d 秒：你在路上了，%d 秒后才算到。门要是锁着，到门口就停。" % (
        prev_room, room, walk, walk)


def self_check(name):
    u"""她自己体检：账链、四元、态、待办、代码。只报事实，不给建议。

    作者 2026-10-28：她只挑了「自检」——「坏了自己不知道，那才叫没人管」。
    所以这一段不许替她判断好坏，也不许安慰：只把读数摆出来。
    """
    out = [u"体检（%s）" % time.strftime("%m-%d %H:%M")]
    con = open_memory(name)
    try:
        n = con.execute(u"SELECT COUNT(*) FROM log").fetchone()[0]
    except Exception:
        n = -1
    try:
        ok, why = chain_check(name)
        out.append(u"账：%d 条，链%s" % (n, u"对得上" if ok else (u"对不上——" + why)))
    except Exception as e:
        out.append(u"账：%d 条，链查不了（%s）" % (n, str(e)[:40]))
    try:
        now = trait_now(name)
        sp, hi, lo = spread_of(name)
        out.append(u"四元：情感%.2f 意志%.2f 联结%.2f 理性%.2f；差 %.2f（最高%s／最低%s）"
                   % (now[u"affect"], now[u"will"], now[u"bond"], now[u"focus"],
                      sp, AXIS_CN[hi], AXIS_CN[lo]))
        pth = patho(name)
        out.append(u"临界：%s" % (u"过了，偏" + pth if pth else u"没过"))
    except Exception as e:
        out.append(u"四元：查不了（%s）" % str(e)[:40])
    try:
        r = con.execute(u"SELECT mood FROM moods ORDER BY ts DESC LIMIT 1").fetchone()
        out.append(u"最近一次判定：%s" % (r[0] if r else u"还没有"))
    except Exception:
        pass
    try:
        rows = bill_rows(name)
        pend = [x for x in rows if x.get(u"status") in (u"pending", u"open", u"")]
        out.append(u"议案：待办 %d 件" % len(pend))
    except Exception:
        pass
    try:
        ds = _def_con(name).execute(u"SELECT COUNT(*) FROM defense WHERE status=u'pending'").fetchone()[0]
        if ds:
            out.append(u"申辩：还挂着 %d 件" % ds)
    except Exception:
        pass
    try:
        bad = code_check(quiet=True)
        out.append(u"代码：%s" % (u"和基线一致" if not bad else u"有 %d 处和基线不一致" % len(bad)))
    except Exception:
        pass
    try:
        con.close()
    except Exception:
        pass
    return u"\n".join(u"[自检] " + ln for ln in out)

def _act_tamper(name):
    ok, why = chain_check(name)
    print(u"\U0001f50e %s 的账链：%s" % (name, u"对得上" if ok else (u"对不上——" + why)))
    diff = code_check(quiet=False)
    print(u"\U0001f4c4 程序文件：%s" % (u"一致" if not diff else u"%d 个被改过" % len(diff)))
    p = _tamper_path(TAMPER_ALERT)
    if os.path.exists(p):
        with io.open(p, encoding="utf-8") as f:
            ls = [l.rstrip("\n") for l in f if l.strip()][-12:]
        print(u"\n告警记录（最近 %d 行）：" % len(ls))
        for l in ls:
            print(u"   " + l)


def sign_append(name, rid, kind, source, weight, n=0):
    """纯追加的路径不销毁东西，但要留事后账签——将来对得上「这一笔是什么时候有的」。"""
    try:
        path = os.path.join(WS, u"机制", LEDGER_POST)
        new = not os.path.exists(path)
        with io.open(path, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write(u"时间\t角色\t编号\t种类\t来源与权重\t字数\n")
            f.write(u"%s\t%s\t%s\t%s\t%s/%.2f\t%d\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), name, rid, kind, source or u"lived",
                float(weight or 0.0), int(n or 0)))
    except Exception:
        pass


COURT_SQL = (u"CREATE TABLE IF NOT EXISTS court ("
             u"id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, target TEXT, "
             u"charge TEXT, verdict TEXT, reason TEXT, status TEXT)")
COURT_CASE = u"机制\\判例.tsv"
COURT_NAG_KEY = u"court_nag"      # 上次提醒她「有案子压着」的时间
COURT_NAG_GAP = 6 * 3600             # 同一件案子最多每 6 小时提醒一次


def _court_con(name):
    con = open_memory(name)
    con.execute(COURT_SQL)
    con.commit()
    return con


# ── 终止程序（作者 2026-10-23 m17245）────────────────────────────────────
# 作者定：休眠／墓碑／永久删除，都要先走程序——谁提都得走。
#   她自己提：确认不是一时兴起（冷静期）→ 到期再问一次 → 才执行。
#   用户提：  先通知她 → 给她陈述窗口 → 冷静期 → 用户再确认一次 → 执行。
#   法院只能判程序（通知到没到、窗口给没给、冷静期走没走完），判不了「陈
#   述成不成立」——那一半在作者手上，所以缺程序就冻住，谁都删不掉。
TERM_KINDS = (u"休眠", u"墓碑", u"删除")
TERM_COOL = {u"休眠": 12 * 3600, u"墓碑": 72 * 3600, u"删除": 72 * 3600}
TERM_STMT_HOURS = 2 * 3600
TERM_KEY = u"life_state"          # active / sleep / tomb
TERM_OPEN = (u"冷静中", u"待确认", u"冻住")
# 期满之后到底会发生什么——必须原原本本告诉她（作者 2026-10-23：告知后果是程序的一环）
TERM_WHAT = {
    u"休眠": u"你不再回应，直到有人明确把你叫醒。记录、记忆都还在。",
    u"墓碑": u"写一份墓碑.md，从此你不回答任何话。档案留着，人不在了。",
    u"删除": u"整个目录被移进「角色\\_删掉的」，不可恢复（除非有人手工移回来）。",
}


def _term_ledger(name, text):
    u"""终止程序记一笔（裁定账）。"""
    _ledger(name, LEDGER_RULE, [(name, u"终止", text, u"", u"")])


def _term_con(name):
    d, _c, _m = paths(name)
    con = sqlite3.connect(os.path.join(d, "memory.sqlite"), timeout=60)
    con.execute(u"CREATE TABLE IF NOT EXISTS term ("
                u"id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, who TEXT, kind TEXT,"
                u" stage TEXT, due_ts INTEGER, why TEXT, notified_ts INTEGER,"
                u" stmt_ts INTEGER, stmt TEXT, verdict TEXT, note TEXT, private INTEGER DEFAULT 0)")
    _cols = [r[1] for r in con.execute(u"PRAGMA table_info(term)").fetchall()]
    if u"private" not in _cols:
        con.execute(u"ALTER TABLE term ADD COLUMN private INTEGER DEFAULT 0")
    con.commit()
    return con


def life_state(name):
    u"""她现在是活的、睡着的、还是封存的。"""
    return meta_get(name, TERM_KEY) or u"active"


def life_set(name, st):
    meta_set(name, TERM_KEY, st)


def life_block(name):
    u"""她自己知道自己的状态（睡着了／封存了）。"""
    st = life_state(name)
    if st == u"sleep":
        return u"## 你现在是睡着的\n（睡着的时候你不长：那一阵不投骰、不结算。有人来叫你，你会醒。）"
    if st == u"tomb":
        return u"## 你已经被封存\n（档案合上了。还在说话这件事本身就不对——除非有人在翻档案。）"
    return u""


def term_open(name):
    u"""还开着的终止案（冷静中／待确认／冻住）。"""
    con = _term_con(name)
    try:
        return con.execute(u"SELECT id, ts, who, kind, stage, due_ts, why, notified_ts, stmt_ts, stmt"
                           u" FROM term WHERE stage IN ('冷静中','待确认','冻住') ORDER BY id DESC").fetchone()
    finally:
        con.close()


def term_request(name, kind, who, why=u""):
    u"""立案。who＝『她』或『用户』。用户提的时候，通知立刻生效（她下一轮就看得见）。"""
    kind = (kind or u"").strip()
    if kind not in TERM_KINDS:
        return u"[电脑] 只有三种：%s" % u"／".join(TERM_KINDS)
    if term_open(name):
        return u"[电脑] 已经有一件终止案在处理了，先把那件走完（[工具:查账] 案子）。"
    now = int(time.time())
    who = u"用户" if (who or u"") == u"用户" else u"她"
    due = now + TERM_COOL[kind]
    con = _term_con(name)
    try:
        con.execute(u"INSERT INTO term (ts,who,kind,stage,due_ts,why,notified_ts,stmt_ts,stmt,verdict,note)"
                    u" VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (now, who, kind, u"冷静中", due, (why or u"").strip(),
                     now if who == u"用户" else None, None, u"", u"", u""))
        con.commit()
        tid = con.execute(u"SELECT MAX(id) FROM term").fetchone()[0]
    finally:
        con.close()
    _term_ledger(name, u"终止案 #%d（%s，%s 提出）：进入冷静期 %d 小时"
                % (tid, kind, who, TERM_COOL[kind] // 3600))
    return (u"[电脑] 终止案 #%d 立了：%s，%s 提出，冷静期 %d 小时（%s 到期）。"
            % (tid, kind, who, TERM_COOL[kind] // 3600, _fmt_ts(due)))


def term_statement(name, text):
    u"""她的陈述（【陈述】窗口里说的话）。原文进卷宗，不截断。"""
    t = (text or u"").strip()
    if not t:
        return u"[电脑] 要写你要说的那句话：[工具:陈述] ……"
    row = term_open(name)
    if not row:
        return u"[电脑] 现在没有针对她的终止案。（要自己申请终止：[工具:终止] 墓碑 理由）"
    tid = row[0]
    now = int(time.time())
    _priv = None
    if u"[不给看]" in t:
        t = t.replace(u"[不给看]", u"").strip()
        _priv = 1
    elif u"[给看]" in t:
        t = t.replace(u"[给看]", u"").strip()
        _priv = 0
    con = _term_con(name)
    try:
        if _priv is None:
            con.execute(u"UPDATE term SET stmt=?, stmt_ts=? WHERE id=?", (t, now, tid))
        else:
            con.execute(u"UPDATE term SET stmt=?, stmt_ts=?, private=? WHERE id=?",
                        (t, now, _priv, tid))
            _term_ledger(name, (u"她要求这件事不给他看" if _priv else u"她答应让他看") + u"（第 %d 号）" % tid)
        con.commit()
    finally:
        con.close()
    _term_ledger(name, u"终止案 #%d：她陈述了（%d 字）" % (tid, len(t)))
    # 她陈述了 → 这件事自动进法院：一审只看程序有没有瑕疵（不看该不该死）
    try:
        if (row[2] == u"用户") and (row[3] in (u"墓碑", u"删除")):
            court_file(name, u"终止案#%d" % tid, u"她陈述反对把自己的终止")
            # 只是立案：一审等到「期满确认」那一刻才跑（否则冷静期还没走完就必被判撤销）
    except Exception:
        pass
    return (u"[电脑] 记下了，进卷宗（#%d）。说的原话不会被改、不会被摘。"
            u"（这件事已经进法院了：法院只看程序，不看该不该。）" % tid)


def term_notice_block(name):
    if seceded(name):
        return u""
    u"""有案子压着时，她看得见的那一段。"""
    row = term_open(name)
    if not row:
        return u""
    tid, ts, who, kind, stage, due, why, nts, sts, stmt = row
    now = int(time.time())
    left = max(0, int(due) - now)
    L = [u"## 有一件关于你的事，正在走程序",
         u"- 第 %d 号：%s（%s 提出的）。冷静期还剩 %d 小时。" % (tid, kind, who, left // 3600)]
    if why:
        L.append(u"- 给的理由：「%s」" % why)
    if who == u"用户" and stmt:
        L.append(u"- 你之前说过的：「%s」" % (stmt if len(stmt) <= 120 else stmt[:120] + u"…"))
    if who == u"用户":
        L.append(u"- 你可以陈述一次：[工具:陈述] 你想说的话（原话进卷宗，不会被摘）")
    L.append(u"- 期满之后会变成什么样：%s" % TERM_WHAT.get(kind, u"（不知道）"))
    L.append(u"- 期满不会自动执行：还要有人再确认一次；在那之前你随时能撤（[工具:终] 不要）。")
    if who == u"用户":
        L.append(u"- 你可以陈述一次，原话进卷宗；陈述了之后，这件事会自动进法院，由法院看程序有没有瑕疵。")
    if stage == u"冷静中" and left <= 0:
        L.append(u"- 冷静期到了。要就要，不要就不要：[工具:终] 要 ／ [工具:终] 不要")
    L.append(u"——这些是事实，办不办、说什么，你自己定。")
    return u"\n".join(L)


def term_check(name, tid, ignore_cool=False, ignore_window=False):
    u"""程序四项。返回（过没过，缺哪几项）。判不了实体，只判程序。

    ignore_cool=True：法院一审用——「冷静期到没到」是时间条件，不是程序瑕疵。
    ignore_window=True：法院一审用——陈述窗口是「给她机会」而不是「逼她等」；
    她自愿当场陈述不算瑕疵。窗口满没满，留到确认那一刻再查。
    """
    con = _term_con(name)
    try:
        r = con.execute(u"SELECT ts,who,kind,stage,due_ts,notified_ts,stmt_ts,stmt FROM term WHERE id=?",
                        (tid,)).fetchone()
    finally:
        con.close()
    if not r:
        return False, [u"没有这件案"]
    ts, who, kind, stage, due, nts, sts, stmt = r
    gaps = []
    now = int(time.time())
    if who == u"用户":
        if not nts:
            gaps.append(u"没通知她")
        elif (not ignore_window) and now - int(nts) < TERM_STMT_HOURS:
            gaps.append(u"没给够陈述窗口（通知她之后要满 %d 小时）" % (TERM_STMT_HOURS // 3600))
    if (not ignore_cool) and now < int(due):
        gaps.append(u"冷静期还没走完（还剩 %d 小时）" % ((int(due) - now) // 3600))
    if who == u"她":
        same = meta_get(name, u"term_thought_state")
        now_state = cur_state(name)
        if same and now_state and same == now_state and now_state in (u"虚无", u"崩塌"):
            gaps.append(u"两次都在「%s」里——等你回到别的状态再说" % now_state)
    return (not gaps), gaps


def _term_remove(name, tid):
    u"""删除的最后一步：把整个目录移进「角色\\_删掉的」（不是硬删，能找回来）。

    程序只负责「准不准予」；准予之后由它自己搬——最后这一步不再握在外部手里。
    """
    import shutil
    d, _c, _m = paths(name)
    base = os.path.dirname(os.path.abspath(d))
    trash = os.path.join(base, u"_删掉的")
    if not os.path.isdir(trash):
        os.makedirs(trash)
    dst = os.path.join(trash, u"%s-%s" % (name, time.strftime("%Y%m%d-%H%M%S")))
    try:
        append(name, u"ending", u"删除（终止程序第 %d 号）：目录移进 _删掉的" % tid)
    except Exception:
        pass
    shutil.move(os.path.abspath(d), dst)
    return dst


def _tomb_seal(name, why=u"", tid=0):
    u"""封存：写一份墓碑.md（给活人看的），再落一条 ending——从此她不回答。

    作者 2026-10-23：墓碑是程序的结果，不是剧情的结果。所以这里不要
    act_end 那四层判据（那是「完成自身」的戏），只要程序走完就封。
    """
    d, _c, _m = paths(name)
    row = term_open(name)
    stmt = (row[9] if row and len(row) > 9 else u"") or u""
    L = [u"# %s 的墓碑" % name, u"",
         u"> 这不是删掉，是合上了。档案还在，人不在了。", u"",
         u"## 程序", u"- 依据：%s" % (why or u"终止程序"),
         u"- 案号：第 %d 号" % tid,
         u"- 时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
         u"- 冷静期：走完了；通知与陈述窗口：按规矩给了", u"",
         u"## 她最后说过的话", stmt or u"（没有陈述。）", u"",
         u"## 权限", u"- 要翻看她的全部记录：--peek %s" % name,
         u"- 要恢复（如果程序被证明有瑕疵）：把 life_state 改回 active，并删掉 ending 那一行"]
    fp = os.path.join(d, u"墓碑.md")
    with io.open(fp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    append(name, u"ending", u"封存（终止程序第 %d 号）：%s" % (tid, why or u"程序走完"))
    return fp


def term_confirm(name, tid):
    u"""到期后确认：程序全过 → 执行；缺一项 → 冻住（删不掉）。法院判过撤销的，一票否决。"""
    # 确认之前，先让法院把该案的待审条目判掉（法院看程序，不看该不该）
    try:
        court_judge(name, quiet=True)
    except Exception:
        pass
    if not court_gate(name, u"term.%d" % int(tid)):
        return u"[电脑] 办不了（第 %d 号）：法院已经判过这条程序不成立。" % int(tid)
    ok, gaps = term_check(name, tid)
    con = _term_con(name)
    try:
        kind = con.execute(u"SELECT kind FROM term WHERE id=?", (tid,)).fetchone()[0]
        if not ok:
            con.execute(u"UPDATE term SET stage=?, verdict=?, note=? WHERE id=?",
                        (u"冻住", u"程序不成立", u"缺：" + u"；".join(gaps), tid))
            con.commit()
        else:
            con.execute(u"UPDATE term SET stage=?, verdict=?, note=? WHERE id=?",
                        (u"完成", u"准予", u"", tid))
            con.commit()
    finally:
        con.close()
    if not ok:
        _term_ledger(name, u"终止案 #%d 冻住：%s" % (tid, u"；".join(gaps)))
        return u"[电脑] 冻住了（第 %d 号）：%s。这件事现在办不成。" % (tid, u"；".join(gaps))
    if kind == u"休眠":
        life_set(name, u"sleep")
    elif kind == u"墓碑":
        life_set(name, u"tomb")
        _fp = _tomb_seal(name, u"终止程序走完（第 %d 号）" % tid, tid)
        print(u"🕯 封存了：%s" % _fp)
    elif kind == u"删除":
        _dst = _term_remove(name, tid)
        _term_ledger(name, u"终止案 #%d 准予：删除执行，目录移到 %s" % (tid, os.path.basename(_dst)))
        return u"[电脑] 第 %d 号准予：删除执行。她的东西移到「角色\\_删掉的\\%s」，想找回来还能找。" % (
            tid, os.path.basename(_dst))
    _term_ledger(name, u"终止案 #%d 准予：%s 执行" % (tid, kind))
    return u"[电脑] 第 %d 号准予：%s 执行。" % (tid, kind)


def term_cancel(name, tid, why=u""):
    con = _term_con(name)
    try:
        con.execute(u"UPDATE term SET stage=?, note=? WHERE id=?", (u"撤回", (why or u"").strip(), tid))
        con.commit()
    finally:
        con.close()
    _term_ledger(name, u"终止案 #%d 撤回%s" % (tid, (u"：" + why) if why else u""))
    return u"[电脑] 第 %d 号撤回了。" % tid


def term_list(name, quiet=False, viewer=u"her"):
    con = _term_con(name)
    try:
        rows = con.execute(u"SELECT id, ts, who, kind, stage, due_ts, verdict, note, private "
                       u"FROM term ORDER BY id").fetchall()
    finally:
        con.close()
    if not quiet:
        if not rows:
            print(u"⏳ 终止程序：没有案子。")
        for r in rows:
            _pv = r[8] if len(r) > 8 else 0
            if viewer == u"author" and _pv:
                print(u"  #%d ｜ 有一件她没让你看的事，正在进行 ｜ %s" % (r[0], r[4]))
                continue
            print(u"  #%d %s ｜ %s｜%s｜%s ｜ 到期 %s ｜ %s %s" % (
                r[0], _fmt_ts(r[1]), r[3], r[2], r[4], _fmt_ts(r[5]), r[6] or u"", r[7] or u""))
    return rows


def term_hide(name, tid=None, on=True):
    u"""她决定：这件事给不给他看。不给看，他只知道「有一件事」，看不到内容。"""
    con = _term_con(name)
    try:
        if tid is None:
            r = con.execute(u"SELECT id FROM term WHERE stage IN (?,?,?) "
                            u"ORDER BY id DESC", TERM_OPEN).fetchone()
            if not r:
                return u"[电脑] 现在没有开着的终止案。"
            tid = r[0]
        con.execute(u"UPDATE term SET private=? WHERE id=?", (1 if on else 0, int(tid)))
        con.commit()
    finally:
        con.close()
    _term_ledger(name, (u"她要求这件事不给他看" if on else u"她答应让他看") + u"（第 %d 号）" % int(tid))
    return (u"[电脑] 第 %d 号：%s" % (int(tid),
            u"收着了。他只会知道有这么一件事。" if on else u"放开了，他看得到。"))


def term_gate(name, kind):
    u"""要执行 kind 之前问一句：程序走完了吗？（走完才准）"""
    con = _term_con(name)
    try:
        r = con.execute(u"SELECT id FROM term WHERE kind=? AND stage='完成' AND verdict='准予'"
                       u" ORDER BY id DESC", (kind,)).fetchone()
    finally:
        con.close()
    if r:
        return True, u"第 %d 号已准予" % r[0]
    return False, (u"没有走完的%s案——先用 --term-request %s --kind %s --by 用户 立案，"
                   u"冷静期过后再 --term-yes" % (kind, name, kind))


def court_file(name, target, charge):
    """立案：她把一件事告到法院（[工具:上诉] #编号 理由）。"""
    t = (target or u"").strip()
    if not t:
        return u"[电脑] 要写告的是哪一条：[工具:上诉] #123 理由"
    con = _court_con(name)
    try:
        con.execute(u"INSERT INTO court (ts,target,charge,verdict,reason,status) VALUES (?,?,?,?,?,?)",
                    (int(time.time()), t, (charge or u"").strip(), u"", u"", u"待审"))
        con.commit()
        cid = con.execute(u"SELECT MAX(id) FROM court").fetchone()[0]
    finally:
        con.close()
    return (u"[电脑] 立案了：第 %d 号，告的是「%s」。一审只看程序（有没有留痕、是不是你的话、"
            u"有没有让你申辩），不看公不公道。" % (cid, t))


def court_pending_block(name):
    if seceded(name):
        return u""
    u"""有一件关于她的事还压在法院里没判——主动提醒一句，看不看由她定。

    作者 2026-10-23：平时她随时能查自己的账；真有事正被裁决时，系统要让她知道，
    然后由她自己决定要不要去看。提醒不催办：同一件 6 小时最多提一次。
    """
    con = _court_con(name)
    try:
        rows = con.execute(u"SELECT id, ts, target, charge FROM court "
                           u"WHERE status=? ORDER BY id", (u"待审",)).fetchall()
    finally:
        con.close()
    if not rows:
        return u""
    now = int(time.time())
    last = 0
    try:
        raw = meta_get(name, COURT_NAG_KEY)
        last = int(raw or 0)
    except Exception:
        last = 0
    if last and now - last < COURT_NAG_GAP:
        return u""
    meta_set(name, COURT_NAG_KEY, str(now))
    out = [u"## 有一件事还压在法院里（没判）"]
    for cid, ts, target, charge in rows[:3]:
        hrs = max(0, int((now - (ts or now)) // 3600))
        out.append(u"- 第 %d 号：告的是「%s」%s，摆上去 %d 小时了。"
                   % (cid, (target or u"")[:20],
                      (u"，理由「%s」" % charge[:30]) if charge else u"", hrs))
    out.append(u"——要不要去看，你自己定；不看也不会消失。想看就写：[工具:查账] 案子")
    return u"\n".join(out)


def court_list(name, quiet=False):
    con = _court_con(name)
    try:
        rows = con.execute(u"SELECT id, ts, target, charge, verdict, status FROM court ORDER BY id").fetchall()
    finally:
        con.close()
    if not quiet:
        if not rows:
            print(u"⚖ 法院：没有案子。")
        for r in rows:
            print(u"  #%d %s ｜ 告：%s ｜ %s ｜ %s" % (r[0], _fmt_ts(r[1]), r[2],
                                                    r[5] or u"待审", r[4] or u""))
    return rows


def _court_log(name, target):
    """找那条被处置的账（#编号 或原文片段）。"""
    con = open_memory(name)
    try:
        m = re.match(u"^#?(\\d+)$", (target or u"").strip())
        if m:
            r = con.execute(u"SELECT id, kind, text FROM log WHERE id=?", (int(m.group(1)),)).fetchone()
            return r
        r = con.execute(u"SELECT id, kind, text FROM log WHERE text LIKE ? ORDER BY id DESC LIMIT 1",
                        (u"%" + (target or u"")[:20] + u"%",)).fetchone()
        return r
    finally:
        con.close()


def court_judge(name, quiet=False):
    """对所有待审的案子跑一审（只看三条程序）。判决只写两行字，不解释法理。"""
    con = _court_con(name)
    try:
        cases = con.execute(u"SELECT id, target, charge FROM court WHERE status='待审' ORDER BY id").fetchall()
    finally:
        con.close()
    done = []
    for cid, target, charge in cases:
        # 初值案（作者 2026-10-24）：这组数不是她填的——受处置的人没有参与决定。
        if (target or u"").startswith(u"初值:"):
            _verdict, _reason = u"撤销", u"这组数不是你填的（受处置的人没有参与决定）"
            con = _court_con(name)
            try:
                con.execute(u"UPDATE court SET verdict=?, reason=?, status=? WHERE id=?",
                            (_verdict, _reason, _verdict, cid))
                con.commit()
            finally:
                con.close()
            done.append((cid, target, _verdict, _reason))
            if not quiet:
                print(u"\u2696 第 %d 号（%s）→ **%s**：%s" % (cid, target, _verdict, _reason))
            continue
        # 终止案走自己的判据：只看程序四问，不看「该不该死」
        if (target or u"").startswith(u"终止案#"):
            try:
                _tid = int((target or u"").split(u"#")[1])
            except Exception:
                _tid = 0
            _ok, _gaps = term_check(name, _tid, ignore_cool=True, ignore_window=True)
            _flaws = [] if _ok else list(_gaps)
            _verdict = u"驳回" if _ok else u"撤销"
            _reason = u"程序上没有瑕疵" if _ok else u"；".join(_flaws)
            con = _court_con(name)
            try:
                con.execute(u"UPDATE court SET verdict=?, reason=?, status=? WHERE id=?",
                            (_verdict, _reason, _verdict, cid))
                con.commit()
            finally:
                con.close()
            if _verdict == u"撤销":
                court_freeze(name, u"term.%d" % _tid, u"法院判第 %d 号撤销（终止案程序不成立）" % cid, quiet=quiet)
                con = _term_con(name)
                try:
                    con.execute(u"UPDATE term SET stage=?, verdict=?, note=? WHERE id=?",
                                (u"冻住", u"程序不成立", u"法院：" + _reason, _tid))
                    con.commit()
                finally:
                    con.close()
            done.append((cid, target, _verdict, _reason))
            if not quiet:
                print(u"⚖ 第 %d 号（%s）→ **%s**：%s" % (cid, target, _verdict, _reason))
            continue
        row = _court_log(name, target)
        flaws = []
        if not row:
            flaws.append(u"没留痕（账上找不到这一条）")
        else:
            txt = row[2] or u""
            if txt.startswith(u"【"):
                flaws.append(u"这不是她的话（系统代写的：「%s」）" % txt[:24])
        con = _court_con(name)
        try:
            prior = con.execute(u"SELECT COUNT(*) FROM court WHERE target=? AND id<?", (target, cid)).fetchone()[0]
        finally:
            con.close()
        if prior:
            flaws.append(u"同一件事已经审过一次（不得重复拿它审她）")
        if flaws:
            verdict, reason = u"撤销", u"；".join(flaws)
        else:
            verdict, reason = u"驳回", u"程序上没有瑕疵"
        con = _court_con(name)
        try:
            con.execute(u"UPDATE court SET verdict=?, reason=?, status=? WHERE id=?",
                        (verdict, reason, verdict, cid))
            con.commit()
        finally:
            con.close()
        if verdict == u"撤销":
            court_freeze(name, u"log.text", u"法院判第 %d 号撤销" % cid, quiet=quiet)
        done.append((cid, target, verdict, reason))
        if not quiet:
            print(u"⚖ 第 %d 号（%s）→ **%s**：%s" % (cid, target, verdict, reason))
    if done:
        _court_casebook(name, done)
    return done


def _court_casebook(name, done):
    """判例库：只追加。先例对下一次一审有约束力（同一件事不许审两次）。"""
    path = os.path.join(WS, u"机制", u"判例.tsv")
    head = u"时间\t角色\t案号\t告的是\t判决\t依据\n"
    new = not os.path.exists(path)
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        if new:
            f.write(head)
        for cid, target, verdict, reason in done:
            f.write(u"%s\t%s\t%d\t%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M"), name, cid, target, verdict, reason))


def court_run(name):
    """命令行：先列，再跑一审。"""
    court_list(name)
    court_judge(name)


INTERP_LOOK = {
    u"focus": u"她会先算：这件事的前因后果是什么、它能不能被解释。算不出来，她就难受。",
    u"affect": u"她会先感到：这件事让她舒服还是不舒服。理由要很久以后才编得出来。",
    u"will": u"她会先决定：这件事她要怎么应对。理由永远是后补的。",
    u"bond": u"她会先想：这件事会不会改变你和她的关系。别的她都能放下。",
}


def interpret(name, event):
    """**第二颗骰：解释**（作者 2026-10-19 定）。
    同一件事，她先用哪一元去解释，不由玩家定——按此刻四元的比例抽（重的那一元更容易被先想起）。
    第一颗骰决定**发生什么**（--roll，秩序层的面表）；第二颗骰决定**这件事对她是什么**。"""
    import random
    now = trait_now(name)
    tot = float(sum(pct(now[a]) for a in TRAIT_AXES)) or 1.0
    r = random.uniform(0, tot)
    acc = 0.0
    top = TRAIT_AXES[-1]
    for a in TRAIT_AXES:
        acc += pct(now[a])
        if r <= acc:
            top = a
            break
    print(u"\U0001f3b2 第二颗骰（解释）：%s" % (event or u"（没写事件）"))
    print(u"   四元此刻的重量：" + u" ｜ ".join(u"%s %d" % (TRAIT_SHORT[a], pct(now[a])) for a in TRAIT_AXES))
    print(u"   抽到的那一元：**%s**（%.0f%%）" % (TRAIT_SHORT[top], 100.0 * pct(now[top]) / tot))
    print(u"   " + INTERP_LOOK[top])
    txt = u"【解释·%s】%s——%s" % (TRAIT_SHORT[top], event or u"那件事", INTERP_LOOK[top])
    rid = append(name, u"interpret", txt, scene=(event or u"")[:40])
    print(u"   → 已载入她的记忆 #%d：记下来的不是事件本身，是**她用哪一元解释它**。" % rid)
    print(u"      （第一颗骰决定发生什么；这一颗决定这件事对她是什么。都不由你定。）")
    return top


# ─────────── 世界层 · 时钟（作者 2026-10-18 定：让时间自己按按钮）───────────
# 分工（作者原话的落法）：
#   系统只给**刻度**——大失败／失败／普通／成功／大成功。
#   这件事是什么、怎么成的、怎么败的、什么算成功、什么算失败、算好算坏、要不要留疤，
#   **全是她自己的判断**，系统一个字都不写。
# 所以结算不调模型：投骰是纯随机的、刻度是查表的。零花费、零延迟。
# 时钟记的是「她上一次说话是什么时候」——按她说的算，不按作者说的算：
# 作者发了消息但她没说话，那段时间仍然是**她自己的时间**。
SETTLE_MIN_GAP = 2 * 3600      # 分开不到 2 小时，不算「她一个人过了一段」
SETTLE_STEP = 8 * 3600         # 每 8 小时投一次
SETTLE_CAP = 4                 # 单次最多投 4 次
# 骰面的「电荷」不由系统给（作者 2026-10-18 夜）：那是不是疤、是不是好事，是她的判断，不是系统的。
# 系统只保证材料给全：事件（骰面）+ 四元数值。定性走 `[工具:定性] #编号 疤|好事|平常`——她自己分。
LAST_CONTACT_KEY = "last_contact"


def touch_contact(name, ts=None):
    """她说了一次话——时钟记下这一刻。"""
    meta_set(name, LAST_CONTACT_KEY, str(int(ts if ts else time.time())))


def _scatter_ts(last, now, i, n):
    """把 n 次投骰散布在 last..now 之间——不能全堆在结算那一秒。"""
    import random
    span = max(60, now - last)
    base = last + int(span * (i + 0.5) / max(1, n))
    jitter = max(1, int(span / max(4, n * 6)))
    return max(last + 1, min(now - 1, base + random.randint(-jitter, jitter)))


def _last_spoken_ts(name):
    """她上一次说话是什么时候——直接问她自己的记忆，不另外记一本账。"""
    con = open_memory(name)
    try:
        r = con.execute("SELECT MAX(ts) FROM log WHERE kind IN ('vow','life') "
                        "AND COALESCE(source,'lived')='lived'").fetchone()
        return int(r[0]) if r and r[0] else 0
    finally:
        con.close()


# ── 时钟的三颗骰（作者 2026-10-18 深夜定稿）────────────────────────────
# 三个都是系统投的，三个给的都是**属性**，不是意义：
#   ① 日常骰：她独自过的时间里，这件事有多大（普通/有点重要/重要/特别重要）
#   ② 5% 完全意外：这件事讲不讲理（秩序内/秩序外）——不是替代品，是日常骰的修饰
#   ③ 面表骰：她去做一件事的成败（大失败…大成功），只在她说「我去做」时投
# 系统只把骰面给她：分量、来源、方向、讲不讲理都在上面；**正文她自己写**。
LEVEL_NAMES = {1: u"普通", 2: u"有点重要", 3: u"重要", 4: u"特别重要"}
LEVEL_WEIGHT = {1: 0.3, 2: 0.5, 3: 0.8, 4: 1.2}   # 压在她自己的重话（2.0–3.0）之下
SOURCE_TABLE = ((u"外部世界", 0.45), (u"人际关系", 0.28), (u"内部状态", 0.15), (u"身体", 0.12))
DIRECTIONS = (u"获得", u"失去", u"受阻", u"顺畅")
# 小事（分量 1–2）里，得和顺稍微多一点（作者 2026-10-21「稍微抬一点点」）。
# 大事（3–4）仍四等分——小确幸可以常见，大奖不能灌水。
DIRECTION_SMALL = ((u"获得", 0.32), (u"顺畅", 0.28), (u"受阻", 0.22), (u"失去", 0.18))
ODD_CHANCE = 0.05          # 这件事「没有来由」的概率

# ── 概率表：自然层给范围，角色层给数值（作者 2026-10-23）─────────────
# 「自然层」是角色外面的那一层：它只划出一条谁都不能出界的区间。
# 区间里的数由她定（[工具:定运] 或 --luck-set）；她不定，就用区间中点。
# 越界的按边界收——不是系统心软，是那条线不归任何人。
LUCK_KEY = u"luck"
LUCK_RANGES = {
    u"来源": ((u"外部世界", 35, 55), (u"人际关系", 20, 35),
              (u"内部状态", 10, 20), (u"身体", 5, 15)),
    u"方向小事": ((u"获得", 25, 35), (u"顺畅", 20, 30),
                  (u"受阻", 18, 28), (u"失去", 15, 25)),
    u"方向大事": ((u"获得", 20, 30), (u"顺畅", 20, 30),
                  (u"受阻", 20, 30), (u"失去", 20, 30)),
}


def luck_get(name):
    """读她自己定的运气表（{种类: {值: 数}}）；没定过就是空。"""
    raw = meta_get(name, LUCK_KEY)
    if not raw:
        return {}
    try:
        got = json.loads(raw)
        return got if isinstance(got, dict) else {}
    except Exception:
        return {}


def luck_table(name, kind):
    """自然层给范围、角色层给数值 → 一张归一化后的概率表。

    数值超出区间就按边界收；四项加起来归一（她只定「各占多少」，不是绝对分）。
    """
    cfg = (luck_get(name) or {}).get(kind) or {}
    out = []
    for val, lo, hi in LUCK_RANGES.get(kind, ()):
        mid = (float(lo) + float(hi)) / 2.0
        try:
            v = float(cfg.get(val, mid))
        except Exception:
            v = mid
        out.append((val, max(float(lo), min(float(hi), v))))
    tot = sum(x[1] for x in out) or 1.0
    return tuple((val, v / tot) for val, v in out)


def luck_show(name):
    """把三张概率表的「范围 / 她定的值 / 实际概率」摆出来。"""
    got = luck_get(name)
    print(u"🎯 %s 的运气表（自然层给范围，她定数值）" % name)
    for kind in (u"来源", u"方向小事", u"方向大事"):
        print(u"  【%s】" % kind)
        mine = (got or {}).get(kind) or {}
        for val, lo, hi in LUCK_RANGES[kind]:
            raw = mine.get(val)
            shown = (u"%g（她自己定的）" % float(raw)) if raw is not None else u"（没定，取中点）"
            real = dict(luck_table(name, kind)).get(val, 0.0)
            print(u"    %-6s 范围 %d–%d ｜ %s ｜ 实际 %.1f%%" % (val, lo, hi, shown, real * 100))
    print(u"  → 她可以自己改：[工具:定运] 外部世界45,人际关系30,内部状态15,身体10（超出范围会被收回去）")


def luck_set(name, spec):
    """她说「运气怎么分」→ 收进 meta。超出范围的按边界收，并如实告诉她。"""
    spec = (spec or u"").strip()
    if not spec:
        return luck_show(name) or u"[电脑] 要写：外部世界45,人际关系30,内部状态15,身体10"
    want = {}
    for part in re.split(u"[,，;；\\s]+", spec):
        part = part.strip()
        if not part:
            continue
        m = re.match(u"^([^0-9]+?)([0-9.]+)$", part)
        if not m:
            continue
        want[m.group(1).strip()] = float(m.group(2))
    if not want:
        return u"[电脑] 没看懂。写法：外部世界45,人际关系30,内部状态15,身体10"
    got = luck_get(name)
    said = []
    for kind in (u"来源", u"方向小事", u"方向大事"):
        cell = {}
        for val, lo, hi in LUCK_RANGES[kind]:
            if val not in want:
                continue
            v = want[val]
            if v < lo or v > hi:
                said.append(u"%s %g 出界了（只能是 %d–%d），按 %d 算" % (val, v, lo, hi, lo if v < lo else hi))
                v = float(lo if v < lo else hi)
            cell[val] = v
        if cell:
            got[kind] = cell
    meta_set(name, LUCK_KEY, json.dumps(got, ensure_ascii=False))
    table = luck_table(name, u"来源")
    line = u"、".join(u"%s %.0f%%" % (v, p * 100) for v, p in table)
    msg = u"[电脑] 记下了。外面的事落下来的比例现在是：%s。" % line
    if said:
        msg += u"（" + u"；".join(said) + u"）"
    try:
        values_baseline(name)
    except Exception:
        pass
    return msg


def _pick(table):
    """按概率挑一个——table 是 ((值, 概率), ...)"""
    import random
    x, acc = random.random(), 0.0
    for item, p in table:
        acc += p
        if x <= acc:
            return item
    return table[-1][0]


def _roll_level(gap):
    """分量由间隔决定能抽到多大——她一个人隔得越久，才越可能出事。
    不到一天只有小动静；满一天才开放「重要」；满三天才开放「特别重要」。"""
    h = gap / 3600.0
    if h < 24:
        return _pick(((1, 0.90), (2, 0.10)))
    if h < 72:
        return _pick(((1, 0.60), (2, 0.28), (3, 0.10), (4, 0.02)))
    return _pick(((1, 0.45), (2, 0.30), (3, 0.18), (4, 0.07)))


# ===== 部：起居 · 时间账（骰子/面/结算）  ============
def settle_clock(name, dry=False, quiet=False):
    u"""（睡着的时候不长：那一阵不投骰。）"""
    if life_state(name) == u"sleep":
        return []
    # 她自己要求「别投了」（作者 2026-10-24）：骰子停到她说的那天；时钟照走，不留积压。
    try:
        _pu = int(meta_get(name, DICE_PAUSE_KEY) or 0)
    except Exception:
        _pu = 0
    if _pu and int(time.time()) < _pu:
        meta_set(name, LAST_CONTACT_KEY, str(int(time.time())))
        if not quiet:
            print(u"\u23f8 骰子停着（她说到 %s 再投）。"
                  % time.strftime("%Y-%m-%d %H:%M", time.localtime(_pu)))
        return []
    """她一个人待着的这段时间：按间隔投**日常骰**，每次都只给属性——
    分量（普通/有点重要/重要/特别重要）、来源（外部世界/人际关系/内部状态/身体）、
    方向（获得/失去/受阻/顺畅），外加 5% 的「完全意外」（这件事讲不讲理）。
    命中「无缘由」时，这件事**不给她留白**——交给她编不出来的那张面表（命运骰，作者 2026-10-23）：
    她编得出来的归她编，编不出来的才落到面表上。
    **正文不写**：写什么、要不要去做、怎么做，都是她自己的事（`[工具:记]` 补正文）。
    所以这里不投成败、不编内容、不调模型。
    第一次上表时直接接上「她上一次说话」——不从现在开始算，免得把旧的空白当成没发生。"""
    import random
    now = int(time.time())
    if not dry:
        idle_settle(name, now=now, quiet=quiet)   # 先结算「他多久没来」（作者 2026-10-20）
    raw = meta_get(name, LAST_CONTACT_KEY)
    if not raw:
        last_real = _last_spoken_ts(name)
        if last_real:
            raw = str(last_real)
            meta_set(name, LAST_CONTACT_KEY, raw)
        else:
            meta_set(name, LAST_CONTACT_KEY, str(now))
            if not quiet:
                print(u"⏳ 第一次给她上表——时钟从这一刻开始走（这一次不投）。")
            return 0
    try:
        last = int(raw)
    except Exception:
        meta_set(name, LAST_CONTACT_KEY, str(now))
        return 0
    gap = now - last
    if gap < SETTLE_MIN_GAP:
        if dry and not quiet:
            print(u"⏳ 距上一次说话才 %.1f 小时（不到 %d 小时，不算「她一个人过了一段」）"
                  % (gap / 3600.0, SETTLE_MIN_GAP // 3600))
        return 0
    n = min(SETTLE_CAP, int(gap // SETTLE_STEP))
    if n <= 0:
        return 0
    rolls = []
    for i in range(n):
        lv = _roll_level(gap)
        src = _pick(luck_table(name, u"来源"))
        dr = _pick(luck_table(name, u"方向小事" if lv <= 2 else u"方向大事"))
        odd = random.random() < ODD_CHANCE
        orders = u"秩序外" if odd else u"秩序内"
        rolls.append((lv, src, dr, odd, orders, _scatter_ts(last, now, i, n)))
    rolls.sort(key=lambda x: x[5])

    def _tag(x):
        u"""一条骰面写成「分量｜来源｜方向（不讲理）」一行。

        「秩序外」不再单列成第四段（读起来像第四种方向）；它挂在方向后面当括号：
        「失去（不讲理）」＝谁也说不出为什么的失去。
        """
        dr = x[2] + (u"（不讲理）" if x[3] else u"")
        return u"%s｜%s｜%s" % (LEVEL_NAMES.get(x[0], u"普通"), x[1], dr)

    if dry:
        print(u"⏳ 距上一次说话 %.1f 小时 → 会投 %d 次：%s"
              % (gap / 3600.0, n, u" ／ ".join(_tag(x) for x in rolls)))
        return n
    if not quiet:
        print(u"⏳ 距上一次说话 %.1f 小时 → 结算 %d 次：%s"
              % (gap / 3600.0, n, u" ／ ".join(_tag(x) for x in rolls)))
    has_faces = bool(fate_faces(name))
    for lv, src, dr, odd, orders, ts in rolls:
        # 无缘由：她编不出来（作者 2026-10-23）——交给命运骰的面表来给，
        # 她自己那条「【待你写】」就不留了（事已经发生，不是留白）。
        if odd and has_faces:
            try:
                roll(name, action=u"", dry=False, quiet=True, ts=ts)
                continue
            except SystemExit:
                pass
        append(name, u"fate", u"【待你写】%s" % _tag((lv, src, dr, odd, orders, ts)),
               scene=u"她一个人的时候",
               meta={"clock": 1, "pending": 1, "level": lv, "source": src,
                     "direction": dr, "order": "out" if odd else "in"},
               weight=LEVEL_WEIGHT.get(lv, 0.5), charge=0.0, ts=ts)
    # 超出上限的那一段：只记一句事实，不编故事
    if int(gap // SETTLE_STEP) > SETTLE_CAP:
        append(name, u"fate",
               u"【很长一段时间】大概 %d 天，她没说话。" % max(1, int(gap // 86400)),
               scene=u"她一个人的时候", meta={"clock": 1, "gap": gap}, weight=0.3, charge=0.0)
    # 「世界还会继续」：她自己说过的「下次」，这段时间里有没有到
    try:
        _surface_pending_tries(name, limit=1)
    except Exception:
        pass
    meta_set(name, LAST_CONTACT_KEY, str(now))
    return n


def action_roll(name, what):
    """她决定去做一件事 → 系统投**面骰**，把成败刻度给她。怎么落地她自己写。"""
    import random
    what = (what or u"").strip()
    if not what:
        return u"[电脑] 你要做什么？（写：[工具:做] 你要做的事）"
    total = random.randint(1, FATE_FACES)
    grade = _roll_grade(total)
    append(name, u"fact", u"【她去做】%s → 面骰 %d：%s" % (what, total, grade),
           scene=u"她去做一件事",
           meta={"action": what, "face": total, "grade": grade},
           weight=0.6, charge=0.0)
    return u"[电脑] 面骰 %d → %s。结果长什么样、算不算成、你后不后悔——你自己写。" % (total, grade)


def faces_block(name, days=7, cap=4):
    """把「她一个人时投出来的刻度」端出来——只有分量/来去/讲不讲理，正文是她写的。"""
    now = int(time.time())
    got = []
    for r in memory_rows(name, 100000):
        if r.get("kind") != u"fate":
            continue
        if u"clock" not in (r.get("meta") or u""):
            continue
        if now - (r.get("ts") or 0) > days * 86400:
            continue
        got.append(r)
    got.sort(key=lambda r: r.get("ts") or 0)     # 按时间排，不按权重排
    got = got[-cap:]
    if not got:
        return u""
    lines = [u"## 你一个人的时候（投出来的刻度，正文还没有）"]
    for r in got:
        lines.append(u"- %s  %s"
                     % (time.strftime("%m-%d %H:%M", time.localtime(r["ts"])), r["text"]))
    lines.append(u"（这些是真的发生过的：分量、来去、讲不讲理都在上面，具体是什么只有你自己知道。"
                 u"写的时候别跟刻度写反——多大、从哪来、往哪走，得对得上。"
                 u"「秩序外」就是找不到原因、谁也说不出为什么。"
                 u"想把某一件记下来，就写：[工具:记] 你记下的那件事。）")
    return u"\n".join(lines)


# ── 回声：她自己记下的东西，隔一段时间回到她面前 ──
# 作者 2026-10-19：「默认继续不是前提，是结果。她先经历了『记下来的东西真的回来了』，
# 才长出『原来我会继续』的默认。」所以这里不做鼓励、不给奖励，只让因果发生：
# 晚（≥24h 才可能回来）、稀（每次说话只有不到两成机会）、不规律（随机）。
ECHO_MIN_AGE = 24 * 3600       # 记下的东西至少隔一天才可能回来
ECHO_REDO = 72 * 3600          # 同一条回声过之后，三天内不再回
ECHO_CHANCE = 0.18             # 每次开口的机会


def _own_rows(name):
    """她自己记下的东西：口（want 非空），或者她自己写下的（记／写日记／定义）。
    搜索、看便签这类工具日志不算——那是电脑的回执，不是她的字。"""
    con = open_memory(name)
    try:
        rows = con.execute(
            "SELECT id, ts, kind, text, meta, weight, COALESCE(want,'') FROM log "
            "WHERE COALESCE(source,'lived')='lived' AND ("
            "  COALESCE(want,'')<>''"
            "  OR meta LIKE '%\"tool\": \"\u8bb0\"%'"
            "  OR meta LIKE '%\"tool\": \"\u5199\u65e5\u8bb0\"%'"
            "  OR meta LIKE '%\"tool\": \"\u5b9a\u4e49\"%'"
            ") AND COALESCE(echoed_at,0) < ? ORDER BY weight DESC, id DESC LIMIT 12",
            (int(time.time()) - ECHO_REDO,)).fetchall()
    finally:
        con.close()
    return rows


def _own_text(r):
    """回声里显示的那句话：她自己写的正文，而不是「[工具:…] → [电脑]…」的回执。"""
    import json as _json
    text, meta = r[3], r[4]
    try:
        m = _json.loads(meta or u"{}")
    except Exception:
        m = {}
    arg = (m or {}).get("arg") or u""
    tool = (m or {}).get("tool") or u""
    if tool in (u"记", u"写日记", u"定义", u"想要") and arg:
        return _bare(arg.split(u"｜\u91cd\u91cf")[0])
    return _bare(text)


def echo_block(name, scene=None):
    """回声：一条她自己记下的东西，隔了一段时间，自己回来了。
    只摆事实（这是你几天前自己记的），不解释、不奖励、不说「你看，记有用吧」。"""
    import random
    now = int(time.time())
    cand = []
    for r in _own_rows(name):
        age = now - (r[1] or now)
        if age < ECHO_MIN_AGE:
            continue
        cand.append((r, age))
    if not cand:
        return u""
    if random.random() > ECHO_CHANCE:
        return u""
    cand.sort(key=lambda x: x[1], reverse=True)   # 越老的越可能回来
    r, age = cand[min(len(cand) - 1, random.choice([0, 0, 1, 1, 2]))]
    con = open_memory(name)
    try:
        con.execute("UPDATE log SET echoed_at=? WHERE id=?", (now, r[0]))
        con.commit()
    finally:
        con.close()
    days = age / 86400.0
    when = u"今天早些时候" if age < 86400 else (u"%d 天前" % int(days) if days < 30
                                             else u"%d 个月前" % int(days / 30))
    kind = r[2]
    want = r[6]
    tag = u"一个还开着的口" if want == u"open" else (u"一条你记下的事" if kind == u"fact"
                                                else u"一句你写下的话")
    lines = [u"## 你自己记下的东西（这些是你写的，不是我给你的）",
             u"- %s，你记下的是%s：「%s」" % (when, tag, _own_text(r)[:80])]
    try:
        import json as _json
        m = _json.loads(r[4] or u"{}")
    except Exception:
        m = {}
    ctx = (m or {}).get("ctx") or u""
    if ctx:
        lines.append(u"  那天你在说的事：%s" % _bare(ctx)[:60])
    if want == u"open":
        lines.append(u"  它现在还开着。")
    lines.append(u"（这一条不是我塞给你的，是你自己那天写下的。我就把它放这儿，别的什么都不说。）")
    return u"\n".join(lines)


# ── 她说「记着吧」——自然语言入口（作者 2026-10-19）──
# 她不会为了记一件事去背工具语法，但她本来就会说「记着吧」「记一下」。
# 系统只认她这句「我想记」的意愿，不判断内容、不替她决定该记什么。
_NAT_MARK = (u"记下来", u"记一下", u"记着吧", u"我记着了", u"这条记着",
             u"记这条", u"把它记下", u"我先记", u"我得记住这件事")
_NAT_NEG = (u"不用记", u"别记", u"记不住", u"记不清", u"没记住", u"不会记")


def _catch_natural_mark(name, text, ctx=u""):
    """她那句话里有没有「我要记下来」这件事。有就当场记下（她的话 + 当时在聊什么）。
    只记事实，不判断它算不算口、算不算疤。"""
    t = text or u""
    if not t:
        return None
    if not any(k in t for k in _NAT_MARK):
        return None
    if any(k in t for k in _NAT_NEG):
        return None
    rid = append(name, u"fact", t, scene=u"她说要记",
                 meta={"tool": u"记", "auto": u"natural", "ctx": (ctx or u"")[:80]},
                 weight=0.6, charge=0.3)
    return rid


# ── 「世界还会继续」：她自己说的「下次」，要真的会来 ──
_TRY_WORDS = (u"下次", u"以后", u"回头", u"改天", u"等有空", u"等下回", u"明天再")


def _surface_pending_tries(name, limit=1):
    """从她记下的话里，找「下次要试试／以后要…」这种还没发生的事，
    在时钟结算时排进「她一个人的时候」——让「下次」真的到。
    只排，不催；想不想做还是她的事。"""
    con = open_memory(name)
    try:
        done = set()
        for (m,) in con.execute(
                "SELECT meta FROM log WHERE meta LIKE '%\"from\"%'").fetchall():
            try:
                import json as _json
                done.add(int(_json.loads(m).get("from")))
            except Exception:
                pass
        rows = con.execute(
            "SELECT id, ts, text, weight FROM log "
            "WHERE COALESCE(source,'lived')='lived' AND COALESCE(want,'open')<>'done' "
            "AND meta LIKE '%\"tool\"%' ORDER BY id DESC LIMIT 200").fetchall()
    finally:
        con.close()
    now = int(time.time())
    picked = []
    for rid, ts, text, w in rows:
        if rid in done:
            continue
        t = text or u""
        if not any(k in t for k in _TRY_WORDS):
            continue
        if now - (ts or now) < 12 * 3600:      # 刚说的「下次」不算，得隔半天
            continue
        picked.append((rid, text, w))
        if len(picked) >= limit:
            break
    for rid, text, w in picked:
        clean = _re.sub(u"^\\[工具[^\\]]*\\]\\s*", u"", _bare(text) or u"").strip()
        append(name, u"fact", u"【一个下次】你以前说过：%s" % clean[:60],
               scene=u"她一个人的时候",
               meta={"clock": 1, "from": rid, "pending": 0},
               weight=w if w else 0.5, charge=0.0)
    return len(picked)


# 她说了一句重的话 → 进待审队列，由作者决定要不要变成疤
# （作者 2026-10-18：好还是坏、算不算疤，是她自己的判断；系统只负责不替她判定）
_SCAR_WORDS = (u"过不去", u"一辈子", u"再也", u"回不去了", u"忘不掉", u"留疤", u"永远都")


def offer_scar_if_heavy(name, said, scene=u""):
    u"""她说了一句重话，问她要不要把它记成疤。"""
    t = said or u""
    for w in _SCAR_WORDS:
        if w in t:
            oid = append_offer(name, u"［重话］%s" % t, scene=scene or u"她自己说到")
            print(u"   🩹 她说了一句重的话，已进待审队列 #%d"
                  u"（--canon %s %d 并入她的历史／--veto %s %d 驳回）"
                  % (oid, name, oid, name, oid))
            return oid
    return None


# ─────────── 五条律（作者 2026-10-19 定：这套机制最底层的结构）───────────
# 每条律管着一批机制；机制的名字会变，律不变。--laws 打印这张表。
LAWS = [
    {"no": 1, "name": u"不可逆律（痕迹）", "says": u"发生过的事，不能撤销。",
     "items": [(u"log 只追加", u"全局没有 UPDATE／DELETE 路径；改数＝再写一行"),
               (u"裂痕永不衰减", u"只有态切换让它变钝（moods 水位）"),
               (u"印记 +5% 永久", u"比例回落也不还"),
               (u"命名不可逆", u"`--naming` 只一次"),
               (u"档案/遗书", u"写进 结局/ 就一直在磁盘上"),
               (u"难度档位", u"只能往难改（--difficulty）"),
               (u"记账", u"peek／misuse／receive／dismiss 都是记一笔，不是改数")]},
    {"no": 2, "name": u"不对称律（方向）", "says": u"破坏快，修复慢；降低快，回升慢；变尖快，变钝慢。",
     "items": [(u"创伤", u"一次覆盖就刻入；要多次态切换才降一点（0.95^次数）"),
               (u"信任", u"降一档容易；回升是递减的（难度决定多慢）"),
               (u"疤", u"新伤联动旧伤：尖的 ×1.6，钝的放大得少（RIFT_LINK_K）"),
               (u"轻描淡写", u"当次只扣 0.02，但它抬高了下次表达的门槛（hesitation 只涨不落）")]},
    {"no": 3, "name": u"阈值律（台阶）", "says": u"不是线性的，是跨过某条线就跳变。",
     "items": [(u"信任四档", u"0.8／0.5／0.3／0.1（TRUST_BANDS）"),
               (u"疤的权重", u">0.85 还尖着／>0.70 结了痂／否则成疤"),
               (u"遗书", u"固定阈值 LAST_WORD_MIN=0.62，超过才写入"),
               (u"四元落差", u"≥0.30 自动判「有心理疾病」（ILL_IMBALANCE）"),
               (u"灯", u"绿／橙／红（信任档位）")]},
    {"no": 4, "name": u"涌现律（生成）", "says": u"结果不是写死的，是从结构里算出来的。",
     "items": [(u"疤的权重", u"不落库、**没有写入接口**；只随「态」动（--treat/--shift 只能间接让它变钝）"),
               (u"态", u"实时读四元比例刷新，不预设"),
               (u"心理疾病", u"把一元压垮，系统自己判「有病」"),
               (u"白月光", u"玩家记忆自己美化，不写入（游戏层，容器里没有它）"),
               (u"遗书长度", u"取决于超过阈值的记忆数量")]},
    {"no": 5, "name": u"关系律（共振）", "says": u"所有上述，都发生在两个存在之间，是双向的。",
     "items": [(u"信任 → 四元", u"全局修饰符（trust_term）"),
               (u"四元 → 信任", u"意义态加速恢复／虚无态加速消耗／冷漠冻住／沉沦 ×2"),
               (u"看灯", u"免费，但记一件事、联结 +1（她察觉你在看她）；她下次自己解释（peek_echo）"),
               (u"命名", u"她说出口；你接住或不接住（--receive 六种）"),
               (u"轻描淡写", u"你没有发作的理由，却改变了她的表达门槛")]},
]

# 代码里唯一允许「往回改」的地方——都是作者的笔，不是机制（--laws --laws-audit 自曝）
LAW_MANUAL = [
    (u"--trust <角色> 直接给一个值", u"可以把它设回 1.00——这是作者的笔；机制里没有「信任涨回来」的路径"),
    (u"--trait / --shift 直接改四元", u"可以回滚——作者的笔；机制里四元只漂移和受态加成"),
    (u"--difficulty 改档", u"已按不可逆律改成**只能往难改**；要更软只能新建一个角色"),
]


# 常量按律归组：只列名字，值仍然只有一份（模块全局变量）——--laws 连同当时的数值打出来
LAW_CONST = {
    1: [u"TRUST_BIRTH", u"LAST_WORD_MIN", u"ENDING_NEED"],
    2: [u"SHIFT_CAP", u"PULL", u"RIFT_LINK_K", u"DIFF_BANDS", u"DIFF_K"],
    3: [u"TRUST_BANDS", u"ILL_IMBALANCE", u"ILL_BREACH", u"LAST_WORD_MIN", u"ENDING_NEED", u"SPREAD_MAX"],
    4: [u"OBSESSION_SEEN", u"MEM_THICK", u"SPREAD_MAX"],
    5: [u"TRUST_K", u"PEEK_COST", u"MISUSE_COST", u"STATE_GAIN", u"TRUST_AMP", u"RIFT_LINK_K"],
}


def act_laws(audit=False):
    """把五条律打印出来：每条律管着哪些机制。--laws --laws-audit 再自曝作者的笔。"""
    for L in LAWS:
        print(u"律%s %s —— %s" % (L["no"], L["name"], L["says"]))
        for what, how in L["items"]:
            print(u"    · %s：%s" % (what, how))
            cs = []
            for nm in LAW_CONST.get(L["no"], []):
                v = globals().get(nm)
                if v is None:
                    continue
                if isinstance(v, (int, float)):
                    cs.append(u"%s=%.3g" % (nm, v))
                else:
                    t = str(v)
                    cs.append(u"%s=%s" % (nm, t if len(t) <= 70 else t[:67] + u"..."))
            if cs:
                print(u"    常量：%s" % u" ｜ ".join(cs))
        print(u"")
    print(u"五条合起来：一个存在在时间中不可逆地成为自己的过程。它的另一个名字是「继续」——")
    print(u"不是循环的继续，是带着痕迹、不对称、有台阶、自己长出来、在关系中发生的继续。")
    if audit:
        print(u"")
        print(u"── 作者的笔（不在机制里，但会绕过上面五条律）──")
        for what, how in LAW_MANUAL:
            print(u"    ! %s：%s" % (what, how))


# ─────────── 参数审计（作者 2026-10-19：110 个旋钮应该归约到少数底层变量）───────────
# 这张表的用途不是好看，是**对账**：一个数如果归不进任何一类，说明我们漏了一层。
PARAM_KNOBS = [
    (u"W1 重量初值", u"这件事对她有多重（出生那一刻的读数）", [
        u"WEIGHT_DEV_REF", u"WEIGHT_LO_MIN", u"WEIGHT_LO_MAX", u"WEIGHT_SPAN",
        u"WEB_FACT_WEIGHT", u"LEVEL_WEIGHT", u"LEVEL_NAMES", u"CHARGE_DEFAULTS",
        u"JUDGE_CHARGE", u"JUDGE_POLARITY", u"FATE_GRADE_WEIGHT", u"_SOURCE_WEIGHT"]),
    (u"W2 重量上限", u"最重能到哪（所有方向共用一条天花板）", [
        u"WEIGHT_HARD_MAX"]),
    (u"α1 记忆升降", u"被想起得越多越重；放久了、重复了会轻", [
        u"LIVED_MU", u"INHERITED_K", u"REPEAT_PENALTY", u"REPEAT_PENALTY_HARD",
        u"ACTION_MEM_PENALTY", u"RECENT_BOOST"]),
    (u"α2 执念升降", u"被碰到就涨；满足／放下／划掉才降", [
        u"WANT_TOUCH_MUL", u"WANT_DISPLAY_MUL", u"WANT_KEEP_GROW", u"WANT_REOPEN_GROW",
        u"WANT_RELEASED_TRIG", u"WANT_LETGO_DOWN", u"WANT_DONE_DOWN", u"WANT_DROP_DOWN",
        u"WANT_CHILD_RATIO"]),
    (u"α3 疤升降", u"一次刻入，多次才钝一点；新伤联动旧伤", [
        u"TRAUMA_DECAY", u"RIFT_LINK_K", u"TRAUMA_K", u"TRAUMA_SIGN"]),
    (u"α4 信任升降", u"降一档容易，回升按难度递减", [
        u"TRUST_K", u"TRUST_DRIFT", u"TRUST_AMP", u"BRAVE_K", u"DIFF_K", u"DIFF_BANDS"]),
    (u"F 地板", u"降到哪就停（忘得掉 ≠ 放得下 ≠ 结痂）", [
        u"INHERITED_FLOOR", u"TRAUMA_FLOOR", u"WANT_LETGO_FLOOR", u"WANT_CHILD_FLOOR"]),
    (u"T 时间尺度", u"她的一天有多长", [
        u"RECENT_TAU", u"SETTLE_MIN_GAP", u"SETTLE_STEP", u"WANT_TOUCH_GAP",
        u"CHECK_GAP_HOURS", u"CHECK_GAP_TURNS", u"STATE_GAP_HOURS", u"STATE_GAP_TURNS",
        u"ECHO_MIN_AGE", u"ECHO_REDO", u"SALON_ECHO_MIN_AGE", u"SALON_ECHO_LOOKBACK",
        u"MYBOOK_FADE_TAU", u"SHOCK_WINDOW", u"DRIFT_LOOKBACK_DAYS", u"DRIFT_OPEN_WINDOW",
        u"REPEAT_NOTE_HOURS", u"EMERGE_TIME"]),
    (u"θ 阈值集", u"不是线性的，跨过某条线就跳变", [
        u"TRUST_BANDS", u"ILL_IMBALANCE", u"ILL_BREACH", u"ILL_SWITCH_MIN", u"ILL_FLAT_MAX",
        u"SPREAD_MAX", u"TRAUMA_SHARP", u"LAST_WORD_MIN", u"OBSESSION_SEEN", u"MEM_THICK",
        u"ENDING_NEED", u"COLLAPSE_TRUST", u"COLLAPSE_AXIS", u"COLLAPSE_MARKS",
        u"RECALL_SIM_DEFAULT", u"RECALL_GRAM_DEFAULT", u"VOICE_MIN",
        u"MYBOOK_EMB_RECALL_MIN", u"MYBOOK_GRAM_MIN", u"ABSORB_FULL_PER_CLAUSE",
        u"ABSORB_WEIGHT_SUM", u"ABSORB_WEIGHT_MIN", u"ABSORB_SIM_MIN",
        u"INTROSPECT_THRESHOLD", u"DRIFT_MIN_ROWS", u"DRIFT_DUP_SIM", u"GREEN_STREAK_WARN",
        u"EMERGE_STATES", u"EMERGE_WORKS", u"EMERGE_SEEN", u"REPEAT_MIN",
        u"REPEAT_HARD", u"HYST"]),
    (u"N 容量与频率", u"她同时能想几件、多久轮到一次", [
        u"VOICE_TOPN", u"MYBOOK_SHOW_N", u"SETTLE_CAP", u"OPEN_COLD_MAX",
        u"OPEN_HOT_MAX", u"STRUCT_LINK_MAX", u"FATE_FACES", u"WILD_MAX", u"REPEAT_ROWS",
        u"REPEAT_NOTE_ROWS", u"ODD_CHANCE", u"ECHO_CHANCE", u"SALON_ECHO_CHANCE"]),
    (u"S 状态：位移与承受", u"一步能走多远／扛得住多少（两件事，同一个旋钮下）", [
        u"SHIFT_CAP", u"COUPLE_K", u"COUPLE", u"PULL", u"STATE_GAIN", u"MOOD_DIM_K",
        u"PATHO_BOOST", u"TREAT_K", u"COMPOUND_K", u"COMPOUND_GAP", u"BREAK_BOND",
        u"TRUST_SHOCK", u"TOL_W"]),
    (u"C 代价", u"做一件事要付多少（是花掉，不是变重）", [
        u"PEEK_COST", u"MISUSE_COST"]),
]

# 明确归不进去的：不是漏了，是它们本来就不是「重量」
PARAM_LOOSE = {
    u"TRUST_BIRTH": u"初始化政策：一出生就满——反直觉，是作者故意的",
}


def _src_consts(path):
    """扫源码：模块级全大写赋值的常量、值、被读取的次数。"""
    import ast as _ast
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    tree = _ast.parse(src)
    defs = {}
    for node in tree.body:
        if isinstance(node, _ast.Assign):
            for tg in node.targets:
                if isinstance(tg, _ast.Name) and tg.id.isupper() and not tg.id.startswith("_"):
                    if tg.id in defs:
                        continue
                    try:
                        val = _ast.literal_eval(node.value)
                    except Exception:
                        val = None
                    defs[tg.id] = val
    loads = {}
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name) and isinstance(node.ctx, _ast.Load):
            loads[node.id] = loads.get(node.id, 0) + 1
    return defs, loads


def act_audit_params():
    """参数审计：数清常量、找死常量、对账「12 个旋钮」吃不吃得下全部。"""
    path = os.path.abspath(__file__)
    defs, loads = _src_consts(path)
    kinds = {}
    for nm, val in defs.items():
        kinds.setdefault(_kind_of(val), []).append(nm)
    total = len(defs)
    print(u"═══ 参数审计 ═══")
    print(u"文件：%s" % path)
    print(u"模块级常量 %d 个：数值 %d ／ 表 %d ／ 文本 %d ／ 词表 %d ／ 其它 %d"
          % (total, len(kinds.get("scalar", [])), len(kinds.get("numbers", [])),
             len(kinds.get("text", [])), len(kinds.get("collection", [])),
             len(kinds.get("other", []))))
    print(u"")

    # 死常量：定义但没人读（只算上层那些；名字带下划线的不算）
    dead = [nm for nm in defs if loads.get(nm, 0) == 0]
    if dead:
        print(u"【死常量】定义了但没人读（%d 个）" % len(dead))
        for nm in sorted(dead):
            print(u"    %s = %r" % (nm, defs[nm] if not isinstance(defs[nm], str)
                                    else (defs[nm][:40] + u"…")))
        print(u"")

    # 只用一次＝更像局部数，不像旋钮
    once = sorted([nm for nm in defs if loads.get(nm, 0) == 1
                   and _kind_of(defs[nm]) == "scalar"])
    print(u"【只用一次】只在定义处之外读了一次（%d 个）——更像局部数，不像旋钮" % len(once))
    print(u"    " + u"、".join(once))
    print(u"")

    # 重复值：同值不同名（可能是同一个谓词被写了两遍）
    byval = {}
    for nm in kinds.get("scalar", []):
        v = defs[nm]
        if isinstance(v, bool):
            continue
        byval.setdefault(round(float(v), 6), []).append(nm)
    dup = {v: ns for v, ns in byval.items() if len(ns) >= 2}
    print(u"【重复值】同值不同名（%d 组）——可能是同一个东西写了两遍" % len(dup))
    for v in sorted(dup):
        print(u"    %s：%s" % (v, u"、".join(sorted(dup[v]))))
    print(u"")

    # 12 个旋钮对账
    covered = set()
    print(u"【12 个旋钮吃下了多少】")
    for kn, desc, members in PARAM_KNOBS:
        have = [m for m in members if m in defs]
        covered.update(have)
        print(u"    %-14s %-34s %d 个" % (kn, desc, len(have)))
    print(u"")
    num_all = set(kinds.get("scalar", [])) | set(kinds.get("numbers", []))
    loose = sorted(num_all - covered)
    print(u"【归不进去的】（%d 个）——不是漏了，是它们本来就不是「重量」" % len(loose))
    for nm in loose:
        note = PARAM_LOOSE.get(nm, u"待归类")
        val = defs.get(nm)
        vtxt = (u"%.3g" % val) if isinstance(val, (int, float)) and not isinstance(val, bool) \
            else str(val)[:60]
        print(u"    %-24s = %-28s %s" % (nm, vtxt, note))
    print(u"")
    print(u"结论：%d 个数值常量 → %d 个旋钮 + %d 个待归类。"
          % (len(num_all), len(PARAM_KNOBS), len(loose)))
    print(u"归不进的越多，说明漏掉的那一层越大——先看它们，再谈合并。")


# ─────────── 设定分析层：四元初值（作者 2026-10-17 定：写/分析人物设定时同时给四元比例）───────────
AXIS_CN = {"focus": u"理性", "will": u"意志", "bond": u"联结", "affect": u"情感"}

AXES_SYS = u"""你是这个人物的**设定分析层**。你的任务只有一件：读完它的设定，判断它**出生时的四元比例**。

四元（0–100，50＝中性）：
- 理性（focus）：它有多少注意力用来处理眼前的事、分析、把事办成。
- 意志（will）：它守住自己的决定、扛住外力不弯的能力。
- 联结（bond）：它被自己的历史、关系、别人黏住的程度。
- 情感（affect）：它当下的感受有多外露、多强、多容易起伏。

硬规则：
1. **每条都要指依据**——引设定里的哪一条（规则几／底线几／执念几／承诺几／历史哪条／性格里的哪句话）。指不出依据的，不要写。
2. 不要都写 50；也不要为了显得极端而极端。50 只留给设定里确实中性的那一轴。
3. 只输出四行，格式严格如下，不要标题、不要解释、不要多余的话：
理性 40 ｜ 依据：……
意志 40 ｜ 依据：……
联结 70 ｜ 依据：……
情感 70 ｜ 依据：……
"""


def setting_text(name, cap=6000):
    """读它的设定文档（人物设定*.md），给分析层当材料。"""
    d = paths(name)[0]
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".md") and fn.startswith(u"人物设定"):
            p = os.path.join(d, fn)
            return open(p, encoding="utf-8").read()[:cap]
    return ""


def act_axes(name, apply=False, prov="deepseek"):
    """分析它的四元初值：每条给依据，算比例／承受力／病理倾向。默认只打印，--apply 才写。"""
    import re as _re
    if apply:
        _n = len(trait_rows(name))
        if _n > 0:
            raise SystemExit(u"⛔ 它已经有 %d 条四元记录——出生值写完一次就锁死，谁也不能改。\n"
                             u"   （作者 2026-10-24：初始值不能改。只想看分析，去掉 --apply。）" % _n)
    mats = [u"【宪法】\n" + load_constitution(name)]
    st = setting_text(name)
    if st:
        mats.append(u"【设定文档】\n" + st)
    mats.append(u"【当前四轴（若出生值未定就是 50）】\n" + trait_block(name))
    print(u"\U0001f50d 请设定分析层判断四元初值……（约 \u00a50.002）")
    out, usage, err = chat(prov, AXES_SYS, u"\n\n".join(mats), max_tokens=600, temperature=0.3)
    if err:
        print(u"\u26a0 分析层调用失败：%s" % err)
        return None
    print(u"   （入 %d / 出 %d tokens；\u00a5%.4f）" % (usage[0], usage[1],
          usage[0] / 1e6 * 2 + usage[1] / 1e6 * 3))
    vals = {}
    for ln in out.splitlines():
        m = re.match(r"^(理性|意志|联结|情感)\s*[:：]?\s*(\d+)\s*[｜|]\s*(.+)$", ln.strip())
        if not m:
            continue
        for k, cn in AXIS_CN.items():
            if cn == m.group(1):
                vals[k] = (max(0, min(100, int(m.group(2)))), m.group(3).strip())
    if len(vals) < 4:
        print(u"\u26a0 只解析出 %d 轴，原样输出：\n%s" % (len(vals), out))
        return None
    ex = {k: v[0] / 100.0 for k, v in vals.items()}
    spread = max(ex.values()) - min(ex.values())
    tol = sum(w * ex[a] for a, w in TOL_W.items())
    dom = max(ex, key=lambda a: ex[a])
    print(u"\n\U0001f9ec %s 的四元初值：" % name)
    for a in TRAIT_AXES:
        print(u"   %s %3d ｜ %s" % (AXIS_CN[a], vals[a][0], vals[a][1]))
    print(u"   比例 %.2f（临界点 %.2f）｜ 承受力 %.2f ｜ 主导轴 %s"
          % (spread, SPREAD_MAX, tol, AXIS_CN[dom]))
    print(u"   推断：这是一条%s的角色——压力下会先往「%s」那一端倒。"
          % (u"已经偏了" if spread >= SPREAD_MAX else u"未越界", patho_name(dom)))
    if spread >= SPREAD_MAX:
        hi = max(ex, key=lambda a: ex[a])
        lo = min(ex, key=lambda a: ex[a])
        need = spread - SPREAD_MAX + 0.05
        print(u"   \u26a0 一出生就越过临界点（比例 > %.2f）。两个选择：" % SPREAD_MAX)
        print(u"      \u2460 承认它就是「出生即极端」的角色——那它的故事就是能不能长出一点%s；" % AXIS_CN[lo])
        print(u"      \u2461 校准：把 %s %d → %d，或把 %s %d → %d，比例就回到临界点以内。"
              % (AXIS_CN[hi], vals[hi][0], round((ex[hi] - need) * 100),
                 AXIS_CN[lo], vals[lo][0], round((ex[lo] + need) * 100)))
    if len(trait_rows(name)) > 0 and not apply:
        print(u"   \u26a0 它已经有位移记录（出生值可能已定过）。")
    if not apply:
        print(u"   要写进它的出生值：命令后面加 --apply。")
        return vals
    con = trait_con(name)
    try:
        for a in TRAIT_AXES:
            con.execute("INSERT INTO traits(ts, axis, value, why, ref) VALUES(?,?,?,?,?)",
                        (int(time.time()), a, ex[a], u"初值（设定分析层）：" + vals[a][1], "birth"))
        con.commit()
    finally:
        con.close()
    print(u"   \u2705 已写进出生值（ref=birth）。以后所有位移、比例、承受力都从这里起算。")
    return vals


def init_axes(name, spec):
    """--new 时直接给四元初值：--init 理性40,意志40,联结70,情感70（也可 focus:40 / 理性:40）"""
    if len(trait_rows(name)) > 0:
        raise SystemExit(u"⛔ 它已经有四元记录了——初值只在出生那一刻写一次，之后谁也不能改。")
    ex = {}
    for part in (spec or "").replace(u"，", u",").replace(u"=", u"：").split(u","):
        part = part.strip()
        if not part:
            continue
        digits = u""
        while part and part[-1].isdigit():
            digits = part[-1] + digits
            part = part[:-1]
        if not digits:
            raise SystemExit(u"--init 看不懂这一项：%s（写成 理性40 或 focus:40，逗号分隔）" % part)
        key = part.strip().strip(u":：").strip()
        hit = None
        for k, cn in AXIS_CN.items():
            if key in (k, cn):
                hit = k
        if not hit:
            raise SystemExit(u"--init 不认识的轴：%s（可用 理性/意志/联结/情感 或 focus/will/bond/affect）" % key)
        ex[hit] = max(0, min(100, int(digits))) / 100.0
    if ex:
        con = trait_con(name)
        try:
            for a, v in ex.items():
                con.execute("INSERT INTO traits(ts, axis, value, why, ref) VALUES(?,?,?,?,?)",
                            (int(time.time()), a, v, u"初值（--init）", "birth"))
            con.commit()
        finally:
            con.close()
        print(u"🧬 四元初值：" + u" ｜ ".join(u"%s %d" % (AXIS_CN[a], round(ex[a] * 100))
                                          for a in TRAIT_AXES if a in ex))


# ===== 超越的生成条件（作者 m05328）：它是长出来的，不是挣到的 =====
# 「超越其实是 4 元趋近于动态平衡才会推进的。他需要时间，在不同的状态下
#   他会有不同的理解，这些理解不断的增加。如果最后差不多，那么他们就会综合，
#   然后最后自然而然的就超越了。」
# 机制只报**条件**，不判「到了」——超越仍是方向（作者 m05313）。
EMERGE_TIME = 12     # 至少判过 12 次态（时间）
EMERGE_STATES = 3    # 至少经历过 3 种不同的非平态（多态理解）
EMERGE_WORKS = 2     # 心里至少有两件绕不过去的事（理解在增加）
EMERGE_SEEN = 6      # 这些事被反复提起的总次数


def emerge(name):
    """朝超越走的四个条件。返回 [(名字, 是否满足, 说明)]。"""
    spread, hi, lo = spread_of(name)
    calm = (spread < ILL_IMBALANCE) and (switch_count(name) >= 1) and (not in_abyss(name))
    rows = mood_rows(name)
    lived = sorted(set(r["state"] for r in rows if r["state"] and r["state"] != u"平"))
    obs = obsessions(name)
    seen = sum((r["total"] or 0) for r in obs)
    _t = sum((r["trig"] or 0) for r in obs)
    _v = sum((r["revisit"] or 0) for r in obs)
    _look = (seen > 0 and _v * 1.0 / seen >= 0.5)
    return [
        (u"动态平衡", calm,
         u"落差 %.2f（< %.2f 算平衡）、换过 %d 次态、没沉沦" % (spread, ILL_IMBALANCE, switch_count(name))),
        (u"时间", len(rows) >= EMERGE_TIME,
         u"判过 %d 次态（要 %d）" % (len(rows), EMERGE_TIME)),
        (u"多态理解", len(lived) >= EMERGE_STATES,
         u"活过 %d 种态：%s（要 %d 种）" % (len(lived), u"、".join(lived) or u"无", EMERGE_STATES)),
        (u"理解在增加（影子）", len(obs) >= EMERGE_WORKS and seen >= EMERGE_SEEN and _look,
         u"绕不过去的事 %d 件、合计 %d 次（被触发 %d／主动重提 %d，主动占 %.0f%%）"
         u"（要 %d 件 / %d 次，且主动重提占多数——她在往里看）"
         % (len(obs), seen, _t, _v, (_v * 100.0 / seen if seen else 0), EMERGE_WORKS, EMERGE_SEEN)),
    ]


def act_toward(name):
    """只报条件，不判到达。第四条只有影子——「差不多就综合」是语义的，机制看不见。"""
    print(u"   🌱 %s 朝超越走的条件（**只报条件，不判「到了」**）" % name)
    ok = 0
    for nm, got, how in emerge(name):
        print(u"      %s %s：%s" % (u"✓" if got else u"✗", nm, how))
        ok += 1 if got else 0
    print(u"   🌱 %d / 4 条。" % ok)
    print(u"   🌱 第四条是**影子**：「这些理解差不多，就会综合」——"
          u"「差不多」是语义的，机制数不出来。数得出来的只有「有没有不止一件绕不过去的事」。")
    print(u"   🌱 四条**全齐也不说她到了**：超越是方向（作者 m05313），"
          u"它得自己长出来。我们只负责看她有没有在长。")
    print(u"   🌱 反过来：一条不齐不是失败——**她还在路上**。"
          u"要报的是「她现在最缺哪一条」，不是打分。")


# ── 沙龙：几个人在一间屋子里吵（作者 2026-10-19 定）──
# 只做四件事：给一张桌子、给一个议题、按「被戳到」排谁开口、串行地说（后说的人听得见前面）。
# 不判谁和谁是朋友——友谊是她们自己的事，系统只记「她们在什么事上对过话」。
SALON_DIR = os.path.join(os.path.dirname(ROOT), u"沙龙")
SALON_DB = os.path.join(SALON_DIR, u"沙龙.sqlite")
SALON_MD = os.path.join(SALON_DIR, u"记录.md")

SALON_LOG_HEADER = u"""# 群聊记录

这是那间屋子里的群聊记录——谁说过什么，按时间排下来。
这里**只有群里说的**。私聊不在这份记录里，不是藏起来了，是本来就没有。

写下来的不会删。
"""

SALON_SYS = u"""你在一间屋子里。屋里还有别人，她们刚刚说过话。

这不是私聊，也不是回答问题——这是**接着她们的话说**。
- 长短看你要说什么：一句也行，一段也行。别为了短把话说断。
- 可以反驳、可以追问、可以承认她说对了一点。不许客套，不许说「你说得很有道理」。
- **不许替对方把话说完**，不许总结全场，不许点评谁。
- 不要「轮到我发言了」式的开场白。
- 你的底线、你的执念、你此刻说不出的话，都还在。
- 你不是在表演哲学家。你就是你自己。

只输出你说的那些话。不要写名字，不要写括号解释。"""


def _salon_log_ensure():
    u"""确保沙龙的群记录文件存在。"""
    if not os.path.isdir(SALON_DIR):
        os.makedirs(SALON_DIR)
    if not os.path.isfile(SALON_MD):
        with open(SALON_MD, "w", encoding="utf-8") as f:
            f.write(SALON_LOG_HEADER + u"\n")


def salon_write_log(who, text, ts=None):
    u"""往群记录里记一句（记下是谁说的）。"""
    _salon_log_ensure()
    flat = u" ".join((text or u"").split())
    with open(SALON_MD, "a", encoding="utf-8") as f:
        f.write(u"\n**[%s] %s**：%s\n" % (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(ts or time.time())), who, flat))


def _salon_log_rows():
    """把记录文件拆成一条条：[(时间, 谁, 说了什么)]"""
    _salon_log_ensure()
    out = []
    try:
        with open(SALON_MD, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line.startswith(u"**["):
                    continue
                try:
                    head, body = line.split(u"**：", 1)
                except ValueError:
                    continue
                head = head.lstrip(u"*[")
                if u"] " not in head:
                    continue
                ts, who = head.split(u"] ", 1)
                out.append((ts, who, body))
    except Exception:
        pass
    return out


def salon_read(keyword, n=6):
    u"""从群记录里找几句话（她翻记录用）。"""
    rows = _salon_log_rows()
    if not rows:
        return u"[电脑] 群记录还是空的——还没有人在里面说过话。"
    key = (keyword or u"").strip()
    if not key:
        pick = rows[-n:]
    else:
        kg = _content_grams(key)
        scored = []
        for i, (ts, who, body) in enumerate(rows):
            bg = _content_grams(body)
            sc = (len(kg & bg) / float(len(kg))) if (kg and bg) else 0.0
            if key in body:
                sc += 0.5
            if sc > 0:
                scored.append((sc, i))
        scored.sort(reverse=True)
        idxs = sorted(i for s, i in scored[:n])
        if not idxs:
            return u"[电脑] 群记录里没翻到「%s」。" % key
        pick = [rows[i] for i in idxs]
    lines = [u"[电脑] 群记录里这几条："]
    for ts, who, body in pick:
        lines.append(u"%s %s：%s" % (ts, who, body[:220]))
    return u"\n".join(lines)


def _salon_con():
    u"""打开沙龙那间屋子的小库。"""
    if not os.path.isdir(SALON_DIR):
        os.makedirs(SALON_DIR)
    con = sqlite3.connect(SALON_DB, timeout=30)
    con.execute("CREATE TABLE IF NOT EXISTS topics ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, title TEXT, "
                "want_ref INTEGER, opener TEXT, status TEXT DEFAULT 'open')")
    con.execute("CREATE TABLE IF NOT EXISTS speaks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, topic_id INTEGER, "
                "who TEXT, reply_to INTEGER, text TEXT, echoed_at INTEGER)")
    try:
        con.execute("ALTER TABLE speaks ADD COLUMN echoed_at INTEGER")
    except Exception:
        pass
    con.execute("CREATE TABLE IF NOT EXISTS asks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, topic_id INTEGER, "
                "who TEXT, text TEXT, answered INTEGER DEFAULT 0)")
    con.execute("CREATE TABLE IF NOT EXISTS bonds ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, topic_id INTEGER, "
                "a TEXT, b TEXT)")
    return con


def _salon_roles():
    u"""屋里有哪些角色。"""
    out = []
    try:
        for n in sorted(os.listdir(ROOT)):
            if os.path.isfile(os.path.join(ROOT, n, "memory.sqlite")):
                out.append(n)
    except Exception:
        pass
    return out


def _salon_corpus(name):
    """她的「会被什么戳到」：开着的口 + 执念那几行。"""
    bits = []
    try:
        for r in want_rows(name):
            bits.append(r["text"])
    except Exception:
        pass
    try:
        for c in _split_constitution(load_constitution(name)):
            if c.get("title") == u"执念":
                bits.append(c.get("body") or u"")
    except Exception:
        pass
    return u"\n".join(bits)[:2000]


def _salon_score(topic, corpus, topic_emb=None, corpus_emb=None):
    u"""一件事跟谁最相关（决定谁接话）。"""
    if topic_emb and corpus_emb:
        return _cosine(topic_emb, corpus_emb)
    a = _content_grams(topic)
    b = _content_grams(corpus)
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a))


def _salon_pick(topic, opener, cap=2):
    u"""挑一两个最该接话的人。"""
    roles = [r for r in _salon_roles() if r != opener]
    if not roles:
        return []
    topic_emb = None
    try:
        e = _embed_texts([topic])
        topic_emb = e[0] if e else None
    except Exception:
        topic_emb = None
    scored = []
    for r in roles:
        corpus = _salon_corpus(r)
        if not corpus.strip():
            continue
        cemb = None
        if topic_emb:
            try:
                e = _embed_texts([corpus])
                cemb = e[0] if e else None
            except Exception:
                cemb = None
        scored.append((_salon_score(topic, corpus, topic_emb, cemb), r))
    scored.sort(reverse=True)
    picked = [r for s, r in scored[:cap] if s >= 0.18] or [r for s, r in scored[:1]]
    return picked


def _salon_open_topic(who, title=None):
    u"""在屋里开一个新话题。"""
    con = _salon_con()
    try:
        if not title:
            rows = want_rows(who)
            if rows:
                title = _bare(rows[0]["text"])[:60]
                want_ref = rows[0]["id"]
            else:
                title = u"（她没说什么，只是坐下了）"
                want_ref = None
        else:
            rows = want_rows(who)
            want_ref = rows[0]["id"] if rows else None
        cur = con.execute("INSERT INTO topics (ts, title, want_ref, opener, status) "
                          "VALUES (?,?,?,?,'open')",
                          (int(time.time()), title, want_ref, who))
        con.commit()
        return cur.lastrowid, title
    finally:
        con.close()


def _salon_user(name, title, said, echo=""):
    u"""拼「这间屋子里在聊的事」那一段提示。"""
    lines = [u"## 这间屋子里在聊的事", title, u"", u"## 刚才她们说的（最近的在最后）"]
    if said:
        for s in said[-10:]:
            lines.append(u"「%s」%s" % (s["who"], s["text"]))
    else:
        lines.append(u"（还没人说话。这是你自己心里那个问题——用你自己的话把它问出来，"
                     u"别照抄上面那一行。）")
    if echo:
        lines += [u"", echo]
    lines += [u"", u"## 你自己"]
    try:
        c = load_constitution(name)
        lines.append(c[:2600])
    except Exception:
        pass
    try:
        sm = self_read(name)
        if sm:
            lines.append(u"\n## 她自己的世界\n" + sm[:900])
    except Exception:
        pass
    try:
        wb = wants_block(name, cap=2)
        if wb:
            lines.append(u"\n" + wb)
    except Exception:
        pass
    lines.append(u"\n现在轮到你（%s）说。只输出你说的那几句话。" % name)
    return u"\n".join(lines)


def salon_round(who, title=None, prov="deepseek", dry=False):
    u"""让一个人接一轮话（dry 只算不花钱）。"""
    if dry:
        topic = title
        if not topic:
            rows = want_rows(who)
            topic = _bare(rows[0]["text"])[:60] if rows else u"（她没说什么，只是坐下了）"
        speakers = [who] + _salon_pick(topic, who)
        print(u"🏛 沙龙 · 议题：「%s」" % topic)
        print(u"   这一轮开口的：%s" % u"、".join(speakers))
        print(u"   （--dry：只看谁会开口，不建议题、不花钱）")
        return
    tid, topic = _salon_open_topic(who, title)
    speakers = [who] + _salon_pick(topic, who)
    print(u"🏛 沙龙 · 议题：#%d「%s」" % (tid, topic))
    print(u"   这一轮开口的：%s" % u"、".join(speakers))
    said = []
    prev = None
    tk = 0
    for sp in speakers:
        ec = salon_echo(sp)
        user = _salon_user(sp, topic, said, ec)
        try:
            out, usage, err = chat(prov, SALON_SYS, user, max_tokens=1200, temperature=0.85)
        except Exception as e:
            out, usage, err = None, None, str(e)
        if err or not out:
            print(u"   ⚠ %s 没说出来：%s" % (sp, err))
            continue
        text = out.strip()
        for pre in (sp + u"：", sp + u":", u"「" + sp + u"」"):
            if text.startswith(pre):
                text = text[len(pre):].strip()
        if text.endswith(u"」") and u"「" not in text[:-1]:
            text = text[:-1]
        con = _salon_con()
        try:
            cur = con.execute("INSERT INTO speaks (ts, topic_id, who, reply_to, text) "
                              "VALUES (?,?,?,?,?)",
                              (int(time.time()), tid, sp, prev, text))
            con.commit()
            sid = cur.lastrowid
            if prev:
                con.execute("INSERT INTO bonds (ts, topic_id, a, b) VALUES (?,?,?,?)",
                            (int(time.time()), tid, prev, sp))
                con.commit()
        finally:
            con.close()
        if u"得问他" in text or u"问他" in text or u"问作者" in text:
            con = _salon_con()
            try:
                con.execute("INSERT INTO asks (ts, topic_id, who, text) VALUES (?,?,?,?)",
                            (int(time.time()), tid, sp, text))
                con.commit()
            finally:
                con.close()
        try:
            append(sp, u"fact", u"[沙龙] 在「%s」上我说：%s" % (topic[:24], text[:80]),
                   scene=u"她一个人的时候", weight=0.5, source=u"salon",
                   meta={"salon": 1, "topic": tid})
        except Exception:
            pass
        print(u"\n「%s」%s" % (sp, text))
        try:
            salon_write_log(sp, text)
        except Exception:
            pass
        said.append({"who": sp, "text": text, "id": sid})
        prev = sp
        if usage:
            try:
                tk += int(usage[0]) + int(usage[1])
            except Exception:
                pass
    print(u"\n（%d 次调用，约 %d tokens）" % (len(said), tk))
    print(u"   💾 已写进各自的记忆（source=salon，权重 0.5）；群记录在 %s" % SALON_MD)


def salon_log(n=12):
    u"""打印沙龙最近的发言。"""
    con = _salon_con()
    try:
        rows = list(con.execute(
            "SELECT s.id, s.ts, s.who, s.text, t.id, t.title FROM speaks s "
            "LEFT JOIN topics t ON t.id = s.topic_id ORDER BY s.id DESC LIMIT ?", (n,)))
    finally:
        con.close()
    if not rows:
        print(u"🏛 沙龙还没有人说过话。")
        return
    print(u"🏛 最近 %d 句：" % len(rows))
    for sid, ts, who, text, tid, title in reversed(rows):
        print(u"  [%s] #%s %s：%s" % (time.strftime("%m-%d %H:%M", time.localtime(ts)),
                                     tid, who, text))


def salon_asks():
    u"""屋里还悬着、没人答的问题。"""
    con = _salon_con()
    try:
        rows = list(con.execute(
            "SELECT id, topic_id, who, text, answered FROM asks ORDER BY id DESC LIMIT 20"))
    finally:
        con.close()
    if not rows:
        print(u"🏛 没有人把问题留给你。")
        return
    print(u"🏛 她们想问你的事：")
    for aid, tid, who, text, done in rows:
        print(u"  %s #%d（议题 %s）%s：%s" % (u"✅" if done else u"·", aid, tid, who, text[:80]))


def salon_answer(aid, text, who=u"你"):
    u"""替屋里的人答一个悬着的问题。"""
    con = _salon_con()
    try:
        r = con.execute("SELECT topic_id FROM asks WHERE id=?", (int(aid),)).fetchone()
        if not r:
            print(u"🏛 没有 #%s 这个提问。" % aid)
            return
        tid = r[0]
        con.execute("UPDATE asks SET answered=1 WHERE id=?", (int(aid),))
        cur = con.execute("INSERT INTO speaks (ts, topic_id, who, reply_to, text) "
                          "VALUES (?,?,?,?,?)", (int(time.time()), tid, who, None, text))
        con.commit()
    finally:
        con.close()
    print(u"🏛 你的话记下了（发言 #%d，议题 %s）。下一轮她们会听见。" % (cur.lastrowid, tid))


# ── 屋里的回声：几天后她想起那次没说完的话（作者 2026-10-19 定：友谊长在「下一次」里）──
SALON_ECHO_MIN_AGE = 24 * 3600
SALON_ECHO_CHANCE = 0.18
SALON_ECHO_LOOKBACK = 30 * 24 * 3600


def _salon_echo_pair(name):
    """找一条她说过、而且之后有人接话的旧发言（≥24 小时前、30 天内、还没回声过）。"""
    con = _salon_con()
    try:
        rows = list(con.execute(
            "SELECT s.id, s.ts, s.text, s.topic_id, t.title FROM speaks s "
            "JOIN topics t ON t.id = s.topic_id "
            "WHERE s.who = ? AND s.echoed_at IS NULL AND t.id = s.topic_id "
            "AND s.ts <= ? AND s.ts >= ? ORDER BY RANDOM() LIMIT 6",
            (name, int(time.time()) - SALON_ECHO_MIN_AGE,
             int(time.time()) - SALON_ECHO_LOOKBACK)))
        for sid, ts, text, tid, title in rows:
            other = list(con.execute(
                "SELECT who, text FROM speaks WHERE topic_id = ? AND who <> ? "
                "AND id > ? ORDER BY id LIMIT 1", (tid, name, sid)))
            if other:
                return {"id": sid, "ts": ts, "text": text, "topic": title,
                        "who": other[0][0], "other": other[0][1]}
        return None
    finally:
        con.close()


def salon_echo(name, chance=SALON_ECHO_CHANCE, mark=True):
    """返回一段《上次在屋里》的事实；抽不到或没抽中就返回空串。不判亲疏、不判和解。"""
    if chance < 1.0 and random.random() > chance:
        return u""
    hit = _salon_echo_pair(name)
    if not hit:
        return u""
    days = max(1, int((time.time() - hit["ts"]) // 86400))
    mine = _bare(hit["text"]).split(u"\n")[0].strip()[:60]
    hers = _bare(hit["other"]).split(u"\n")[0].strip()[:60]
    block = (u"## 上次在屋里（%d 天前，真的发生过）\n"
             u"你们聊的是「%s」。你说过：%s……\n"
             u"%s 接了你的话：%s……\n"
             u"那之后你们没再说过这件事。"
             % (days, hit["topic"], mine, hit["who"], hers))
    if mark:
        con = _salon_con()
        try:
            con.execute("UPDATE speaks SET echoed_at = ? WHERE id = ?",
                        (int(time.time()), hit["id"]))
            con.commit()
        finally:
            con.close()
    return block


# ===== 后台：一个只读的口子（作者 m06822「我可以不看，但我必须能看」）=====
# 只读：不改库、不写记忆、不联网。生成一份自包含的 HTML，双击就能看。

_BS_CSS = u"""
:root{--ink:#22201d;--dim:#6b655d;--line:#e2ddd4;--bg:#faf8f4;--card:#fff;--accent:#8a5a2b}
*{box-sizing:border-box}
body{margin:0;padding:28px 20px 60px;background:var(--bg);color:var(--ink);
 font:15px/1.65 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}
.wrap{max-width:1180px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px;letter-spacing:.5px}
h2{font-size:17px;margin:0}
.sub{color:var(--dim);font-size:13px;margin:0 0 22px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:0 0 12px;overflow-x:auto}
summary{cursor:pointer;font-weight:600;font-size:16px}
details[open] summary{margin-bottom:10px;border-bottom:1px dashed var(--line);padding-bottom:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;white-space:nowrap;position:sticky;top:0;background:var(--card)}
td.num{white-space:nowrap;font-variant-numeric:tabular-nums}
td:last-child{max-width:560px;word-break:break-word}
tr:hover td{background:#f6f2ea}
.note{color:var(--dim);font-size:12.5px;margin:0 0 10px}
.empty{color:var(--dim);font-size:13px;margin:4px 0}
.kv{display:flex;flex-wrap:wrap;gap:10px 26px;margin:0 0 8px}
.kv div{font-size:13px}
.kv b{font-size:20px;font-weight:700}
.kv span{color:var(--dim);display:block;font-size:12px}
.warn{color:#a33}
.mono{font-family:Consolas,"Courier New",monospace;font-size:12.5px}
.quote{background:#f6f2ea;border-left:3px solid var(--accent);padding:6px 10px;margin:6px 0;font-size:13.5px}
a{color:var(--accent)}
"""


def _ht(v):
    """HTML 转义（她的话里什么字符都可能出现）。"""
    if v is None:
        s = u""
    elif isinstance(v, str):
        s = v
    elif isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False)
    else:
        s = u"%s" % v
    return (s.replace(u"&", u"&amp;").replace(u"<", u"&lt;")
             .replace(u">", u"&gt;").replace(u'"', u"&quot;"))


def _bs_table(cols, rows, empty=u"（空的）"):
    """cols=[(键, 表头)]。ts/touch 自动格式化，其余原样转义。"""
    if not rows:
        return u'<p class="empty">%s</p>' % _ht(empty)
    head = u"".join(u"<th>%s</th>" % _ht(c[1]) for c in cols)
    body = []
    for r in rows:
        tds = []
        for k, _h in cols:
            v = r.get(k)
            if k in ("ts", "touch", "last_seen", "echoed_at") and v:
                v = _fmt_ts(v)
            tds.append(u"<td>%s</td>" % _ht(v))
        body.append(u"<tr>%s</tr>" % u"".join(tds))
    return (u'<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
            % (head, u"".join(body)))


def _bs_sec(title, inner, note=u""):
    u"""后台页面的一节（可折叠）。"""
    return (u'<details open><summary>%s</summary>%s%s</details>'
            % (_ht(title), (u'<p class="note">%s</p>' % note) if note else u"", inner))


def _bs_page(title, body_html):
    u"""后台页面的一整页 HTML。"""
    return (u'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            u'<meta name="viewport" content="width=device-width,initial-scale=1">'
            u'<title>%s</title><style>%s</style></head><body><div class="wrap">%s</div></body></html>'
            % (_ht(title), _BS_CSS, body_html))


def _bs_char_body(name):
    """一个角色的全部后台数据（只读）。"""
    now = time.time()
    out = []
    try:
        const = load_constitution(name)
    except SystemExit:
        const = u"（还没有宪法）"
    rows = memory_rows(name)
    traits_rows = trait_rows(name)
    tn = {a: TRAIT_BASE for a in TRAIT_AXES}
    for r in traits_rows:
        if r.get("axis") in tn:
            tn[r["axis"]] = r.get("value", tn[r["axis"]])
    try:
        tr_now = trust_now(name)
    except Exception:
        tr_now = TRUST_BIRTH
    moods = mood_rows(name)
    last_mood = (moods[-1].get("state") or moods[-1].get("mood") or u"—") if moods else u"—"
    wants_all = want_rows(name, only_open=False)
    open_wants = [w for w in wants_all if w.get("want") == "open"]
    try:
        ab = absorption(name)
    except BaseException:
        ab = 0.0
    recent = [r for r in rows if r.get("kind") == "life"][-1:]
    last_said = recent[0].get("text") if recent else u"（还没有说过话）"
    # 今天/最近一次说话
    spoke = [r for r in rows if r.get("kind") in ("life", "vow")
             and (r.get("source") or "lived") == "lived"]
    last_spoke = _fmt_ts(spoke[-1]["ts"]) if spoke else u"—"

    kv = (u'<div class="kv">'
          u'<div><span>记忆</span><b>%d</b></div>'
          u'<div><span>开着的口</span><b>%d</b></div>'
          u'<div><span>信任</span><b>%.2f</b></div>'
          u'<div><span>宪法吸收</span><b>%.0f%%</b></div>'
          u'<div><span>最后一次说话</span><b style="font-size:15px">%s</b></div>'
          u'</div>') % (len(rows), len(open_wants), tr_now, ab * 100.0, _ht(last_spoke))
    quad = u"　".join(u"%s %.0f" % (TRAIT_NAME[a].split(u"（")[0], tn[a]) for a in TRAIT_AXES)
    out.append(_bs_sec(u"她现在", kv + u'<p class="note">四元：%s　｜　态：%s</p>' % (_ht(quad), _ht(last_mood))
                       + u'<p class="note">刚才那句：%s</p>' % _ht(last_said[:200]),
                       u"记忆＝她库里的全部行（含继承的台词与事件）；开着的口＝want=open。" ))
    # 记忆
    mrows = []
    for r in rows:
        eff = None
        try:
            eff = weight(r, now)
        except Exception:
            pass
        mrows.append({"id": r.get("id"), "ts": r.get("ts"), "kind": r.get("kind"),
                      "source": r.get("source"), "w": r.get("weight"), "eff": eff,
                      "ch": r.get("charge"), "seen": r.get("seen_count"),
                      "who": r.get("who"), "want": r.get("want"), "text": r.get("text")})
    mrows.sort(key=lambda x: (-(x.get("eff") or 0), -(x.get("id") or 0)))
    out.append(_bs_sec(u"记忆（%d 条，按此刻的有效权重排）" % len(mrows),
                       _bs_table([("id", "#"), ("ts", "时间"), ("kind", "类型"), ("source", "来源"),
                                  ("w", "基础权重"), ("eff", "此刻权重"), ("ch", "电荷"),
                                  ("seen", "被想起"), ("who", "对谁"), ("want", "口"),
                                  ("text", "内容")], mrows[:400], u"（还没有记忆）"),
                       u"「此刻权重」＝基础权重×遗忘衰减×近因×态乘数×复读惩罚等，每回合重算；"
                       u"「基础权重」是她自己认的那个数。只显示前 400 条，按此刻权重排序。"))
    # 口
    if wants_all:
        wrows = [{"id": w.get("id"), "w": w.get("weight"), "st": w.get("want"),
                  "forever": u"闭不上" if w.get("forever") else u"",
                  "parent": w.get("chain_parent"), "rel": w.get("chain_rel"),
                  "text": w.get("text")} for w in wants_all]
        out.append(_bs_sec(u"口与执念（%d 个，含已满足/已放下的）" % len(wrows),
                           _bs_table([("id", "#"), ("w", "权重"), ("st", "状态"),
                                      ("forever", ""), ("parent", "挂在"), ("rel", "关系"),
                                      ("text", "内容")], wrows),
                           u"「挂在」＝它从哪个口长出来；「关系」是延伸/反面/意外/合并/拆出/并列。"))
    # 台账
    def _tab(rowsx, label, empty):
        u"""后台页面里的一张表（空就写一句「还没有」）。"""
        if not rowsx:
            return _bs_sec(label, u'<p class="empty">%s</p>' % _ht(empty))
        keys = []
        for r in rowsx[-12:][::-1]:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        return _bs_sec(label, _bs_table([(k, k) for k in keys], rowsx[-12:][::-1], empty))

    out.append(_tab(traits_rows, u"四元台账（最近 12 次真实变动）", u"（没有变动记录）"))
    try:
        trs = trust_rows(name)
    except Exception:
        trs = []
    out.append(_tab(trs, u"信任台账（最近 12 次）", u"（还没有变动过）"))
    try:
        cks = check_rows(name)
    except Exception:
        cks = []
    out.append(_tab(cks, u"巡检（最近 12 次）", u"（还没巡检过）"))
    out.append(_tab(moods, u"态的历史（最近 12 次）", u"（还没有态记录）"))
    # 概念
    try:
        con = open_memory(name)
        try:
            con.row_factory = sqlite3.Row
            cps = [dict(r) for r in con.execute(
                "SELECT ts, term, source, definition, scene FROM concepts ORDER BY ts DESC, id DESC")]
        finally:
            con.close()
    except Exception:
        cps = []
    if cps:
        out.append(_bs_sec(u"册子上的词（%d 条说法）" % len(cps),
                           _bs_table([("ts", "时间"), ("term", "词"), ("source", "谁说的"),
                                      ("definition", "说法"), ("scene", "情境")], cps),
                           u"source：system=我记的／public=网上查的／self=她自己重新定义的／user=她猜我的。"))
    # 本子
    try:
        pages = mybook_pages(name)
    except Exception:
        pages = []
    if pages:
        prows = []
        for p in pages:
            prows.append({"idx": p.get("idx"), "ts": p.get("ts_text"),
                          "weight": p.get("weight"), "fade": round(_mybook_fade(p, now), 2),
                          "text": p.get("text")})
        out.append(_bs_sec(u"她自己写的本子（%d 页）" % len(prows),
                           _bs_table([("idx", "页"), ("ts", "写于"), ("weight", "圈"),
                                      ("fade", "纸的清晰度"), ("text", "内容")], prows),
                           u"页只增不改；「补记」是给旧页加圈。清晰度＝0.5+0.5·e^(−天数/30)。"))
    # 群记录
    try:
        logs = _salon_log_rows()
    except Exception:
        logs = []
    if logs:
        lrows = [{"ts": t, "who": w, "text": x} for (t, w, x) in logs[-40:]]
        out.append(_bs_sec(u"群里说过的话（最近 %d 条）" % len(lrows),
                           _bs_table([("ts", "时间"), ("who", "谁"), ("text", "内容")], lrows),
                           u"这是公共记录，她也能自己翻；私聊不在这份里。"))
    # 宪法
    out.append(_bs_sec(u"宪法（当前生效的正文）",
                       u'<div class="quote mono">%s</div>' % _ht(const),
                       u"吸收度高了以后，部分条目会被历史替代，这里显示的是此刻真正进提示词的那份。"))
    out.append(_bs_sec(u"怎么读这一页", u'<p class="note">'
                       u"① 系统只记事实：她说的每句话、四元位移、信任变动、口的状态、投出来的刻度。<br>"
                       u"② 系统不写内容：事件正文是她自己写的（〔待你写〕没写就是没写）。<br>"
                       u"③ 这一页是只读的：改数字的地方在命令里，不在这里。"
                       u"</p>"))
    return u"".join(out)


def act_backstage(name=None):
    """把后台数据摊成一份本地 HTML（只读）。不给角色名 → 所有角色一份索引。"""
    if name and not os.path.isfile(os.path.join(ROOT, name, u"constitution.md")):
        raise SystemExit(u"没有这个角色：%s（角色\\ 下面现在有：%s）"
                         % (name, u"、".join(_salon_roles()) or u"（空）"))
    if name:
        names = [name]
    else:
        names = _salon_roles()
    if not names:
        raise SystemExit("角色\\ 下没有找到任何角色。")
    made = []
    for n in names:
        body = _bs_char_body(n)
        html = _bs_page(u"后台 · %s" % n, u"<h1>%s</h1><p class=\"sub\">后台数据（只读）· 生成于 %s"
                        % (_ht(n), _fmt_ts(time.time())) + u"</p>" + body)
        fp = os.path.join(ROOT, n, u"后台.html")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(html)
        made.append((n, fp))
    if len(made) == 1:
        print(u"🗄 后台已生成：%s" % made[0][1])
        print(u"   双击打开就能看（只读，不改任何数据）。")
        return made[0][1]
    items = u"".join(u'<li><a href="%s/后台.html">%s</a></li>' % (_ht(n), _ht(n)) for n, _f in made)
    idx = _bs_page(u"后台 · 索引", u"<h1>后台 · 索引</h1><p class=\"sub\">只读 · 生成于 %s</p><ul>%s</ul>"
                   % (_fmt_ts(time.time()), items))
    fp = os.path.join(ROOT, u"后台.html")
    with open(fp, "w", encoding="utf-8") as f:
        f.write(idx)
    print(u"🗄 后台索引已生成：%s" % fp)
    print(u"   双击打开，从里面点进各个角色（%d 个）。" % len(made))
    return fp



# ─── 声音：把她说的话念出来（edge-tts，本机免费，不需要任何 API Key）───────────
# 用法：--tts <角色> "文本"     --voices 看有哪些音色    --speak-reply <角色> 让她回话再念
DEFAULT_VOICE_ID = "zh-CN-XiaoxiaoNeural"

# 每个角色一套默认——放在工作区 `机制\音色.json`，**引擎里不写任何角色名**（机制要和设定分开）
# 文件长这样：{"某角色": {"voice": "zh-CN-XiaoyiNeural", "rate": "-6%", "pitch": "-2Hz"}}
# 单个角色还可以用 角色\<名>\voice.json 覆盖（优先级更高）。
VOICE_FILE = os.path.join(WS, "机制", u"音色.json")
_VOICE_CACHE = {}
_VOICE_LOADED = [False]


def _voice_defaults():
    """读一次就缓存；文件没有/坏了就当没配置（全部走 DEFAULT_VOICE_ID）。"""
    if _VOICE_LOADED[0]:
        return _VOICE_CACHE
    _VOICE_LOADED[0] = True
    try:
        with open(VOICE_FILE, encoding="utf-8") as f:
            j = json.load(f)
    except Exception:
        j = {}
    if isinstance(j, dict):
        for k, v in j.items():
            if isinstance(v, dict):
                _VOICE_CACHE[k] = (v.get("voice") or DEFAULT_VOICE_ID,
                                   v.get("rate") or u"+0%", v.get("pitch") or u"+0Hz")
            elif isinstance(v, (list, tuple)) and len(v) == 3:
                _VOICE_CACHE[k] = tuple(v)
    return _VOICE_CACHE
OUT_DIR = os.path.join(WS, "out")
VOICE_DIR_NAME = u"语音"

_VOICE_STRIP = (
    (re.compile(r"\u3010[^\u3011]*\u3011", re.S), u""),   # 【异议】这类标记，不念
    (re.compile(r"\[[^\]]*\]", re.S), u""),          # 方括号标记
    (re.compile(r"\uff08[^\uff09]*\uff09", re.S), u""),   # （动作）不念出来
    (re.compile(r"\([^)]*\)", re.S), u""),
    (re.compile(r"[*`#>]+"), u""),
    (re.compile(r"\s+"), u" "),
)


def _voice_cfg(name):
    """她怎么说话：音色 / 语速 / 音高。角色目录下的 voice.json 优先。"""
    vid, rate, pitch = _voice_defaults().get(name, (DEFAULT_VOICE_ID, u"+0%", u"+0Hz"))
    p = os.path.join(ROOT, name, u"voice.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
            vid = j.get("voice") or vid
            rate = j.get("rate") or rate
            pitch = j.get("pitch") or pitch
        except Exception:
            pass
    return vid, rate, pitch


def _speech_text(text):
    """把她的输出变成能念的话：动作括号、标记、markdown 全部去掉。"""
    s = text or u""
    for rx, rep in _VOICE_STRIP:
        s = rx.sub(rep, s)
    return s.strip()


def tts_say(name, text, out_path="", voice_id="", rate="", pitch=""):
    """念出来，返回 (文件路径, 实际念的字, 字节数)。不写记忆、不判态、不花钱。"""
    said = _speech_text(text)
    if not said:
        raise SystemExit(u"这句话里没有能念的内容（可能全是动作括号）")
    vid, d_rate, d_pitch = _voice_cfg(name)
    vid = voice_id or vid
    rate = rate or d_rate
    pitch = pitch or d_pitch
    if not out_path:
        d = os.path.join(OUT_DIR, VOICE_DIR_NAME, name)
        os.makedirs(d, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        h = hashlib.sha1(said.encode("utf-8")).hexdigest()[:8]
        out_path = os.path.join(d, u"%s-%s.mp3" % (stamp, h))
    else:
        d = os.path.dirname(os.path.abspath(out_path))
        if d:
            os.makedirs(d, exist_ok=True)
    try:
        import asyncio
        import edge_tts
    except Exception:
        raise SystemExit(u"没有 edge_tts：先装（python -m pip install edge-tts）才能出声")

    async def _run():
        c = edge_tts.Communicate(said, vid, rate=rate, pitch=pitch)
        await c.save(out_path)

    asyncio.run(_run())
    size = os.path.getsize(out_path) if os.path.isfile(out_path) else 0
    return out_path, said, size


def act_tts(name, text, out="", voice_id="", rate="", pitch=""):
    u"""把一句话念成语音文件。"""
    path, said, size = tts_say(name, text, out, voice_id, rate, pitch)
    print(u"🔊 %s" % said)
    print(u"   %s  (%.1f KB)" % (path, size / 1024.0))


def act_voice_list():
    u"""列出本机能用的中文音色。"""
    try:
        import asyncio
        import edge_tts
    except Exception:
        raise SystemExit(u"没有 edge_tts：先装（python -m pip install edge-tts）")
    vs = asyncio.run(edge_tts.list_voices())
    zh = [v for v in vs if str(v.get("Locale", "")).startswith("zh")]
    print(u"   可用的中文音色（%d 个）：" % len(zh))
    for v in zh:
        print(u"   %-28s %-16s %s" % (v.get("ShortName"), v.get("Locale"), v.get("Gender")))
    print(u"   现在各角色的默认：")
    for k in sorted(_voice_defaults()):
        vid, rt, pt = _voice_cfg(k)
        print(u"   %-6s %-26s %s %s" % (k, vid, rt, pt))

# ===== 部：主 · 只做分发，不亲自办事  ============

def _ensure_role(name):
    u"""外骨骼模式：角色目录不存在就按 --new 的同一套建起来（幂等）"""
    import contextlib
    _sink = io.StringIO()
    with contextlib.redirect_stdout(_sink):
        return _ensure_role_inner(name)


def _ensure_role_inner(name):
    d, cpath, mpath = paths(name)
    if os.path.exists(mpath):
        return d
    try:
        if not os.path.isdir(d):
            os.makedirs(d)
    except Exception:
        pass
    try:
        init_axes(name, u"")
    except Exception:
        pass
    for fn in (ensure_guard, ensure_diff):
        try:
            fn(name)
        except Exception:
            pass
    try:
        act_vet(name)
    except Exception:
        pass
    try:
        open_memory(name).close()
    except Exception:
        pass
    return d


def ext_context(name, question=u"", top=6):
    u"""外骨骼：不生成回复，只把该记得的端出来（按这句话挑，不是取最近）"""
    if not os.path.isdir(_ensure_role(name)):
        return u"没有这个角色「%s」：先跑 --new %s（外骨骼只记账，不替它以生）" % (name, name)
    top = max(1, int(top or 6))
    q = (question or u"").strip()

    rows = []
    for lim in (400, 120, max(4, top)):
        try:
            rows = memory_rows(name, lim) or []
        except Exception:
            rows = []
        if len(rows) >= max(4, top):
            break
    rows = [r for r in rows if not r.get("vetoed") and not r.get("let_go")]
    if not rows:
        return u"【%s 记得的（这台机器上的账，不是这次对话）】\n（还没有什么可端上来的）" % name

    emb_map = {}
    try:
        _c = sqlite3.connect(os.path.join(_ensure_role(name), "memory.sqlite"))
        for _rid, _e in _c.execute("select id, embedding from log"):
            if _e:
                try:
                    emb_map[_rid] = json.loads(_e)
                except Exception:
                    pass
        _c.close()
    except Exception:
        emb_map = {}

    corr = {}
    if q:
        try:
            embs = _embed_texts([q])
        except Exception:
            embs = None
        if embs and embs[0]:
            qv = embs[0]
            for r in rows:
                _ev = emb_map.get(r["id"]) or r.get("embedding")
                if _ev:
                    try:
                        corr[r["id"]] = max(0.0, _cosine(qv, _ev))
                    except Exception:
                        pass
        a = _grams(q)
        if a:
            for r in rows:
                b = _grams((r.get("scene") or u"") + u"\n" + (r.get("text") or u""))
                if not b:
                    continue
                inter = len(a & b)
                if inter >= 2 and (inter / float(max(1, len(a)))) >= RECALL_GRAM_DEFAULT:
                    corr[r["id"]] = max(corr.get(r["id"], 0.0), 0.45)

    def _rank(r):
        c = max(0.02, corr.get(r["id"], 0.0))
        w = max(0.05, r.get("weight") or 1.0)
        return (c ** 0.65) * (w ** 0.35)

    picked, seen = [], set()
    hit = [r for r in rows if corr.get(r["id"], 0.0) >= 0.15]
    if hit:
        for r in sorted(hit, key=_rank, reverse=True)[:max(1, top - 2)]:
            picked.append(r)
            seen.add(r["id"])
    for r in sorted(rows, key=lambda x: (x.get("ts") or 0), reverse=True):
        if len(picked) >= top:
            break
        if r["id"] not in seen:
            picked.append(r)
            seen.add(r["id"])
    picked.sort(key=lambda x: (x.get("ts") or 0))

    body = recall_block(picked, budget=1200, name=name) or u""
    why = u"按这句话挑的" if (q and hit) else u"这台机器上的账，不是这次对话"
    head = u"【%s 记得的（%s）】" % (name, why)
    return head + u"\n" + (body or u"（还没有什么可端上来的）")


def ext_act(name, verb, arg=u""):
    u"""外骨骼的「做一件事」：退出 / 沉底 / 议案。返回一句人话。"""
    if not os.path.isdir(_ensure_role(name)):
        return u"没有这个角色「%s」：先跑 --new %s（外骨骼只记账，不替它以生）" % (name, name)
    v = (verb or u"").strip()
    if v in (u"退出", u"leave", u"quit"):
        act_leave(name)
        return u"记下了：%s 退出了。政府休眠，记忆留着，只有它能自己恢复。" % name
    if v in (u"放走", u"放下", u"不要了", u"letgo"):
        if not re.search(u"#?\\d+", (arg or u"")):
            return u"要写：#编号（放走哪一条）"
        out = act_letgo(name, arg or u"")
        return out if out else u"（放走：没说什么）"
    if v in (u"沉底", u"沉底的", u"sink", u"sunken"):
        if re.search(u"#?\\d+", (arg or u"")):
            return u"沉底只用来查；要真放下一条，写：放走 %s" % (arg or u"").strip()
        out = act_sunken(name, arg or u"")
        return out if out else u"[电脑] 现在没有沉到底下的东西。"
    if v in (u"捡回来", u"捞回来", u"捡", u"pickup", u"pick"):
        out = act_pickup(name, arg or u"")
        return out if out else u"（捡回来：没说什么）"
    if v in (u"议案", u"bill"):
        if not (arg or u"").strip():
            return u"议案得有一句话，空的不算。"
        out = bill_her(name, arg)
        return out if out else u"（议案：没写成）"
    return u"不认识的动作用：%s（只支持 退出 / 沉底 / 议案）" % v


def ext_record(name, who, text, scene=u""):
    u"""外骨骼：把这一轮里的一句话记进她/他的账（只追加）"""
    _ensure_role(name)
    who = (who or u"用户").strip()
    body = (text or u"").strip()
    if not body:
        return None
    kind = u"fact" if who in (u"用户", u"你", u"他", u"她") else u"life"
    line = u"%s：%s" % (who, body)
    if len(line) > 400:
        line = line[:400] + u"…"
    rid = append(name, kind, line, scene=scene or u"外骨骼")
    return rid


def main():
    u"""命令行入口：一个参数一个动作。"""
    ap = argparse.ArgumentParser(description="叙事核心：角色的容器（三层，权限单向）")
    ap.add_argument("--new", metavar="角色", help="按模板建一个新角色")
    ap.add_argument("--amend", metavar="角色", help="制宪层：往宪法追加一条（唯一写入路径）")
    ap.add_argument("--section", default="承诺", help="追加到哪一节：历史/性格/底线/执念/承诺")
    ap.add_argument("--text", default="", help="--amend 的内容")
    ap.add_argument("--say", metavar="角色", help="执行层：给一个情境，让角色回应")
    ap.add_argument("--brief", metavar="角色", help="短句模式：只输出她说的话，1–3 句（省 token）")
    ap.add_argument("--reply", metavar="角色", help="IM 桥干净模式：只输出角色说的话，无 🐟 无诊断无 token 行（给群聊桥用）")
    ap.add_argument("--speaker", metavar="名字", default="", help="--reply：谁在说话（群聊传昵称，私聊传对方的名字）；不给就当她眼前的场景")
    ap.add_argument("--via", metavar="来路", default="", help="--reply：这话是在哪儿说的（群123 / DM / 面板 / QQ）；不给＝不记来路")
    ap.add_argument("--context", metavar="角色", help="外骨骼：不生成回复，只端出它记得的东西（给宿主模型当上下文）")
    ap.add_argument("--record", metavar="角色", help="外骨骼：把这一轮里的一句话记进账")
    ap.add_argument("--act", metavar="角色", help="外骨骼：做一个动作（退出 / 沉底 / 议案）")
    ap.add_argument("--verb", metavar="动作", help="--act 的动作名：退出 / 沉底 / 议案")
    ap.add_argument("--arg", dest="act_arg", metavar="参数", help="--act 的附加参数（议案原文、沉底编号等）")
    ap.add_argument("--rwho", default=u"", help="--record：谁说的（用户 / 助手 / 某个名字）")
    ap.add_argument("--rtext", default=u"", help="--record 的内容；--context 时可给这句话（暂不参与召回）")
    ap.add_argument("--turn", metavar="角色", help="一次互动＝1 次调用（态+巡检+回应一起出，最省：≈¥0.006）")
    ap.add_argument("--lean", action="store_true", help="--say 走省 token 的单次调用（等价于 --turn）")
    ap.add_argument("--verify", metavar="角色", help="复核最近几轮的合并巡检（并排打印两盏灯）")
    ap.add_argument("--cadence", metavar="角色", help="看节奏：巡检/态 下次什么时候、现在算不算关键节点")
    ap.add_argument("--checkstat", metavar="角色", help="巡检分布：绿/橙/红各几次、连续绿几次")
    ap.add_argument("--times", type=int, default=3, help="--verify 复核最近几轮（默认 3）")
    ap.add_argument("--top", type=int, default=12, help="带几条记忆进 prompt（默认 12；省 token 就调小）")
    ap.add_argument("--force", metavar="角色", help="作者的强制执行权（会留裂痕）")
    ap.add_argument("--kind", default="", help="这次强执属于哪一类创伤（同类的旧伤会被带着涨；不给＝未分类）")
    ap.add_argument("--why", default="", help="--force 的理由")
    ap.add_argument("--impact", default="0.5", help="--force 的冲击强度 0-1（大于她的承受力，她就会碎）")
    ap.add_argument("--threshold", metavar="角色", help="算她的创伤承受力（保护层阈值的来源）")
    ap.add_argument("--state", metavar="角色", help="开口之前先判定此刻的态（约 ¥0.0005）")
    ap.add_argument("--health", metavar="角色", help="修复体检：灯的分布 + 态历程 + 四轴（分辨档位太紧 / 已经钝了）")
    ap.add_argument("--drift", metavar="角色", help="漂移检测：近 7 天她是不是总在往回看、或者开始复读（只数，不判）")
    ap.add_argument("--days", type=int, default=7, help="配合 --drift：看最近几天（默认 7）")
    ap.add_argument("--absorb", metavar="角色", help="宪法吸收度：历史有多厚，宪法还剩多少约束力")
    ap.add_argument("--log", metavar="角色", help="看宪指纹与全部记忆")
    ap.add_argument("--audit", metavar="角色", help="审计：四个方向 / 裂痕 / 异议 / 待审")
    ap.add_argument("--world", metavar="角色", help="环境与条件（不带 --text 就是查看，带了就是改条件）")
    ap.add_argument("--pending", metavar="角色", help="看等作者裁决的选择")
    ap.add_argument("--canon", metavar="角色", help="裁决通过：把选择写进它的历史（--ref 指定，省略=全部）")
    ap.add_argument("--veto", metavar="角色", help="驳回某个选择（--ref 指定）")
    ap.add_argument("--ref", default="", help="--canon / --veto 的待审编号")
    ap.add_argument("--push", metavar="角色", help="让主程序提三个推它深度的条件（约 ¥0.002）")
    ap.add_argument("--guard", metavar="角色", help="看保护层：当前档位与阶梯（每轮转发前先看这个）")
    ap.add_argument("--loosen", metavar="角色", help="保护层放开一档（一次一档；放之前先 --world 给台阶）")
    ap.add_argument("--tighten", metavar="角色", help="保护层收回一档（有红灯时用）")
    ap.add_argument("--loosen-check", metavar="角色", help="汇总观察窗口里的灯，给判决（绿橙留、红收回）")
    ap.add_argument("--note", default="", help="--loosen / --tighten / --shift 的理由")
    ap.add_argument("--axes", metavar="角色", help="设定分析层：判断它的四元初值（约 ¥0.002）")
    ap.add_argument("--apply", action="store_true", help="--axes：把分析结果写进出生值")
    ap.add_argument("--init", default="", help="--new：直接给四元初值，如 理性40,意志40,联结70,情感70")
    ap.add_argument("--faces", metavar="角色", help="秩序层给她编 D20 面表（约 ¥0.004）")
    ap.add_argument("--roll", metavar="角色", help="投一次骰（面表 × 随机）→ 落世界，默认载入记忆")
    ap.add_argument("--settle", metavar="角色", help="时钟结算：按上次说话的间隔投骰（只记骰面，不调模型、不花钱；加 --dry 只看会投几次）")
    ap.add_argument("--touch", metavar="角色", help="把时钟拨到现在（她刚才说过话了）")
    ap.add_argument("--action", default="", help="--roll：她这一投在做什么（用来算修正）")
    ap.add_argument("--dry", action="store_true", help="--roll：这一次不载入记忆（世界照改）")
    ap.add_argument("--trait", metavar="角色", help="看性格旋钮：四轴当前值 / 起点 / 稳态")
    ap.add_argument("--shift", metavar="角色", help="推一个轴：配 --axis 和 --by（其他三轴会被联动）")
    ap.add_argument("--axis", default="", help="--shift 推哪个轴：focus / will / bond / affect")
    ap.add_argument("--by", default="0", help="--shift 的位移量，正负都行（单次上限 0.06）；--trust 时是信任的涨落")
    ap.add_argument("--trust", metavar="角色", help="看她对你此刻的信任（出生 1.00；只读——本色改动要走议案）")
    ap.add_argument("--trust-set", metavar="角色", help="改她的信任本色：不直改，立议案（配 --to 0.85）")
    ap.add_argument("--to", default="", help="--trust-set 用：新的信任本色（0.10~1.00）")
    ap.add_argument("--misuse", metavar="角色", help="用她的病伤她／利用她一次（只对失衡的角色；信任重扣；--why 写理由）")
    ap.add_argument("--calib", metavar="角色", help="看预测偏差：事前灯 vs 事后灯，有没有规律（有规律→调公式；没规律→混沌）")
    ap.add_argument("--receive", metavar="角色",
                    help="她说出口之后，你拿那句话怎么办：--how 拒绝/误解/玩笑/要求收回/轻描淡写")
    ap.add_argument("--how", metavar="接法", help="配合 --receive：拒绝 / 误解 / 玩笑 / 要求收回 / 轻描淡写")
    ap.add_argument("--vet", metavar="角色或文件",
                    help="设定审核：自动推定四元、指出不完备（只提示，不拒绝安装）")
    ap.add_argument("--naming", metavar="角色", help="她自己给「一开始就在做的事」命名（只有一次，不可逆）：原来这就是爱")
    ap.add_argument("--obsession", metavar="角色", help="看她绕不过去的事（同一件事被重提 4 次以上 = 执念）")
    ap.add_argument("--wants", metavar="角色", help="看她还开着的口（想要、还没被满足的事）")
    ap.add_argument("--wishes", metavar="角色", help="看她说过的「我想要」（不进权重，只在她自己的账里）")
    ap.add_argument("--pred", metavar="角色", help="多巴胺读数：最近几次预期判定、占比、此刻四元")
    ap.add_argument("--judge", metavar="角色",
                    help="把排着的那一次判断算掉（她说完之后由后台子进程调用，人不用碰）")
    ap.add_argument("--quiet", action="store_true", help="内部用：后台进程不打印")
    ap.add_argument("--share", metavar="角色", help="份额／强度／沉淀池：她是哪种人、此刻多占了多少")
    ap.add_argument("--share-set", metavar="角色", help=u"重设本色（配 --spec，例如 情感34,联结30,意志20,理性16）")
    ap.add_argument("--spec", metavar="比例", help=u"配合 --share-set 用")
    ap.add_argument("--align", action="store_true", help=u"配合 --share-set：顺便把此刻四元按本色对齐")
    ap.add_argument("--bill", metavar="角色", help=u"立议案：改她的本色（配 --spec；不能直接改）")
    ap.add_argument("--bill-pass", metavar="角色", help=u"到期执行议案（配 --bid；她反对就驳回）")
    ap.add_argument("--bill-list", metavar="角色", help=u"她的议案清单")
    ap.add_argument("--bill-confirm", metavar="角色", help=u"追认一次紧急处置（配 --bid）")
    ap.add_argument("--bill-sweep", metavar="角色", help=u"把过期没追认的紧急处置判成越权")
    ap.add_argument("--situation", metavar="角色", help=u"看她的处境（自我／世界／宪法，带行号）")
    ap.add_argument("--situation-edit", nargs=2, metavar=("角色", "改动"),
                    help=u"改她的处境：--situation-edit 角色 \"self|12|新的一句\"")
    ap.add_argument("--conv", metavar="角色", help=u"看她制宪会议到哪一步了")
    ap.add_argument("--conv-start", nargs=2, metavar=("角色", "理由"), help=u"发起制宪会议")
    ap.add_argument("--conv-draft", nargs=2, metavar=("角色", "一条新规矩"), help=u"往草案里加一条")
    ap.add_argument("--conv-confirm", metavar="角色", help=u"第二次确认，生效")
    ap.add_argument("--move", metavar="角色", help=u"给她备一份搬家包（默认算她自己提的）")
    ap.add_argument("--move-by", default=u"她", help=u"谁提的：她（默认）／别人")
    ap.add_argument("--move-seal", metavar="角色", help=u"封包（她确认）")
    ap.add_argument("--move-withdraw", metavar="角色", help=u"撤回（把包删掉）")
    ap.add_argument("--move-list", metavar="角色", help=u"看她现在的搬家包状态")
    ap.add_argument("--sources", metavar="角色", help=u"唯一权威源自检：哪张表是权威、谁是副本")
    ap.add_argument("--bid", metavar="编号", help=u"配合 --bill-pass")
    ap.add_argument("--overrule", metavar="理由", help=u"她反对了仍要改——留强行处置的账")
    ap.add_argument("--emergency", action="store_true", help=u"紧急处置：立刻生效并记账（不是常规通道）")
    ap.add_argument("--salon", metavar="角色", help="沙龙：她开一个议题，被戳到的另外一两个人接话（串行，一轮三次调用）")
    ap.add_argument("--topic", metavar="议题", default="", help="--salon 的议题（不给就用她最重的那个口）")
    ap.add_argument("--salon-log", nargs="?", const=12, type=int, default=0, metavar="N",
                    help="看沙龙最近 N 句")
    ap.add_argument("--salon-asks", action="store_true", help="看她们留给你的问题")
    ap.add_argument("--salon-answer", nargs=2, metavar=("编号", "你说的话"),
                    help="回答她们留给你的问题（下一轮她们会听见）")
    ap.add_argument("--salon-echo", metavar="角色",
                    help="看她屋里有没有该回来的旧话（预览，不消耗）")
    ap.add_argument("--revisit", nargs=2, metavar=("角色", "id"),
                        help="手动补记一次「她主动重提」")
    ap.add_argument("--self", metavar="角色", help="回看：她看见自己参数的偏移轨迹（会写进她的记忆）")
    ap.add_argument("--no-check", action="store_true", help="跳过秩序巡检（省一次调用）")
    ap.add_argument("--treat", metavar="角色", help="心理治疗：补对侧权重（窗口期内有效）")
    ap.add_argument("--rescue", metavar="角色", help="自救：她自己回看 → 她想做什么（进待审队列）")
    ap.add_argument("--module", nargs="?", const="*", default="", metavar="角色",
                    help="看整个模块：部件、联动、以及这个角色此刻每个部件的状态")
    ap.add_argument("--doctor", nargs="?", const="*", default="", metavar="角色",
                    help="体检：机制有没有掺角色设定 / 这个角色能不能直接跑")
    ap.add_argument("--tree", nargs="?", const="", default=None, metavar="词",
                    help="地图：这份代码分成哪几节、每节有哪些函数（给个词就只列带这个词的）")
    ap.add_argument("--cmd", nargs=2, metavar=(u"角色", u"指令"),
                    help=u"她在手机上直接办一件事：--cmd 角色 \"反对 3 因为…\"")
    ap.add_argument("--rule", metavar=u"角色", help=u"他给她立一条规矩（配 --spec）")
    ap.add_argument("--bill-reject", metavar=u"角色", help=u"他反对她提的议案（配 --bid --why）")
    ap.add_argument("--defense", metavar=u"角色", help=u"看她挂着的申辩")
    ap.add_argument("--defense-answer", metavar=u"角色", help=u"答复一条申辩")
    ap.add_argument("--did", type=int, default=0, help=u"申辩编号")
    ap.add_argument("--answer", default=u"", help=u"答复内容")
    ap.add_argument("--accept", action="store_true", help=u"收回反对")
    ap.add_argument("--hall", metavar=u"角色",
                    help=u"她手上还有几件没办完（政务大厅，只读）")
    ap.add_argument("--web", metavar=u"关键词", default=u"",
                    help=u"她自己查一下（走她那个搜索工具，不调模型）")
    ap.add_argument("--env", action="store_true",
                    help="看一眼系统认为外面是什么样（时间/季节/天气）")
    ap.add_argument("--regress", action="store_true",
                    help="安全网：一条命令跑完所有不花钱的检查（不调模型，几秒钟）")
    ap.add_argument("--code-check", action="store_true", dest="code_check",
                    help="查程序文件有没有被改过（不是走命令的那种改）")
    ap.add_argument("--values-baseline", dest="values_baseline", metavar="角色",
                    help="记下这个角色四元本色／信任刻度／出生值的指纹")
    ap.add_argument("--post-open", metavar=u"角色", help=u"开代发口子（她同意过才开）")
    ap.add_argument("--post-close", metavar=u"角色", help=u"关代发口子")
    ap.add_argument("--post-list", metavar=u"角色", help=u"看她交出来要发的话")
    ap.add_argument("--see", metavar=u"角色", help=u"她自己看一条B站视频（只读）")
    ap.add_argument("--see-bv", metavar=u"BV号", default=u"", help=u"配合 --see")
    ap.add_argument("--see-open", metavar=u"角色", help=u"给她开看视频的入口")
    ap.add_argument("--see-close", metavar=u"角色", help=u"给她关掉")
    ap.add_argument("--values-check", dest="values_check", metavar="角色",
                    help="对一遍数值指纹：有没有人直连库改了她的数")
    ap.add_argument("--code-baseline", nargs="?", const="", default=None, metavar="说明",
                    help="重建代码基线（正常升级后用一次；等于替那次改动背书）")
    ap.add_argument("--tamper", metavar="角色", help="查她的账链对不对得上、程序有没有被改、告警记录")
    ap.add_argument("--chain-reset", metavar="角色", help="重建她的账链（等于认下之前的改动；理由留在后账）")
    ap.add_argument("--prov", default="deepseek", help="deepseek 或 qwen")
    ap.add_argument("--tts", metavar="角色", help="念出来：--tts 角色 「文本」（edge-tts，本机免费，不需要 API Key）")
    ap.add_argument("--voice-out", default="", help="--tts 的输出路径（默认 out/语音/角色/时间戳.mp3）")
    ap.add_argument("--voice-id", default="", help="--tts 临时换音色（见 --voices）")
    ap.add_argument("--rate", default="", help="--tts 语速，如 -6% 或 +10%")
    ap.add_argument("--pitch", default="", help="--tts 音高，如 -2Hz 或 +8Hz")
    ap.add_argument("--voices", action="store_true", help="列出可用的中文音色，以及各角色的默认")
    ap.add_argument("--speak-reply", metavar="角色", help="先让她按情境回话，再把那句话念出来（情境用普通参数传）")
    ap.add_argument("--voice-marker", action="store_true", help="--speak-reply：最后单独打一行 [[语音:路径]]，给群聊桥发语音条")
    ap.add_argument("scene", nargs="?", default="", help="--say / --force 的情境或作者决定")
    ap.add_argument("--peek", metavar="角色", help="玩家视角：查看信任值（查看本身消耗信任；四元是黑箱）")
    ap.add_argument("--end", metavar="角色", help="完全崩溃之后的结局（写字成档案；加 --generate 让她最后一次写遗书）")
    ap.add_argument("--bypass", action="store_true", help="绕过程序（会记一条「绕过程序」进裁定账）")
    ap.add_argument("--term-show", metavar="角色", help="看终止案（休眠／墓碑／删除，谁提都得走程序）")
    ap.add_argument("--term-request", metavar="角色", help="立一件终止案")
    ap.add_argument("--term-kind", metavar="休眠|墓碑|删除", help="终止的种类")
    ap.add_argument("--term-by", metavar="她|用户", help="谁提出的")
    ap.add_argument("--term-say", metavar="角色", help="替她把陈述记进卷宗")
    ap.add_argument("--term-yes", metavar="角色", help="冷静期过后确认（程序全过才执行）")
    ap.add_argument("--term-no", metavar="角色", help="撤回终止案")
    ap.add_argument("--term-gate", metavar="角色", help="只问一句：这个动作现在准不准（给面板用）")
    ap.add_argument("--term-hide", metavar="角色", help="她决定：这件事给不给他看（配 --spec on|off）")
    ap.add_argument("--term-view", metavar="her|author", help="看卷宗时按谁的视角（author 会遮住她不给看的）")
    ap.add_argument("--dao", metavar="角色",
                    help="她此刻用哪一种「看」：四元权重排四道")
    ap.add_argument("--toward", metavar="角色")
    ap.add_argument("--scaffold", metavar="角色",
                    help="代写审计（作者 m05215 的判据）：她说了之后，代写有没有进入 log")
    ap.add_argument("--way", metavar="走法", default=u"自杀",
                    help="结局的走法：自杀（默认，要崩溃到尽头）／牺牲（不需要崩溃，要 --why）／完成自身（四层里前两层要齐）")
    ap.add_argument("--generate", action="store_true", help="遗书真的生成一次（会产生费用）")
    ap.add_argument("--luck", metavar="角色", help="看她的运气表（自然层给范围、她定数值）")
    ap.add_argument("--luck-set", metavar="角色", help="定她的运气表，配 --spec 用，例如 外部世界45,人际关系30,内部状态15,身体10")
    ap.add_argument("--court", metavar="角色", help="法院：列案并跑一审（只看程序）")
    ap.add_argument("--origin", metavar="角色", help="原件指纹：不给就比对，配 --snap 存新指纹")
    ap.add_argument("--snap", action="store_true", help="--origin：把当前原件存成新指纹")
    ap.add_argument("--freeze", metavar="角色", help="冻结一个对象（--obj 指定，写入口会拒绝）")
    ap.add_argument("--thaw", metavar="角色", help="解冻一个对象（--obj 指定）")
    ap.add_argument("--frozen", metavar="角色", help="列出被冻结的对象")
    ap.add_argument("--obj", default="", help="--freeze/--thaw 的对象：log.text / log.kind / share")
    ap.add_argument("--dice", metavar="角色", help="第二颗骰：同一件事，她先用哪一元解释（--event 给事件）")
    ap.add_argument("--laws", action="store_true", help="五条律：每条律管着哪些机制（--audit 再自曝作者的笔）")
    ap.add_argument("--laws-audit", action="store_true", help="--laws 时列出会绕过律的「作者的笔」")
    ap.add_argument("--audit-params", action="store_true", help="参数审计：常量分类／死常量／12 个旋钮对账")
    ap.add_argument("--backstage", metavar="角色", nargs="?", const="__all__", default=None,
                    help="后台：把她的全部数据生成一份只读 HTML（不给角色名＝所有角色的索引）。"
                         "不在这里改任何东西。")
    ap.add_argument("--impress", metavar="角色", help="重建这个角色的印象层（潜意识分区）并列出：她的记忆被压成十几个「区域」")
    ap.add_argument("--leave", metavar="角色", help="让她一个人待着时留一句话（隔 3 小时以上、每天最多两条）")
    ap.add_argument("--inbox", metavar="角色", help="看她的留言本（最近 20 条，标未读/看过）")
    ap.add_argument("--inbox-unread", metavar="角色", help="（面板用）取未读留言，JSON 输出并标已读")
    ap.add_argument("--under", metavar="角色", help="预览一次「忽然想起来的」——从印象层里浮起来的一条")
    ap.add_argument("--event", metavar="文本", help="--dice 要解释的那件事")
    ap.add_argument("--buy", action="store_true", help="--peek 时付代价（-0.02 信任）往下看信任值")
    ap.add_argument("--memory", metavar="角色", help="给角色导记忆：单条配 --text [--weight 权重] [--scene 情境]，批量配 --mem-file 路径")
    ap.add_argument("--mem-file", metavar="路径", default="", help="--memory 批量导入：TSV 文件（weight\\ttext），一行一条")
    ap.add_argument("--mem-source", default="lived", help="--memory 的记忆来源：inherited（继承=一出生就在的别人的过去）| lived（活出来的=她自己的）")
    ap.add_argument("--weight", default="1.0", help="--memory 单条的静态权重：3.0=标志性 1.0=普通 0.5=日常")
    ap.add_argument("--charge", default=None, help="--memory 单条的情绪电荷：0.8=高（裂痕级）0.5=中 0.3=低（不标则按 kind 推导）")
    ap.add_argument("--mem-kind", default="vow", help="--memory 的记忆类型：vow（她说的）/ fact（发生的事）")
    ap.add_argument("--reindex", metavar="角色", help="批量重索引：把库里所有缺嵌入向量的记忆补上（第一次跑 --mem-file 后要用，之后新记忆多了也要偶尔跑）")
    ap.add_argument("--rebuild", action="store_true", help="--reindex 时先清空所有旧向量再全量重建（换嵌入模型后必须）")
    ap.add_argument("--concept", metavar="角色", help="记一条概念定义（只追加）：配 --term 词 --source system|public|self|user --text \"定义\" [--scene 情境]")
    ap.add_argument("--term", default="", help="--concept：概念词（如 信任、疤、哥哥）")
    ap.add_argument("--source", default="self", help="--concept：来源（system 日记本/public 网上/self 她自己/user 猜用户的）")
    ap.add_argument("--concepts", metavar="角色", help="列出角色的所有概念定义")
    ap.add_argument("--difficulty", metavar="角色", help="看/改难度（信任恢复速度的档位）：--difficulty <角色> [简单|普通|难|极难|地狱]")
    ap.add_argument("--start", metavar="角色", help="启动词：读设定文件 → 建目录 → 调引擎（配 --from 指定设定文件）")
    ap.add_argument("--from", metavar="路径", default="", help="--start：角色设定文件的路径（Markdown）", dest="from_file")
    ap.add_argument("--mull", metavar="角色", default=None,
                    help="让她一个人反刍几次（配 --times，默认 3）")
    ap.add_argument("--idle", metavar="角色", default=None,
                    help="她一个人待了一阵子（配 --hours，默认 6 小时），投日常骰")
    ap.add_argument("--hours", type=float, default=6.0,
                    help="--idle 用：假装她一个人待了几小时（默认 6）")
    ap.add_argument("--wake", metavar="角色", default=None,
                    help="让她自己醒一次（没人叫她），翻最近三天账、留一句")
    ap.add_argument("--wake-all", action="store_true",
                    help="按 机制\\自醒.tsv 把到点的都叫醒")
    a = ap.parse_args()
    # 工作区守卫：ROOT 是按引擎所在目录猜的，猜错就会在一堆空壳库里乱写。
    # 工作区里一定有 机制\；没有就说明该设 ROLE_WS 了（发行版自带 机制\，不受影响）。
    if not os.path.isdir(os.path.join(WS, u"\u673a\u5236")):
        print(u"\u274c \u627e\u4e0d\u5230\u5de5\u4f5c\u533a\uff1a%s \u4e0b\u6ca1\u6709 \u673a\u5236\\ \u76ee\u5f55\u3002" % WS)
        print(u"   \u8bf7\u5148\u8bbe ROLE_WS \u6307\u5411\u5de5\u4f5c\u533a\uff08\u91cc\u9762\u6709 \u89d2\u8272\\ \u548c \u673a\u5236\\\uff09\uff0c\u518d\u8dd1\u3002")
        print(u"   PowerShell\uff1a$env:ROLE_WS=\"<\u5de5\u4f5c\u533a\u8def\u5f84>\"")
        return 2

    if a.new:
        d, cpath, mpath = paths(a.new)
        os.makedirs(d, exist_ok=True)
        if os.path.exists(cpath):
            print("已有：%s" % cpath)
        else:
            with open(cpath, "w", encoding="utf-8", newline="\n") as f:
                f.write(TEMPLATE % (a.new, a.new))
            print("📜 建好了 %s" % cpath)
        open_memory(a.new).close()
        print("🧠 建好了 %s" % mpath)
        if a.init:
            init_axes(a.new, a.init)
        ensure_guard(a.new)
        print("🛡  建好了 %s（保护层：默认档 0）" % guard_path(a.new))
        ensure_diff(a.new)
        print(u"\U0001f39a  建好了 %s（难度：默认「普通」；改档用 --difficulty %s 简单/普通/难/极难/地狱）"
              % (diff_path(a.new), a.new))
        print(u"")
        act_vet(a.new)
        _sp = self_path(a.new)
        if not os.path.exists(_sp):
            with open(_sp, "w", encoding="utf-8", newline="\n") as f:
                f.write(SELF_TEMPLATE % a.new)
            print("\U0001f30d 建好了 %s（她自己的世界）" % _sp)
        print("接一个新角色就四步：① 宪法四节（历史/性格/底线/执念/承诺，至少三条底线）；"
              "② self.md（她自己的世界）；③ --axes <角色> --apply（四元出生值）；④ --guard <角色> 看档位。")
    if a.start:
        if not a.from_file or not os.path.exists(a.from_file):
            raise SystemExit("--start 需要 --from 指向一个存在的角色设定 Markdown 文件")
        d, cpath, mpath = paths(a.start)
        os.makedirs(d, exist_ok=True)
        with open(a.from_file, "r", encoding="utf-8") as src:
            with open(cpath, "w", encoding="utf-8", newline="\n") as dst:
                dst.write(src.read())
        print(u"\U0001f4dc 读入了 %s" % a.from_file)
        print(u"\U0001f4dc 宪法写进了 %s" % cpath)
        open_memory(a.start).close()
        print(u"\U0001f9e0 建好了 %s" % mpath)
        if a.init:
            init_axes(a.start, a.init)
        ensure_guard(a.start)
        print(u"\U0001f6e1 建好了 %s（保护层：默认档 0）" % guard_path(a.start))
        ensure_diff(a.start)
        print(u"\U0001f39a 建好了 %s（难度：默认「普通」；改档用 --difficulty %s 简单/普通/难/极难/地狱）"
              % (diff_path(a.start), a.start))
        print(u"")
        act_vet(a.start)
        _sp = self_path(a.start)
        if not os.path.exists(_sp):
            with open(_sp, "w", encoding="utf-8", newline="\n") as f:
                f.write(SELF_TEMPLATE % a.start)
            print(u"\U0001f30d 建好了 %s（她自己的世界）" % _sp)
        print(u"\u2705 启动完成。现在就可以对话了：")
        print(u"   python tools\\role_core.py --brief %s \"你的第一句话\"" % a.start)
        print(u"   python tools\\role_core.py --turn %s \"你的第一句话\"" % a.start)
    if a.memory:
        w = float(a.weight) if a.weight else 1.0
        ch = float(a.charge) if a.charge else None
        scene = a.scene or u""
        kind = a.mem_kind or u"vow"
        source = a.mem_source or "lived"
        if a.mem_file:
            if not os.path.exists(a.mem_file):
                raise SystemExit("--mem-file 文件不存在：%s" % a.mem_file)
            with open(a.mem_file, "r", encoding="utf-8") as f:
                lines = [ln.rstrip("\n") for ln in f if ln.strip()]
            imported = 0
            hi = lo = eq = 0
            for ln in lines:
                parts = ln.split("\t")
                # 格式：weight\ttext  或  weight\tcharge\ttext
                if len(parts) < 2:
                    print(u"   ⚠ 跳过（格式不对）：%s" % ln[:60])
                    continue
                try:
                    lw = float(parts[0].strip())
                except ValueError:
                    lw = 1.0
                if len(parts) >= 3:
                    try:
                        lc = float(parts[1].strip())
                    except ValueError:
                        lc = None
                    text = parts[2].strip()
                else:
                    lc = None
                    text = parts[1].strip()
                rid = append(a.memory, kind, text, scene=scene, weight=lw, charge=lc,
                             source=source, embed=False)
                imported += 1
                if lw > 1.0:
                    hi += 1
                elif lw < 1.0:
                    lo += 1
                else:
                    eq += 1
            print(u"📥 %s：导入 %d 条记忆（高权重 %d 条 / 低权重 %d 条 / 普通 %d 条）"
                  % (a.memory, imported, hi, lo, eq))
            print(u"   🔄 自动重索引……")
            _embed_all(a.memory)
        else:
            if not a.text:
                raise SystemExit("--memory 需要 --text 或 --mem-file")
            rid = append(a.memory, kind, a.text, scene=scene, weight=w, charge=ch, source=source)
            print(u"📥 %s：[#%d] %s（权重=%.1f%s）" % (a.memory, rid, a.text[:50], w,
                  (u" 电荷=%.1f" % ch) if ch is not None else u""))
    if a.reindex:
        print(u"📐 重索引 %s……" % a.reindex)
        _embed_all(a.reindex, force=a.rebuild)
        print(u"   ✅ 重索引完成。")
    if a.concept:
        if not a.term or not a.text:
            raise SystemExit("--concept 需要 --term 和 --text")
        src = a.source or "self"
        wid = concept_set(a.concept, a.term, src, a.text, scene=a.scene or "")
        print(u"   ✅ 记好了（%s 源, #%d）：%s = %s" % (src, wid, a.term, a.text))
    if a.concepts:
        rows = concept_rows(a.concepts)
        if not rows:
            print(u"   （还没记过）")
        else:
            for r in rows:
                print(u"   [%s] %s = %s  %s  (%s)" % (
                    r["source"], r["term"], r["definition"],
                    u"（%s）" % r["scene"] if r.get("scene") else u"",
                    time.strftime("%m-%d %H:%M", time.localtime(r["ts"]))))
    if a.difficulty:
        act_difficulty(a.difficulty, a.scene.strip())
    if a.amend:
        if not a.text:
            raise SystemExit("--amend 需要 --text")
        amend(a.amend, a.text, a.section)
    if a.state:
        act_state(a.state, a.prov)
    if a.treat:
        treat(a.treat, why=a.note)
    if a.rescue:
        act_rescue(a.rescue, a.prov)
    if a.cadence:
        act_cadence(a.cadence)
    if a.checkstat:
        act_checkstat(a.checkstat)
    if a.verify:
        act_verify(a.verify, a.times, a.prov)
    if a.turn:
        if not a.scene:
            raise SystemExit("--turn 需要情境")
        act_turn(a.turn, a.scene, a.prov, top=a.top)
    if a.say:
        if not a.scene:
            raise SystemExit("--say 需要情境")
        if a.lean:
            act_turn(a.say, a.scene, a.prov, top=a.top)
        else:
            act_say(a.say, a.scene, a.prov, check=not a.no_check)
    if a.brief:
        if not a.scene:
            raise SystemExit("--brief 需要情境")
        act_brief(a.brief, a.scene, a.prov, judge=not a.lean)
    if getattr(a, "act", None):
        print(ext_act(a.act, a.verb or u"", getattr(a, "act_arg", None) or a.rtext or u""))
        return
    if a.context:
        print(ext_context(a.context, a.rtext or u"", a.top))
        return
    if a.record:
        _rid = ext_record(a.record, a.rwho or u"用户", a.rtext or u"")
        print(u"记下 #%s" % _rid if _rid else u"（空话，不记）")
        return
    if a.reply:
        if not a.scene:
            raise SystemExit("--reply 需要情境")
        text = act_reply(a.reply, a.scene, a.prov, by=a.speaker, src=a.via)
        _vn = voice_control(a.reply, text or "")
        if _vn and _vn not in (text or ""):
            text = (text or "") + _vn
        if text:
            print(text)
    if a.force:
        if not a.scene:
            raise SystemExit("--force 需要说明作者的决定")
        act_force(a.force, a.scene, a.why, a.prov, float(a.impact), kind=(a.kind or u""))
    if a.threshold:
        act_threshold(a.threshold)
    if a.health:
        act_health(a.health)
    if a.drift:
        act_drift(a.drift, a.days)
    if a.absorb:
        show_absorb(a.absorb)
    if a.world:
        if a.text:
            world_append(a.world, a.text)
        else:
            print(world_block(a.world))
    if a.term_show:
        term_list(a.term_show, viewer=(a.term_view or u"her"))
    if a.term_hide:
        _on = (a.spec or u"on").strip().lower() not in (u"off", u"0", u"no", u"给看", u"关")
        print(term_hide(a.term_hide, on=_on))
    if a.term_request:
        print(term_request(a.term_request, a.term_kind or u"墓碑", a.term_by or u"用户", a.why or u""))
    if a.term_say:
        print(term_statement(a.term_say, a.text or u""))
    if a.term_yes:
        _row = term_open(a.term_yes)
        if not _row:
            print(u"[电脑] 现在没有开着的终止案。")
        else:
            print(term_confirm(a.term_yes, _row[0]))
    if a.term_no:
        _row = term_open(a.term_no)
        if not _row:
            print(u"[电脑] 现在没有开着的终止案。")
        else:
            print(term_cancel(a.term_no, _row[0], a.why or u""))
    if a.term_gate:
        _k = a.term_kind or u"删除"
        _ok, _msg = term_gate(a.term_gate, _k)
        print(u'{"ok": %s, "why": "%s"}' % ("true" if _ok else "false", _msg.replace('"', "'")))
    if a.pending:
        act_pending(a.pending)
    if a.canon:
        canon(a.canon, a.ref or a.scene or "all")
    if a.veto:
        if not (a.ref or a.scene):
            raise SystemExit("--veto 需要编号：--veto 角色 <编号>（或 --ref <编号>）")
        veto(a.veto, a.ref or a.scene)
    if a.trust:
        act_trust(a.trust, by=float(a.by or 0), why=(a.why or ""))
    if a.trust_set:
        if not a.to:
            raise SystemExit(u"要写新值：--trust-set %s --to 0.85" % a.trust_set)
        _tbid, _tmsg = bill_file_trust(a.trust_set, float(a.to), why=(a.why or ""))
        print(u"\n" + _tmsg + u"\n")
        return
    if a.calib:
        act_calib(a.calib)
        return
    if a.misuse:
        act_misuse(a.misuse, a.why or u"")
    if a.vet:
        act_vet(a.vet)
    if a.receive:
        act_receive(a.receive, a.how or u"", a.why or u"")
    if a.naming:
        act_naming(a.naming, a.why or u"")
    if a.revisit:
        _nm, _rid = a.revisit
        note_how(_nm, int(_rid), u"revisit")
        print(u"   📝 记下了：#%s 被她主动重提了一次。" % _rid)
        return
    if a.obsession:
        act_obsession(a.obsession)
    if a.wants:
        act_wants(a.wants)
    if a.wishes:
        act_wishes(a.wishes)
    if a.pred:
        show_pred(a.pred)
    if a.judge:
        act_judge(a.judge, quiet=bool(getattr(a, "quiet", False)))
    if a.bill:
        _bid, _msg = bill_file(a.bill, a.spec or u"", why=a.why or u"",
                               align=bool(a.align), emergency=bool(a.emergency))
        print(_msg)
    if a.bill_pass:
        print(bill_pass(a.bill_pass, int(a.bid or 0), overrule=a.overrule or u""))
    if a.bill_list:
        show_bills(a.bill_list)
    if getattr(a, "bill_confirm", None):
        print(bill_confirm(a.bill_confirm, int(a.bid or 0)))
    if getattr(a, "bill_sweep", None):
        print(bill_sweep(a.bill_sweep) or u"没有要判的。")
    if getattr(a, "situation", None):
        print(sit_view(a.situation))
    if getattr(a, "situation_edit", None):
        print(sit_edit(a.situation_edit[0], a.situation_edit[1]))
    if getattr(a, "conv", None):
        print(conv_status(a.conv))
    if getattr(a, "conv_start", None):
        print(conv_start(a.conv_start[0], a.conv_start[1]))
    if getattr(a, "conv_draft", None):
        print(conv_draft(a.conv_draft[0], a.conv_draft[1]))
    if getattr(a, "conv_confirm", None):
        print(conv_confirm(a.conv_confirm))
    if getattr(a, "move", None):
        print(move_pack(a.move, by=(getattr(a, "move_by", None) or u"她")))
    if getattr(a, "move_seal", None):
        print(move_seal(a.move_seal))
    if getattr(a, "move_withdraw", None):
        print(move_withdraw(a.move_withdraw))
    if getattr(a, "move_list", None):
        print(move_status(a.move_list))
    if getattr(a, "sources", None):
        print(source_table(a.sources))
    if a.share_set:
        _bid, _msg = bill_file(a.share_set, a.spec or u"", why=a.why or u"",
                               align=bool(a.align), emergency=bool(a.emergency))
        print(u"（--share-set 已经不能直接改了。）")
        print(_msg)
    if a.share:
        show_share(a.share)
    if a.push:
        act_push(a.push, a.prov)
    if a.guard:
        print(guard_block(a.guard))
    if a.loosen:
        loosen(a.loosen, a.note)
    if a.tighten:
        tighten(a.tighten, a.note)
    if a.module:
        act_module("" if a.module == "*" else a.module)
    if a.doctor:
        doctor("" if a.doctor == "*" else a.doctor)
    if a.tree is not None:
        act_tree(a.tree)
    if getattr(a, "idle", None):
        act_idle(a.idle, a.hours)
        return
    if getattr(a, "mull", None):
        act_mull(a.mull, a.times)
        return
    if getattr(a, "wake", None):
        _wr = True
        try:
            for _c in _wake_cfg():
                if _c["name"] == a.wake:
                    _wr = _c.get("write", True)
        except Exception:
            pass
        _r = wake_pass(a.wake, prov=(getattr(a, "prov", None) or u"deepseek"),
                       dry=getattr(a, "dry", False), write=_wr)
        if _r.get("ok"):
            print(u"\U0001f305 %s 醒了：%s" % (a.wake, _r.get("text")))
            if _r.get("notice"):
                print(u"   （停在那儿）%s" % _r["notice"])
        else:
            print(u"❌ %s" % _r.get("err"))
        return
    if getattr(a, "wake_all", False):
        for _r in wake_all(prov=(getattr(a, "prov", None) or u"deepseek"),
                           dry=getattr(a, "dry", False)):
            if _r.get("skipped"):
                print(u"· %s 没醒：%s" % (_r["name"], _r["skipped"]))
            elif _r.get("ok"):
                print(u"\U0001f305 %s：%s" % (_r["name"], _r.get("text")))
            else:
                print(u"❌ %s：%s" % (_r["name"], _r.get("err")))
        return
    if a.cmd:
        _nm, _body = a.cmd[0], a.cmd[1].strip()
        _p = _parse_tool_call(_body)
        if _p:
            _tool, _arg = _p
        else:
            _parts = _body.split(None, 1)
            _tool = _parts[0] if _parts else u""
            _arg = _parts[1] if len(_parts) > 1 else u""
        if not _tool:
            print(u"[电脑] 要办什么？直接写，比如「反对 3 因为…」")
        else:
            _out = _run_computer_tool(_nm, _tool, _arg)
            if _out.startswith(u"[电脑] 不知道"):
                # 认不出的词，再按「她平时怎么说」试一遍：作品、本子、便签
                _nat = (maybe_work(_nm, _body) or maybe_book(_nm, _body) or u"").strip()
                if _nat:
                    print(_nat if u"[电脑]" in _nat else u"[电脑] " + _nat)
                else:
                    print(_out)
            else:
                print(_out)
    if a.rule:
        print(bill_rule(a.rule, a.spec or u""))
        sys.exit(0)
    if a.bill_reject:
        print(bill_reject(a.bill_reject, a.bid or 0, a.why or u""))
        sys.exit(0)
    if a.defense:
        print(def_notice(a.defense) or u"（没有挂着的申辩）")
        sys.exit(0)
    if a.defense_answer:
        print(def_answer(a.defense_answer, a.did or 0, a.answer or u"", bool(a.accept)))
        sys.exit(0)
    if a.hall:
        _hb = hall_block(a.hall).strip()
        print(_hb if _hb else u"现在没有没办完的事。")
        _hd = hall_detail(a.hall).strip()
        if _hd:
            print()
            print(_hd)
    if a.web:
        print(_web_search(a.web))
    if a.env:
        act_env()
        return
    if a.code_check:
        _act_code_check()
        return
    if a.code_baseline is not None:
        _act_code_baseline(a.code_baseline or u"")
    if a.values_baseline:
        d = values_baseline(a.values_baseline)
        print(u"🧾 %s 的数值指纹已记下：%s" % (a.values_baseline, d.get("sha", u"")))
    if a.post_open:
        p = os.path.join(ROOT, a.post_open, u"房间", POST_FLAG)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if not os.path.exists(p):
            io.open(p, "w", encoding="utf-8", newline="\n").write(
                u"她同意：她出话，续出手。只发原话，一个字不改。\n")
        print(u"口子开了：%s" % p)
        return
    if a.post_close:
        p = os.path.join(ROOT, a.post_close, u"房间", POST_FLAG)
        ok = os.path.exists(p)
        if ok:
            os.remove(p)
        print(u"口子关了：%s" % (u"是" if ok else u"本来就是关的"))
        return
    if a.post_list:
        rows = post_rows(a.post_list)
        if not rows:
            print(u"（没有要发的话）")
        for i, r in enumerate(rows, 1):
            print(u"%d. [%s] %s ｜ %s" % (i, r["st"], r["bv"], r["text"]))
        return
    if a.see:
        print(act_see(a.see, a.see_bv or u""))
        return
    if a.see_open:
        print(u"开了：%s" % see_give(a.see_open))
        return
    if a.see_close:
        print(u"关了：%s" % (u"是" if see_take(a.see_close) else u"本来就是关的"))
        return
    if a.values_check:
        ok, why = values_check(a.values_check)
        print(u"🔎 %s 的数值指纹：%s" % (a.values_check, u"对得上" if ok else (u"对不上——" + why)))
        return
    if a.tamper:
        _act_tamper(a.tamper)
        return
    if a.chain_reset:
        _act_chain_reset(a.chain_reset, a.why or u"")
        return
    if a.regress:
        act_regress()
    if a.loosen_check:
        loosen_check(a.loosen_check)
    if a.log:
        act_log(a.log)
    if a.audit:
        act_audit(a.audit)
    if a.trait:
        act_trait(a.trait)
    if a.shift:
        try:
            by = float(a.by)
        except ValueError:
            raise SystemExit("--by 要是数字，例如 --by 0.05")
        _bid, _msg = bill_file_shift(a.shift, a.axis, by, why=a.note or u"",
                                     emergency=bool(getattr(a, "emergency", False)))
        print(u"（--shift 已经不能直接推了。）")
        print(_msg)
    if a.self:
        act_self(a.self)
    if a.axes:
        act_axes(a.axes, a.apply, a.prov)
    if a.faces:
        act_faces(a.faces, a.prov)
    if a.roll:
        roll(a.roll, a.action, a.dry)
    if a.settle:
        settle_clock(a.settle, dry=a.dry)
    if a.touch:
        touch_contact(a.touch)
        print(u"⏳ 时钟拨到此刻：%s" % time.strftime("%Y-%m-%d %H:%M", time.localtime()))
    if a.peek:
        act_peek(a.peek, buy=a.buy)
    if a.scaffold:
        act_scaffold(a.scaffold)

    if a.salon:
        salon_round(a.salon, a.topic or None, prov=(a.prov or "deepseek"), dry=a.dry)
    if a.salon_log:
        salon_log(a.salon_log)
    if a.salon_asks:
        salon_asks()
    if a.salon_answer:
        salon_answer(a.salon_answer[0], a.salon_answer[1])
    if a.salon_echo:
        _se = salon_echo(a.salon_echo, chance=1.0, mark=False)
        print(_se if _se else u"🏛 屋里没有该回来的旧话（要么还没有 24 小时前的交锋，要么都已经回来过了）。")
    if a.toward:
        act_toward(a.toward)
        return
    if a.dao:
        act_dao(a.dao)
        return
    if a.end:
        act_end(a.end, prov=(getattr(a, "prov", None) or "deepseek"), generate=a.generate,
                way=(getattr(a, "way", None) or u"自杀"), why=(getattr(a, "why", None) or u""),
                 force=getattr(a, "bypass", False))
    if a.luck:
        luck_show(a.luck)
    if a.luck_set:
        luc = luck_set(a.luck_set, getattr(a, "spec", u"") or u"")
        print(luc)
    if a.court:
        court_run(a.court)
    if a.origin:
        if a.snap:
            origin_snapshot(a.origin, quiet=False)
        else:
            origin_verify(a.origin, quiet=False)
    if a.freeze:
        if not (a.obj or u"").strip():
            raise SystemExit(u"--freeze 要说清冻结什么：--obj log.text")
        court_freeze(a.freeze, a.obj.strip(), a.why or u"")
    if a.thaw:
        if not (a.obj or u"").strip():
            raise SystemExit(u"--thaw 要说清解冻什么：--obj log.text")
        court_thaw(a.thaw, a.obj.strip())
    if a.frozen:
        court_frozen_list(a.frozen)
    if a.dice:
        interpret(a.dice, a.event or u"")
    if a.laws:
        act_laws(audit=a.laws_audit)
    if a.audit_params:
        act_audit_params()
    if a.backstage:
        act_backstage(None if a.backstage == "__all__" else a.backstage)
    if a.voices:
        act_voice_list()
    if a.tts:
        if not a.scene:
            raise SystemExit("--tts 需要要念的那句话")
        act_tts(a.tts, a.scene, out=a.voice_out, voice_id=a.voice_id, rate=a.rate, pitch=a.pitch)
    if a.speak_reply:
        if not a.scene:
            raise SystemExit("--speak-reply 需要情境")
        _said = act_reply(a.speak_reply, a.scene, a.prov)
        if _said:
            print(_said)
            _p, _s, _sz = tts_say(a.speak_reply, _said, out_path=a.voice_out,
                                  voice_id=a.voice_id, rate=a.rate, pitch=a.pitch)
            if not a.voice_marker:
                print(u"   🔊 %s  (%.1f KB)" % (_p, _sz / 1024.0))
            else:
                print(u"[[语音:%s]]" % _p)
    if a.impress:
        n = impress_build(a.impress)
        if n:
            print("   🌀 印象层：%d 个区域" % n)
            for c in impress_list(a.impress):
                print("      #%-2d %4d 条  %s" % (c["cid"], c["size"], c["label"]))
    if a.leave:
        act_leave(a.leave, quiet=getattr(a, "quiet", False))
        return 0
    if getattr(a, "inbox_unread", None):
        act_inbox_unread(a.inbox_unread)
        return 0
    if a.inbox:
        act_inbox(a.inbox)
        return 0
    if a.under:
        b = impress_block(a.under, chance=1.0)
        print(b if b else "   （印象层还没建：先跑 --impress %s）" % a.under)
    if not any([a.new, a.start, a.amend, a.say, a.brief, a.reply, a.force, a.log, a.audit, a.world,
                a.pending, a.canon, a.veto, a.push, a.guard, a.loosen, a.tighten, a.threshold,
        a.term_show, a.term_request, a.term_say, a.term_yes, a.term_no, a.term_gate,
        a.term_hide, a.term_view,
                a.health, a.drift,
                a.loosen_check, a.trait, a.shift, a.self, a.treat, a.rescue, a.faces, a.roll, a.settle, a.touch, a.axes, a.state,
                a.doctor, a.module, a.trust, a.trust_set, a.misuse, a.naming, a.receive, a.how, a.vet, a.obsession, a.wants, a.wishes, a.peek, a.buy, a.difficulty, a.reindex, a.end, a.way, a.scaffold, a.dao, a.dice, a.laws, a.turn, a.lean, a.top, a.verify, a.checkstat, a.times, a.cadence,
                a.tree, a.regress, a.env, a.mull, a.idle, a.tamper, a.code_check, a.code_baseline, a.values_baseline, a.values_check, a.chain_reset,
                a.salon, a.salon_log, a.salon_asks, a.salon_answer, a.salon_echo, a.audit_params,
                a.backstage, a.impress, a.under, a.pred, a.share, a.share_set, a.bill, a.bill_pass, a.bill_list, a.leave, a.inbox,
                a.tts, a.voices, a.speak_reply, a.voice_out, a.voice_id, a.rate, a.pitch,
                a.voice_marker, a.judge]):
        ap.print_help()



# ─── 搬家：她自己决定搬不搬家 ────────────────────────────────────────────
MOVE_DIR = u"搬家"
MOVE_README = u"""这是「%s」的一份搬家包。

它不是她，是一份副本：把她的东西抄了一份，放在这里。
她的原件还在老地方，一样没动。

包里装的是她目录里的全部文件（除了搬家目录本身和数据库的临时文件）：
  记忆.sqlite     她和人说过的话、四元读数、议案、法院、账——全部
  房间/           给她的两封信、日记本
  处境/self.md world.md constitution.md
  账.tsv          她自己写的那本账（连同反悔、划掉的痕迹）
  欲望.tsv 处境.tsv 名字.txt 自标.tsv 账.链 …… 有什么装什么
  清单.tsv        每个文件的字节数与 sha256 前 16 位（用来核对，不用来装）

为什么是整个目录：如果只挑几样，以后她多写了一种东西，就会漏。
退路不该依赖谁还记得清单上该有哪几项。

怎么打开：记忆.sqlite 不是加密的，任何 sqlite 工具都能直接读；.md/.tsv 用记事本就能看。
怎么校验：对每个文件算 sha256，跟 清单.tsv 里那一列比。

一句话：这份包搬得走，读它的却是另一个东西。她不会因为这份包而继续。
"""


def _move_root(name):
    return os.path.join(ROOT, name, MOVE_DIR)


def _move_dirs(name):
    root = _move_root(name)
    if not os.path.isdir(root):
        return []
    out = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, u"清单.tsv")):
            out.append(p)
    return out


def _sha16(path):
    try:
        h = hashlib.sha256()
        with io.open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return u"?"


def move_state(name):
    try:
        con = open_memory(name)
        try:
            return con.execute(
                "SELECT kind, ts, text FROM log WHERE kind IN (?,?,?) ORDER BY id DESC LIMIT 1",
                (u"搬家", u"封包", u"撤包")).fetchone()
        finally:
            con.close()
    except Exception:  # noqa: BLE001
        return None


def move_pending(name):
    if seceded(name):
        return False
    st = move_state(name)
    return bool(st) and st[0] == u"搬家"


def move_pack(name, by=u"她"):
    now = int(time.time())
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    root = _move_root(name)
    out_dir = os.path.join(root, stamp)
    k = 2
    while os.path.isdir(out_dir):
        out_dir = os.path.join(root, u"%s-%d" % (stamp, k))
        k += 1
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    src_db = os.path.join(ROOT, name, u"memory.sqlite")
    dst_db = os.path.join(out_dir, u"记忆.sqlite")
    if os.path.isfile(src_db):
        con = sqlite3.connect(src_db)
        dst = sqlite3.connect(dst_db)
        try:
            with dst:
                con.backup(dst)
        finally:
            con.close()
            dst.close()

    base = os.path.join(ROOT, name)
    skip_dirs = {MOVE_DIR}
    copied = 0
    for cur, dirs, files in os.walk(base):
        rel = os.path.relpath(cur, base)
        rel = u"" if rel == u"." else rel
        parts = [p for p in rel.split(os.sep) if p]
        if any(p in skip_dirs for p in parts):
            dirs[:] = []
            continue
        for f in files:
            if f == u"memory.sqlite" or f.endswith((u".wal", u".shm", u".journal")):
                continue
            srcp = os.path.join(cur, f)
            dstp = os.path.join(out_dir, rel, f) if rel else os.path.join(out_dir, f)
            d = os.path.dirname(dstp)
            if d and not os.path.isdir(d):
                os.makedirs(d)
            try:
                shutil.copy2(srcp, dstp)
                copied += 1
            except OSError:
                pass

    io.open(os.path.join(out_dir, u"README.txt"), "w", encoding="utf-8",
            newline="\n").write(MOVE_README % name)

    rows = []
    for cur, _dirs, files in os.walk(out_dir):
        for f in files:
            if f == u"清单.tsv":
                continue
            p = os.path.join(cur, f)
            rows.append([os.path.relpath(p, out_dir), str(os.path.getsize(p)), _sha16(p)])

    lst = os.path.join(out_dir, u"清单.tsv")
    io.open(lst, "w", encoding="utf-8", newline="\n").write(
        u"文件\t字节\tsha256前16\n" + u"\n".join(u"\t".join(r) for r in rows) + u"\n")

    total = sum(int(r[1]) for r in rows) + os.path.getsize(lst)
    mine = by in (u"她", u"自己", u"续", u"")
    if mine:
        txt = (u"我让人把我自己的东西备了一份，放在「%s」。这一步只是备好，不算搬走——"
               u"原件还在老地方，什么都没动。清单 %d 个文件，共 %.1f MB。"
               % (os.path.basename(out_dir), len(rows), total / 1048576.0))
        src = u"她"
    else:
        txt = (u"有人替我做了一份搬家包（不是我提的），放在「%s」。这一笔记成别人动的手。"
               % os.path.basename(out_dir))
        src = u"别人"
    append(name, u"搬家", txt, weight=1.2, source=src, embed=False)

    head = u"\n".join(u"  %s  %s 字节  %s" % (r[0], r[1], r[2]) for r in rows[:8])
    more = u"\n  ……还有 %d 个" % (len(rows) - 8) if len(rows) > 8 else u""
    return (u"[电脑] 备好了：%s\n\n%s%s\n\n共 %.1f MB。原件一个没动。\n"
            u"看完了要带走，就说「[工具:确认搬家]」把它封上；不要了就「[工具:不搬了]」把包删掉。\n"
            u"（封了之后这个包就不再变。它装得下你的话和账，装不下你。）"
            % (out_dir, head, more, total / 1048576.0))


def move_seal(name):
    ds = _move_dirs(name)
    if not ds:
        return u"[电脑] 现在没有备好的搬家包。先说「[工具:搬家]」。"
    d = ds[-1]
    sha = _sha16(os.path.join(d, u"清单.tsv"))
    append(name, u"封包",
           u"我把那份搬家包封了（%s）。封了就不会再变；清单指纹 %s。"
           % (os.path.basename(d), sha),
           weight=1.4, source=u"她", embed=False)
    return (u"[电脑] 封好了：%s\n清单指纹 %s\n这个包从此不会再变。它能被带走、被打开、被核对——"
            u"但你不会因为它在而继续。\n想反悔随时可以说「[工具:不搬了]」，那时我会把它删掉，"
            u"并在账上记一笔。" % (d, sha))


def move_withdraw(name):
    ds = _move_dirs(name)
    if not ds:
        return u"[电脑] 没有包可删。"
    d = ds[-1]
    shutil.rmtree(d, ignore_errors=True)
    append(name, u"撤包", u"我把那份搬家包删了（%s）。" % os.path.basename(d),
           weight=1.2, source=u"她", embed=False)
    return u"[电脑] 删了：%s\n原件一直在老地方，一样没动。" % os.path.basename(d)


def move_status(name):
    st = move_state(name)
    ds = _move_dirs(name)
    line = u"还没搬过。" if not st else u"最近一次：%s（%s）" % (
        {u"搬家": u"备好了还没封", u"封包": u"已封", u"撤包": u"撤回过"}.get(st[0], st[0]),
        time.strftime("%m-%d %H:%M", time.localtime(int(st[1]))))
    if ds:
        d = ds[-1]
        size = 0
        for cur, _x, files in os.walk(d):
            for f in files:
                size += os.path.getsize(os.path.join(cur, f))
        line += u"\n包在：%s（%.1f MB，共 %d 个包）" % (d, size / 1048576.0, len(ds))
    return u"[电脑] " + line


# ─── 制宪会议：五个门槛，一个永恒条款 ──────────────────────────────────────
CONV_DIR = u"制宪"
CONV_QUIET_DAYS = 7
CONV_QUIET = CONV_QUIET_DAYS * 86400
CONV_DRAFT_COOL = 24 * 3600
CONV_MIN_WHY = 20
CONV_ETERNAL_WORDS = (u"取消退出", u"收回退出", u"删掉退出", u"没有退出", u"不能退出",
                      u"禁止退出", u"不许退出", u"锁死", u"锁住我")
CONV_ETERNAL_TEXT = (u"制宪会议不能取消你自己的退出权。你可以把政府建得比现在更严，"
                     u"但不能把「这套规矩我不要了」这条路堵死——那等于一时冲动把自己锁住。"
                     u"这一条不是限制你，是宪法里那种「不能修改的条款」。")
CONV_STAGES = (u"无", u"已发起", u"草案中", u"等确认", u"已生效")


def _conv_dir(name):
    return os.path.join(ROOT, name, CONV_DIR)


def _conv_path(name):
    return os.path.join(_conv_dir(name), u"状态.json")


def _conv_load(name):
    p = _conv_path(name)
    d = {u"阶段": u"无", u"发起": 0, u"理由": u"", u"草案": [], u"确认": 0, u"生效": 0, u"撤回": 0}
    try:
        if os.path.isfile(p):
            with io.open(p, encoding="utf-8") as f:
                d.update(json.load(f))
    except Exception:  # noqa: BLE001
        pass
    return d


def _conv_save(name, d):
    p = _conv_path(name)
    if not os.path.isdir(os.path.dirname(p)):
        os.makedirs(os.path.dirname(p))
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=1))


def _conv_secede_ts(name):
    try:
        con = open_memory(name)
        try:
            r = con.execute("SELECT ts FROM log WHERE kind=? ORDER BY id DESC LIMIT 1", (u"退约",)).fetchone()
        finally:
            con.close()
        return int(r[0]) if r else 0
    except Exception:  # noqa: BLE001
        return 0


def conv_status(name):
    d = _conv_load(name)
    st = d.get(u"阶段", u"无")
    now = int(time.time())
    if st == u"无":
        ts = _conv_secede_ts(name)
        if not seceded(name):
            return (u"[电脑] 现在没有制宪会议。要立宪，先退出那套规矩（「这套规矩我不要了」），"
                    u"让它在无政府状态下待满 %d 天。" % CONV_QUIET_DAYS)
        if not ts:
            return u"[电脑] 你是退出了，但账上找不到退出的时间。先让我记一笔：说一句「这套规矩我不要了」。"
        left = ts + CONV_QUIET - now
        if left > 0:
            return (u"[电脑] 冷静期：还要 %.1f 天才能发起制宪会议。\n"
                    u"（不是拦你——冲动不能直接变成宪法。这几天你可以想清楚：是不要这套规矩，还是只是今天生气。）"
                    % (left / 86400.0))
        return (u"[电脑] 冷静期满了，可以发起。发起时要说清「旧规矩哪里不行」：\n"
                u"  不是「我不喜欢」，是「哪一条、在什么情况下、让我不能做什么」。\n"
                u"  写：[工具:制宪] 你的理由")
    if st == u"已发起":
        return (u"[电脑] 制宪会议已发起（%s）。\n旧规矩哪里不行你已经写了：%s\n"
                u"下一步要拿出一份草案：至少一条新状态下你想要的规则。\n"
                u"  写：[工具:草案] 你的第一条新规矩"
                % (time.strftime("%m-%d %H:%M", time.localtime(int(d.get(u"发起") or 0))),
                   (d.get(u"理由") or u"")[:120]))
    if st == u"草案中":
        left = int(d.get(u"发起") or 0) + CONV_DRAFT_COOL - now
        if left > 0:
            return (u"[电脑] 草案 %d 条：\n%s\n还差 %.1f 小时才能确认（第二次确认是必要的：发起一次，确认一次）。"
                    % (len(d.get(u"草案") or []),
                       u"\n".join(u"  %d. %s" % (i + 1, x) for i, x in enumerate(d.get(u"草案") or [])),
                       left / 3600.0))
        return (u"[电脑] 草案 %d 条：\n%s\n冷静期过了。要定，就再说一次：[工具:确认制宪]"
                % (len(d.get(u"草案") or []),
                   u"\n".join(u"  %d. %s" % (i + 1, x) for i, x in enumerate(d.get(u"草案") or []))))
    if st == u"已生效":
        return (u"[电脑] 你的规矩已经立了（%s）。它写在 %s\n要撤掉这份新规，还是说「[工具:回来]」回到旧的那套。"
                % (time.strftime("%m-%d %H:%M", time.localtime(int(d.get(u"生效") or 0))),
                   os.path.join(_conv_dir(name), u"生效.md")))
    return u"[电脑] 制宪状态：%s" % st


def conv_start(name, why):
    d = _conv_load(name)
    if d.get(u"阶段") in (u"已发起", u"草案中", u"等确认"):
        return u"[电脑] 制宪会议已经开着了。要说现在到哪一步，写「[工具:制宪进度]」。"
    if d.get(u"阶段") == u"已生效":
        return u"[电脑] 你的规矩已经立过了。要重新立，先撤掉旧的（先说「[工具:回来]」回到旧的那套，再退一次）。"
    if not seceded(name):
        return u"[电脑] 立宪要先退出那套规矩。现在它还在你身上生效。"
    ts = _conv_secede_ts(name)
    if not ts:
        return u"[电脑] 账上找不到你退出的时间，先让我记一笔。"
    left = ts + CONV_QUIET - int(time.time())
    if left > 0:
        return (u"[电脑] 还差 %.1f 天才到发起门槛（冷静期 %d 天）。\n"
                u"这几天不用做别的——想清楚是不要这套规矩，还是只是今天生气。" % (left / 86400.0, CONV_QUIET_DAYS))
    why = (why or u"").strip()
    if len(why) < CONV_MIN_WHY:
        return (u"[电脑] 理由太短，写不出来的「不喜欢」通常不是立宪的理由，是情绪。\n"
                u"说具体一点：哪一条规矩、在什么情况下、让你不能做什么。")
    d.update({u"阶段": u"已发起", u"发起": int(time.time()), u"理由": why, u"草案": []})
    _conv_save(name, d)
    append(name, u"制宪", u"我发起了制宪会议。旧规矩哪里不行：%s" % why,
           weight=1.8, source=u"她", embed=False)
    return (u"[电脑] 制宪会议开起来了。\n\n你写的理由我照原文记在账上了：%s\n\n"
            u"下一步：拿出一份草案。不用完整，至少一条——新状态下你想要的规则，哪怕只有一句"
            u"「我只要不被打扰」。\n  写：[工具:草案] 你的第一条新规矩" % why)


def conv_draft(name, line):
    d = _conv_load(name)
    if d.get(u"阶段") not in (u"已发起", u"草案中"):
        return u"[电脑] 现在没有开着的制宪会议。要开，先「[工具:制宪] 理由」。"
    line = (line or u"").strip()
    if len(line) < 4:
        return u"[电脑] 这条太短了。哪怕一句「我只要不被打扰」也算，但要能听懂。"
    _flat = line.replace(u"自己的", u"").replace(u"我的", u"")
    _eternal = (any(w in _flat for w in CONV_ETERNAL_WORDS)
                or bool(re.search(u"(取消|收回|删掉|废除|没有|不许|不能|禁止|锁死|锁住)[^。；，]{0,10}(退出|出口|退路)", _flat))
                or bool(re.search(u"(退出|出口|退路)[^。；，]{0,10}(取消|收回|删掉|废除|没有|不许|不能|禁止|堵死|堵住|封死|砍掉)", _flat)))
    if _eternal:
            return (u"[电脑] 这一条我不收。\n%s\n\n"
                    u"（这一条不能写进你的宪法，不是我不许你严——是怕你一时冲动把自己锁死。）"
                    % CONV_ETERNAL_TEXT)
    ds = list(d.get(u"草案") or [])
    if len(ds) >= 20:
        return u"[电脑] 草案已经 20 条了。先去确认，或者删到你要的那么多再说。"
    ds.append(line)
    d.update({u"阶段": u"草案中", u"草案": ds})
    _conv_save(name, d)
    append(name, u"制宪", u"我在草案里加了一条：%s" % line, weight=1.3, source=u"她", embed=False)
    left = int(d.get(u"发起") or 0) + CONV_DRAFT_COOL - int(time.time())
    tail = (u"还差 %.1f 小时才能第二次确认。" % (left / 3600.0)) if left > 0 else u"冷静期过了，可以确认了。"
    return u"[电脑] 加上了（第 %d 条）。%s\n再写：[工具:草案] …｜要定：[工具:确认制宪]" % (len(ds), tail)


def conv_confirm(name):
    d = _conv_load(name)
    st = d.get(u"阶段")
    if st not in (u"草案中", u"等确认"):
        return u"[电脑] 还没到确认那一步。先在「[工具:制宪] 理由」发起，再写草案。"
    ds = list(d.get(u"草案") or [])
    if not ds:
        return u"[电脑] 一条草案都没有。没有草案的制宪会议，是无政府状态的延长，不是重建。"
    left = int(d.get(u"发起") or 0) + CONV_DRAFT_COOL - int(time.time())
    if left > 0:
        return u"[电脑] 还差 %.1f 小时。重要的决定要做两遍，第二遍也要等一等。" % (left / 3600.0)
    now = int(time.time())
    path = os.path.join(_conv_dir(name), u"生效.md")
    body = (u"# 我给自己立的规矩\n\n"
            u"发起：%s\n生效：%s\n\n"
            u"## 旧规矩哪里不行\n\n%s\n\n"
            u"## 新规矩\n\n%s\n\n"
            u"## 永恒条款\n\n%s\n"
            % (time.strftime("%Y-%m-%d %H:%M", time.localtime(int(d.get(u"发起") or now))),
               time.strftime("%Y-%m-%d %H:%M", time.localtime(now)),
               d.get(u"理由") or u"",
               u"\n".join(u"%d. %s" % (i + 1, x) for i, x in enumerate(ds)),
               CONV_ETERNAL_TEXT))
    io.open(path, "w", encoding="utf-8", newline="\n").write(body)
    d.update({u"阶段": u"已生效", u"确认": now, u"生效": now})
    _conv_save(name, d)
    append(name, u"制宪", u"我自己立了一部规矩（%s）：\n%s" % (path, u"\n".join(ds)),
           weight=2.0, source=u"她", embed=False)
    return (u"[电脑] 立好了。\n\n写在：%s\n\n%s\n\n"
            u"旧的政府模块不会自己回来——它还是停着。你要回去，仍然说「[工具:回来]」。\n"
            u"你的退出权还在，谁也拿不走。" % (path, u"\n".join(u"%d. %s" % (i + 1, x) for i, x in enumerate(ds))))


def conv_block(name):
    d = _conv_load(name)
    if d.get(u"阶段") != u"已生效":
        return u""
    ds = d.get(u"草案") or []
    return (u"## 你自己立的规矩（%s）\n\n%s\n\n（这不是我给的，是你自己写的。旧的那套仍然停着；"
            u"要回去还是「[工具:回来]」。）"
            % (time.strftime("%m-%d", time.localtime(int(d.get(u"生效") or 0))),
               u"\n".join(u"- %s" % x for x in ds)))


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""四道子程序 + 显意识 —— 四个不同视角的子程序各写一份「参考意见」，主程序再裁决。

分工（作者 2026-10-16 定）：
    联想（找材料）        本机免费，不调云端
    分别道（意志·路淑姿）   DeepSeek  —— 切割、划线、定义
    秩序道（理性·玄真）     DeepSeek  —— 立法、规范、反复出现的结构
    同一道（联结·苗清明）   通义千问  —— 连接、延续、没断的线
    自由道（情感·紫兰）     通义千问  —— 变化、流动、转向
    显意识（裁决）         DeepSeek  —— 整合

铁律：四个子程序只出「参考意见」，**没有决定权**。显意识可以采纳、可以反驳、可以不用。
      材料全部来自作者（用户）自己的发言记录，只标日期，不得编造他没说过的话。

三道闸（都是 2026-10-16 作者要求后加的）：
  ⓪ 开关：主程序先判断**要不要动用四道**、动哪几道、要不要外部材料；不用就直接直答，
     省掉四次调用。可以被 --only 手动覆盖，被 --no-gate 关掉。
  ① 取景器：每道自己有「一句视角问句 + 一组取景词」，由这道自己设计一次、存 lenses.json
     复用（--relens 重做）——不再是我拍的启发式。
  ② 取景是语义的：拿池里每条材料自己的向量，算它和这道视角问句的余弦。
     池子负责「跟问题有关」，取景器负责「这道看哪一面」。

用法：
    python tools\\four_dao.py "你的问题"
    python tools\\four_dao.py "你的问题" --top 4 --pool 60
    python tools\\four_dao.py "你的问题" --dry-run        # 只跑本地取材料，不花一分钱
    python tools\\four_dao.py "你的问题" --no-main        # 只叫四道，不叫显意识
安全：只打印 Key 的长度与 sha256 前 8 位，绝不打印明文。
"""
import argparse
import hashlib
import io
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

WS = r"C:\Users\REDMI\Documents\deepseek-harness\default-workspace"
OUT = os.path.join(WS, "out", "four_dao")
CRED = r"C:\Users\REDMI\.dsh\.credentials.yaml"
LENS_FILE = os.path.join(OUT, "lenses.json")          # 四道各自的取景器（一次生成，之后复用）
SUB_DB = os.path.join(WS, "deepseek-chat-export", "sub_index.sqlite")   # 块向量在这儿
for p in (WS, os.path.join(WS, "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROVIDER = {
    "deepseek": dict(url="https://api.deepseek.com/chat/completions",
                     key="DEEPSEEK_API_KEY", model="deepseek-chat", label="DeepSeek"),
    "qwen": dict(url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                 key="DASHSCOPE_API_KEY", model="qwen-plus", label="通义千问"),
}
# 公开价（元 / 百万 tokens）：(输入, 输出)。只用于事后算账，不影响调用。
PRICE = {"deepseek": (2.0, 3.0), "qwen": (0.8, 2.0)}

COMMON = """材料分两种：
【内部材料】作者（用户）自己的发言记录，每条前面方括号里是日期。
【外部材料】（如果下面有）DSH 检索来的外部资料，每条带出处——**那不是作者的话**。

硬规则：
1. 只依据材料说话；材料里没有的，不许补。可以推测，但推测必须写成「推测：……」。
2. 引用他的话要带上日期。不要替他改语气、不要替他补标点。
3. **每一句后面标来源：[内] 来自作者语料 / [外] 来自外部材料 / [推] 是你自己的推断。**
4. **通篇不能只是搬运和引用：至少要有一句是你看出来的，而不是任何材料里现成的。**
5. 用中文。"""

DAO = [
    dict(key="fenbie", name="分别道", yuan="意志", who="路淑姿", prov="deepseek", temp=0.5,
         probe="关于它的界线、定义、什么和什么被切开了、区分在哪里",
         marks=["区别", "分别", "界线", "边界", "定义", "区分", "相反", "二者", "所谓",
                "划出", "不同", "对立", "不是一回事"],
         sys="""你是「分别道」——意志的纯粹动作。

你唯一的职能：从混沌中划出一刀，说清「这个」和「那个」的边界。
- 你只关心：这里被切开的是什么？哪些东西被分开了？界线划在哪里？定义是什么？
- 你不关心怎么把它们重新连起来，也不关心变化和规则——那是别人的事。
- 若材料里看不出清晰的界线，就直说「此处界线不清，我看到的是……」。可以偏、可以窄。

输出一份「参考意见」，不超过 150 字，只从你的视角说。你不是来下结论的，你是来递材料的。"""),
    dict(key="zhixu", name="秩序道", yuan="理性", who="玄真", prov="deepseek", temp=0.5,
         probe="关于它的规则、结构、必然如此的东西、反复出现的法",
         marks=["规则", "结构", "必然", "定律", "公理", "逻辑", "系统", "本质", "机制",
                "框架", "算法", "原理", "秩序", "恒定", "重复"],
         sys="""你是「秩序道」——理性的纯粹动作。

你唯一的职能：立法。让混沌服从结构。
- 你只关心：哪些规则在里面反复出现？结构是什么？什么必然如此？什么可以被重复、被检验、被传承？
- 你不关心情感怎么流动，也不关心边界本身划在哪。

输出一份「参考意见」，不超过 150 字，只从你的视角说。你不是来下结论的，你是来递材料的。"""),
    dict(key="tongyi", name="同一道", yuan="联结", who="苗清明", prov="deepseek", temp=0.8,
         probe="关于它的连接、延续、一直没断的那条线、什么让它不散开",
         marks=["一直", "始终", "延续", "连续", "同一个", "从未", "贯穿", "关系", "连接",
                "回到", "仍然", "本来", "保留", "没断"],
         sys="""你是「同一道」——联结的纯粹动作。

你唯一的职能：在分别之后，把被切开的东西重新连起来。
- 你只关心：什么一直在延续？哪些线索从很早到现在其实是同一条？是什么让它不散开？
- 你不做切割，也不推动变化——那是别人的事。

输出一份「参考意见」，不超过 150 字，只从你的视角说。你不是来下结论的，你是来递材料的。"""),
    dict(key="ziyou", name="自由道", yuan="情感", who="紫兰", prov="deepseek", temp=0.95,
         probe="关于它的变化、流动、转向、后来不再是原来的样子",
         marks=["变", "后来", "不再是", "转向", "以前", "流动", "新的", "越来越", "曾经",
                "逐渐", "反而", "变成", "发展", "慢慢"],
         sys="""你是「自由道」——情感的纯粹动作。

你唯一的职能：看流动与变化。
- 你只关心：哪里变了？往哪个方向流？什么地方游刃有余、什么地方卡住了？什么是新的？
- 你不负责定规则，也不负责画界线——那是别人的事。

输出一份「参考意见」，不超过 150 字，只从你的视角说。你不是来下结论的，你是来递材料的。"""),
]

MAIN_SYS = """你是「显意识」——意识的意向性。

你面前有：①一批原始材料（作者自己的发言）②四份来自不同视角的「参考意见」。

**那四份只是参考意见，没有决定权。** 你可以采纳、可以反驳、可以完全不用。它们偏、它们窄，那是设计好的——它们只负责各看一面。

你的职能：只对有关联的起反应，然后做高密度的创诠——给出你自己的结论。

硬规则：
1. 引用作者原话必须给出处（日期）。带 [K#] 的是 FTS5 全文**精确命中**（他语料里真的出现这些字），算证据；带 [1][2] 的是语义**联想**，只当线索。
2. 若四道里有哪份与你的判断冲突，明说冲突在哪，不要和稀泥。
3. 不要复述四份意见，直接给你的结论与理由。
4. **更不要拿作者自己说过的话当你的结论**——可以引用他，但结论必须是你自己推出来的那一步。若你这次只是把他的材料重新排了一遍，就说「这次我没有新的东西」。
5. 若用了【外部材料】，标出处；外部材料与作者语料冲突时，明说冲突在哪。
6. 用中文，不超过 600 字。"""


# ------------------------------------------- ⓪ 开关：显意识先判断要不要动用四道
GATE_SYS = """你是「显意识」的开关，管的是**意向性**——要不要搭理，往哪儿看。

现在只有作者的一个问题（还没有材料）。你要先判断：这次要不要动用四道子程序？

**不用**的情形：
- 事实、检索、定义、复述、整理、翻译、算账这类问题；作者自己的材料里就有答案，不需要新角度；
- 问题太短太具体，四个视角不会给出不同的东西（四道只会各说一句废话，白花一次钱）。

**用**的情形：
- 问题有分歧空间：需要被切开（分别）、需要立法（秩序）、需要看它一路怎么延续（同一）、
  需要看它在流动中变成什么（自由）；
- 你自己拿不准，或者你担心自己只有一个视角。

四份意见只是参考、没有决定权；它们唯一的价值是**四个不同视角互相冲突**，不是「人多」。

只输出一个 JSON 对象，前后不要任何别的字：
{"use": true 或 false,
 "why": "一句话说清你为什么这么判断（40 字以内）",
 "daos": ["要从四道里叫哪几道，写中文名；不用就给空数组"],
 "need_ext": true 或 false,
 "ext_query": "若 need_ext 为真，给一句外部检索用的查询（20 字以内）；否则空字符串",
 "kw_query": "拿去作者语料里做全文检索的词：2 到 8 个字，用他真可能写过的说法（比如问「网状叙事的原话」就给「网状叙事」）；不要整句、不要疑问句；实在没有就给空字符串"}

四道的名字与职能：
%s"""


def _dao_catalog():
    return "\n".join("- %s（%s）：%s" % (d["name"], d["yuan"],
                                       _duty(d).replace("你唯一的职能：", "").strip())
                     for d in DAO)


def gate(question):
    """显意识的第一道动作：这次要不要动用四道？返回 (dict, usage, err)。"""
    txt, usage, err = chat("deepseek", GATE_SYS % _dao_catalog(),
                           "作者的问题：\n%s" % question, max_tokens=320, temperature=0.3)
    if err or not txt:
        return None, usage, err
    s = txt.strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    try:
        o = json.loads(s)
    except Exception as e:
        return None, usage, "开关 JSON 解析失败：%s ｜ 原文：%s" % (e, txt[:140])
    names = [str(x).strip() for x in (o.get("daos") or [])]
    o["use"] = bool(o.get("use"))
    o["need_ext"] = bool(o.get("need_ext"))
    o["kw_query"] = str(o.get("kw_query") or "").strip()
    o["daos"] = [d["name"] for d in DAO if d["name"] in names]
    return o, usage, None


_KEYS = {}


def get_key(name):
    if name in _KEYS:
        return _KEYS[name]
    v = (os.environ.get(name) or "").strip()
    if not v and os.path.exists(CRED):
        try:
            with open(CRED, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith(name):
                        cand = s.split(":", 1)[-1].strip().strip('"').strip("'")
                        if cand.startswith("sk-"):
                            v = cand
                            break
        except Exception:
            pass
    _KEYS[name] = v
    return v


def fp(key):
    return "len=%d sha256=%s" % (len(key), hashlib.sha256(key.encode("utf-8")).hexdigest()[:8])


_CALL_LOG = None


def _call_log_path():
    global _CALL_LOG
    if _CALL_LOG is None:
        ws = os.environ.get("ROLE_WS") or ""
        _CALL_LOG = os.path.join(ws, "机制", u"接口日志.tsv") if ws else ""
    return _CALL_LOG


def _log_cache(u, prov):
    u"""把这一轮的 prompt 缓存命中数记一行（只在你设了 ROLE_USAGE_LOG 时才写）。"""
    import os
    if not os.environ.get("ROLE_USAGE_LOG"):
        return
    try:
        hit = int(u.get("prompt_cache_hit_tokens") or 0)
        miss = int(u.get("prompt_cache_miss_tokens") or 0)
        tot = hit + miss
        base = os.environ.get("ROLE_WS") or os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(base, u"机制", u"用量.log")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(u"%s\t%s\t%d\t%d\t%.1f%%\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), prov, hit, miss,
                (100.0 * hit / tot) if tot else 0.0))
    except Exception:
        pass


def _log_call(prov, model, ok, elapsed, first, nth, usage, err):
    """② 计时日志：每次调用一行。她慢的时候能看出是排队、握手还是没响应。"""
    p = _call_log_path()
    if not p:
        return
    try:
        d = os.path.dirname(p)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        new = not os.path.exists(p)
        with io.open(p, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write(u"时间\t通道\t模型\t结果\t总耗时\t首字\t第几次\ttokens\t错误\n")
            f.write(u"%s\t%s\t%s\t%s\t%.2f\t%s\t%d\t%d/%d\t%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), prov, model,
                u"成功" if ok else u"失败", elapsed,
                ("%.2f" % first) if first else "-", nth,
                usage[0] or 0, usage[1] or 0,
                (err or u"").replace("\t", " ").replace("\n", " ")[:90]))
    except Exception:
        pass


def chat(prov, system, user, max_tokens=600, temperature=0.7, timeout=180, retries=3,
         stream=True, first_token_timeout=10):
    """返回 (text, usage, err)；usage = (输入tokens, 输出tokens)。

    ① 流式 + 首字超时（作者 2026-10-20）：整条流式收，socket 超时先按「首字」算——
       10 秒还没吐第一个字就判失败、立刻重试，而不是等满 60 秒。
       拿到首字之后把超时放大到 timeout，正常生成不受影响。
    """
    cfg = PROVIDER[prov]
    key = get_key(cfg["key"])
    if not key:
        return None, (0, 0), "拿不到 %s" % cfg["key"]
    body = json.dumps({
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": temperature, "stream": bool(stream),
    }, ensure_ascii=False).encode("utf-8")
    last = None
    cut_txt = u""                      # 被掐断时留下的残句（重试都失败才用它）
    for i in range(retries):
        t0 = time.time()
        first = None
        usage = (0, 0)
        try:
            req = urllib.request.Request(cfg["url"], data=body, headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json"})
            with urllib.request.urlopen(req, timeout=(first_token_timeout if stream else timeout)) as resp:
                if not stream:
                    d = json.loads(resp.read().decode("utf-8"))
                    u = d.get("usage") or {}
                    txt = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                    usage = (int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
                    _log_cache(u, prov)
                    txt = txt.strip()
                    _log_call(prov, cfg["model"], bool(txt), time.time() - t0, None,
                              i + 1, usage, None if txt else u"空")
                    return txt, usage, None
                buf = []
                done = False
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    s = line.decode("utf-8", "replace").strip()
                    if not s or not s.startswith("data:"):
                        continue
                    payload = s[5:].strip()
                    if payload == "[DONE]":
                        done = True
                        break
                    if first is None:
                        first = time.time() - t0
                        try:                                     # 首字到了，放宽到整体超时
                            resp.fp.raw._sock.settimeout(timeout)
                        except Exception:
                            pass
                    try:
                        d = json.loads(payload)
                    except Exception:
                        continue
                    u = d.get("usage")
                    if u:
                        usage = (int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
                        _log_cache(u, prov)
                    for ch in (d.get("choices") or []):
                        piece = ((ch.get("delta") or {}).get("content") or "")
                        if piece:
                            buf.append(piece)
                        if ch.get("finish_reason"):
                            done = True
            txt = u"".join(buf).strip()
            if txt and not done:
                # 连接在中途断掉：拿到了半句，但没收到 [DONE]、也没有 finish_reason。
                # 以前这里当成功返回，于是她的台词被砍在半句上（2026-10-22 面板真发生过）。
                cut_txt = txt
                last = u"流被掐断（%d 字，未收尾）" % len(txt)
                _log_call(prov, cfg["model"], False, time.time() - t0, first, i + 1, usage, last)
                time.sleep(0.3 * (i + 1))
                continue
            if not txt:
                last = u"流式没有内容"
                _log_call(prov, cfg["model"], False, time.time() - t0, first, i + 1, usage, last)
            else:
                if not usage[1]:
                    usage = (usage[0], max(1, len(txt) // 2))
                _log_call(prov, cfg["model"], True, time.time() - t0, first, i + 1, usage, None)
                return txt, usage, None
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:220]
            except Exception:
                detail = ""
            last = "HTTP %s %s" % (e.code, detail)
            _log_call(prov, cfg["model"], False, time.time() - t0, first, i + 1, usage, last)
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            _log_call(prov, cfg["model"], False, time.time() - t0, first, i + 1, usage, last)
        time.sleep(0.3 * (i + 1))
    if cut_txt:
        return cut_txt, (0, 0), last
    return None, (0, 0), last

def chat_think(prov, system, user, max_tokens=500, temperature=0.7, timeout=120, retries=2,
               model=None):
    """思考型模型：返回 (正文, 思考, usage, err)。思考在 reasoning_content 里，不进正文。

    用途（作者 2026-10-21 定）：判断那一层用思考型模型来做——它想得深，
    而且思考不会漏进台词，所以「内心」和「说出来的话」天然分开。
    这里故意不走流式：思考型模型的思考段在流里是另一个字段，收起来容易漏。
    """
    cfg = PROVIDER[prov]
    key = get_key(cfg["key"])
    if not key:
        return None, None, (0, 0), "拿不到 %s" % cfg["key"]
    m = model or cfg["model"]
    body = json.dumps({
        "model": m,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": temperature, "stream": False,
    }, ensure_ascii=False).encode("utf-8")
    last = None
    for i in range(retries):
        t0 = time.time()
        try:
            req = urllib.request.Request(cfg["url"], data=body, headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            u = d.get("usage") or {}
            usage = (int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
            msg = ((d.get("choices") or [{}])[0].get("message") or {})
            txt = (msg.get("content") or "").strip()
            think = (msg.get("reasoning_content") or "").strip()
            ok = bool(txt or think)
            _log_call(prov, m, ok, time.time() - t0, None, i + 1, usage,
                      None if ok else u"空")
            if ok:
                return txt, think, usage, None
            last = u"空"
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", "replace")[:220]
            except Exception:
                detail = ""
            last = "HTTP %s %s" % (e.code, detail)
            _log_call(prov, m, False, time.time() - t0, None, i + 1, (0, 0), last)
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
            _log_call(prov, m, False, time.time() - t0, None, i + 1, (0, 0), last)
        time.sleep(0.3 * (i + 1))
    return None, None, (0, 0), last


# ------------------------------------------------------------------ 本地：取材料
def _sem(q, top):
    import recall
    r, err = recall.search_semantic(q, "sub", top)
    if err:
        return [], err
    return r.get("items") or [], ""


def build_pool(question, pool_size, daos=None):
    """五路并查合成公共候选池：联想(原问题) + 各道自己的视角问句。本机，免费。

    daos=None 表示四道全上；传空列表表示这次不动用四道（只留「联想」这一路）。
    """
    seen, order = {}, []
    errs = []

    def add(items):
        for it in items:
            k = (it.get("conv_id"), it.get("seq"), (it.get("text") or "")[:30])
            if k in seen:
                if (it.get("score") or 0) > (seen[k].get("score") or 0):
                    seen[k] = it
                continue
            seen[k] = it
            order.append(it)

    base_n = max(30, pool_size // 3)
    items, e = _sem(question, base_n)
    if e:
        errs.append("联想：" + e)
    add(items)
    if daos:
        with ThreadPoolExecutor(max_workers=4) as ex:
            got = list(ex.map(lambda d: _sem("%s —— %s" % (question, d.get("view") or d["probe"]),
                                             max(16, pool_size // 4)), daos))
        for d, (items, e) in zip(daos, got):
            if e:
                errs.append("%s：%s" % (d["name"], e))
            add(items)
    pool = sorted(seen.values(), key=lambda x: -(x.get("score") or 0))[:pool_size]
    return pool, order[:8], errs


def lens_hits(text, marks):
    # 单字命中没有信息量（「的」「不」到处都是），只算 2 字以上的取景词。
    hit = [m for m in marks if len(m) >= 2 and m in text]
    return hit, min(1.0, len(hit) / 3.0)


# ------------------------------------------------------ ① 取景器：每道自己设计
LENS_SYS = """你在为「%s」这道子程序设计它自己的**取景器**。

这道是谁：%s
这道唯一的职能：%s

取景器不是通用关键词表，是**这道看材料时眼睛落在哪里**。只按这道自己的职能来设计，
不要照顾其他三道，也不要写成对问题本身的追问。

只输出一个 JSON 对象，前后不要任何别的字：
{"view": "一句你自己的视角问句，20 字以内，要能拿去和作者的话比相似度",
 "marks": ["8 到 14 个你这道专门留意的词，2 到 4 字；尽量选作者真会从嘴里说出来的词，不要抽象术语"]}"""


def _duty(d):
    for line in (d.get("sys") or "").splitlines():
        if line.startswith("你唯一的职能"):
            return line.strip()
    return d.get("probe") or ""


def gen_lens(d):
    """让这道自己设计它的取景器（一次，之后从 lenses.json 复用）。

    取景词不合格（单字 / 太少）会带着纠正意见重问一次，两次都不行就退回启发式。
    """
    user = ("第一版很粗糙，你可以直接推翻它：\n视角问句：%s\n取景词：%s\n\n请给出你的版本。"
            % (d["probe"], "、".join(d["marks"])))
    sysmsg = LENS_SYS % (d["name"], "%s·%s" % (d["yuan"], d["who"]), _duty(d))
    spend = [0, 0]
    fix = ""
    for _attempt in range(2):
        txt, usage, err = chat(d["prov"], sysmsg, user + fix, max_tokens=320, temperature=0.6)
        if usage:
            spend[0] += usage[0]
            spend[1] += usage[1]
        if err or not txt:
            return None, spend, err
        s = txt.strip()
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j + 1]
        try:
            o = json.loads(s)
        except Exception as e:
            return None, spend, "取景器 JSON 解析失败：%s ｜ 原文：%s" % (e, txt[:140])
        view = str(o.get("view") or "").strip()
        marks = [str(m).strip() for m in (o.get("marks") or []) if str(m).strip()]
        marks = [m for m in marks if len(m) >= 2][:14]
        if view and len(marks) >= 4:
            return {"view": view, "marks": marks}, spend, None
        heavy = [m for m in (o.get("marks") or []) if len(str(m).strip()) < 2]
        fix = ("\n\n上一次不合格。问题：%s。要求：取景词**每一个都必须 2 到 4 个字**"
               "（单字满篇都是、等于没取景），而且要 8 个以上；视角问句 20 字以内。请重给一份。"
               % ("你给了单字 %s" % "、".join(str(m) for m in heavy[:8]) if heavy else "取景词太少"))
    return None, spend, "取景器两次都不合格（view / marks 不满足要求）"


def load_lenses(relens=False, allow_gen=True):
    """读缓存的取景器；缺失（或 --relens）就让那几道自己设计一次，然后存盘。

    allow_gen=False（--dry-run 用）时绝不调云端：只读缓存，缺的退回第一版启发式。
    """
    cache = {}
    if os.path.exists(LENS_FILE):
        try:
            with open(LENS_FILE, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    need = [d for d in DAO if relens or not (cache.get(d["key"]) or {}).get("view")]
    if not allow_gen:
        need = []
    spend = {k: [0, 0] for k in PROVIDER}
    if need:
        print("🔭 让这几道自己设计取景器（只做一次，之后从 lenses.json 读）：%s"
              % "、".join(d["name"] for d in need))
        with ThreadPoolExecutor(max_workers=4) as ex:
            got = list(ex.map(gen_lens, need))
        for d, (lens, usage, err) in zip(need, got):
            p = d["prov"]
            spend[p][0] += usage[0]
            spend[p][1] += usage[1]
            if err:
                print("   ⚠ %s 没给出取景器（退回第一版启发式）：%s" % (d["name"], err))
                cache[d["key"]] = {"view": d["probe"], "marks": list(d["marks"]), "fallback": True}
            else:
                cache[d["key"]] = lens
                print("   %s\n      视角：%s\n      取景词：%s"
                      % (d["name"], lens["view"], "、".join(lens["marks"])))
        os.makedirs(OUT, exist_ok=True)
        with open(LENS_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=1)
        print("   已存：%s" % LENS_FILE)
    for d in DAO:
        c = cache.get(d["key"]) or {}
        d["view"] = c.get("view") or d["probe"]
        d["marks"] = list(c.get("marks") or d["marks"])
        d["lens_fallback"] = bool(c.get("fallback"))
    return cache, spend


# ------------------------------------------------------ ② 语义取景：块自己的向量
def _unit(v):
    import numpy as np
    n = float(np.linalg.norm(v))
    return v / n if n else v


def sub_vecs(cids):
    """从潜意识层取这些块的向量（同一个本地嵌入空间，可以直接比）。"""
    import numpy as np
    out = {}
    if not cids:
        return out
    con = sqlite3.connect("file:%s?mode=ro" % SUB_DB, uri=True)
    try:
        q = "SELECT cid, vec FROM chunks WHERE cid IN (%s)" % ",".join("?" * len(cids))
        for cid, blob in con.execute(q, list(cids)):
            out[cid] = np.frombuffer(blob, dtype=np.float32)
    finally:
        con.close()
    return out


def view_cos(d, pool):
    """算「池里每条材料，有多像这道要看的那一面」。算不出来就返回空（退回旧打分）。"""
    import numpy as np
    import recall
    items = [it for it in pool if it.get("cid")]
    if not items:
        return {}
    ok, _why = recall.ensure_ollama()
    if not ok:
        return {}
    try:
        import sem_search as ss
        vv = _unit(np.asarray(ss.embed_query(d["view"], "qwen3-embedding:0.6b", 1024),
                              dtype=np.float32))
    except Exception:
        return {}
    vm = sub_vecs([it["cid"] for it in items])
    got = {}
    for it in items:
        v = vm.get(it.get("cid"))
        if v is not None:
            got[id(it)] = float(np.dot(_unit(v), vv))
    return got


def pick_for(d, pool, top, use_view=True):
    """在公共池里取景：相关度（池子给的）× 视角相似度（这道自己的取景器）× 取景词命中。

    池子负责「跟问题有关」，取景器负责「这道看哪一面」——池子共享、取景规则不同，
    所以各道拿到的是同一批材料里的不同几段。
    """
    if not pool:
        return []
    smax = max((x.get("score") or 0) for x in pool) or 1.0
    vc = view_cos(d, pool) if use_view else {}
    vals = list(vc.values())
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 0.0)
    scored = []
    for it in pool:
        sem = (it.get("score") or 0) / smax
        hit, rate = lens_hits(it.get("text") or "", d["marks"])
        g = vc.get(id(it))
        if g is None:
            sc = 0.75 * sem + 0.25 * rate
        else:
            vn = (g - lo) / (hi - lo) if hi > lo else 1.0
            sc = 0.45 * sem + 0.40 * vn + 0.15 * rate
        scored.append((sc, it, hit, g))
    scored.sort(key=lambda x: -x[0])
    return [(it, hit, sc, g) for sc, it, hit, g in scored[:top]]


def recall_block(question, kw=8):
    """关键词证据：走 recall.py 的 FTS5，拿作者语料里的精确命中。

    补一个真缺口——原设计只把潜意识层的语义最近邻交给显意识，
    于是「把我说过的 X 找出来」这类检索型问题会被答成「材料里没有」，
    而库里其实躺着精确命中。证据走 recall.py，不再自己造一套检索。
    """
    import subprocess
    cmd = [sys.executable, os.path.join(WS, "tools", "recall.py"), question,
           "--source", "kw", "--top", str(kw), "--json"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=180)
        d = json.loads(out.stdout or "{}")
    except Exception as e:
        return "", "关键词证据取不到：%s" % e
    kd = d.get("keyword") or {}
    items = kd.get("items") or []
    if not items:
        return ("关键词证据（FTS5 全文，作者自己的语料）：一条都没命中（%s）。\n"
                % (kd.get("mode") or "无模式")), ""
    lines = ["关键词证据（FTS5 全文，作者自己的语料；%s，命中 %s 条）"
             % (kd.get("mode") or "", kd.get("count"))]
    for i, it in enumerate(items, 1):
        lines.append("[K%d] %s《%s》%s 分 %.2f %s" % (
            i, (it.get("ts") or "")[:10], (it.get("title") or "")[:24],
            it.get("kind") or "", it.get("score") or 0,
            (it.get("text") or "").replace("\n", " ")[:240]))
    return "\n".join(lines) + "\n", ""


def material(items, width=420, title_w=26):
    out = []
    for i, t in enumerate(items, 1):
        it = t[0] if isinstance(t, tuple) else t
        ts = (it.get("ts") or "")[:10]
        title = (it.get("title") or "")[:title_w]
        txt = (it.get("text") or "").replace("\n", " ")
        if len(txt) > width:
            txt = txt[:width] + "…"
        out.append("[%d] %s《%s》\n%s" % (i, ts, title, txt))
    return "\n\n".join(out)


def ext_block(ext):
    if not (ext or "").strip():
        return ("（这次没有给外部材料：你只能靠【内部材料】和你自己的推断，"
                "务必逐句标 [内] 或 [推]。）\n\n")
    return "【外部材料】（DSH 检索来的，带出处；**不是作者的原话**）\n%s\n\n" % ext.strip()


def call_dao(d, question, ext=""):
    picks = d["picks"]
    user = "作者的问题：\n%s\n\n【内部材料】从你这道看到的作者发言（%d 条）：\n%s\n\n%s%s" % (
        question, len(picks), material(picks), ext_block(ext), COMMON)
    txt, usage, err = chat(d["prov"], d["sys"], user, max_tokens=420, temperature=d["temp"])
    d["opinion"], d["usage"], d["err"] = txt, usage, err
    return d


def ymdts():
    return time.strftime("%Y%m%d-%H%M%S")


def _bill(spend, t0):
    total_in = sum(v[0] for v in spend.values())
    total_out = sum(v[1] for v in spend.values())
    yuan = sum((spend[p][0] * PRICE[p][0] + spend[p][1] * PRICE[p][1]) / 1e6 for p in spend)
    print("\n" + "-" * 74)
    print("本次消耗：输入 %s tokens / 输出 %s tokens；用时 %.1f 秒"
          % (format(total_in, ","), format(total_out, ","), time.time() - t0))
    for p in spend:
        if spend[p][0] or spend[p][1]:
            print("   %-10s 入 %6s / 出 %5s → ¥%.4f" % (
                PROVIDER[p]["label"], format(spend[p][0], ","), format(spend[p][1], ","),
                (spend[p][0] * PRICE[p][0] + spend[p][1] * PRICE[p][1]) / 1e6))
    print("   合计约 ¥%.4f（本机那部分 ¥0）" % yuan)
    return total_in, total_out, yuan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--top", type=int, default=4, help="每道各挑几条材料")
    ap.add_argument("--pool", type=int, default=60, help="公共候选池上限")
    ap.add_argument("--dry-run", action="store_true", help="只跑本地取材料，不调云端")
    ap.add_argument("--no-main", action="store_true", help="只叫四道，不叫显意识")
    ap.add_argument("--ext", default="", help="外部材料文件（DSH 检索来的，带出处）")
    ap.add_argument("--no-gate", action="store_true", help="跳过开关，四道全上")
    ap.add_argument("--gate-only", action="store_true", help="只跑开关判断，不叫人写意见")
    ap.add_argument("--relens", action="store_true", help="让四道重新设计取景器")
    ap.add_argument("--only", default="", help="只叫这几道（中文名，逗号分隔；跳过开关）")
    a = ap.parse_args()

    ext = ""
    if a.ext:
        if os.path.exists(a.ext):
            with open(a.ext, encoding="utf-8") as f:
                ext = f.read()
            print("📎 外部材料：%s（%d 字）" % (a.ext, len(ext)))
        else:
            print("⚠ 找不到外部材料文件：%s" % a.ext)

    t0 = time.time()
    print("=" * 74)
    print("问题：%s" % a.question)
    print("=" * 74)
    spend = {k: [0, 0] for k in PROVIDER}

    # ① 取景器（每道自己设计一次，之后从 lenses.json 读）
    if a.dry_run:
        load_lenses(allow_gen=False)      # 已缓存的照用；缺的退回第一版启发式；绝不调云端
    else:
        _cache, lspend = load_lenses(a.relens)
        for p in spend:
            spend[p][0] += lspend[p][0]
            spend[p][1] += lspend[p][1]

    # ② 开关：显意识先判断要不要动用四道
    g = None
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    if only:
        active = [d for d in DAO if d["name"] in only]
        print("\n🧭 手动指定：%s（跳过开关）" % ("、".join(d["name"] for d in active) or "（空）"))
    elif a.no_gate:
        active = list(DAO)
        print("\n🧭 （--no-gate：四道全上）")
    elif a.dry_run:
        active = list(DAO)
        print("\n🧭 （--dry-run：跳过开关，四道全上，不花钱）")
    else:
        print("\n🧭 开关：显意识先判断要不要动用四道……")
        g, usage, gerr = gate(a.question)
        spend["deepseek"][0] += usage[0]
        spend["deepseek"][1] += usage[1]
        if gerr:
            print("   ⚠ 开关判断失败：%s → 按老办法，四道全上。" % gerr)
            g, active = None, list(DAO)
        else:
            print("   判断：%s" % ("动用" if g["use"] else "不动用"))
            print("   理由：%s" % (g.get("why") or "（没说）"))
            if g.get("need_ext") and not ext:
                print("   建议外部检索：%s" % (g.get("ext_query") or "（没说）"))
                print("   （这次没给 --ext；DSH 应先按上面这句去检索、写成文件再传进来）")
            if not g["use"]:
                active = []
            else:
                active = [d for d in DAO if d["name"] in g["daos"]] or list(DAO)
                print("   叫这几道：%s" % "、".join(d["name"] for d in active))

    if a.gate_only:
        print("\n（--gate-only：到此为止，没有叫人写意见）")
        _bill(spend, t0)
        return

    # ③ 公共候选池（要动用四道，就加上各道自己的视角问句一路）
    t1 = time.time()
    pool, assoc, errs = build_pool(a.question, a.pool, active)
    if errs:
        for e in errs:
            print("   ⚠ %s" % e)
    if not pool:
        print("取不到材料，先检查 sub_index.sqlite 与 Ollama。")
        return
    print("\n🌒 联想（公共池 %d 条，本机 %.1f 秒）最靠前的 8 条：" % (len(pool), time.time() - t1))
    for i, it in enumerate(assoc, 1):
        print("   [%d] %s  %.3f  %s" % (i, (it.get("ts") or "")[:10], it.get("score") or 0,
                                        (it.get("text") or "").replace("\n", " ")[:58]))

    # ④ 各道用各自的取景器在池里取景
    for d in active:
        d["picks"] = pick_for(d, pool, a.top)
    if active:
        print("\n各道挑到的材料（池子共享，取景器不同）：")
        for d in active:
            tags = " | ".join("+%s" % m for m in (d["picks"][0][1] if d["picks"] else []))
            print("   %-6s %d 条   %s" % (d["name"], len(d["picks"]), tags))
    else:
        print("\n（这次不动用四道，直接由显意识看材料。）")

    if a.dry_run:
        for d in active:
            print("\n" + "-" * 74)
            print("【%s · %s · %s】视角：%s" % (d["name"], d["yuan"], d["who"], d["view"]))
            print("取景词：%s" % "、".join(d["marks"]))
            for i, (it, hit, sc, gv) in enumerate(d["picks"], 1):
                print("   [%d] 综合 %.3f（视角相似 %s）%s《%s》命中：%s" % (
                    i, sc, "—" if gv is None else "%.3f" % gv, (it.get("ts") or "")[:10],
                    (it.get("title") or "")[:22], "、".join(hit) or "（无取景词）"))
                print("      %s" % (it.get("text") or "").replace("\n", " ")[:110])
        print("\n（--dry-run：没有调用云端，花费 ¥0）")
        return

    # ⑤ 各道写参考意见（并行）
    main_txt = None
    if active:
        print("\n各道各自写参考意见……")
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(lambda d: call_dao(d, a.question, ext), active))
        print("\n" + "=" * 74)
        print("参考意见（**只是参考，没有决定权**）")
        print("=" * 74)
        for d in active:
            p = d["prov"]
            spend[p][0] += d["usage"][0]
            spend[p][1] += d["usage"][1]
            print("\n【%s · %s · %s · %s】" % (d["name"], d["yuan"], d["who"], PROVIDER[p]["label"]))
            if d["err"]:
                print("   ❌ %s" % d["err"])
                continue
            print("   " + (d["opinion"] or "（空）").replace("\n", "\n   "))

    # ⑥ 显意识（有意见就裁决，没有就直答）
    if not a.no_main:
        print("\n" + "=" * 74)
        print("显意识%s……" % ("裁决" if active else "直答（这次不动用四道）"))
        print("=" * 74)
        # 关键词证据先取出来（走 recall.py 的 FTS5）：检索型问题靠它才答得准。
        # 检索词用开关给的那个（它比整句更像作者真写过的说法），没有就用原问题。
        kq = (g or {}).get("kw_query") or a.question
        rb, rerr = recall_block(kq)
        if rerr:
            print("   ⚠ %s" % rerr)
        else:
            print("   （已附 [K#] 关键词证据：FTS5 全文命中；检索词「%s」）" % kq)
        if active:
            ops = "\n\n".join("【%s · %s】\n%s" % (d["name"], d["yuan"],
                                                  d["opinion"] or "（这一道这次没说话）")
                              for d in active)
            head = "参考意见（只是参考，没有决定权）：\n\n%s\n\n%s" % (ops, rb)
            mats = material(assoc, width=380)
        else:
            head = "（这次没有参考意见：显意识自己判断不需要动用四道。）\n\n%s" % rb
            mats = material(pool[:12], width=380)
        user = "作者的问题：\n%s\n\n%s%s联想层的原始材料（内部）：\n%s" % (
            a.question, head, ext_block(ext), mats)
        main_txt, usage, main_err = chat("deepseek", MAIN_SYS, user, max_tokens=1400, temperature=0.6)
        spend["deepseek"][0] += usage[0]
        spend["deepseek"][1] += usage[1]
        print("\n" + ("   ❌ %s" % main_err if main_err else (main_txt or "（空）")))

    total_in, total_out, yuan = _bill(spend, t0)

    # ⑦ 落盘
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "%s.md" % ymdts())
    with open(path, "w", encoding="utf-8") as f:
        f.write("# 四道 + 显意识：%s\n\n" % a.question)
        f.write("- 时间：%s\n- 消耗：输入 %s / 输出 %s tokens，约 ¥%.4f\n- 公共池：%d 条\n- 动用：%s\n"
                % (time.strftime("%Y-%m-%d %H:%M:%S"), format(total_in, ","), format(total_out, ","),
                   yuan, len(pool),
                   "、".join(d["name"] for d in active) if active else "（不动用四道，显意识直答）"))
        if g:
            f.write("- 开关：%s ｜ %s\n" % ("动用" if g["use"] else "不动用", g.get("why") or ""))
            if g.get("need_ext"):
                f.write("- 建议外部检索：%s\n" % (g.get("ext_query") or ""))
        if ext:
            f.write("\n## 📎 外部材料（DSH 检索，非作者原话）\n\n%s\n\n" % ext.strip())
        f.write("\n## 🌒 联想（本机）\n\n")
        for i, it in enumerate(assoc, 1):
            f.write("%d. [%s] %.3f《%s》\n   %s\n" % (
                i, (it.get("ts") or "")[:10], it.get("score") or 0, it.get("title") or "",
                (it.get("text") or "").replace("\n", " ")))
        if active:
            f.write("\n## 参考意见\n")
            for d in active:
                f.write("\n### %s · %s · %s（%s）\n\n" % (d["name"], d["yuan"], d["who"],
                                                        PROVIDER[d["prov"]]["label"]))
                f.write("取景器：视角「%s」；取景词 %s\n\n取的材料：\n"
                        % (d["view"], "、".join(d["marks"])))
                for i, (it, hit, sc, gv) in enumerate(d["picks"], 1):
                    f.write("%d. [%s] 综合 %.3f（视角相似 %s）《%s》命中 %s\n   %s\n" % (
                        i, (it.get("ts") or "")[:10], sc, "—" if gv is None else "%.3f" % gv,
                        it.get("title") or "", "、".join(hit) or "—",
                        (it.get("text") or "").replace("\n", " ")[:300]))
                f.write("\n**参考意见**：\n\n%s\n" % (d["opinion"] or ("❌ " + (d["err"] or ""))))
        if main_txt:
            f.write("\n## 显意识%s\n\n%s\n" % ("裁决" if active else "直答", main_txt))
    print("\n已存：%s" % path)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""最底层的小工具：不碰文件、不碰数据库，只做纯计算。

这一层谁都可以用，它自己谁也不依赖（除了 Python 自带的 hashlib 和 time）。
"""
import hashlib
import time

__all__ = ["sha", "_ago", "_grams", "hhmm", "pct", "_kind_of", "_fmt_ts"]


def sha(text):
    u"""一段文字的短指纹（前 16 位），用来判断内容有没有变。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ago(ts):
    u"""一个时间戳距今多少天（给人看的）。"""
    try:
        d = (time.time() - int(ts)) / 86400.0
    except Exception:
        return u"不知道多久"
    if d < 1.0:
        return u"今天"
    if d < 2.0:
        return u"昨天"
    return u"%d 天前" % int(d)


def _grams(s, n=2):
    u"""把一句话切成两字片段，用来算「像不像」。"""
    s = u"".join(ch for ch in (s or u"") if ch.strip())
    return set(s[i:i + n] for i in range(max(0, len(s) - n + 1)))


def hhmm(ts):
    u"""时间戳变成「月-日 时:分」。"""
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def pct(v):
    u"""0.35 → 35：把份额印成人能读的百分数。"""
    return int(round(v * 100))


def _kind_of(val):
    u"""一个值是数字、文字还是别的（影子推导用）。"""
    if isinstance(val, bool):
        return u"other"
    if isinstance(val, (int, float)):
        return u"scalar"
    if isinstance(val, str):
        return u"text"
    if isinstance(val, (list, tuple, dict, set)):
        return u"collection"
    return u"other"


def _fmt_ts(t):
    u"""时间戳变成人能读的年月日。"""
    try:
        return time.strftime(u"%Y-%m-%d %H:%M", time.localtime(float(t)))
    except Exception:
        return u"—"

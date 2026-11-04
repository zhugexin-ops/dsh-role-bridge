u"""环境层：今天几号、几点、什么季节、外面什么天气、月相、日出日落。

规矩（作者 2026-10-20 定）：
1. 只给数据，不写成叙述句，也不给判词（不写「下雨容易忧郁」这种）。
2. 全部白嫖，不算：天气/风向/湿度来自中国天气网，日出日落来自 sunrise-sunset.org，
   月相用一条公开的正弦公式算（不联网，永远能得数）。
3. 拿不到就降级：天气按日期凑并标记为假；月相和钟点永远是真的。
4. 星座运势一律不进来——那是别人替她下的判词，不是数据。

配置：机制\\环境.json  {"city": "如东", "code": "101190504", "lat": 32.32, "lng": 121.19}
节假日：机制\\节假日.json  {"days": {"2026-10-01": "国庆节"}}（留空就只报工作日/周末）
缓存：机制\\环境缓存.json
"""
import io
import json as _json
import math
import os
import time
import urllib.request as _ur

WEATHER_TTL = 1800      # 天气半小时
SUN_TTL = 21600         # 日出日落 6 小时
SKY_TTL = 3600        # 星象 1 小时
HOLIDAY_TTL = 43200     # 节假日 12 小时
TIMEOUT = 6
UA = {"User-Agent": "Mozilla/5.0", "Referer": "http://www.weather.com.cn/"}
MOON_NAME = (u"新月", u"蛾眉月", u"上弦月", u"盈凸月", u"满月", u"亏凸月", u"下弦月", u"残月")


def _paths(ws):
    return (os.path.join(ws, u"机制", u"环境.json"),
            os.path.join(ws, u"机制", u"环境缓存.json"),
            os.path.join(ws, u"机制", u"节假日.json"))


def _read_json(path, default):
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            return _json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            _json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass


def _get(url, headers=UA, timeout=TIMEOUT):
    try:
        req = _ur.Request(url, headers=headers)
        return _ur.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except Exception:
        return ""


def _sk(code):
    u"""中国天气网实况：温度、天气、风向、风力、湿度、AQI、降水（一次全给）。"""
    t = _get("http://d1.weather.com.cn/sk_2d/%s.html" % code)
    i = t.find("{")
    if i < 0:
        return {}
    try:
        d = _json.loads(t[i:].strip())
    except Exception:
        return {}
    return {"weather": d.get("weather") or u"",
            "temp": d.get("temp") or u"",
            "wd": d.get("WD") or u"",
            "ws": d.get("WS") or u"",
            "sd": d.get("SD") or d.get("sd") or u"",
            "aqi": d.get("aqi") or u""}


def _sun(lat, lng):
    u"""日出日落（北京时间 HH:MM）。"""
    t = _get("https://api.sunrise-sunset.org/json?lat=%s&lng=%s&formatted=0" % (lat, lng))
    i = t.find("{")
    if i < 0:
        return {}
    try:
        r = _json.loads(t[i:]).get("results") or {}
    except Exception:
        return {}

    def hhmm(iso):
        try:
            h = int(iso[11:13])
            m = int(iso[14:16])
            m += 8 * 60
            return "%02d:%02d" % ((h + m // 60) % 24, m % 60)
        except Exception:
            return ""

    return {"sunrise": hhmm(r.get("sunrise") or ""), "sunset": hhmm(r.get("sunset") or "")}


def moon(ts=None):
    u"""月相名字——按朔望月正弦算，不联网。"""
    ts = ts or time.time()
    ref = 947181840.0          # 2000-01-07 02:14 UTC 的一次朔
    age = ((ts - ref) % 2551442.976) / 2551442.976
    return MOON_NAME[int(age * 8 + 0.5) % 8]


def _holiday(ws):
    u"""节假日名字（表里没有就返回空）；周末单独判。"""
    cfg = _read_json(_paths(ws)[2], {})
    days = cfg.get("days") or {}
    return days.get(time.strftime("%Y-%m-%d"), u"")


def _season(mon):
    return (u"冬", u"冬", u"春", u"春", u"春", u"夏",
            u"夏", u"夏", u"秋", u"秋", u"秋", u"冬")[mon - 1]


def _pseudo_weather(day):
    u"""取不到真天气时按日期凑一个（调用方负责标记为假）。"""
    seed = sum(ord(c) for c in day)
    return ((u"晴", u"多云", u"阴", u"小雨", u"晴"), seed % 5)[0], 12 + seed % 18


def now(ws):
    u"""此刻外面：一天的日期、星期、钟点、季节、天气、风向风力湿度、月相、日出日落。"""
    cfg = _read_json(_paths(ws)[0], {})
    code = str(cfg.get("code") or "")
    lat, lng = cfg.get("lat"), cfg.get("lng")
    cache_path = _paths(ws)[1]
    cache = _read_json(cache_path, {})
    t = time.time()
    c = cache.get("now") if isinstance(cache.get("now"), dict) else {}

    if not c or t - float(c.get("at") or 0) > WEATHER_TTL:
        w = _sk(code) if code else {}
        if w.get("weather"):
            c = {"at": t, "weather": w.get("weather"), "temp": w.get("temp"),
                 "wd": w.get("wd"), "ws": w.get("ws"), "sd": w.get("sd"),
                 "aqi": w.get("aqi"), "pseudo": 0}
        else:
            wt, tp = _pseudo_weather(time.strftime("%Y-%m-%d", time.localtime(t)))
            c = {"at": t, "weather": wt, "temp": tp, "wd": u"", "ws": u"", "sd": u"", "pseudo": 1}
        cache["now"] = c
        _write_json(cache_path, cache)

    if lat and lng and (not cache.get("sun") or t - float(cache["sun"].get("at") or 0) > SUN_TTL):
        s = _sun(lat, lng)
        if s.get("sunrise"):
            cache["sun"] = {"at": t, "sunrise": s["sunrise"], "sunset": s["sunset"]}
            _write_json(cache_path, cache)

    if lat and lng and (not cache.get("sky") or t - float((cache.get("sky") or {}).get("at") or 0) > SKY_TTL):
        pl = _planets(lat, lng)
        if pl:
            cache["sky"] = {"at": t, "list": pl}
            _write_json(cache_path, cache)

    lt = time.localtime(t)
    sun = cache.get("sun") or {}
    try:
        temp = round(float(c.get("temp")), 0)
        temp = int(temp) if temp == int(temp) else temp
    except Exception:
        temp = c.get("temp")
    return {"day": time.strftime("%Y-%m-%d", lt),
            "weekday": u"一二三四五六日"[lt.tm_wday],
            "clock": time.strftime("%H:%M", lt),
            "season": _season(lt.tm_mon),
            "weather": c.get("weather") or u"",
            "temp": temp,
            "wd": c.get("wd") or u"",
            "ws": c.get("ws") or u"",
            "sd": c.get("sd") or u"",
            "aqi": c.get("aqi") or u"",
            "visible": (cache.get("sky") or {}).get("list") or [],
            "moon": moon(t),
            "sunrise": sun.get("sunrise") or u"",
            "sunset": sun.get("sunset") or u"",
            "holiday": _holiday(ws) or _fest(time.strftime("%Y-%m-%d", lt)),
            "pseudo": bool(c.get("pseudo"))}


def block(ws):
    u"""递给她的一行裸数据：只有字段，没有句子。"""
    e = now(ws)
    parts = [u"%s 周%s" % (e["day"], e["weekday"]),
             e["clock"], e["season"],
             (u"%s %s°C" % (e["weather"], e["temp"])) if e["weather"] else u"",
             u"%s%s 湿度%s" % (e["wd"], e["ws"], e["sd"]) if e["wd"] else u"",
             e["moon"],
             (u"空气%s" % e["aqi"]) if e["aqi"] else u"",
             (u"可见" + u"·".join(x["cn"] for x in e["visible"])) if e.get("visible") else u""]
    if e["sunrise"]:
        parts.append(u"日出%s 日落%s" % (e["sunrise"], e["sunset"]))
    if e["holiday"] or _fest(e["day"]):
        parts.append(e["holiday"] or _fest(e["day"]))
    elif e["weekday"] in (u"六", u"日"):
        parts.append(u"周末")
    return u"（%s）" % u"｜".join([p for p in parts if p])


def _planets(lat, lng):
    u"""此刻地平线以上、肉眼够亮的星（月亮/行星），来自 visibleplanets.dev。"""
    t = _get("https://api.visibleplanets.dev/v3?latitude=%s&longitude=%s" % (lat, lng))
    i = t.find("{")
    if i < 0:
        return []
    try:
        data = _json.loads(t[i:]).get("data") or []
    except Exception:
        return []
    out = []
    for d in data:
        cn = PLANET_CN.get(d.get("name") or "")
        alt = d.get("altitude") or 0
        if cn and alt and float(alt) > 5:
            out.append({"cn": cn, "alt": int(float(alt)),
                        "az": (d.get("azimuthString") or u""), "con": d.get("constellation") or u""})
    return out


def _fest(day):
    u"""公历节日（农历节日写在 机制\节假日.json 里，系统不自己推农历）。"""
    return FEST.get(day[5:], u"")


PLANET_CN = {"Moon": u"月亮", "Mercury": u"水星", "Venus": u"金星", "Mars": u"火星",
             "Jupiter": u"木星", "Saturn": u"土星"}
FEST = {"01-01": u"元旦", "02-14": u"情人节", "03-08": u"妇女节", "04-01": u"愚人节",
        "05-01": u"劳动节", "05-04": u"青年节", "06-01": u"儿童节", "07-01": u"建党节",
        "08-01": u"建军节", "09-10": u"教师节", "10-01": u"国庆节", "12-24": u"平安夜",
        "12-25": u"圣诞节", "12-31": u"跨年夜"}


def report(ws):
    u"""--env 给人看的版式。"""
    e = now(ws)
    print(u"\n🌤  %s（周%s）｜%s｜%s" % (e["day"], e["weekday"], e["clock"], e["season"]))
    print(u"    空气质量 %s｜今晚看得见：%s"
          % (e["aqi"] or u"?", u"、".join(x["cn"] + u" " + str(x["alt"]) + u"°" for x in e["visible"]) or u"（没取到）"))
    print(u"    %s%s｜%s%s｜湿度 %s｜%s｜日出 %s 日落 %s%s"
          % (e["weather"], (u" %s°C" % e["temp"]) if e["temp"] != "" else u"",
             e["wd"], e["ws"], e["sd"], e["moon"], e["sunrise"] or u"?", e["sunset"] or u"?",
             u"  ←（联网没成，天气按日期凑的）" if e["pseudo"] else u""))
    print(u"    递给她的是：%s" % block(ws))

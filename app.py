#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屎山代码鉴定所 ShitScan —— 零依赖单文件服务
启动: python3 app.py  (APP_PORT 换端口)
路线: POST /api/generate 鉴定(代码只进内存不落盘,只存指标)
     GET  /api/stats 总鉴定数   GET /api/leaderboard 屎山榜
     GET  /download/<id>.txt 战报文本"""
import json
import os
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
STATIC = os.path.join(BASE, "static")
os.makedirs(DATA_DIR, exist_ok=True)

HOURLY_RATE = 150  # 默认时薪(可被 payload 覆盖)

# ============ 语言特征 ============
LANG_SIGS = {
    "python": [r"\bdef\s", r"\bimport\s", r"\bself\b", r"__name__", r"\belif\b", r"print\("],
    "javascript": [r"\bfunction\b", r"\bconst\b", r"\blet\b", r"=>", r"\bdocument\.", r"\bconsole\.log", r"\brequire\("],
    "java": [r"\bpublic\s+(class|void|static)", r"\bSystem\.out", r"\bprivate\s", r"\bnew\s+\w+\(", r"@Override"],
    "go": [r"\bpackage\s+main", r"\bfunc\s", r":=", r"\bfmt\.", r"\bdefer\b"],
    "c": [r"#include", r"\bprintf\(", r"\bmalloc\(", r"\bstruct\s", r"\bint\s+main\("],
}
LANG_KEYWORDS = {
    "python": {"comment": "#", "cx": r"\b(if|elif|else|for|while|and|or|except|finally|with|assert)\b|&&|\|\|",
               "func": r"^\s*def\s+(\w+)", "brace": False},
    "javascript": {"comment": "//", "cx": r"\b(if|else|for|while|case|catch|switch|do)\b|&&|\|\||\?",
                   "func": r"(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:\(|async))", "brace": True},
    "java": {"comment": "//", "cx": r"\b(if|else|for|while|case|catch|switch|do)\b|&&|\|\||\?",
             "func": r"(?:public|private|protected|static).*?\s+(\w+)\s*\([^)]*\)\s*\{", "brace": True},
    "go": {"comment": "//", "cx": r"\b(if|else|for|range|switch|case|select)\b|&&|\|\|",
           "func": r"^func\s+(?:\([^)]*\)\s*)?(\w+)", "brace": True},
    "c": {"comment": "//", "cx": r"\b(if|else|for|while|case|switch|do)\b|&&|\|\|",
          "func": r"^\w[\w\s\*]*\s+\**(\w+)\s*\([^;]*\)\s*\{?", "brace": True},
}
BAD_NAMES = re.compile(r"^(temp|tmp|tmp1|data|data1|data2|flag|foo|bar|test|test1|test2|aaa|asdf|qwe|ceshi|chuli|jiegou|str\d*|list\d*|map\d*|arr\d*|dict\d*|obj\d*|res\d*|ret\d*|result\d*|value\d*|val\d*|num\d*|i\d+|a|b|c|d|e|aa|bb|cc|dd|ee|x1|x2|y1|y2)$", re.I)
MAGIC_OK = {0, 1, 2, 3, 4, 5, 8, 10, 12, 16, 20, 24, 30, 31, 32, 60, 61, 64, 100, 127, 128, 200, 255, 256, 300, 301, 302, 400, 401, 403, 404, 500, 502, 503, 512, 1000, 1024, 2000, 4096, 8080, 80, 443}

# ============ 毒舌点评模板库(产品护城河) ============
ROAST = {
    "nest": [(6, "嵌套第{n}层——考古学家看了都要先办签证。"),
             (4, "第{n}行嵌套{d}层,箭头已经比你的职业生涯还长。"),
             (0, "嵌套尚在人类理解范围内,勉强。")],
    "longline": [(200, "第{n}行长达{L}字符,横着三块显示器都装不下你的罪。"),
                 (120, "第{n}行{L}个字符,建议直接印成春联。"),
                 (0, "行长控制得不错,看来被 code review 毒打过。")],
    "todo": [(8, "全篇{c}个 TODO——你的 TODO 比功能先上了生产。"),
             (3, "{c}个 TODO 在暗处注视着下一个接盘侠。"),
             (0, "TODO 不多,要么代码新,要么你善良心虚。")],
    "name": [(15, "{c}个迷惑命名(a/b/tmp1/data2),这是代码还是恶意乱码?"),
             (6, "{c}处谜语人命名,三个月后的你自己都解不开。"),
             (0, "命名基本能读,超出预期。")],
    "dup": [(20, "复制粘贴率{p}%——Ctrl+C 才是你真正的 OO 语言。"),
            (8, "重复行占{p}%,这段代码有兄弟,而且长得一模一样。"),
            (0, "重复不多,抽象能力在线(或懒得复制)。")],
    "magic": [(12, "{c}个魔法数字裸奔,256、9999、777——只有天知道它们为什么存在。"),
              (5, "{c}个魔法数字,总有一个会让三年后的运维头发竖起。"),
              (0, "魔法数字收敛,常量意识合格。")],
}
TITLES = [
    (97, "SSS", "代码成精", "建议原地申报世界文化遗产,林纳斯看了会连夜删库。", "#ff2d55"),
    (90, "SS", "万年圣山", "已成精,动一行塌全站,历代程序员绕道烧香。", "#ff6b35"),
    (75, "S", "镇店屎山", "重构成本高于推倒重来,掩埋是最优解。", "#ffb020"),
    (60, "A", "重度灾区", "已经开始发酵,但还没到不可救药——快了。", "#ffd93d"),
    (40, "B", "初具山形", "能闻到味了,趁早重构还能抢救。", "#a3e635"),
    (20, "C", "微瑕之作", "有点乱但整体是人写的,鼓励一下。", "#4ade80"),
    (0, "D", "崭新出厂", "干净得像刚入职第一天的你,建议同事来参观学习。", "#22d3ee"),
]
QUIPS = [
    (90, "经鉴定:此山承包了本团队 80% 的线上事故,建议立即申报文物保护。"),
    (70, "经鉴定:此山已进入稳定发酵期,接手者请自备防毒面具与离职证明。"),
    (50, "经鉴定:重构它需要三天,骂它需要三分钟,忍它已经三年。"),
    (30, "经鉴定:整体还行,但那几处祖传代码正在悄悄长出菌丝。"),
    (0, "经鉴定:基本是人写的,已发送嘉奖,请继续保持。"),
]

# ============ 鉴定引擎 ============
def detect_lang(code):
    best, hits = "python", -1
    for lang, sigs in LANG_SIGS.items():
        n = sum(len(re.findall(s, code)) for s in sigs)
        if n > hits:
            best, hits = lang, n
    return best


def analyze(code, lang=None, rate=HOURLY_RATE):
    lang = lang if lang in LANG_KEYWORDS else detect_lang(code)
    kw = LANG_KEYWORDS[lang]
    lines = code.splitlines()
    n_code = len([l for l in lines if l.strip()])
    if n_code < 3:
        return {"error": "太短了,至少给 3 行代码——一行成不了山,只能算便秘。"}

    # 各维度原始采集
    cx_pts = len(re.findall(kw["cx"], code))
    func_re = re.compile(kw["func"])
    max_nest, deepest_line, deepest_no = 0, "", 0
    max_len, longest_line, longest_no = 0, "", 0
    brace_depth, max_brace = 0, 0
    bad_names, todos, magic_nums = set(), [], []
    line_count_map = {}
    func_starts = []
    chain_calls = 0
    bare_swallow = 0

    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if not s:
            continue
        # 注释行:只查 TODO 罪证(屎山铁证都在注释里),其余指标跳过
        if s.startswith(kw["comment"]):
            if re.search(r"\b(TODO|FIXME|HACK|XXX)\b|先这样|回头|别删|祖传|临时方案", ln, re.I):
                todos.append({"no": i, "text": s[:70]})
            continue
        # 嵌套
        if kw["brace"]:
            brace_depth += ln.count("{") - ln.count("}")
            max_brace = max(max_brace, brace_depth)
            d = max_brace
        else:
            d = (len(ln) - len(ln.lstrip(" "))) // 4 + ln.count("\t")
        if d > max_nest:
            max_nest, deepest_line, deepest_no = d, s[:80], i
        # 行长
        if len(ln) > max_len:
            max_len, longest_line, longest_no = len(ln), s[:80], i
        # 命名
        for m in re.finditer(r"\b([A-Za-z_]\w{0,12})\b", ln):
            w = m.group(1)
            if len(w) <= 2 or BAD_NAMES.match(w):
                if w.lower() not in ("if", "in", "of", "is", "it", "to", "do", "as", "or", "on", "no", "ok", "err", "idx", "ctx", "id"):
                    bad_names.add(w)
        # TODO(代码内联标记)
        if re.search(r"\b(TODO|FIXME|HACK|XXX)\b", ln, re.I):
            todos.append({"no": i, "text": s[:70]})
        # 魔法数字
        for m in re.finditer(r"\b(\d{2,})\b", ln):
            if int(m.group(1)) not in MAGIC_OK:
                magic_nums.append(m.group(1))
        # 链式调用 a.b.c.d
        if re.search(r"\.\w+\.\w+\.\w+\.\w+", ln):
            chain_calls += 1
        # 裸吞异常
        if re.search(r"except\s*:?\s*(pass)?\s*$", ln) and not re.search(r"except\s+\w+", ln):
            bare_swallow += 1
        if re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", ln):
            bare_swallow += 1
        # 重复行
        key = re.sub(r"\s+", " ", s)
        line_count_map[key] = line_count_map.get(key, 0) + 1
        fm = func_re.search(ln)
        if fm:
            func_starts.append((i, fm.group(1) or "匿名函数"))

    dup_lines = sum(c - 1 for c in line_count_map.values() if c >= 3)
    dup_pct = round(dup_lines * 100.0 / n_code, 1)
    # 最长函数
    longest_func, lf_len = "-", 0
    if func_starts:
        bounds = [f[0] for f in func_starts] + [len(lines) + 1]
        for j in range(len(func_starts)):
            seg = bounds[j + 1] - bounds[j]
            if seg > lf_len:
                lf_len, longest_func = seg, func_starts[j][1]

    comment_n = len([l for l in lines if l.strip().startswith(kw["comment"])])
    comment_ratio = comment_n * 100.0 / max(n_code, 1)

    # 六维 0-100
    dims = {
        "复杂度": min(100, cx_pts * 100 // max(n_code, 20)),
        "嵌套": min(100, max_nest * 14),
        "命名": min(100, len(bad_names) * 9),
        "注释": int(100 - min(100, abs(comment_ratio - 15) * 6)) ,  # 注释率离 15% 越远越怪
        "重复": min(100, int(dup_pct * 3)),
        "长度": min(100, max_len // 3 + lf_len),
    }
    score = int(dims["复杂度"] * 0.22 + dims["嵌套"] * 0.22 + dims["命名"] * 0.14 +
                dims["注释"] * 0.10 + dims["重复"] * 0.18 + dims["长度"] * 0.14)
    title = next(t for t in TITLES if score >= t[0])

    # 重构报价: 人日 = 维度加权
    mandays = max(1, round((cx_pts * 0.15 + max_nest * 2 + lf_len * 0.4 + dup_lines * 0.6) / 8))
    cost = mandays * 8 * int(rate)
    cost_wan = cost / 10000.0

    # 罪证现场 top3
    crimes = []
    if max_nest >= 4:
        crimes.append({"no": deepest_no, "type": "嵌套", "text": deepest_line,
                       "roast": ROAST["nest"][0][1].format(n=deepest_no, d=max_nest)})
    if max_len >= 120:
        crimes.append({"no": longest_no, "type": "行长", "text": longest_line,
                       "roast": ROAST["longline"][0][1].format(n=longest_no, L=max_len)})
    for t in todos[:1]:
        crimes.append({"no": t["no"], "type": "TODO", "text": t["text"],
                       "roast": "第" + str(t["no"]) + "行的 TODO 正在注视下一个接盘侠。"})
    if bare_swallow:
        crimes.append({"no": 0, "type": "吞异常", "text": "(异常被静默吃掉)",
                       "roast": str(bare_swallow) + " 处异常被一口吞掉,出了事连个遗言都没有。"})
    crimes = crimes[:4]

    quip = next(q[1] for q in QUIPS if score >= q[0])
    roast_lines = [
        pick_roast("nest", max_nest, n=deepest_no, d=max_nest),
        pick_roast("longline", max_len, n=longest_no, L=max_len),
        pick_roast("todo", len(todos), c=len(todos)),
        pick_roast("name", len(bad_names), c=len(bad_names)),
        pick_roast("dup", dup_pct, p=dup_pct),
        pick_roast("magic", len(magic_nums), c=len(magic_nums)),
    ]

    return {
        "grade": title[1], "title": title[2], "verdict": title[3], "color": title[4],
        "score": score, "quip": quip, "dims": dims, "crimes": crimes,
        "mandays": mandays, "cost": cost, "roasts": roast_lines,
        "meta": {"lang": lang, "loc": n_code, "max_nest": max_nest, "max_len": max_len,
                 "longest_func": longest_func, "longest_func_lines": lf_len,
                 "dup_pct": dup_pct, "bad_names": len(bad_names), "todos": len(todos),
                 "magic_nums": len(magic_nums), "comment_ratio": round(comment_ratio, 1),
                 "swallow": bare_swallow},
    }


def pick_roast(kind, val, **kw):
    for th, tpl in ROAST[kind]:
        if val >= th:
            return tpl.format(**kw)
    return ""


def cert_no():
    return "SM-" + time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:4].upper()

# ============ 存储(只存指标,永不存代码) ============
def save_record(rid, result):
    rec = {"id": rid, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
           "grade": result["grade"], "title": result["title"], "score": result["score"],
           "lang": result["meta"]["lang"], "loc": result["meta"]["loc"],
           "cost": result["cost"]}
    with open(os.path.join(DATA_DIR, rid + ".json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    return rec


def load_records():
    out = []
    if os.path.isdir(DATA_DIR):
        for fn in os.listdir(DATA_DIR):
            if fn.endswith(".json"):
                with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
                    out.append(json.load(f))
    return out

# ============ HTTP ============
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
                return self._send(200, f.read())
        if path == "/api/stats":
            recs = load_records()
            grades = {}
            for r in recs:
                grades[r["grade"]] = grades.get(r["grade"], 0) + 1
            return self._send(200, json.dumps({"total": len(recs), "grades": grades}, ensure_ascii=False),
                              "application/json; charset=utf-8")
        if path == "/api/leaderboard":
            recs = sorted(load_records(), key=lambda r: -r["score"])[:10]
            return self._send(200, json.dumps({"board": recs}, ensure_ascii=False),
                              "application/json; charset=utf-8")
        m = re.match(r"^/download/([0-9a-f-]{36})\.txt$", path)
        if m:
            fp = os.path.join(DATA_DIR, m.group(1) + ".json")
            if os.path.exists(fp):
                with open(fp, encoding="utf-8") as f:
                    rec = json.load(f)
                txt = ("屎山代码鉴定所 · 鉴定战报\n"
                       "========================\n"
                       "证书编号: " + rec["id"][:8].upper() + "\n"
                       "鉴定时间: " + rec["ts"] + "\n"
                       "屎山等级: " + rec["grade"] + " 级「" + rec["title"] + "」\n"
                       "屎山指数: " + str(rec["score"]) + "/100\n"
                       "语言/规模: " + rec["lang"] + " / " + str(rec["loc"]) + " 行\n"
                       "重构报价: ¥" + format(rec["cost"], ",") + "\n"
                       "------------------------\n"
                       "扫描二维码,鉴定你身边的屎山。")
                return self._send(200, txt, "text/plain; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=\"shit_report.txt\""})
        return self._send(404, "not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/api/generate":
            return self._send(404, "not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            result = analyze(data.get("code", ""), data.get("lang"), int(data.get("rate", HOURLY_RATE)))
            if "error" in result:
                return self._send(400, json.dumps(result, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            rid = str(uuid.uuid4())
            save_record(rid, result)
            result["id"] = rid
            return self._send(200, json.dumps(result, ensure_ascii=False),
                              "application/json; charset=utf-8")
        except Exception as e:
            return self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False),
                              "application/json; charset=utf-8")


if __name__ == "__main__":
    port = int(os.environ.get("APP_PORT", "8670"))
    print("shit-scan running at http://127.0.0.1:%d" % port)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()

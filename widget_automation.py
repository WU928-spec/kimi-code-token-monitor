"""Kimi Work Blueprint Automation 采集脚本（非独立运行，由 Blueprint 运行时调用 run(ctx)）。

每 5 分钟扫描 ~/.kimi-code/sessions 下全部 wire.jsonl，按行 hash 全局去重
汇总 usage.record 的 token 用量，产出结构化 artifact 推送给绑定的 Widget。
"""
import json, glob, os, hashlib, datetime, collections

ROOT = os.path.expanduser("~/.kimi-code/sessions")
KEYS = ("inputOther", "inputCacheRead", "inputCacheCreation", "output")

def run(ctx):
    seen = set()
    tot = collections.Counter()
    today = collections.Counter()
    models = collections.defaultdict(collections.Counter)
    model_n = collections.Counter()
    days = collections.defaultdict(collections.Counter)
    day_n = collections.Counter()
    n = 0
    day = datetime.date.today().isoformat()
    now = datetime.datetime.now()

    for fp in glob.glob(os.path.join(ROOT, "**", "wire.jsonl"), recursive=True):
        with open(fp, "rb") as f:
            for raw in f:
                if b'"usage.record"' not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                if d.get("type") != "usage.record":
                    continue
                h = hashlib.md5(raw).hexdigest()
                if h in seen:
                    continue
                seen.add(h)
                u = d["usage"]
                m = d.get("model", "?")
                rec_day = datetime.datetime.fromtimestamp(d["time"] / 1000).strftime("%Y-%m-%d")
                n += 1
                model_n[m] += 1
                day_n[rec_day] += 1
                for k in KEYS:
                    v = u.get(k, 0)
                    tot[k] += v
                    models[m][k] += v
                    days[rec_day][k] += v
                    if rec_day == day:
                        today[k] += v

    return {"artifact": {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M:%S"),
        "totalRecords": n,
        "total": {k: tot[k] for k in KEYS},
        "today": {"inputOther": today["inputOther"], "inputCacheRead": today["inputCacheRead"],
                   "output": today["output"], "records": day_n[day]},
        "byModel": [{"model": m, "output": c["output"], "inputOther": c["inputOther"],
                      "inputCacheRead": c["inputCacheRead"], "records": model_n[m]}
                     for m, c in sorted(models.items(), key=lambda x: -x[1]["output"])],
        "byDay": [{"day": d, "output": c["output"], "inputOther": c["inputOther"],
                    "inputCacheRead": c["inputCacheRead"], "records": day_n[d]}
                   for d, c in sorted(days.items())],
    }}

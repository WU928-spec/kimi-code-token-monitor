#!/usr/bin/env python3
"""实时监控 kimi-code 的 token 用量。用法: python3 kimi_token_monitor.py"""
import json, glob, os, hashlib, time, datetime, collections, sys

ROOT = os.path.expanduser("~/.kimi-code/sessions")
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".token_monitor_state.json")
INTERVAL = 2  # 秒

def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except Exception:
            pass
    return {"offset": {}, "seen": []}

def save_state(st):
    st["seen"] = st["seen"][-20000:]  # 防无限增长
    json.dump(st, open(STATE, "w"))

def scan(st):
    tot = collections.Counter()
    today = collections.Counter()
    models = collections.defaultdict(collections.Counter)
    seen = set(st["seen"])
    new_hashes = []
    day = datetime.date.today().isoformat()
    for fp in glob.glob(os.path.join(ROOT, "**", "wire.jsonl"), recursive=True):
        off = st["offset"].get(fp, 0)
        size = os.path.getsize(fp)
        if size < off:
            off = 0  # 文件被轮转,重扫
        with open(fp, "rb") as f:
            f.seek(off)
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
                new_hashes.append(h)
                u = d["usage"]
                rec_day = datetime.datetime.fromtimestamp(d["time"] / 1000).strftime("%Y-%m-%d")
                keys = ("inputOther", "inputCacheRead", "inputCacheCreation", "output")
                for k in keys:
                    v = u.get(k, 0)
                    tot[k] += v
                    models[d.get("model", "?")][k] += v
                    if rec_day == day:
                        today[k] += v
            st["offset"][fp] = f.tell()
    st["seen"].extend(new_hashes)
    return tot, today, models, len(new_hashes)

def fmt(n):
    return f"{n:,}"

def render(tot, today, models, n_new, started):
    os.system("clear")
    print(f"kimi-code token 监控   每 {INTERVAL}s 刷新 | 本次新增 {n_new} 条 | 已运行 {int(time.time()-started)}s | Ctrl+C 退出")
    print("-" * 78)
    print(f"{'':<28}{'output':>14}{'input':>14}{'cacheRead':>16}")
    print(f"{'累计（所有历史会话）':<24}{fmt(tot['output']):>14}{fmt(tot['inputOther']):>14}{fmt(tot['inputCacheRead']):>16}")
    print(f"{'今日':<26}{fmt(today['output']):>14}{fmt(today['inputOther']):>14}{fmt(today['inputCacheRead']):>16}")
    print("-" * 78)
    print("按模型:")
    for m, c in sorted(models.items(), key=lambda x: -x[1]["output"]):
        print(f"  {m:<34}{fmt(c['output']):>12}{fmt(c['inputOther']):>14}{fmt(c['inputCacheRead']):>16}")
    print("-" * 78)
    print("注: input=inputOther(非缓存); cacheCreate 全为 0 未列出")

def main():
    st = load_state()
    started = time.time()
    print("首次扫描历史记录中...")
    try:
        while True:
            tot, today, models, n_new = scan(st)
            save_state(st)
            render(tot, today, models, n_new, started)
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        save_state(st)
        print("\n已退出,状态已保存(重启不会重复计数)")

if __name__ == "__main__":
    main()

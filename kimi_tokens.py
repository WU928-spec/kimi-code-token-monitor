#!/usr/bin/env python3
"""kimi-code token 用量统计与监控（零依赖，Python 3.8+）

用法:
  python3 kimi_tokens.py stats [--root PATH] [--json OUT] [--csv OUT]
  python3 kimi_tokens.py watch [--root PATH] [--interval N]

数据原理: ~/.kimi-code/sessions/**/wire.jsonl 中的 usage.record 行记录每次
模型响应的真实 token 用量; step.end 事件里的 usage 是它的副本, 已排除。
按行内容 hash 全局去重, 重复运行/多次扫描不会重复计数。
"""
import argparse, collections, csv, datetime, glob, hashlib, json, os, time

KEYS = ("inputOther", "inputCacheRead", "inputCacheCreation", "output")


def find_records(root, seen):
    """流式扫描 root 下全部 wire.jsonl, 产出去重后的 usage.record (dict)。"""
    pattern = os.path.join(root, "**", "wire.jsonl")
    for fp in sorted(glob.glob(pattern, recursive=True)):
        try:
            f = open(fp, "rb")
        except OSError:
            continue
        with f:
            for raw in f:
                if b'"usage.record"' not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                if d.get("type") != "usage.record":
                    continue
                h = hashlib.md5(raw).hexdigest()
                if h in seen:
                    continue
                seen.add(h)
                yield d


def empty_agg():
    return {
        "totalRecords": 0,
        "total": dict.fromkeys(KEYS, 0),
        "today": {"inputOther": 0, "inputCacheRead": 0, "output": 0, "records": 0},
        "byModel": {},
        "byDay": {},
    }


def add_record(agg, d):
    u = d["usage"]
    m = d.get("model", "?")
    day = datetime.datetime.fromtimestamp(d["time"] / 1000).strftime("%Y-%m-%d")
    today = datetime.date.today().isoformat()
    agg["totalRecords"] += 1
    for k in KEYS:
        v = u.get(k, 0)
        agg["total"][k] += v
    for bucket in (agg["byModel"].setdefault(m, {"records": 0}),
                   agg["byDay"].setdefault(day, {"records": 0})):
        bucket["records"] += 1
        for k in KEYS:
            bucket[k] = bucket.get(k, 0) + u.get(k, 0)
    if day == today:
        agg["today"]["records"] += 1
        for k in ("inputOther", "inputCacheRead", "output"):
            agg["today"][k] += u.get(k, 0)


def aggregate(root, seen=None):
    seen = seen if seen is not None else set()
    agg = empty_agg()
    for d in find_records(root, seen):
        add_record(agg, d)
    agg["updatedAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 数组化, 便于 JSON 消费
    agg["byModel"] = [dict(model=m, **c) for m, c in
                      sorted(agg["byModel"].items(), key=lambda x: -x[1]["output"])]
    agg["byDay"] = [dict(day=d, **c) for d, c in sorted(agg["byDay"].items())]
    return agg


def fmt(n):
    return f"{n:,}"


def cmd_stats(args):
    agg = aggregate(args.root)
    t, td = agg["total"], agg["today"]
    print(f"扫描完成: {agg['totalRecords']} 次模型响应 (更新于 {agg['updatedAt']})\n")
    print(f"{'':<20}{'output':>14}{'input(非缓存)':>16}{'cacheRead':>18}")
    print(f"{'累计':<16}{fmt(t['output']):>14}{fmt(t['inputOther']):>16}{fmt(t['inputCacheRead']):>18}")
    print(f"{'今日':<18}{fmt(td['output']):>14}{fmt(td['inputOther']):>16}{fmt(td['inputCacheRead']):>18}"
          f"   ({td['records']} 次响应)")
    print("\n按模型 (按 output 降序):")
    for m in agg["byModel"]:
        print(f"  {m['model']:<38}{fmt(m['output']):>12}{fmt(m['records']):>8} 次")
    print("\n按天:")
    for d in agg["byDay"]:
        print(f"  {d['day']}  {fmt(d['output']):>12} output  {fmt(d['records']):>6} 次")
    if args.json:
        json.dump(agg, open(args.json, "w"), ensure_ascii=False, indent=1)
        print(f"\nJSON 已写入 {args.json}")
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["day", "records", "inputOther", "inputCacheRead", "output"])
            for d in agg["byDay"]:
                w.writerow([d["day"], d["records"], d["inputOther"],
                            d["inputCacheRead"], d["output"]])
        print(f"CSV 已写入 {args.csv}")


STATE_NAME = ".kimi_tokens_state.json"


def cmd_watch(args):
    """终端实时监控: 增量扫描, 状态落盘, 重启不重复计数。"""
    state_path = os.path.join(os.getcwd(), STATE_NAME)
    offsets, seen, agg = {}, set(), None
    if os.path.exists(state_path):
        try:
            st = json.load(open(state_path))
            offsets, agg = st.get("offset", {}), st.get("agg")
            seen = set(st.get("seen", []))
        except ValueError:
            pass
    started = time.time()

    def save():
        json.dump({"offset": offsets, "seen": list(seen)[-20000:], "agg": agg},
                  open(state_path, "w"))

    print("首次扫描历史记录中...")
    try:
        while True:
            new = 0
            if agg is None:
                agg = empty_agg()  # 保持 dict 结构, 增量分支统一
                for d in find_records(args.root, seen):
                    add_record(agg, d)
                agg["updatedAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                new = agg["totalRecords"]
                offsets = {fp: os.path.getsize(fp) for fp in
                           glob.glob(os.path.join(args.root, "**", "wire.jsonl"), recursive=True)}
            else:
                agg["updatedAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for fp in glob.glob(os.path.join(args.root, "**", "wire.jsonl"), recursive=True):
                    off = offsets.get(fp, 0)
                    size = os.path.getsize(fp)
                    if size < off:
                        off = 0  # 日志轮转, 重扫
                    with open(fp, "rb") as f:
                        f.seek(off)
                        for raw in f:
                            if b'"usage.record"' not in raw:
                                continue
                            try:
                                d = json.loads(raw)
                            except ValueError:
                                continue
                            if d.get("type") != "usage.record":
                                continue
                            h = hashlib.md5(raw).hexdigest()
                            if h in seen:
                                continue
                            seen.add(h)
                            add_record(agg, d)
                            new += 1
                        offsets[fp] = f.tell()
            save()
            os.system("clear")
            t, td = agg["total"], agg["today"]
            print(f"kimi-code token 监控 | 新增 {new} 条 | 已运行 {int(time.time() - started)}s"
                  f" | 更新于 {agg['updatedAt']} | Ctrl+C 退出")
            print("-" * 70)
            print(f"累计: output={fmt(t['output'])}  input={fmt(t['inputOther'])}"
                  f"  cacheRead={fmt(t['inputCacheRead'])}  ({agg['totalRecords']} 次响应)")
            print(f"今日: output={fmt(td['output'])}  input={fmt(td['inputOther'])}"
                  f"  cacheRead={fmt(td['inputCacheRead'])}  ({td['records']} 次响应)")
            print("\n按模型:")
            rows = sorted(agg["byModel"].items(), key=lambda x: -x[1]["output"])
            for m, c in rows:
                print(f"  {m:<38}{fmt(c['output']):>12}{fmt(c['records']):>8} 次")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        save()
        print(f"\n已退出,状态保存在 {state_path} (重启不会重复计数)")


def main():
    p = argparse.ArgumentParser(description="kimi-code token 用量统计与监控")
    p.add_argument("--root", default=os.path.expanduser("~/.kimi-code/sessions"),
                   help="wire.jsonl 所在根目录 (默认 ~/.kimi-code/sessions)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stats", help="一次性全量统计")
    s.add_argument("--json", metavar="OUT", help="同时导出 JSON")
    s.add_argument("--csv", metavar="OUT", help="同时导出按天 CSV")
    s.set_defaults(func=cmd_stats)
    w = sub.add_parser("watch", help="终端实时监控")
    w.add_argument("--interval", type=int, default=2, help="刷新间隔秒数 (默认 2)")
    w.set_defaults(func=cmd_watch)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

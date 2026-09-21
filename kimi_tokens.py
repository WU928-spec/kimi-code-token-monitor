#!/usr/bin/env python3
"""kimi-code token 用量统计与监控（零依赖，Python 3.8+）

统计扣 Kimi Code 会员额度的 token 消耗，覆盖三个客户端的本地日志:
  - kimi-code  : $KIMI_CODE_HOME(默认 ~/.kimi-code)/sessions/**/wire.jsonl   (usage.record)
  - claude-code: $CLAUDE_CONFIG_DIR(默认 ~/.claude)/projects/**/*.jsonl      (Anthropic usage, 仅 model 含 kimi)
  - codex      : $CODEX_HOME(默认 ~/.codex)/**/*.jsonl                       (sessions + archived_sessions, 仅会话模型为 kimi)

用法:
  python3 kimi_tokens.py stats [--root PATH] [--claude-root PATH] [--codex-root PATH]
                               [--no-claude] [--no-codex] [--json OUT] [--csv OUT]
  python3 kimi_tokens.py watch [--interval N] [同上]

去重: 按行内容 hash 全局去重, 重复运行/多次扫描不会重复计数;
claude-code 另按 message.id 去重（同一条助手消息可能落两行）;
codex 的 total_token_usage 是累积值, 与运行基线做差取增量, 字段变小视为计数器重置。
"""
import argparse, collections, csv, datetime, glob, hashlib, json, os, time

KEYS = ("inputOther", "inputCacheRead", "inputCacheCreation", "output")
DEFAULTS = {
    "kimi-code": os.path.join(os.environ.get("KIMI_CODE_HOME") or os.path.expanduser("~/.kimi-code"), "sessions"),
    "claude-code": os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects"),
    "codex": os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"),
}


def norm(u, mapping):
    out = dict.fromkeys(KEYS, 0)
    for src, dst in mapping.items():
        out[dst] = u.get(src, 0) or 0
    return out

KIMI_MAP = {"inputOther": "inputOther", "inputCacheRead": "inputCacheRead",
            "inputCacheCreation": "inputCacheCreation", "output": "output"}
CLAUDE_MAP = {"input_tokens": "inputOther", "cache_read_input_tokens": "inputCacheRead",
              "cache_creation_input_tokens": "inputCacheCreation", "output_tokens": "output"}
CODEX_MAP = {"input_tokens": "inputOther", "cached_input_tokens": "inputCacheRead",
             "output_tokens": "output"}


def parse_kimi(d):
    if d.get("type") != "usage.record":
        return None
    return d.get("model", "?"), d.get("time", 0) / 1000, norm(d["usage"], KIMI_MAP)


def parse_claude(d):
    if d.get("type") != "assistant":
        return None
    m = d.get("message", {})
    if "kimi" not in (m.get("model") or ""):
        return None
    u = m.get("usage")
    if not u:
        return None
    # cache_creation 有 ephemeral_5m/1h 细分字段, 兼容取数 (参考 kimi-builders/usage)
    cc = u.get("cache_creation") or {}
    usage = norm(u, CLAUDE_MAP)
    split = (cc.get("ephemeral_5m_input_tokens") or 0) + (cc.get("ephemeral_1h_input_tokens") or 0)
    usage["inputCacheCreation"] = max(usage["inputCacheCreation"], split)
    try:
        ts = datetime.datetime.fromisoformat(d.get("timestamp", "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        ts = 0
    return m["model"], ts, usage


def parse_codex(d):
    """返回 (kind, ts, usage): kind 为 'delta'(直接计) 或 'cum'(累积值, 需减基线)。"""
    info = d.get("info") or (d.get("payload") or {}).get("info") or {}
    try:
        ts = datetime.datetime.fromisoformat(d.get("timestamp", "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        ts = 0
    if info.get("last_token_usage"):  # 单次请求增量, 最可靠
        return "delta", ts, norm(info["last_token_usage"], CODEX_MAP)
    if info.get("total_token_usage"):  # 会话累积值, 与基线做差
        return "cum", ts, norm(info["total_token_usage"], CODEX_MAP)
    tu = d.get("token_usage") or (d.get("payload") or {}).get("usage")
    if tu:  # token_count 事件等按轮增量
        return "delta", ts, norm(tu, CODEX_MAP)
    return None


PARSERS = {"kimi-code": parse_kimi, "claude-code": parse_claude, "codex": parse_codex}
PATTERNS = {"kimi-code": b'"usage.record"', "claude-code": b'"usage"', "codex": b'usage'}


def classify(fp):
    p = fp.replace(os.sep, "/")
    if "/.claude/" in p:
        return "claude-code"
    if "/.codex/" in p:
        return "codex"
    return "kimi-code"


def resolve_codex(r, fp, codex_bases, codex_seen):
    """codex: 解析结果转为 (model, ts, usage); 累积值与基线做差, 同文件同增量去重。"""
    kind, ts, usage = r
    if kind == "cum":
        base = codex_bases.setdefault(fp, dict.fromkeys(KEYS, 0))
        if any(usage[k] < base[k] for k in KEYS):
            delta = dict(usage)  # 计数器重置(compaction/新窗口), 当前值即增量
        else:
            delta = {k: usage[k] - base[k] for k in KEYS}
        base.update(usage)
        usage = delta
    sig = (usage["inputOther"], usage["inputCacheRead"], usage["output"])
    if sig in codex_seen:
        return None
    codex_seen.add(sig)
    if not any(usage.values()):
        return None
    return "kimi-for-coding", ts, usage


def iter_source(platform, root, seen, msg_ids, codex_bases):
    """流式扫描一个平台, 产出去重后的记录 (platform, model, ts, usage)。"""
    if not root or not os.path.isdir(root):
        return
    pattern = os.path.join(root, "**", "*.jsonl")
    for fp in sorted(glob.glob(pattern, recursive=True)):
        parser, pat = PARSERS[platform], PATTERNS[platform]
        # codex 文件级模型过滤: 会话文件中出现 kimi 模型才统计
        if platform == "codex":
            try:
                if b'"kimi' not in open(fp, "rb").read(200000):
                    continue
            except OSError:
                continue
        try:
            f = open(fp, "rb")
        except OSError:
            continue
        codex_seen = set()  # 同文件内按增量值去重
        with f:
            for raw in f:
                if pat not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                r = parser(d)
                if not r:
                    continue
                if platform == "codex":
                    r = resolve_codex(r, fp, codex_bases, codex_seen)
                    if not r:
                        continue
                elif platform == "claude-code":
                    mid = (d.get("message") or {}).get("id")
                    if mid:
                        if mid in msg_ids:
                            continue
                        msg_ids.add(mid)
                h = hashlib.md5(raw).hexdigest()
                if h in seen:
                    continue
                seen.add(h)
                yield platform, r[0], r[1], r[2]


def collect_all(roots, include, seen=None, msg_ids=None, codex_bases=None):
    seen = seen if seen is not None else set()
    msg_ids = msg_ids if msg_ids is not None else set()
    codex_bases = codex_bases if codex_bases is not None else {}
    for platform in ("kimi-code", "claude-code", "codex"):
        if not include.get(platform, True):
            continue
        root = roots.get(platform)
        if not root:
            continue
        yield from iter_source(platform, root, seen, msg_ids, codex_bases)


def empty_agg():
    return {
        "totalRecords": 0,
        "total": dict.fromkeys(KEYS, 0),
        "today": {"inputOther": 0, "inputCacheRead": 0, "output": 0, "records": 0},
        "byPlatform": {},
        "byModel": {},
        "byDay": {},
    }


def add_record(agg, platform, model, ts, usage):
    day = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "?"
    today = datetime.date.today().isoformat()
    agg["totalRecords"] += 1
    for k in KEYS:
        agg["total"][k] += usage[k]
    for bucket in (agg["byPlatform"].setdefault(platform, {"records": 0}),
                   agg["byModel"].setdefault(model, {"records": 0}),
                   agg["byDay"].setdefault(day, {"records": 0})):
        bucket["records"] += 1
        for k in KEYS:
            bucket[k] = bucket.get(k, 0) + usage[k]
    if day == today:
        agg["today"]["records"] += 1
        for k in ("inputOther", "inputCacheRead", "output"):
            agg["today"][k] += usage[k]


def finalize(agg):
    agg["updatedAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for key in ("byPlatform", "byModel", "byDay"):
        name = {"byPlatform": "platform", "byModel": "model", "byDay": "day"}[key]
        agg[key] = [dict(**{name: n}, **c) for n, c in
                    sorted(agg[key].items(), key=lambda x: -x[1]["output"])]
    return agg


def aggregate(roots, include, seen=None, msg_ids=None):
    agg = empty_agg()
    for platform, model, ts, usage in collect_all(roots, include, seen, msg_ids):
        add_record(agg, platform, model, ts, usage)
    return finalize(agg)


def fmt(n):
    return f"{n:,}"


def cmd_stats(args):
    roots = {"kimi-code": args.root, "claude-code": None if args.no_claude else args.claude_root,
             "codex": None if args.no_codex else args.codex_root}
    include = {"kimi-code": True, "claude-code": not args.no_claude, "codex": not args.no_codex}
    agg = aggregate(roots, include)
    t, td = agg["total"], agg["today"]
    print(f"扫描完成: {agg['totalRecords']} 次模型响应 (更新于 {agg['updatedAt']})\n")
    print(f"{'':<20}{'output':>14}{'input(非缓存)':>16}{'cacheRead':>18}")
    print(f"{'累计':<16}{fmt(t['output']):>14}{fmt(t['inputOther']):>16}{fmt(t['inputCacheRead']):>18}")
    print(f"{'今日':<18}{fmt(td['output']):>14}{fmt(td['inputOther']):>16}{fmt(td['inputCacheRead']):>18}"
          f"   ({td['records']} 次响应)")
    print("\n按客户端:")
    for p in agg["byPlatform"]:
        print(f"  {p['platform']:<14}{fmt(p['output']):>14}{fmt(p['records']):>8} 次")
    print("\n按模型 (按 output 降序):")
    for m in agg["byModel"]:
        print(f"  {m['model']:<38}{fmt(m['output']):>12}{fmt(m['records']):>8} 次")
    print("\n按天:")
    for d in sorted(agg["byDay"], key=lambda x: x["day"]):
        print(f"  {d['day']}  {fmt(d['output']):>12} output  {fmt(d['records']):>6} 次")
    if args.json:
        json.dump(agg, open(args.json, "w"), ensure_ascii=False, indent=1)
        print(f"\nJSON 已写入 {args.json}")
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["day", "records", "inputOther", "inputCacheRead", "output"])
            for d in sorted(agg["byDay"], key=lambda x: x["day"]):
                w.writerow([d["day"], d["records"], d["inputOther"],
                            d["inputCacheRead"], d["output"]])
        print(f"CSV 已写入 {args.csv}")


STATE_NAME = ".kimi_tokens_state.json"


def cmd_watch(args):
    """终端实时监控: 增量扫描三个平台, 状态落盘, 重启不重复计数。"""
    roots = {"kimi-code": args.root, "claude-code": None if args.no_claude else args.claude_root,
             "codex": None if args.no_codex else args.codex_root}
    include = {"kimi-code": True, "claude-code": not args.no_claude, "codex": not args.no_codex}
    state_path = os.path.join(os.getcwd(), STATE_NAME)
    offsets, seen, msg_ids, agg = {}, set(), set(), None
    codex_bases, codex_seen = {}, {}
    if os.path.exists(state_path):
        try:
            st = json.load(open(state_path))
            offsets, agg = st.get("offset", {}), st.get("agg")
            seen = set(st.get("seen", []))
            msg_ids = set(st.get("msgIds", []))
            codex_bases = st.get("codexBase", {})
        except ValueError:
            pass
    started = time.time()

    def save():
        json.dump({"offset": offsets, "seen": list(seen)[-20000:],
                   "msgIds": list(msg_ids)[-20000:], "agg": agg,
                   "codexBase": codex_bases}, open(state_path, "w"))

    def scan_file(fp):
        """返回该文件新增的记录数。"""
        nonlocal agg
        platform = classify(fp)
        if not include.get(platform):
            return 0
        parser, pat = PARSERS[platform], PATTERNS[platform]
        off = offsets.get(fp, 0)
        try:
            size = os.path.getsize(fp)
        except OSError:
            return 0
        if size < off:
            off = 0  # 日志轮转, 重扫
            codex_bases.pop(fp, None)
        new = 0
        with open(fp, "rb") as f:
            f.seek(off)
            for raw in f:
                if pat not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except ValueError:
                    continue
                r = parser(d)
                if not r:
                    continue
                if platform == "codex":
                    seen_set = codex_seen.setdefault(fp, set())
                    r = resolve_codex(r, fp, codex_bases, seen_set)
                    if not r:
                        continue
                elif platform == "claude-code":
                    mid = (d.get("message") or {}).get("id")
                    if mid:
                        if mid in msg_ids:
                            continue
                        msg_ids.add(mid)
                h = hashlib.md5(raw).hexdigest()
                if h in seen:
                    continue
                seen.add(h)
                add_record(agg, platform, r[0], r[1], r[2])
                new += 1
            offsets[fp] = f.tell()
        return new

    def active_roots():
        return [roots[k] for k in ("kimi-code", "claude-code", "codex") if include.get(k) and roots.get(k)]

    print("首次扫描历史记录中...")
    try:
        while True:
            new = 0
            if agg is None:
                agg = empty_agg()
                for platform, model, ts, usage in collect_all(roots, include, seen, msg_ids, codex_bases):
                    add_record(agg, platform, model, ts, usage)
                    new += 1
                finalize(agg)
                for root in active_roots():
                    for fp in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
                        try:
                            offsets[fp] = os.path.getsize(fp)
                        except OSError:
                            pass
            else:
                agg["updatedAt"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for root in active_roots():
                    for fp in glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True):
                        new += scan_file(fp)
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
            print("\n按客户端:")
            for p in agg["byPlatform"]:
                print(f"  {p['platform']:<14}{fmt(p['output']):>12}{fmt(p['records']):>8} 次")
            print("\n按模型:")
            for m in agg["byModel"]:
                print(f"  {m['model']:<38}{fmt(m['output']):>12}{fmt(m['records']):>8} 次")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        save()
        print(f"\n已退出,状态保存在 {state_path} (重启不会重复计数)")


def add_common(p):
    p.add_argument("--root", default=DEFAULTS["kimi-code"],
                   help="kimi-code wire.jsonl 根目录")
    p.add_argument("--claude-root", default=DEFAULTS["claude-code"],
                   help="Claude Code 日志根目录 (~/.claude/projects)")
    p.add_argument("--codex-root", default=DEFAULTS["codex"],
                   help="Codex 会话日志根目录 (~/.codex/sessions)")
    p.add_argument("--no-claude", action="store_true", help="不统计 Claude Code")
    p.add_argument("--no-codex", action="store_true", help="不统计 Codex")


def main():
    common = argparse.ArgumentParser(add_help=False)
    add_common(common)
    p = argparse.ArgumentParser(description="统计各客户端走 Kimi Code 会员的 token 用量")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stats", parents=[common], help="一次性全量统计")
    s.add_argument("--json", metavar="OUT", help="同时导出 JSON")
    s.add_argument("--csv", metavar="OUT", help="同时导出按天 CSV")
    s.set_defaults(func=cmd_stats)
    w = sub.add_parser("watch", parents=[common], help="终端实时监控")
    w.add_argument("--interval", type=int, default=2, help="刷新间隔秒数 (默认 2)")
    w.set_defaults(func=cmd_watch)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

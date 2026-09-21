#!/usr/bin/env python3
"""只读流式提取 ~/.kimi-code/sessions 下 wire.jsonl 的 usage.record 记录并汇总。"""
import json, hashlib, collections, glob, os, datetime

ROOT = os.path.expanduser("~/.kimi-code/sessions")
files = glob.glob(os.path.join(ROOT, "**", "wire.jsonl"), recursive=True)

total_lines = 0
raw_recs = []          # (file, line_no, record_dict)
other_usage_files = collections.Counter()  # 文件 -> 含 usage 但非 usage.record 的行数
fields_seen = collections.Counter()

for fp in files:
    with open(fp, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            total_lines += 1
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") == "usage.record":
                raw_recs.append((fp, i, d))
                for k in d.get("usage", {}):
                    fields_seen[k] += 1
            else:
                other_usage_files[os.path.relpath(fp, ROOT)] += 1

# 去重：同一文件内完全相同的行（含 time）视为重复记录
seen = set()
recs = []
dup = 0
for fp, i, d in raw_recs:
    h = hashlib.md5((fp + "\n" + json.dumps(d, sort_keys=True)).encode()).hexdigest()
    if h in seen:
        dup += 1
        continue
    seen.add(h)
    recs.append((fp, d))

print(f"wire.jsonl 文件总数: {len(files)}")
print(f"总行数: {total_lines}")
print(f"usage.record 原始条数（全部文件）: {len(raw_recs)}")
print(f"同文件内完全重复行: {dup}")
print(f"去重后记录数: {len(recs)}")
print(f"usage 字段分布: {dict(fields_seen)}")
print(f"含 usage 但非 usage.record 的行总数（step.end 等副本）: {sum(other_usage_files.values())}")

tot = collections.Counter()
by_model = collections.defaultdict(collections.Counter)
by_day = collections.defaultdict(collections.Counter)
by_agent = collections.defaultdict(collections.Counter)

for fp, d in recs:
    u = d["usage"]
    day = datetime.datetime.fromtimestamp(d["time"] / 1000).strftime("%Y-%m-%d")
    for k in ("inputOther", "inputCacheRead", "inputCacheCreation", "output"):
        v = u.get(k, 0)
        tot[k] += v
        by_model[d.get("model", "?")][k] += v
        by_day[day][k] += v
        by_agent[d.get("agentId", "?")][k] += v

print("\n=== 汇总（去重后） ===")
for k in ("inputOther", "inputCacheRead", "inputCacheCreation", "output"):
    print(f"{k}: {tot[k]:,}")
print(f"input 合计(不含cache): {tot['inputOther']:,}")
print(f"input 合计(含cache读): {tot['inputOther']+tot['inputCacheRead']+tot['inputCacheCreation']:,}")

print("\n=== 按模型 ===")
for m, c in sorted(by_model.items()):
    print(f"{m}: output={c['output']:,} inputOther={c['inputOther']:,} cacheRead={c['inputCacheRead']:,} 记录数={sum(1 for _,d in recs if d.get('model')==m)}")

print("\n=== 按天 ===")
for day in sorted(by_day):
    c = by_day[day]
    n = sum(1 for _, d in recs if datetime.datetime.fromtimestamp(d['time']/1000).strftime('%Y-%m-%d') == day)
    print(f"{day}: 记录={n} output={c['output']:,} inputOther={c['inputOther']:,} cacheRead={c['inputCacheRead']:,} cacheCreate={c['inputCacheCreation']:,}")

print("\n=== 按 agentId ===")
for a, c in sorted(by_agent.items()):
    print(f"{a}: output={c['output']:,} inputOther={c['inputOther']:,} cacheRead={c['inputCacheRead']:,}")

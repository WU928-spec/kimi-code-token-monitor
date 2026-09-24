import json, glob, os, hashlib, datetime, collections, urllib.request

ROOT = os.path.expanduser("~/.kimi-code/sessions")
KEYS = ("inputOther", "inputCacheRead", "inputCacheCreation", "output")
WIN = 5 * 3600 * 1000  # 5 小时窗口 (ms)
API = "https://api.kimi.com/coding/v1/usages"


def load_api_key(ctx):
    """Key 优先取 run input (x-secret), 兜底读 assets/.apikey (本机文件, 600 权限)。"""
    inp = (ctx or {}).get("input") or {}
    key = inp.get("apiKey")
    if key:
        return key
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        return open(os.path.join(here, ".apikey"), encoding="ascii").read().strip()
    except OSError:
        return None


def fetch_quota(api_key):
    """查询 Kimi Code 会员额度 (5h / 月度窗口, 官方口径)。失败返回 None。"""
    if not api_key:
        return {"error": "api key 未配置"}
    try:
        req = urllib.request.Request(API, headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "KimiCLI/1.6",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        u = d.get("usages") or {}

        def win(key):
            w = u.get(key)
            if not w:
                return None
            return {"usedRatio": w.get("used_ratio"), "resetTime": w.get("reset_time")}

        limit5h = (d.get("limits") or [{}])[0].get("detail") or {}
        booster = d.get("booster_wallet") or {}
        bal = booster.get("balance") or {}
        five = win("limit_5h") or {}
        # usages.limit_5h 在 used=0 时不带 reset_time, 用 limits.detail.resetTime 兜底
        five["resetTime"] = five.get("resetTime") or limit5h.get("resetTime")
        return {
            "fiveHour": five,
            "monthTotal": win("limit_month_total"),
            "monthCode": win("limit_month_code"),
            "fiveHourDetail": {"limit": limit5h.get("limit"), "used": limit5h.get("used")},
            "booster": {"balance": bal.get("amount"), "monthlyUsedCents": (booster.get("monthlyUsed") or {}).get("priceInCents")},
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def run(ctx):
    seen = set()
    tot = collections.Counter()
    today = collections.Counter()
    models = collections.defaultdict(collections.Counter)
    model_n = collections.Counter()
    days = collections.defaultdict(collections.Counter)
    day_n = collections.Counter()
    kimi5h = collections.defaultdict(collections.Counter)   # 按小时区间 (kimi 模型, 近48h)
    kimi5h_n = collections.Counter()
    rolling = collections.Counter()  # 当前滚动 5h (kimi 模型合计)
    rolling_n = 0
    rolling_models = collections.defaultdict(collections.Counter)  # 滚动 5h 分模型
    rolling_models_n = collections.Counter()
    kimi_recs = []  # [(time_ms, usage)] 仅 kimi 模型, 供订阅/加油包推断
    n = 0
    day = datetime.date.today().isoformat()
    now = datetime.datetime.now()
    now_ms = int(now.timestamp() * 1000)

    # Kimi 官方 5h 配额窗口: [resetTime - 5h, resetTime]
    quota = fetch_quota(load_api_key(ctx))
    win_end = None
    rt = ((quota or {}).get("fiveHour") or {}).get("resetTime")
    if rt:
        try:
            win_end = datetime.datetime.fromisoformat(str(rt).replace("Z", "+00:00")).astimezone()
        except Exception:
            win_end = None
    win_start = (win_end - datetime.timedelta(hours=5)) if win_end else None

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
                t = d["time"]
                rec_day = datetime.datetime.fromtimestamp(t / 1000).strftime("%Y-%m-%d")
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
                # --- kimi 模型的近 5h / 按小时统计 ---
                if m.startswith("kimi"):
                    dt = datetime.datetime.fromtimestamp(t / 1000)
                    slot = dt.replace(minute=0, second=0, microsecond=0)
                    label = slot.strftime("%m-%d %H:%M")
                    kimi5h_n[label] += 1
                    for k in KEYS:
                        kimi5h[label][k] += u.get(k, 0)
                    in_win = (win_start and win_end
                              and win_start.timestamp() * 1000 <= t <= win_end.timestamp() * 1000)
                    if in_win or (not win_start and now_ms - t <= WIN):
                        rolling_n += 1
                        rolling_models_n[m] += 1
                        for k in KEYS:
                            v = u.get(k, 0)
                            rolling[k] += v
                            rolling_models[m][k] += v
                    kimi_recs.append((t, u))

    # --- 订阅 / 加油包耗用推断 ---
    # 从历次运行产物重建配额比例时间线, 逐条记录按"当时 5h/月度配额是否打满"判定。
    # 订阅先扣、加油包兜底: 配额未满 -> 订阅; 已打满 -> 加油包。5 分钟粒度, 供参考。
    def parse_ms(s):
        try:
            return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() * 1000
        except Exception:
            return None

    timeline = []
    runs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "runs")
    for rj in glob.glob(os.path.join(runs_dir, "*", "run.json")):
        try:
            rd = json.load(open(rj))
            art = (rd.get("run") or {}).get("artifact") or {}
            q = art.get("quota") or {}
            ua = art.get("updatedAt")
            fh = q.get("fiveHour") or {}
            mc = q.get("monthCode") or {}
            tt = datetime.datetime.fromisoformat(ua.replace(" ", "T")).timestamp() * 1000 if ua else None
            if tt is None:
                continue
            timeline.append({"t": tt, "fh": fh.get("usedRatio"), "fhEnd": parse_ms(fh.get("resetTime")),
                             "mc": mc.get("usedRatio")})
        except Exception:
            continue
    timeline.sort(key=lambda x: x["t"])

    def classify(tt_ms):
        e = None
        for s in timeline:
            if s["t"] <= tt_ms:
                e = s
            else:
                break
        if e is None:
            return "unknown"  # 早于监控起点, 无快照可判
        fh_full = (e.get("fh") is not None and e["fh"] >= 0.999
                   and (e.get("fhEnd") is None or tt_ms <= e["fhEnd"]))
        mc_full = e.get("mc") is not None and e["mc"] >= 0.999
        return "booster" if (fh_full or mc_full) else "subscription"

    billing = collections.defaultdict(collections.Counter)          # 累计
    billing_win = collections.defaultdict(collections.Counter)      # 当前 5h 窗口
    billing_month = collections.defaultdict(collections.Counter)    # 当前会员账期
    mc_end_ms = parse_ms((((quota or {}).get("monthCode") or {}).get("resetTime")))
    mc_start_ms = mc_end_ms - 30 * 86400000 if mc_end_ms else None  # 账期约 30 天
    for t, u in kimi_recs:
        src = classify(t)
        for k in KEYS:
            v = u.get(k, 0)
            billing[src][k] += v
            if win_start and win_end and win_start.timestamp() * 1000 <= t <= win_end.timestamp() * 1000:
                billing_win[src][k] += v
            if mc_start_ms and mc_end_ms and mc_start_ms <= t <= mc_end_ms:
                billing_month[src][k] += v

    def billing_json(b):
        return {s: {"output": c["output"], "inputOther": c["inputOther"],
                    "inputCacheRead": c["inputCacheRead"]}
                for s, c in b.items()}

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
        "kimi": {
            "window5h": {
                "start": win_start.isoformat() if win_start else None,
                "end": win_end.isoformat() if win_end else None,
                "official": bool(win_end),
            },
            "rolling5h": {"inputOther": rolling["inputOther"], "inputCacheRead": rolling["inputCacheRead"],
                           "output": rolling["output"], "records": rolling_n},
            "rolling5hByModel": [{"model": m, "output": c["output"], "inputOther": c["inputOther"],
                                   "inputCacheRead": c["inputCacheRead"], "records": rolling_models_n[m]}
                                  for m, c in sorted(rolling_models.items(), key=lambda x: -x[1]["output"])],
            "byHour": [{"window": w, "output": c["output"], "inputOther": c["inputOther"],
                        "inputCacheRead": c["inputCacheRead"], "records": kimi5h_n[w]}
                       for w, c in sorted(kimi5h.items())][-48:],
            "billing": {
                "win5h": billing_json(billing_win),
                "month": billing_json(billing_month),
                "total": billing_json(billing),
            },
        },
        "quota": quota,
    }}

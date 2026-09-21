# kimi-code-token-monitor

统计并实时监控本机 Kimi Code（kimi-code CLI）的模型 token 消耗。零依赖，Python 3.8+，单文件 CLI。

![demo](demo.png)

## 快速开始

```bash
git clone https://github.com/WU928-spec/kimi-code-token-monitor.git
cd kimi-code-token-monitor

# 一次性全量统计
python3 kimi_tokens.py stats

# 终端实时监控（每 2 秒刷新，Ctrl+C 退出）
python3 kimi_tokens.py watch
```

## 命令

| 命令 | 说明 |
|---|---|
| `stats` | 全量统计：累计/今日总量、按模型、按天分布 |
| `stats --json out.json` | 额外导出 JSON（含完整结构化数据） |
| `stats --csv out.csv` | 额外导出按天 CSV |
| `watch` | 终端实时监控，状态落盘，重启不重复计数 |
| `watch --interval 5` | 自定义刷新间隔（秒） |
| `--root PATH` | 任意命令可用，指定 wire.jsonl 根目录（默认 `~/.kimi-code/sessions`） |

示例：

```bash
python3 kimi_tokens.py stats --json report.json --csv daily.csv
python3 kimi_tokens.py --root /path/to/sessions stats
```

## 数据原理

kimi-code 在 `~/.kimi-code/sessions/<工作区>/<会话>/agents/<agent>/wire.jsonl` 中记录每次模型响应的用量，每行一条 `usage.record`：

```json
{"type":"usage.record","agentId":"main","model":"kimi-code/k3",
 "usage":{"inputOther":1245,"output":1958,"inputCacheRead":19200,"inputCacheCreation":0},
 "usageScope":"turn","time":1789891750109}
```

- 只有 `usage.record` 是准确实测值；`step.end` 事件里的 usage 是它的副本，统计时已排除
- 每个 wire.jsonl 只记录自己的 agentId，不存在跨 agent 文件重复；按行内容 hash 全局去重，重复运行/重启不会重复计数
- 字段含义：`inputOther` 非缓存输入 / `inputCacheRead` 缓存命中读取 / `inputCacheCreation` 缓存写入（目前恒为 0）/ `output` 输出

## 文件

- `kimi_tokens.py` — 唯一入口，含 `stats` / `watch` 两个子命令
- `widget_automation.py` — [Kimi Work](https://www.kimi.com) Blueprint Automation 采集脚本，供看板组件每 5 分钟拉取数据，非独立运行

## 注意事项

- 只统计**本机**终端转录中的用量；账号额度是多端共享的，其他机器上的消耗不在其中
- 转录中没有 `usages` 配额快照（`used_ratio` 等），无法从本地推算账号剩余额度
- 旧会话被清理后用量记录会消失，统计反映的是现存转录
- `watch` 的状态文件 `.kimi_tokens_state.json` 写在运行目录，建议固定在一个目录下运行

## License

[MIT](LICENSE)

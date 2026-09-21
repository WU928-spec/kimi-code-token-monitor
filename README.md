# kimi-code-token-monitor

统计并实时监控本机 Kimi Code（kimi-code CLI）的模型 token 消耗。

## 原理

kimi-code 会在 `~/.kimi-code/sessions/<工作区>/<会话>/agents/<agent>/wire.jsonl` 中记录每次模型响应的用量，每行一条 `usage.record`：

```json
{"type":"usage.record","agentId":"main","model":"kimi-code/k3",
 "usage":{"inputOther":1245,"output":1958,"inputCacheRead":19200,"inputCacheCreation":0},
 "usageScope":"turn","time":1789891750109}
```

- 只有 `usage.record` 是准确实测值；`step.end` 事件里的 usage 是它的副本，统计时必须排除
- 每个 wire.jsonl 只记录自己的 agentId，不存在跨 agent 文件重复；按行内容 hash 去重即可保证幂等
- 字段含义：`inputOther` 非缓存输入 / `inputCacheRead` 缓存命中读取 / `inputCacheCreation` 缓存写入（目前恒为 0）/ `output` 输出

## 文件

- `extract_token_usage.py` — 一次性全量统计：输出总量、按模型、按天分布（只读流式扫描）
- `kimi_token_monitor.py` — 终端实时监控：每 2 秒刷新，状态落盘 `.token_monitor_state.json`，重启不重复计数
- `widget_automation.py` — Kimi Work Widget / Blueprint Automation 采集脚本：每 5 分钟汇总并推送结构化数据给组件，全局行 hash 去重、天然幂等

## 用法

```bash
# 全量统计
python3 extract_token_usage.py

# 终端实时监控（Ctrl+C 退出）
python3 kimi_token_monitor.py
```

## 注意事项

- 只统计**本机**终端转录中的用量；账号额度是多端共享的，其他机器上的消耗不在其中
- 转录中无 `usages` 配额快照（`used_ratio` 等），无法从本地推算账号剩余额度
- 旧会话被清理后用量记录会消失，统计反映的是现存转录

# kimi-code-token-monitor

> **English** · [中文](README.zh.md)

Track token usage against your **Kimi Code membership** across three local clients — zero-dependency, Python 3.8+, single-file CLI.

![demo](demo.png)

## Quick start

```bash
git clone https://github.com/WU928-spec/kimi-code-token-monitor.git
cd kimi-code-token-monitor

# One-shot full statistics (all three clients)
python3 kimi_tokens.py stats

# Live terminal monitor (refresh every 2s, Ctrl+C to quit)
python3 kimi_tokens.py watch
```

## Data sources

| Client | Log location | Format | Filter |
|---|---|---|---|
| kimi-code | `$KIMI_CODE_HOME` (default `~/.kimi-code`) `/sessions/**/wire.jsonl` | `usage.record` | everything |
| claude-code | `$CLAUDE_CONFIG_DIR` (default `~/.claude`) `/projects/**/*.jsonl` | Anthropic `usage` | only requests whose `model` contains kimi |
| codex | `$CODEX_HOME` (default `~/.codex`) `/**/*.jsonl` (incl. `archived_sessions`) | `last_token_usage` / `total_token_usage` / `token_usage` | only sessions running a kimi model |

When you drive `kimi-for-coding` through Claude Code or Codex CLI, it still bills your Kimi Code membership — this tool counts that usage too.

## Commands

| Command | Description |
|---|---|
| `stats` | Full stats: cumulative/today totals, by client, by model, by day |
| `stats --json out.json` | Also export structured JSON |
| `stats --csv out.csv` | Also export per-day CSV |
| `watch` | Live terminal monitor with on-disk state — restarts never double-count |
| `watch --interval 5` | Custom refresh interval (seconds) |
| `--root PATH` | kimi-code logs root |
| `--claude-root PATH` / `--codex-root PATH` | Other clients' log roots |
| `--no-claude` / `--no-codex` | Disable a client |

```bash
python3 kimi_tokens.py stats --json report.json --csv daily.csv
python3 kimi_tokens.py stats --root /path/to/.kimi-code/sessions
```

## How it works & deduplication

kimi-code's `usage.record` lines carry the real per-response usage (`inputOther` non-cached input / `inputCacheRead` cache hits / `inputCacheCreation` cache writes / `output`). The `usage` embedded in `step.end` events is a duplicate copy and is excluded.

- **Global line-hash dedup**: re-runs, restarts, and byte-identical copies of one request across files are counted once
- **claude-code**: additionally deduped by `message.id` (the same assistant message can be written twice); `cache_creation` supports the `ephemeral_5m/1h` split fields
- **codex**: `total_token_usage` is a **session-cumulative** counter, so deltas are taken against a running baseline; any shrinking field is treated as a counter reset (compaction / new window); zero-delta rows are bookkeeping noise and skipped

Parser design informed by [kimi-builders/usage](https://github.com/kimi-builders/usage) (cumulative baselines, ephemeral cache fields, env-var path resolution).

## Files

- `kimi_tokens.py` — single entry point with `stats` / `watch` subcommands
- `widget_automation.py` — collector script for [Kimi Work](https://www.kimi.com) Blueprint Automation (powers a dashboard widget); not meant to run standalone

## Caveats

- Only counts usage present in **local** logs; the membership quota is account-wide, so usage on other machines is not included
- Transcripts contain no quota snapshot (`used_ratio` etc.) — remaining account quota cannot be derived locally
- Cleaned-up sessions are gone from the stats; numbers reflect surviving logs
- Older codex rollout files (before ~2026-05) contain no usage fields at all and cannot be counted
- `watch` writes its state file `.kimi_tokens_state.json` to the current directory — pick one directory and stick with it

## License

[MIT](LICENSE)

# Architecture — Claude Usage Monitor v2

Internal design doc for agents and contributors. The public overview is the README;
working rules are in [`../AGENTS.md`](../AGENTS.md). Epic: #2.

## Layers

```
usage_monitor/            the application package
  paths.py                every filesystem location (app data, Claude dirs, bundled resources)
  log.py                  rotating file log in %APPDATA%\ClaudeUsageMonitor\monitor.log
  settings.py             persisted settings: defaults, v1 migration, debounced atomic save
  data/                   ── data layer (Phase 1). No UI imports, ever.
    models.py             shared dataclasses — THE contract between modules
    pricing.py + pricing.json
    transcripts.py        tolerant JSONL parser → TranscriptItems
    store.py              SQLite (WAL) store: dedupe, rollups, snapshots
    ingest.py             incremental transcript ingest into the store
    usage_api.py          credentials + GET /api/oauth/usage client + parser
    windows.py            time windows + burn rate
    service.py            DataService: the one poller; publishes MonitorState
  (Phase 2) data/analytics.py      cost/value, efficiency, pacing, history queries
  (Phase 3) ui/                    Win32 layered-window widgets, tray, dashboard host
  (Phase 3) render/                Pillow renderers, one skin per theme
  (Phase 3) themes/                theme definitions (tokens + variants)
  (Phase 3) webui/                 dashboard HTML/CSS/JS served by the local API
main.py                   entry point
tests/                    pytest; synthetic fixtures only
```

v1.2 (Phase 1 exit) keeps the v1 Tk UI (`widget.py`, `options.py`, `tray.py`, `theme.py`,
`win_utils.py`) but feeds it from `DataService`. `usage_reader.py` is retired.

## Data-layer rules (apply to every module in `usage_monitor/data/`)

1. **Privacy.** Never store or log message text, prompts, tool inputs/outputs, file contents
   or the OAuth token. Allowed: ids, model ids, token counts, timestamps, project slug, cwd,
   git branch, session titles (`ai-title` / `custom-title`), tool *names*, line counts, PR URLs.
2. **Tolerance.** The transcript format "is internal to Claude Code and changes between
   versions" (Claude Code docs). Parsers never raise on bad input: skip, count, log at debug.
   Every field is optional.
3. **No model calls.** The monitor never runs `claude` and never sends a model request.
4. **No silent swallowing.** Catch broadly at thread boundaries only, and log what was caught
   via `usage_monitor.log.get_logger(__name__)`.
5. **Time.** Store UTC epoch seconds (`REAL`). Convert to local time only for window
   boundaries and display. All `datetime` objects are timezone-aware.
6. **Money words.** Cost from transcripts is *API-equivalent at list prices*, never "spend".
   Real money (usage credits) only comes from the usage API's `spend`.
7. **Testable.** No hidden globals: paths, clock, HTTP opener and pricing are injectable.

## Module contracts

### `paths.py`
```python
app_data_dir() -> Path          # %APPDATA%\ClaudeUsageMonitor (created on demand)
claude_dir() -> Path            # $CLAUDE_CONFIG_DIR or ~/.claude
projects_dirs() -> list[Path]   # existing ones among claude_dir()/projects, ~/.config/claude/projects
credentials_path() -> Path      # claude_dir()/.credentials.json
settings_path() -> Path         # app_data_dir()/settings.json  (same file v1 used)
db_path() -> Path               # app_data_dir()/usage.db
log_path() -> Path              # app_data_dir()/monitor.log
resource_dir() -> Path          # frozen: sys._MEIPASS; source: repo root
```

### `log.py`
`setup_logging(level=logging.INFO)` installs a `RotatingFileHandler` (1 MB × 3) on the
`usage_monitor` logger, idempotently. `get_logger(name)` returns a child logger.

### `settings.py`
```python
class Settings:
    def __init__(self, path: Path | None = None, save_delay_s: float = 0.75): ...
    def get(self, key, default=None); def __getitem__(self, key)
    def set(self, key, value) -> None        # notifies listeners, schedules a debounced save
    def update(self, mapping: dict) -> None
    def on_change(self, cb) -> Callable[[], None]   # cb(key, value); returns unsubscribe
    def save_now(self) -> None               # synchronous atomic write (tmp + os.replace)
    def as_dict(self) -> dict                # persistable copy
```
Keys starting with `_` are runtime-only and never persisted (v1 convention). Unknown keys
are preserved. Thread-safe. v1 files load as-is (same path, same keys) and gain
`settings_version: 2` plus new defaults: `billing_day` (None), `plan_price_override` (None),
`ingest_interval_s` (60). v1 `poll_interval_ms` keeps working.
Value rules: `ingest_interval_s` in [1, 86400], `poll_interval_ms` in [1000, 86400000];
`set()` raises `ValueError` for a bad value and `load` falls back to the default. A change
made inside a listener is queued and delivered after the current one, so every listener sees
changes in apply order. An unreadable (as opposed to missing) settings file is never
overwritten: the object runs on defaults, blocks saves and retries the read on each save,
then merges the file's values under the changes made meanwhile.

### `data/pricing.py` + `pricing.json`
```python
class Pricing:
    version: str
    @classmethod load(cls, path: Path | None = None) -> Pricing   # default: bundled pricing.json
    @classmethod from_dict(cls, data: dict) -> Pricing          # validates; raises ValueError
    def match(self, model: str) -> PriceRow | None
    def display_name(self, model: str) -> str                   # "Opus 5.5"; raw id when unknown
    def cost(self, rec: RequestRecord) -> CostBreakdown
def load_best(bundled: Path, cached: Path) -> Pricing           # newer `version` wins; bad cache ignored
def refresh_remote(url: str, cached: Path, timeout: float = 10, opener=...) -> bool
```
- Longest-prefix match on the lowercased id after stripping provider prefixes
  (`anthropic.`, `us.anthropic.`, …), a trailing `-YYYYMMDD` date, `@date` and `[1m]` suffixes.
  `claude-opus-4-7` must never match the `claude-opus-4` row.
- Cache writes = input rate × `cache_write_multipliers` (`5m` 1.25, `1h` 2.0).
  Cache reads use the row's own `cache_read` rate (not a fixed 0.1×).
- `long_context` tier (e.g. Haiku 5.5 above 100K prompt tokens): when
  `rec.prompt_tokens > threshold_tokens`, use the tier's input/output/cache_read rates.
- `speed == "fast"` multiplies every component by the row's `fast_multiplier` (when set).
- `<synthetic>` and unknown models → `CostBreakdown(priced=False)` with zeros.
- `nocache_total` = (input + cache_read + cache_write) × input rate + output × output rate
  (same tier and fast multiplier as the real cost).

### `data/transcripts.py`
```python
file_context(path: Path, projects_root: Path) -> FileContext
parse_line(line: str, ctx: FileContext) -> list[TranscriptItem]      # never raises
read_new(path: Path, offset: int, ctx: FileContext) -> tuple[list[TranscriptItem], int]
```
`read_new` reads from `offset` to the last complete line and returns the new offset (a line
boundary). It raises `OSError` when the file cannot be opened or read to its end (the
ingestor then keeps the bookmark and commits nothing); `FileNotFoundError` for a file that
vanished after the directory walk is skipped quietly. Counts are clamped to 2**53 and lone
surrogates replaced, so odd values do not block a file. Timestamps before 1970 are rejected.
A cheap substring prefilter skips lines that can't produce an item; it must be
proven equivalent to a full parse on real data.

| Record | Item |
|---|---|
| `type:"assistant"` with `message.usage` (model ≠ `<synthetic>`) | `RequestRecord` — key `"<message.id>\|<requestId>"`, fallback `"uuid\|<record uuid>"` |
| assistant `message.content[]` blocks `type:"tool_use"` | `ToolCall` (key `tool_use_id`) |
| user record `toolUseResult.structuredPatch` (+ / − lines) or `toolUseResult.type=="create"` with `content` | `CodeChange` (key: the user record's `tool_result.tool_use_id`) |
| `toolUseResult.gitOperation` (`commit` / `push` / `pr` keys) | `GitEvent` |
| `type:"pr-link"` | `PullRequest` (key `prUrl`) |
| `type:"ai-title"` / `"custom-title"` | `SessionMeta` |
| `type:"system"`, `subtype:"compact_boundary"` | `Compaction` (`compactMetadata.trigger/preTokens/postTokens`) |
| `type:"system"`, `subtype:"api_error"` | `ApiErrorEvent` |

Usage fields: `input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` (fallback: all of
`cache_creation_input_tokens` as 5m), `output_tokens_details.thinking_tokens`,
`server_tool_use.web_search_requests` / `web_fetch_requests`, `speed`, `service_tier`.
Record-level: `sessionId`, `cwd`, `gitBranch`, `effort`, `entrypoint`, `version`, `timestamp`.
`usage.iterations[]` is per-iteration detail — never add it on top of the top-level usage.

### `data/store.py`
```python
class Store:
    def __init__(self, path: Path | str): ...          # ":memory:" for tests; WAL; migrations
    def close(self)
    # ingest bookkeeping
    def file_state(self, path: str) -> FileState | None
    def save_file_state(self, st: FileState) -> None
    def known_files(self) -> list[str]
    def forget_file(self, path: str) -> None           # keep its data; drop the bookmark
    # writes
    def add_items(self, items: Iterable[TranscriptItem], pricing: Pricing) -> dict[str, int]
    def reprice(self, pricing: Pricing) -> int          # recompute every request's cost
    def pricing_version(self) -> str | None
    def save_usage_snapshot(self, snap: UsageSnapshot) -> None
    def latest_usage_raw(self) -> tuple[datetime, dict] | None
    # reads
    def totals(self, start: datetime | None, end: datetime | None, *, subagent: bool | None = None) -> TokenTotals
    def totals_by_model(self, start: datetime | None, end: datetime | None) -> dict[str, TokenTotals]
    def first_request_ts(self) -> datetime | None
    def unpriced_models(self) -> list[str]
    def recent_requests(self, since: datetime) -> list[tuple[datetime, int, float]]   # (ts, output_tokens, cost)
    def usage_snapshots(self, since: datetime) -> list[dict]
```
- `requests` has one row per key. Merging a duplicate takes the **max** of every token field,
  fills missing metadata, and recomputes cost from the merged tokens (in Python).
- Cost columns are stored per row along with `pricing_version`; `reprice` rewrites them.
- Other items upsert by their dedupe key; `SessionMeta` keeps the latest non-empty titles.
- `usage_snapshots` keeps compact numbers per fetch (session/weekly percent + resets + a
  compact JSON of all limits); the latest full raw response lives in `meta`. Snapshots older
  than 180 days are pruned.
- Schema version 2 (adds `file_state.tail`) through a forward migration. Token sums are
  computed as REAL (no int64 overflow). `add_items` binds the batch first and only falls back
  to row-by-row inside a savepoint when SQLite refuses a value, logging the skipped rows.
- One connection per `Store`, guarded by a lock. Readers in other threads open their own
  `Store` on the same file (WAL makes that safe).

### `data/ingest.py`
```python
class Ingestor:
    def __init__(self, store: Store, pricing: Pricing, roots: list[Path] | None = None): ...
    def run(self, progress: Callable[[int, int], None] | None = None) -> IngestResult
```
New file → read from 0. Grown → read appended bytes, unless the 4 KiB before the bookmark no longer hash to `FileState.tail` (rewritten → read from 0). Same size + mtime → skip without opening.
Smaller than the bookmark → re-read from 0 (dedupe makes it safe). Missing → `forget_file`.
Commit per file so an interrupted backfill resumes. Targets: no-change pass < 0.5 s for 4K
files; 2 GB backfill < 60 s.

### `data/usage_api.py`
```python
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
read_credentials(path: Path | None = None) -> tuple[CredentialsInfo, str | None]   # token never logged
inspect_credentials(path: Path | None = None) -> tuple[CredentialsInfo, str | None, bool]  # + readable
plan_from(subscription_type: str | None, rate_limit_tier: str | None) -> PlanInfo
parse_usage(raw: dict, fetched_at: datetime) -> UsageSnapshot                      # never raises
class UsageClient:
    def __init__(self, credentials_path: Path | None = None, *, timeout: float = 10, opener=None, user_agent: str = ...): ...
    def fetch(self, now: datetime) -> FetchResult      # never raises; honours backoff
    status: ApiStatus; last_snapshot: UsageSnapshot | None; last_ok_at: datetime | None; next_allowed_at: datetime | None
```
- Expired token (`expiresAt` past) → `STALE` without calling the API. 401/403 → `STALE`;
  don't retry until `.credentials.json` changes (mtime). 429 → `RATE_LIMITED`, honour
  `Retry-After`, else exponential backoff 60 s → 15 min. Network / 5xx → `OFFLINE`,
  backoff 30 s → 15 min. 200 → `LIVE`, backoff reset.
- Never writes to `.credentials.json`. A readable file is authoritative (missing sign-in →
  "Unknown plan"); an unreadable or half-written one keeps the current plan and backoff.
- Redirects are refused (a 3xx is an error → `OFFLINE`), so the bearer token never follows a
  redirect. Response bodies are read through `data/netread.read_capped` (size cap plus a 10 s
  total time budget); a trickling body → `OFFLINE` "timed out". Pricing downloads use the same.
- `parse_usage` prefers `limits[]`, attaches `weekly_scoped_shares` by `limit_index`, maps
  legacy `five_hour` / `seven_day` onto session / weekly (`locked_reason`, `*_dollars`), and
  falls back to legacy windows when `limits[]` is absent. Null codename windows are ignored.
- Plans: `max` + `*_max_5x` → "Max 5x" $100; `*_max_20x` → "Max 20x" $200; `pro` → "Pro" $20;
  others → name from the type, price `None` (user can override in settings).

### `data/windows.py`
```python
today(now) / week(now, usage) / month(now) / cycle(now, billing_day) / session(now, usage, recent_ts)
    -> tuple[datetime, datetime] | None      # UTC start/end
burn_rate(recent: list[tuple[datetime, int, float]], now: datetime, window_minutes: int = 30) -> BurnRate
```
`now` is aware. Day/month/cycle boundaries are local midnight. Week = the weekly limit's
window `[resets_at − 7 d, resets_at)` when known, else the last 7 days. Session = the session
limit's window `[resets_at − 5 h, resets_at)` when known, else a 5-hour block: a block starts
at the first request and at any request at or past the open block's end, floored to the hour. Velocity from output tok/min:
0 IDLE, <500 SLOW, <1500 NORMAL, <3000 FAST, else VERY FAST.

### `data/service.py`
```python
class DataService:
    def __init__(self, settings: Settings, *, store_path=None, pricing=None, usage_client=None,
                 roots=None, pause_check=lambda: False, clock=None): ...
    def start(self) -> None
    def stop(self, timeout: float = 5.0) -> None
    def subscribe(self, cb: Callable[[MonitorState], None]) -> Callable[[], None]
    def refresh_now(self) -> None
    state: MonitorState | None
```
One background thread. Each cycle: ingest (every `ingest_interval_s`, default 60) → API fetch
when due (`poll_interval_ms`, min 60 s, subject to client backoff; immediately when the
credentials file changes) → snapshot saved → windows + burn computed → `MonitorState`
published to subscribers **on the service thread** (UIs marshal to their own thread).
Every `MonitorState` carries the `session`, `today`, `week`, `month` and `all` keys (empty
until computed); each window is computed separately and a failing one keeps its last value.
After a failed fetch the next one is due at the client's `next_allowed_at`. Intervals are
clamped to one day; a cycle that raises waits `ERROR_RETRY_S` (60 s).
`pause_check()` true → status `PAUSED`, no work (the credentials file is still watched). Reprices the store when the pricing
version changes; refreshes remote pricing at most daily.

## Phase 3 target (v2.0) — summary

- **Widgets:** a Win32 UI thread hosts layered windows (`WS_EX_LAYERED`, `UpdateLayeredWindow`
  with premultiplied BGRA). Pillow renders each frame at 2× and downsamples. Redraw only on
  data change or animation ticks.
- **Dashboard:** the main process serves `webui/` + a JSON API on `127.0.0.1:<random port>`
  with a per-run token. A separate `--dashboard` process opens it in pywebview (WebView2),
  so it costs nothing while closed and a normal browser can load the same URL for testing.
- **Themes:** `themes/<name>.json` (tokens + variants) read by both the Pillow skins and the
  dashboard CSS. Nostromo (default), Night City, Gates.

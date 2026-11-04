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
   git branch, session titles (`ai-title` / `custom-title`), tool *names*, line counts, PR URLs,
   and the git verbs (`commit` / `push` / `pr`) a shell command ran, never the command itself.
2. **Tolerance.** The transcript format "is internal to Claude Code and changes between
   versions" (Claude Code docs). Parsers never raise on bad input: skip, count, log at debug.
   Every field is optional.
3. **No model calls.** The monitor never sends a model request and never runs `claude -p`. The
   only `claude` commands it runs are in `agent/setup.py`, the usage guard's one-click setup:
   `claude --version` and `claude mcp get` (read-only) for the Settings card, and `claude mcp add`
   / `claude mcp remove` when the user clicks Install or Remove.
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
are preserved; the monitor itself keeps one, `notified_version` (the newest release it has put an update toast
up for, see UI_ARCHITECTURE.md "Updates"), which the settings API does not expose. Thread-safe. v1 files load as-is (same path, same keys) and gain
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
vanished after the directory walk is skipped quietly (one that is still there is reported in
`IngestResult.errors`). The file is opened through `paths.fs_path`, so a path of 260 or more
characters (Windows keeps that limit for `open()` unless `LongPathsEnabled`) reads like any other.
Counts are clamped to 2**53 and lone
surrogates replaced, so odd values do not block a file. Timestamps before 1970 are rejected.
A cheap substring prefilter skips lines that can't produce an item; it must be
proven equivalent to a full parse on real data.

| Record | Item |
|---|---|
| `type:"assistant"` with `message.usage` (model ≠ `<synthetic>`) | `RequestRecord` — key `"<message.id>\|<requestId>"`, fallback `"uuid\|<record uuid>"` |
| assistant `message.content[]` blocks `type:"tool_use"` | `ToolCall` (key `tool_use_id`); `git_ops` for `Bash` / `PowerShell` |
| user `message.content[]` blocks `type:"tool_result"` with `is_error: true` | `ToolError` (key `tool_use_id`) |
| user record `toolUseResult.structuredPatch` (+ / − lines) or `toolUseResult.type=="create"` with `content` | `CodeChange` (key: the user record's `tool_result.tool_use_id`) |
| `toolUseResult.gitOperation` (`commit` / `push` / `pr` keys) | `GitEvent` |
| `type:"pr-link"` | `PullRequest` (key `prUrl`) |
| `type:"ai-title"` / `"custom-title"` | `SessionMeta` |
| `type:"system"`, `subtype:"compact_boundary"` | `Compaction` (`compactMetadata.trigger/preTokens/postTokens`) |
| `type:"system"`, `subtype:"api_error"` | `ApiErrorEvent` |

Git activity from shell commands. Since Claude Code 2.1.284 a commit no longer writes
`toolUseResult.gitOperation` (only `pr` and `push` still appear), so for `tool_use` blocks named
`Bash` or `PowerShell` whose `input.command` is a string the parser sets `ToolCall.git_ops`:
`"commit"`, `"push"` and `"pr"` joined with `+` in that order, else `None`. `git_ops(command, tool)`
takes the tool name because the quoting rules differ. Detection runs on a copy of the command
blanked by one linear left-to-right pass (a `<<` or an apostrophe inside a string or a heredoc
body means nothing, so the constructs cannot be blanked one kind after the other):
- a heredoc body is the text after a `<<`, `<<-` or `<<~` marker up to the line equal to the word.
  The word is any run without whitespace or shell operators, bare, `'quoted'`, `"quoted"` or
  `\escaped` (`END-OF-FILE`, `EOF.1`). A marker with no closing line is not a heredoc (so
  `$((1 << n))` is not), and a `<<` inside a quoted string is never a marker. `<<<` is a here-string.
- a quoted string loses its contents but keeps its quotes. Bash: `\` escapes inside `"..."`.
  PowerShell: a backtick escapes inside `"..."`, a backslash is an ordinary character (so
  `git -C "D:\repo\" commit` works), and a doubled quote (`''`, `""`) stays inside the string.
  `$( ... )` inside `"..."` is scanned with the same rules, so quotes in a heredoc body there cannot
  unbalance the string. A `"` with no closing quote is plain text.
- PowerShell here-strings `@'...'@` and `@"..."@` are blanked whole, whatever apostrophes the body holds.
- a `#` comment is dropped, so an apostrophe in a comment cannot open a string.
- a quoted path ending in `\git.exe`, `/git.exe` or `/git` becomes the bare word `git`, so
  `& "C:\Program Files\Git\cmd\git.exe" commit` counts.

Then:
- commit: `\bgit(?:\.exe)?\b(?:\s+(?:-[cC]\s+VALUE|(?!-[cC]\b)--?[A-Za-z][\w-]*(?:=\S+)?))*\s+commit(?![\w-])`,
  with `VALUE` = `"[^"]*"`, `'[^']*'` or a word not starting with a quote. The branches cannot both
  match one option, so a failed match costs linear time, not 2 to the power of the option count.
- push: the same with `push`
- pr: `\bgh\s+pr\s+create\b`

So `git -C "C:/repo" commit` and `git -c user.name=x commit` count, while `git log --grep commit`,
`git commit-tree`, `echo "git push"` and a commit message that mentions `git push` do not.
Known limits: a git command that only runs inside a script written to a file by a heredoc is not
a commit; `--dry-run` still counts. A `tool_result` block with `is_error: true` yields a
`ToolError` whether or not the record has a `toolUseResult`. The prefilter has the two spellings
of `"is_error":true` for it.

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
    def query(self, sql: str, params: Sequence = ()) -> list[sqlite3.Row]   # the read path for analytics
```
- `requests` has one row per key. Merging a duplicate takes the **max** of every token field,
  fills missing metadata, and recomputes cost from the merged tokens (in Python).
- Cost columns are stored per row along with `pricing_version`; `reprice` rewrites them.
- Other items upsert by their dedupe key; `SessionMeta` keeps the latest non-empty titles.
- `usage_snapshots` keeps compact numbers per fetch (session/weekly percent + resets + a
  compact JSON of all limits); the latest full raw response lives in `meta`. Snapshots older
  than 180 days are pruned.
- `query` runs exactly one statement, which must be a `SELECT` or `WITH` (leading whitespace and
  comments allowed); anything else, or a second statement, raises `ValueError`. It runs with
  `PRAGMA query_only` on, so a `WITH ... DELETE` fails instead of writing.
- Schema version 3 through forward migrations: 2 adds `file_state.tail`; 3 adds
  `tool_calls.git_ops TEXT` and the table `tool_errors(tool_use_id TEXT PRIMARY KEY, session_id,
  ts REAL NOT NULL)` with a `ts` index. The 2 to 3 migration also deletes every `file_state` row,
  so the next ingest re-reads all transcripts (dedupe keeps that safe) and back-fills `git_ops`
  and `tool_errors`. The `tool_calls` insert is an upsert: a repeat sighting changes nothing
  except to fill `git_ops` when the stored value is NULL (a stored value is never replaced, so a
  later change to the detection rule needs a re-ingest into a fresh database). Token sums are
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
Paths: bookmarks are keyed by the plain absolute path; every directory listing and read goes
through `paths.fs_path` (the Win32 extended-length form, `\\?\C:\...`), because Claude Code (Node)
writes subagent transcripts at 260+ characters that `open()` and `os.stat()` cannot reach by their
plain name. A file the walk listed that still will not open is an entry in `IngestResult.errors`
(Data health shows it), never a silent skip.

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
- `parse_usage` rounds every `resets_at` to the nearest minute (the API jitters the same
  window end by up to a second between fetches).
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
                 roots=None, pause_check=lambda: False, clock=None, pricing_refresh=None,
                 status_dir=None): ...
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
With `status_dir` (the app passes the app data folder), each finished cycle also refreshes
`state.json` there for Claude sessions (`agent/status.py`, #24): the limits with their pace from
`pacing_report`, the burn rate and the plan, never the token. It is rewritten only when its
`fingerprint` changes (the reading without the fields that move with the clock alone, and the burn
rate reduced to its velocity), and otherwise every `REFRESH_S` (15 min). A paused cycle writes it
once per pause. While `agent-demand` in the same folder is younger than an hour (readers renew it, at most every 5 min),
`pause_check()` is overridden and the cycles run on a locked screen, which is when unattended
runs happen. A failed write is logged once per kind of failure and never stops the cycle.
Each cycle also evaluates the usage guard (`agent/guard.py`, #27) into `MonitorState.guard`, and
`state.json` carries it with the policy it was evaluated against. A limit whose window reset after
the last reading brings the next fetch forward: the first cycle at least `RESET_FETCH_DELAY` (30 s)
after the reset fetches, about a minute after it with the 60 s cycle, instead of waiting for the
poll interval. Waiting sessions do not wait for it: each reader evaluates the guard again, and a
window whose reset has passed no longer counts.

### `agent/` (#24, #27)
`status.py` builds, writes and reads `state.json` (schema 1) and makes the one-line summary.
`guard.py` is the usage guard's verdict (off, armed or holding, the limits over their caps and when
work can resume), pure and evaluated again by each reader, because a window may have reset since
the reading. `sessions.py` owns the files the guard's processes share: who was told
(`hook-seen.json`, read and rewritten under the byte-range lock `hook-seen.lock`, since parallel
subagents run their hooks at once), who waits (`waiting/<pid>.json`, with the process's creation
time, so a file left by a killed wait whose id Windows gave to another process counts for nothing
and is removed), when the last wait was let go (`wait-released`: waits go oldest first, a stagger
apart) and the `resume-now` marker.
`cli.py` holds `--status [--json]`, `--hook` (which on PostToolUse also tells a working session once
per holding episode that a cap was reached), `--gate` and `--wait` (a background command that ends
when the guard lets work resume, which wakes its session), and `mcp.py` a stdio MCP server whose one tool,
`get_plan_usage`, returns the summary and the reading. They run as light processes beside the app:
no UI imports, no single-instance mutex, logs in `agent.log`. The app is the only poller; a
reader asks the usage API itself only when the reading is missing or stale (the app is closed,
or an older version writes no file), and keeps that answer in `state.json` for two minutes so
the readers in that time share it. A reader that must ask
first takes a byte-range lock on `state.lock` (Windows frees it when its holder is killed), re-reads
the file under it, and never writes a reading older than the one on disk. `--hook` never fetches
except at `SessionStart`, and `--hook` and `--gate` exit 0 on every path. The guard only informs:
nothing in it blocks a tool or stops a session. `setup.py` is its one-click setup, run by the
Settings card through the local API: it backs up Claude Code's user settings and writes the three
`--hook` entries, and registers the MCP tool with `claude mcp add`. It knows its own entries by the
exe's file name and `--hook`, so a moved install is replaced rather than doubled; it refuses when the
file changed since it was read or holds what strict JSON cannot write back; and a `claude` that runs
past its timeout is ended with its whole process tree (an npm `claude.cmd` leaves node.exe holding
the pipes). It runs only in the installed app, never in a dev instance. How to wire it into Claude
Code: [`AGENT_HOOK.md`](AGENT_HOOK.md).

## Phase 2 — stats (#11–#14)

Read-only reports for the dashboard (`GET /api/analytics/<name>`, `GET /api/export`). They run
on the server's own `Store` (WAL), never on the service thread, and never write.

### `data/analytics.py`
```python
WINDOW_NAMES = ("session", "today", "week", "month", "cycle", "all")
def resolve_window(name: str, store: Store, *, now: datetime, usage: UsageSnapshot | None,
                   billing_day: int | None, tz: tzinfo | None = None) -> tuple[datetime | None, datetime | None]
def project_name(project: str, cwd: str | None) -> str     # basename of cwd, else the slug
def cost_report(store, pricing, *, now, usage, plan: PlanInfo, plan_price: float | None,
                billing_day: int | None, breakdown_window: str = "cycle", tz=None) -> dict
def efficiency_report(store, *, start, end, tz: tzinfo | None = None) -> dict   # tz only sets how active_days counts local days
def pacing_report(store, *, now, usage: UsageSnapshot | None) -> dict
def history_report(store, *, now, tz=None, days: int = 371) -> dict
def sessions_report(store, pricing, *, start, end, limit: int = 25) -> dict
def daily_rollups(store, *, tz=None, start=None, end=None) -> list[dict]
def export_csv(rows: list[dict]) -> str
def export_json(rows: list[dict]) -> str
```
Shared rules:
- Output is JSON-ready: str / int / float / bool / None / list / dict only. Datetimes are
  ISO-8601 UTC strings ending in `Z`; local calendar dates are `"YYYY-MM-DD"`. Money is a USD
  float (not rounded); tokens are int. Every report that shows cost carries
  `"basis": "API-equivalent at list prices"`.
- Windows come from `data/windows.py` (`resolve_window`): `session` and `week` follow the
  usage API windows when known; a `session` with no API window falls back to the 5-hour block
  holding `now`, and is `(now, now)` when no block does; `cycle` = `windows.cycle(now,
  billing_day)` (calendar month when `billing_day` is None); `all` = (None, None). Unknown name →
  `ValueError`.
- Local days and hours use `tz` (None = the machine's local zone) and must be DST-correct:
  aggregate in SQL into 900-second UTC buckets, convert buckets to local time in Python.
- SQL lives in `analytics.py` and goes through `Store.query(sql, params=()) -> list[sqlite3.Row]`
  (new public read-only helper). Each report must finish in < 300 ms on 70K requests /
  90K tool calls; no Python loop over every request row unless a statistic needs it.
- `project_name`: group by `requests.project`; the display name is the basename of the most
  common `cwd` for that project, else the slug.

`cost_report` → `{basis, windows: {name: W}, plan, value, monthly, breakdowns, top_sessions_week, spend}`
- `W` = `{start, end, requests, tokens: {input, output, cache_read, cache_write, thinking, total},
  cost_usd, nocache_cost_usd, unpriced_requests}` for every name in `WINDOW_NAMES`.
  `tokens.total` = input + output + cache read + cache write; it excludes `thinking`, which is
  already part of output and is reported separately.
- `plan` = `{name, subscription_type, rate_limit_tier, price_usd, price_source}`; `price_usd`
  = `plan_price` (source `"override"`), else `plan.monthly_price_usd` (`"detected"`), else None.
- `value` = `{cycle_start, cycle_end, cycle_cost_usd, multiplier, projected_cycle_cost_usd}`;
  multiplier = cycle cost / price (None without a price); projection = cost / elapsed fraction
  of the cycle (None in the first hour).
- `monthly` = one row per local calendar month with activity, oldest first:
  `{month: "YYYY-MM", requests, tokens, cost_usd, multiplier}`.
- `breakdowns` (for `breakdown_window`): `window`; `by_model` `[{model, name, requests,
  tokens, cost_usd, share}]` (cost desc; `name` = `pricing.display_name`; `share` is the share of
  the window's cost, not of requests or tokens); `by_project` (same shape with `project` and `name`;
  top 15, then one `"other"` row); `by_folder` (same shape with `folder` and `name`; see below);
  `by_thread` `{main,
  subagent}`; `haiku` `{main, subagent}` (Haiku-family models only); `fast` `{requests,
  cost_usd}` (`speed == "fast"`). Thread and haiku values are `{requests, tokens, cost_usd}`.
- `by_folder` groups by where the work happened rather than by the folder Claude Code was
  launched from (`requests.project` hides that when one launch folder is used for everything).
  `folder` = the nearest ancestor of the request `cwd`, the `cwd` itself included, that contains a
  `.git` entry; else the `cwd` itself. When that entry is a file naming a linked worktree
  (`gitdir: <main>/.git/worktrees/<name>`, absolute or relative), the folder is `<main>`: a
  worktree's work belongs to its repository, and so a worktree that is live or deleted lands in the
  same row (a deleted one inside the repository, as `<repo>\.claude\worktrees\<name>`, finds
  `<repo>` by the walk). A submodule's `.git` file (`gitdir: ...\.git\modules\<name>`) is a
  repository of its own and stays one. A `cwd` under the
  system temp directory (`tempfile.gettempdir()`, compared case-insensitively on Windows) or any
  path containing `\AppData\Local\Temp\` collapses to `"(temp)"`; a missing `cwd` is
  `"(unknown)"`. `name` = basename of the folder. Folders that differ only in case on Windows are
  one row, shown with the spelling that carried the most cost. The lookup is cached per distinct
  `cwd` in a module-level dict (cleared at 4096 entries), so a repository created later is not
  seen until the process restarts. It reuses the grouped breakdown query, which already carries
  `cwd`. The `cwd` is the session's working directory as Claude Code records it; work that
  `cd`s elsewhere inside one session still counts under the session's folder.
- `top_sessions_week` = the 5 most expensive sessions in the `week` window (rows as in
  `sessions_report`).
- `spend` = the usage API's `Spend` as a dict (real money), or None. Never added to cost.

`efficiency_report` → `{start, end, basis, cache, thinking, context, compactions, active_days,
tools, productivity, effort, api_errors, web}`
- `cache` = `{hit_rate, fresh_input_tokens, read_tokens, write_tokens, write_5m_tokens,
  write_1h_tokens, cost_usd, nocache_cost_usd, saved_usd}`. Hit rate = read / (fresh + read +
  write); saved = max(0, nocache − cost).
- `thinking` = `{thinking_tokens, output_tokens, share}`.
- `context` = prompt tokens per request (input + cache read + cache writes):
  `{requests, avg, p50, p95, max}`.
- `compactions` = `{count, auto, manual, per_active_day, avg_pre_tokens}`. `active_days` =
  local days with ≥ 1 request in the window.
- `tools` = `{total, errors, error_rate, builtin: [{name, count}], mcp: [{server, count, tools}]}`.
  MCP tool names `mcp__<server>__<tool>` are grouped by server (`tools` = distinct tool names);
  sorted by count. `errors` = `tool_errors` rows in the window whose `tool_use_id` is a tool call
  in the window; `error_rate` = errors / total, None when total is 0.
- `productivity` = `{lines_added, lines_removed, files_created, commits, pushes, prs,
  cost_per_pr_usd, cost_per_100_lines_usd}`. `commits` / `pushes` = distinct `tool_use_id`s in the
  window from two sources, unioned so one call counts once: `git_events` rows with `is_commit` /
  `is_push` (older transcripts), and `tool_calls` whose `git_ops` contains the op and that have no
  `tool_errors` row (a failed command did not commit). A `git_events` row is a success record and
  is not filtered by errors. Each id has one timestamp: the call's `tool_calls.ts` when that row
  exists, else the `git_events.ts` of the result, so an id whose command and result straddle a
  window edge is counted in one window, and daily or weekly counts add up to the total. One
  exception to the error rule: a `commit` is still counted for an errored call whose `git_ops` is
  `commit+push` or `commit+push+pr`, because a failed commit short-circuits the `&&` before the
  push runs, so the error is far more often a rejected push after a good commit. The push (and
  PR) of that call is not counted. The heuristic can over-count a commit that itself failed. PRs stay as they were: distinct `pull_requests` URLs whose `ts` is in
  the window, never `gh pr create` commands. Ratios are None when the divisor is 0.
- `effort` = `[{effort, requests, cost_usd}]` (a None effort is reported as `"default"`);
  `api_errors` = count; `web` = `{searches, fetches}`.
- An empty window gives zeros and None ratios, never an exception.

`pacing_report` → `{now, session, weekly, scoped}`
- `session` = `{percent, resets_at, window_start, slope_pct_per_hour, projected_limit_at,
  verdict, points}`. Points = stored snapshots in the current session window (same
  `session_resets_at` within 60 s), plus `usage` when it is newer, as `[iso, pct]`. Slope = least
  squares over the points from the last 90 minutes; it needs ≥ 2 points spanning ≥ 10 minutes.
  Verdict: percent ≥ 100 → `LIMITED`; no slope → `UNKNOWN`; slope < 0.5 %/h → `IDLE`;
  projected time (now + (100 − pct) / slope) ≥ resets_at → `SAFE`; otherwise `AT_RISK`, with
  `projected_limit_at` (it is set only for `AT_RISK`, None for every other verdict).
- `weekly` = `{percent, resets_at, window_start, elapsed_pct, pace_delta_pp, verdict,
  daily_budget_pct, even_daily_pct, projected_end_pct, points}`. Window = [resets_at − 7 d,
  resets_at); `pace_delta_pp` = percent − elapsed_pct. Verdict `AHEAD` (> +10), `BEHIND`
  (< −10), otherwise `ON_PACE` (`windows.PACE_BAND_POINTS`, the band every widget uses too);
  `UNKNOWN` without a weekly limit. `daily_budget_pct` = (100 −
  percent) / max(days left, 1); `even_daily_pct` = 100/7; `projected_end_pct` = percent /
  elapsed fraction (None while elapsed < 5 %). Points are downsampled to one per hour.
- `scoped` = one entry per scoped limit (for example the Fable weekly) with the `weekly`
  fields (window from its `window_seconds`, default 7 d), plus `label`, `model` (the scoped
  model's display name; not `scope_model`), `allowance_pct` and `used_pct_of_weekly`.
- Every limit block is None-safe: missing usage → percent None, verdict `UNKNOWN`.

`history_report` → `{tz, days, hour_weekday, streaks, busiest_day, first_use, active_days, odometer}`
- `days` = the last `days` local days ending today, zero-filled, oldest first:
  `{date, requests, tokens, output_tokens, cost_usd}`.
- `hour_weekday` = `{requests: 7×24, cost_usd: 7×24}` over the same range; row 0 = Monday.
- `streaks` = `{current_days, longest_days, longest_start, longest_end}`. An active day has
  ≥ 1 request; the current streak ends today, or yesterday when today has no activity yet.
- `busiest_day` = `{date, cost_usd, tokens}` by cost over all time (None when empty);
  `first_use` = the first request ts; `active_days` = the all-time count.
- `odometer` = `{total_tokens, output_tokens, requests, cost_usd, comparisons}`. Comparisons
  are `[{key, label, value}]` for `war_and_peace` (output tokens / 783,000), `britannica`
  (total tokens / 58,700,000) and `page_stack_m` (output tokens × 0.75 / 500 pages ×
  0.0001 m).

`sessions_report` → `{start, end, basis, sessions: [...]}`: sessions with requests in the
window, cost desc, at most `limit` rows: `{session_id, title, project, project_name, first_ts,
last_ts, requests, tokens, cost_usd, subagent_cost_usd, models (top 3 display names by cost),
prs}`. `title` = the custom title, else the AI title, else None.

`daily_rollups` → one row per local day with activity, oldest first: `{date, requests,
input_tokens, output_tokens, cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens,
thinking_tokens, cost_usd, nocache_cost_usd}`. `export_csv` writes a header row plus one row
per dict (columns in first-seen order, CRLF line ends; no rows gives `""`, since there are no
columns to name); `export_json` writes `{"basis": ..., "rows": [...]}`.

Store change: `pull_requests` keeps the earliest `ts` for a URL (upsert with `MIN`), so a PR
counts in the window where it first appeared.

### `data/alerts.py` (#13)
```python
@dataclass(slots=True)
class Alert:
    key: str          # dedupe key
    kind: str         # "threshold" | "reset" | "credits"
    severity: str     # "info" | "warning" | "critical"
    title: str        # "Session limit at 90%"
    message: str      # "Resets 4:40 PM"
class AlertEngine:
    def __init__(self, settings: Settings, *, tz: tzinfo | None = None): ...
    def evaluate(self, state: MonitorState, *, suppressed: bool = False) -> list[Alert]
```
New settings (in `DEFAULTS`, with value rules): `alerts_enabled` (True), `alert_thresholds`
([80, 90, 100]: 1–5 ints in 1..100), `alert_on_threshold` / `alert_on_reset` /
`alert_on_credits` (True), `alert_quiet_hours` (None, or `{"start": "HH:MM", "end": "HH:MM"}`
in local time; may wrap midnight).
- Runs only when `state.status` is `LIVE` and `state.usage` is set. Limits: session, weekly
  and each scoped limit. A limit's window identity is (kind, label) plus `resets_at` within a 60 s
  tolerance (the API jitters it); each window also keeps a high-water mark, the highest percent
  seen in it, and a threshold at or below that mark never fires (so a threshold added or lowered
  in settings does not toast for a level already passed).
- The first evaluation with usage records a baseline and returns nothing (no toasts for
  levels already reached at startup).
- Threshold: the first time percent ≥ T in a window → one alert for the highest T crossed;
  lower ones are marked done. Severity is `critical` for T ≥ 100, else `warning`. The message
  gives the local reset time ("Resets 4:40 PM"; weekly: "Resets Tue 4:00 PM").
- Reset: a window that ended and whose last percent was ≥ the lowest threshold → `info`
  "Session limit reset". The usage API does not announce the end of a window (the 5-hour session
  drops out of the answer, or keeps showing the window that ended, until the next message starts
  another), so the engine ends a window itself: on the first LIVE state with `updated_at` 60 s
  past the window's `resets_at` it announces the reset ("Usage is back to 0%.") and closes the
  window. A closed window ignores whatever the API still shows for it (no threshold alerts for a
  window that is over); the next window to appear starts a quiet baseline, and a limit first seen
  after its window ended is a baseline too. When the API shows the next window before the clock
  has ended the old one (a change of identity), the reset is announced from that change instead,
  with the new window's reset time in the message. Either way one alert per window.
- Credits: spend enabled and `used` above the last seen value → `info` "Usage credits in
  use", at most once per local day.
- Master off, kind toggled off, quiet hours, or `suppressed=True` (the caller's fullscreen
  check): the state still advances and nothing is returned. Never raises.

## Phase 3 target (v2.0) — summary

- **Widgets:** a Win32 UI thread hosts layered windows (`WS_EX_LAYERED`, `UpdateLayeredWindow`
  with premultiplied BGRA). Pillow renders each frame at 2× and downsamples. Redraw only on
  data change or animation ticks.
- **Dashboard:** the main process serves `webui/` + a JSON API on `127.0.0.1:<random port>`
  with a per-run token. A separate `--dashboard` process opens it in pywebview (WebView2),
  so it costs nothing while closed and a normal browser can load the same URL for testing.
- **Themes:** `themes/<name>.json` (tokens + variants) read by both the Pillow skins and the
  dashboard CSS. Nostromo (default), Night City, Gates.

"""Shared data contracts for the usage_monitor data layer.

Every module in usage_monitor.data produces or consumes these types. Change them
deliberately: the transcript parser, the SQLite store, the service and the UI all
depend on the field names below.

All datetimes are timezone-aware UTC unless a field name says otherwise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ── Transcript items ──────────────────────────────────────────────────────────
# One transcript line can yield several items. Items are deduplicated by the
# store using the key noted on each class, so re-reading a file is always safe.

@dataclass(slots=True)
class FileContext:
    """Where a transcript file sits under ~/.claude/projects."""
    path: str                       # absolute path of the .jsonl file
    project: str                    # project folder slug, e.g. "C--Users-Chris-...-claude-mem"
    is_subagent: bool               # file lives under <session>/subagents/
    agent_id: str | None = None     # "a4c15b14dd22664f0" from agent-<id>.jsonl
    session_hint: str | None = None # session id taken from the file / parent folder name


@dataclass(slots=True)
class RequestRecord:
    """One billable API request.

    Dedupe key: ``key`` = "<message.id>|<requestId>". Claude Code writes one line
    per content block and repeats the usage on every line; streamed partial lines
    can carry a smaller output_tokens. The store keeps ONE row per key and takes
    the max of each token field.
    """
    key: str
    message_id: str | None
    request_id: str | None
    ts: datetime
    session_id: str | None
    project: str
    cwd: str | None
    git_branch: str | None
    is_subagent: bool
    agent_id: str | None
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    thinking_tokens: int = 0
    web_search_requests: int = 0
    web_fetch_requests: int = 0
    speed: str | None = None          # "standard" | "fast"
    service_tier: str | None = None
    effort: str | None = None         # assistant record "effort", e.g. "xhigh"
    entrypoint: str | None = None     # "cli", "claude-vscode", ...
    cc_version: str | None = None

    @property
    def cache_write_tokens(self) -> int:
        return self.cache_write_5m_tokens + self.cache_write_1h_tokens

    @property
    def prompt_tokens(self) -> int:
        """Everything the model read for this request (fresh + cached)."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass(slots=True)
class ToolCall:
    """A tool_use content block. Dedupe key: tool_use_id."""
    tool_use_id: str
    name: str                         # "Bash", "Edit", "mcp__n8n-mcp__n8n_get_workflow", ...
    request_key: str | None
    session_id: str | None
    ts: datetime
    is_subagent: bool


@dataclass(slots=True)
class CodeChange:
    """A file edit reported in a user record's toolUseResult. Dedupe key: tool_use_id."""
    tool_use_id: str
    session_id: str | None
    ts: datetime
    lines_added: int
    lines_removed: int
    is_new_file: bool


@dataclass(slots=True)
class GitEvent:
    """toolUseResult.gitOperation (keys commit / push / pr). Dedupe key: tool_use_id."""
    tool_use_id: str
    session_id: str | None
    ts: datetime
    commit: bool
    push: bool
    pr: bool


@dataclass(slots=True)
class PullRequest:
    """A "pr-link" record. Dedupe key: url."""
    url: str
    number: int | None
    repository: str | None
    session_id: str | None
    ts: datetime | None


@dataclass(slots=True)
class SessionMeta:
    """Session titles ("ai-title" / "custom-title" records). Key: session_id; latest wins."""
    session_id: str
    project: str
    ai_title: str | None = None
    custom_title: str | None = None


@dataclass(slots=True)
class Compaction:
    """A system record with subtype "compact_boundary". Dedupe key: uuid."""
    uuid: str
    session_id: str | None
    ts: datetime
    trigger: str | None               # "auto" | "manual"
    pre_tokens: int | None
    post_tokens: int | None


@dataclass(slots=True)
class ApiErrorEvent:
    """A system record with subtype "api_error". Dedupe key: uuid."""
    uuid: str
    session_id: str | None
    ts: datetime


TranscriptItem = (RequestRecord | ToolCall | CodeChange | GitEvent | PullRequest
                  | SessionMeta | Compaction | ApiErrorEvent)


@dataclass(slots=True)
class FileState:
    """Incremental-ingest bookmark for one transcript file."""
    path: str
    size: int                          # file size when last read
    mtime: float
    offset: int                        # bytes consumed, always at a line boundary
    tail: str | None = None            # SHA-256 of the bytes just before offset; None when not recorded


# ── Pricing ───────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class CostBreakdown:
    """API-equivalent cost of one request (or a sum of requests) in USD."""
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write_5m: float = 0.0
    cache_write_1h: float = 0.0
    nocache_total: float = 0.0        # same tokens if nothing had been cached
    priced: bool = True               # False when the model has no price row

    @property
    def total(self) -> float:
        return self.input + self.output + self.cache_read + self.cache_write_5m + self.cache_write_1h


# ── Usage API (GET /api/oauth/usage) ─────────────────────────────────────────

class ApiStatus(str, Enum):
    LOADING = "loading"              # nothing fetched yet
    LIVE = "live"                    # last fetch succeeded recently
    STALE = "stale"                  # token expired / rejected; showing last-known values
    OFFLINE = "offline"              # network error; backing off
    RATE_LIMITED = "rate_limited"    # HTTP 429; backing off
    NO_CREDENTIALS = "no_credentials"
    PAUSED = "paused"                # workstation locked / screensaver


@dataclass(slots=True)
class DollarWindow:
    limit: float | None = None
    used: float | None = None
    remaining: float | None = None


@dataclass(slots=True)
class Limit:
    """One entry of the usage API's limits[] (or a legacy five_hour/seven_day window)."""
    kind: str                         # "session" | "weekly_all" | "weekly_scoped" | other
    group: str | None                 # "session" | "weekly" | None
    percent: float                    # 0..100 (may exceed 100)
    severity: str | None              # "normal" | "warning" | "critical" | ... (API-defined)
    resets_at: datetime | None
    is_active: bool
    label: str                        # UI label: "SESSION", "WEEKLY", "FABLE WEEKLY"
    scope_model: str | None = None    # display name of a scoped model, e.g. "Fable"
    scope_surface: str | None = None
    window_seconds: int | None = None # 18000 for 5h, 604800 for 7d when known
    allowance_pct: float | None = None       # weekly_scoped_shares: share of weekly it may use
    used_pct_of_weekly: float | None = None  # weekly_scoped_shares: share of weekly it has used
    locked_reason: str | None = None
    dollars: DollarWindow | None = None

    @property
    def fraction(self) -> float:
        return max(0.0, self.percent) / 100.0


@dataclass(slots=True)
class BreakdownRow:
    key: str                          # "claude_code" | "chat" | "cowork" | "other" | ...
    name: str                         # display name from the API
    percent: float


@dataclass(slots=True)
class Spend:
    """Usage credits / extra usage. Real money, unlike API-equivalent cost."""
    enabled: bool
    used: float | None = None         # in currency units (already divided by 10**exponent)
    limit: float | None = None
    percent: float | None = None
    severity: str | None = None
    currency: str | None = None
    balance: float | None = None
    disabled_reason: str | None = None


@dataclass(slots=True)
class UsageSnapshot:
    fetched_at: datetime
    limits: list[Limit] = field(default_factory=list)
    breakdown: list[BreakdownRow] = field(default_factory=list)
    breakdown_window_start: datetime | None = None
    spend: Spend | None = None
    raw: dict = field(default_factory=dict)

    @property
    def session(self) -> Limit | None:
        return next((l for l in self.limits if l.kind == "session"), None)

    @property
    def weekly(self) -> Limit | None:
        return next((l for l in self.limits if l.kind == "weekly_all"), None)

    @property
    def scoped(self) -> list[Limit]:
        return [l for l in self.limits if l.kind not in ("session", "weekly_all")]


@dataclass(slots=True)
class PlanInfo:
    subscription_type: str | None     # "max", "pro", "team", "enterprise", ...
    rate_limit_tier: str | None       # "default_claude_max_5x", ...
    name: str                         # "Max 5x", "Pro", "Unknown plan"
    monthly_price_usd: float | None   # list price; None when unknown


@dataclass(slots=True)
class CredentialsInfo:
    present: bool
    expires_at: datetime | None
    mtime: float | None
    plan: PlanInfo

    def is_expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at


# ── Aggregates the service publishes ──────────────────────────────────────────

@dataclass(slots=True)
class TokenTotals:
    requests: int = 0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write_5m: int = 0
    cache_write_1h: int = 0
    thinking: int = 0
    cost_usd: float = 0.0             # API-equivalent at list prices
    nocache_cost_usd: float = 0.0     # what the same tokens would cost uncached
    unpriced_requests: int = 0

    @property
    def cache_write(self) -> int:
        return self.cache_write_5m + self.cache_write_1h

    @property
    def total_tokens(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_write

    @property
    def cache_hit_rate(self) -> float:
        """Share of prompt tokens served from cache (0..1)."""
        prompt = self.input + self.cache_read + self.cache_write
        return self.cache_read / prompt if prompt else 0.0

    @property
    def caching_saved_usd(self) -> float:
        return max(0.0, self.nocache_cost_usd - self.cost_usd)


@dataclass(slots=True)
class WindowStats:
    name: str                          # "session", "today", "week", "month", "cycle", "all"
    start: datetime | None
    end: datetime | None
    totals: TokenTotals = field(default_factory=TokenTotals)
    by_model: dict[str, TokenTotals] = field(default_factory=dict)


@dataclass(slots=True)
class BurnRate:
    tokens_per_min: float = 0.0       # output tokens per minute over the recent window
    usd_per_hour: float = 0.0
    velocity: str = "IDLE"            # IDLE | SLOW | NORMAL | FAST | VERY FAST
    window_minutes: int = 30


@dataclass(slots=True)
class IngestProgress:
    running: bool = False
    files_done: int = 0
    files_total: int = 0
    last_duration_s: float | None = None
    last_error: str | None = None


@dataclass(slots=True)
class MonitorState:
    """Everything the UI needs, published by DataService after every cycle."""
    status: ApiStatus
    status_detail: str
    usage: UsageSnapshot | None
    usage_fetched_at: datetime | None
    plan: PlanInfo
    windows: dict[str, WindowStats]
    burn: BurnRate
    unpriced_models: list[str]
    ingest: IngestProgress
    updated_at: datetime

# Plan usage for Claude sessions

Claude Usage Monitor already knows how much of your plan is left. From v2.1 it can tell Claude
Code, so a long or parallel run (a multi-agent workflow, a big fan-out) can pace itself instead of
burning through the 5-hour session or the week before the reset.

Every way in reads the same thing: `%APPDATA%\ClaudeUsageMonitor\state.json`, which the running app
rewrites whenever the reading changes (a new fetch, or a change of status or of burn velocity), and at
least every 15 minutes. All of them are read-only and make no model request of their own. What they
return joins the conversation (about 100 tokens for the hook line, a few hundred for a tool call),
and none of them shows the sign-in token.

| Way in | What it does | Set up with |
|---|---|---|
| MCP tool `get_plan_usage` | Claude calls it when it wants the numbers, for example between workflow phases | `claude mcp add` (below) |
| `--hook` | Adds a one-line summary to Claude's context at session start, and when a limit passes a threshold | `hooks` in `settings.json` |
| `--gate` | Refuses new agents and workflows once the session or weekly limit reaches a cap you set | `hooks` in `settings.json` |
| The usage guard (v2.2) | Tells working sessions to wrap up when usage reaches a cap you set, and wakes the ones that wait when the limits allow work again | Settings → Usage guard, one click |
| `--status [--json]` | Prints the summary line, or the full reading as JSON, for scripts | a terminal or a script |

The app installs to `%LOCALAPPDATA%\ClaudeUsageMonitor\ClaudeUsageMonitor.exe`. The commands below
are for PowerShell, the default Windows terminal, and find that folder by themselves.

## The MCP tool

Run this once in PowerShell. `--scope user` makes the tool available in every project:

```
claude mcp add --transport stdio --scope user claude-usage '--' "$env:LOCALAPPDATA\ClaudeUsageMonitor\ClaudeUsageMonitor.exe" --mcp
```

Keep the quotes around `'--'`. When Claude Code was installed with npm, PowerShell runs `claude` as a
script and swallows a bare `--`, and `claude mcp add` then rejects `--mcp`. In cmd.exe or Git Bash a
bare `--` works.

Check it:

```
claude mcp get claude-usage
```

It should say `Status: ✓ Connected` and `Args: --mcp`. *Failed to connect* with empty `Args`, or a
path that does not exist, means it was added wrongly: remove it with
`claude mcp remove claude-usage -s user` and run the add command again. In a Claude Code window that
was already open, `/mcp` reconnects it.

The tool, `get_plan_usage`, takes no arguments and returns a summary line and the reading as JSON:

```
Claude plan usage (Max 5x, reading from 2m ago): session 30%, resets in 1h 50m (14:30), safe at this rate | weekly 80%, resets in 4d 3h (Wed 16:00), 39 pts ahead of an even pace, at its average pace so far 100% is reached in 17h 10m (Sun 05:49) | Fable weekly 0%, resets in 4d 3h (Wed 16:00), 41 pts under an even pace | burn 1.2k output tokens/min (fast).
```

Times are local. "An even pace" is the share of the window that has passed: 80% used when 41% of
the week has gone is 39 points ahead of it.

For unattended runs, allow the tool so that it never stops at a permission prompt: add
`"mcp__claude-usage__get_plan_usage"` to `permissions.allow` in your settings. Installing a monitor
update closes the server; reconnect it with `/mcp`, or start a new session.

## Hooks

Hooks go in your user settings file, `%USERPROFILE%\.claude\settings.json`, under `"hooks"`. To open
it from PowerShell, run `notepad "$env:USERPROFILE\.claude\settings.json"`. Each entry below starts
the exe directly with its arguments (the `args` form), so no shell is involved and the spaces in the
path need no quoting.

The `args` form needs **Claude Code 2.1.139 or newer** (check with `claude --version`, update with
`claude update`). An older build ignores `args` and starts the bare exe, which opens the dashboard on
every hook call. The VS Code extension and the `claude` in your terminal can be different versions,
and both read this file, so check both.

JSON can't look up your folder, so each `command` needs the exe's full path. This PowerShell line
prints it, with the forward slashes JSON wants:

```
"$env:LOCALAPPDATA\ClaudeUsageMonitor\ClaudeUsageMonitor.exe".Replace('\','/')
```

Paste what it prints over `PASTE-THE-PATH-HERE` in each entry (four places). A placeholder left in
place makes the hook fail with an error you can see, rather than run the wrong thing.

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "PASTE-THE-PATH-HERE",
            "args": ["--hook", "--always"],
            "timeout": 30
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "PASTE-THE-PATH-HERE",
            "args": ["--hook", "--threshold", "80"],
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "PASTE-THE-PATH-HERE",
            "args": ["--hook"],
            "timeout": 10
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Agent|Workflow",
        "hooks": [
          {
            "type": "command",
            "command": "PASTE-THE-PATH-HERE",
            "args": ["--gate", "--session", "95", "--weekly", "85"],
            "timeout": 45
          }
        ]
      }
    ]
  }
}
```

Use any of them on its own. If you already have a `"hooks"` block, add these events to it. The usage
guard's **Install** button adds the first three for you (below).

**`--hook`** adds the summary line to Claude's context.
- With `--always` (the SessionStart entry), it does so every time. SessionStart also runs after
  `/compact` and on resume, so the numbers come back after a compaction.
- Without `--always`, it stays silent until a limit reaches `--threshold` (default 80). It then
  speaks once, and again each time a limit climbs another 5 points. That is counted per session,
  and a new window starts from nothing.
- On PostToolUse (after every tool call) it adds nothing unless the usage guard holds or a limit
  passes the threshold. It starts the exe each time, which adds about a quarter of a second per tool
  call.
- It always exits 0, so it can never block a prompt or a tool.
- On UserPromptSubmit and PostToolUse it only reads the file and never calls the network.

**`--gate`** runs before every `Agent` or `Workflow` call.
- When the session or weekly limit is at or past its cap (defaults 95 and 90), the call is denied,
  and Claude is told which limit is over and when it resets.
- Calls that are already running are left alone.
- A limit whose window has reset no longer counts.
- If anything goes wrong (no reading, a damaged file), the gate lets the call run.

## The usage guard

The guard lets a long run wind down by itself before it uses up the plan, and pick up again when the
limits allow. It only informs: it never blocks a tool, stops a session or decides how Claude wraps
up. Claude Code's own handling of a limit that is actually reached is unchanged.

**Turning it on.** Open Settings → Usage guard, or tick **Usage guard** in the tray menu. Set a cap
for each limit you want watched: the 5-hour session (95% by default), the week (90%) and the
model-limited week such as Fable (off by default). Then press **Install** on the same card, once.

**When usage reaches a cap:**
- Each Claude session that is working hears it after its next tool call. It is told which limits
  are over their caps, that nothing is blocked, to wrap up at a safe point, and when work can
  resume. Each subagent hears it once too. Idle sessions are not disturbed.
- Each session hears it once per holding episode. A new window over a cap is a new episode. A
  session that starts, resumes or is compacted while the guard holds hears it again.
- The widgets show the guard holding, with the time left until work can resume.

**When work can resume:** only when every watched limit is under its cap again. The guard holds
until the latest reset among the limits that are over their caps, so a fresh 5-hour window while
the week is still over its cap wakes nobody. After a window resets, the app fetches the new numbers
30 seconds later.

**Waking up.** The notice offers the session a command to run as a background command:

```
"C:/Users/you/AppData/Local/ClaudeUsageMonitor/ClaudeUsageMonitor.exe" --wait --session <id>
```

- `--wait` ends when work can resume, and Claude Code wakes a session when one of its background
  commands ends. That works in the terminal and in VS Code.
- When several sessions wait, they are woken one after another, two minutes apart by default
  (**Stagger** on the card), oldest first, so they don't all start at once.
- **Resume now** on the card, or **Resume waiting sessions** in the tray menu, wakes them all at once,
  whatever the limits say. Turning the guard off wakes them too.
- `--wait` reads `state.json` every 30 seconds. With the app closed, it asks the usage API itself at
  most every 15 minutes. With no reading at all, it keeps waiting.

**What Install does:**
- It backs up your Claude Code user settings (`%USERPROFILE%\.claude\settings.json`, or the folder in
  `CLAUDE_CONFIG_DIR`) to `settings.json.cum-backup-<date>-<time>` beside the file.
- It adds the SessionStart, UserPromptSubmit and PostToolUse entries shown under [Hooks](#hooks), and
  leaves your other hooks alone, including a `--gate` you added yourself.
- It registers the `get_plan_usage` MCP tool.

**Remove** takes out only what Install added. Install needs Claude Code 2.1.139 or newer. The VS Code
extension has its own version, and needs it too.

The guard and `--gate` are separate. The gate refuses new agents and workflows past the caps in its
own arguments. The guard refuses nothing.

## From a script or a terminal

In PowerShell:

```
& "$env:LOCALAPPDATA\ClaudeUsageMonitor\ClaudeUsageMonitor.exe" --status | Out-String
& "$env:LOCALAPPDATA\ClaudeUsageMonitor\ClaudeUsageMonitor.exe" --status --json | Out-String
```

The first prints one line. The second prints the whole reading, plus `monitor_running`,
`usage_age_s`, `stale` and `summary`. The app is a windowed program, so it only answers into a pipe:
that is what `| Out-String` is for (in Git Bash, use `| cat`). Claude Code always uses a pipe. The
exit code is 1 when there is no reading at all.

## How fresh the numbers are

- **While the app runs**, it is the only thing that asks Anthropic for the numbers. It polls every
  15 minutes by default (Settings → Data & plan → Fetch usage every). For long unattended runs, a shorter interval gives
  fresher numbers. `usage_fetched_at` in the reading says when it was taken.
- **While the screen is locked**, the app normally pauses. When a Claude session has asked for the
  usage in the last hour, it keeps polling instead, because a locked screen is when unattended runs
  happen. A reader that finds a paused reading older than a poll interval waits up to 15 seconds
  (the gate, 5) for the app to wake up and fetch.
- **When there is no current reading** (the app is closed, or an older version is running),
  `--status`, the MCP tool, the gate and the SessionStart hook ask the usage API themselves, one at a
  time: the others wait for that answer, and it is kept in `state.json` for two minutes so that the
  callers in that time share it. The burn rate is then left out (it comes from the transcripts the
  running app reads). The pace still uses the history the app stored while it ran.
- A reading is marked **stale** when it is older than 30 minutes, or than two poll intervals plus a
  minute when that is longer.

## state.json

Schema 1. Times are UTC, in ISO 8601. Percentages run from 0 to 100, and a limit that is reached
can read over 100.

| Field | Meaning |
|---|---|
| `source` | `monitor` (the running app wrote it) or `cli` (a reader fetched it while the app was closed) |
| `written_at`, `usage_fetched_at` | When the file was written; when the numbers were taken |
| `status`, `status_detail` | `live`, `paused`, `stale` (the sign-in has expired), `offline`, `rate_limited` or `no_credentials`, with the app's own words |
| `poll_interval_s` | The app's poll interval |
| `plan` | `name` (such as `Max 5x`) and `tier` |
| `session` | `percent`, `resets_at`, `window_start`, `slope_pct_per_hour` (the climb over the last 90 minutes), `projected_limit_at` and `verdict`: `SAFE`, `AT_RISK` (the limit falls before the reset at this rate), `IDLE`, `LIMITED` or `UNKNOWN` |
| `weekly` | `percent`, `resets_at`, `window_start`, `elapsed_pct`, `pace_delta_pp` (percent minus elapsed), `verdict` (`AHEAD`, `ON_PACE`, `BEHIND`, `UNKNOWN`), `daily_budget_pct`, `projected_end_pct` and `projected_limit_at` (when 100% falls at the week's average pace so far, or null if not before the reset) |
| `scoped` | The model-scoped weekly limits, such as Fable: the `weekly` fields plus `label`, `model`, `allowance_pct` and `used_pct_of_weekly` |
| `burn` | `output_tokens_per_min`, `usd_per_hour` (API-equivalent), `velocity` and `window_minutes`; null when a reader fetched the numbers itself |
| `extra_usage` | Usage credits: `enabled`, `used`, `limit`, `percent` and `currency`, or null |
| `guard` | The usage guard: `state` (`off`, `armed` or `holding`), `over` (each watched limit at or past its cap: `name`, `label`, `percent`, `cap`, `resets_at`), `resume_at` (the latest of their resets) and `policy` (the caps and the stagger set in Settings). Null when a reader wrote the file; readers then use the settings file |

`--status --json` and the MCP tool add `monitor_running`, `usage_age_s`, `stale` and `summary`.

## Privacy

These modes only read. They write only small files in the app data folder:
- `state.json`, the reading above.
- `agent-demand`, an empty file whose date says when a session last asked.
- `hook-seen.json`, which records which steps the hook has already reported, and which usage-guard
  episode each session and subagent was told of, per session ID, for seven days.
- `waiting\<process ID>.json`, one per running `--wait`: its session ID, when it started and its
  folder. It is removed when the wait ends.
- `resume-now`, an empty file whose date tells the running waits that Resume now was pressed.
- `state.lock`, an empty file that lets one reader at a time ask the usage API.
- `agent.log`, warnings only.

No prompt, transcript text, file content or token goes into any of them. On a quiet machine they
add a few small writes an hour: `state.json` only changes with the reading, and the demand file
is renewed at most every 5 minutes. When there is no current
reading, the request a reader makes is the same usage request the app makes, with the same sign-in,
to `api.anthropic.com`. With the app closed, a reader also opens `usage.db` to work out the pace,
and creates it if it is missing.

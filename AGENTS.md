# AGENTS.md — claude-usage-monitor

> Parent: [`../../AGENTS.md`](../../AGENTS.md) — universal Golden Rules.

## What this is

Local workspace for the `claude-usage-monitor` project — a tray widget that
surfaces Claude Code token usage. Standalone repo under Chris's personal
projects.

## README + UI style

- **General README style:** [`../../personal-docs/style-guides/README_STYLE_GUIDE.md`](../../personal-docs/style-guides/README_STYLE_GUIDE.md)
- **App-specific style:** [`../../personal-docs/style-guides/CLAUDE_USAGE_STYLE_GUIDE.md`](../../personal-docs/style-guides/CLAUDE_USAGE_STYLE_GUIDE.md)

The monitor has a CRT / NASA-punk visual identity — preserve it.

## Local-only docs

Keep these local (not committed):
- `CLAUDE.md` (delegating stub) + `AGENTS.md` (this file, if the parent
  gitignore hasn't caught it — check)
- `NOTES.md` / `*NOTES*.md`

## Release policy

Compiled binaries and installers ship through **GitHub Releases**, not tracked
in the repo root.

## Publishing + feedback flow

Wired into the [TCS Discord bot](https://github.com/The-Canadian-Space/tcs-forum-watcher) with tag `Claude Monitor` under the `tools` category. Full flow: [`discord/release-and-feedback-flow`](https://docs.thecanadian.space/discord/release-and-feedback-flow/) (wiki, Cloudflare-Access-gated).

- Issues labelled `bug` or `suggestion` → `🐛-tool-feedback` thread (auto). Other labels don't mirror.
- GitHub releases → persistent thread in `📦-tool-updates`. **Public — users watch this thread for new versions.**
- **Releases are user-driven.** Every release fires a public Discord post. Do NOT run `gh release create` autonomously. If the work looks releasable (new feature, notable fix, packaged binary re-cut), **ask** first with proposed release notes.

## Related

- Parent: [`../../AGENTS.md`](../../AGENTS.md)
- Style guides: [`../../personal-docs/style-guides/`](../../personal-docs/style-guides/)

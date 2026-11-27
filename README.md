# Tavla

Issues in. Code out.

A terminal app to manage agent sessions: GitHub issues on a kanban board, each card listing the Claude Code sessions working on it. *Tavla* is Swedish for board.

## Requirements

- macOS with [iTerm2](https://iterm2.com) or [Ghostty](https://ghostty.org) 1.3 or later: attaching to a session opens a new tab there.
- [Claude Code](https://claude.com/claude-code) as `claude` on `PATH`, a version with background sessions (`claude --bg`, `claude agents`).
- [GitHub CLI](https://cli.github.com) as `gh`, signed in with the `project` scope so it can read and set project status: `gh auth refresh -s project`.
- [uv](https://docs.astral.sh/uv/) to install it.

## Install

```sh
uv tool install git+https://github.com/lindeskar/tavla
```

`uv tool upgrade tavla` updates it and `uv tool uninstall tavla` removes it. To run from a clone instead, so that a `git pull` takes effect without reinstalling:

```sh
git clone https://github.com/lindeskar/tavla
uv tool install --editable ./tavla
```

uv puts the command in `~/.local/bin`; if that is not on `PATH`, `uv tool update-shell` adds it.

## Configure

Tavla works without configuration: the board then shows the open issues assigned to you and any issue a session links to, all under Todo. `~/.config/tavla/config.toml` adds the rest:

```toml
# Where new sessions start. Default: the directory tavla was started in.
session_dir = "~/Code/my-notes"

# "tavla" (the default) or any built-in Textual theme, e.g. "nord" or "textual-light". For a light terminal pick
# "solarized-light": under any light theme merged PRs, session cards and the selected card adapt to the light background.
# A theme picked with ctrl+p recolours the board at once but is not saved: the next start uses this one.
theme = "tavla"

# Where attaching opens its tab: "iterm2" or "ghostty". Default: "ghostty" when tavla
# runs inside Ghostty (TERM_PROGRAM), otherwise "iterm2".
terminal = "ghostty"

# Days a finished session on no issue stays listed under Unlinked. Default: 10.
unlinked_days = 10

# GitHub Projects whose Status field places a card in a column, in precedence order:
# the first project that holds the issue wins. The non-Done items of a project marked
# `backlog = true` become cards even when they are not assigned to you.
[[projects]]
owner = "octocat"       # user or organization
number = 1
backlog = true

[[projects]]
owner = "octo-org"
number = 12
sprint = "Sprint"       # its iteration field: the board shows only the issues in the sprint

# Repositories the new-issue dialog (`i`) and `tavla create` file issues in; the first is the default.
[[issue_repos]]
repo = "octocat/todo"
label = "Personal"

[[issue_repos]]
repo = "octo-org/tracker"
label = "Team"
```

A project's Status options are matched by name, ignoring case: Backlog, Todo or Ready, In Progress or In Review, Blocked, and Done. Any other status counts as Todo, and a closed issue as Done.

A project with a `sprint` key is a *sprint project* (see `GLOSSARY.md` for the sprint terms). The board then shows only the issues *in the sprint*: their sprint, on any sprint project, has started, so the current sprint plus any *leftover* from an earlier sprint that is still open. The current sprint is the one that has started and not ended; between two sprints, the most recently started one. An issue *outside the sprint* (on a sprint project with no sprint, or *planned* for a sprint that hasn't started) leaves the board in every column and view unless a session is on it; `/` still finds it and `s` shows it, unless it is a sub-issue of a board issue, which shows it inside that issue whatever its sprint. A sub-issue under a board issue that is in the sprint is *covered by the sprint*: it needs no sprint of its own, so it is not outside the sprint, carries no chip, counts nowhere in `N outside`, and `n` never moves it into the sprint. Under a board issue on no sprint project, or one outside the sprint, a sub-issue keeps its own sprint. Issues on no sprint project and closed issues are not filtered. A field name that doesn't exist is reported in the status line (`no iteration field "Sprnt"`), and that project's issues show as if it had no `sprint` key. Without the key nothing changes.

Set `TAVLA_HOME` to keep the config file and the local state in another directory.

## Terminal app

```sh
tavla
```

While it runs, the terminal tab is named "Tavla"; the previous name comes back on exit.

| Key | Does |
|---|---|
| arrows / tab | Move between cards and columns (in the list view, tab jumps between groups) |
| `home` / `end` | Jump to the first / last card in a column, row in the list view, or session in the sessions panel |
| `/` | Search: filters the board and the list as you type. Every word must appear in an issue's title, `repo#N`, status, labels, parent issue (its title or `repo#N`) or session names (an unlinked session's name, id or refs), ignoring case. A search also finds finished issues that are off the board: yours closed in the last 30 days, and older ones a session still links to. They show in Done (or by their session state) while the filter is on, and every key works on them, so `n` starts a follow-up session. It finds the issues outside the sprint too, dimmed in their own column. A sub-issue of a board issue is not on the board, so a search on the board doesn't find it; inside an opened issue a search filters its sub-issues. `enter` moves to the first match and keeps the filter on; `esc` in the box clears it. The status line shows how many issues match |
| `enter` | On a card with sub-issues: open it (see Opening an issue below). On any other card: focus its sessions. On a session: attach in a new terminal tab, or in iTerm2 switch to the tab already attached to it. On a deleted session (under the `Deleted` divider in the sessions panel): resume it as a background session under the same id, put it back on its issue, and attach |
| `space` | Focus the sessions of the selected card, whether or not it has sub-issues |
| `n` | New background session on the selected issue (asks for an optional prompt). On a Todo or Backlog issue the box also offers to move it to In Progress on the project its status comes from: ticked by default for Todo, unticked for Backlog. On an issue outside the sprint (no sprint, or planned for a later one) the same box also puts it in its sprint project's current sprint (`… to In Progress and into Sprint 15`), so started work is never outside the sprint. A sub-issue covered by the sprint is never moved into it: the box offers only In Progress. A checkbox at the bottom, unticked by default, attaches to the new session in a new terminal tab. With no issue card selected (an unlinked session, or nothing) it does what `N` does |
| `N` | New background session on no issue: asks for a prompt (required), lets Claude Code name the session, and it shows under Unlinked. The same attach checkbox as `n` |
| `i` | New issue: pick one of the `issue_repos` from the config file (the first is the default), a title and an optional body; `enter` in the title or `ctrl+s` creates it. With a sprint project, an "Add to Sprint 15" box (ticked by default) adds the issue to the first sprint project and its current sprint, so it doesn't leave the board. Then asks for an optional prompt to start a session on it and attach (`esc` skips). Inside an opened issue `i` files a sub-issue of it instead: the dialog names that issue, defaults to its repository (listed first when it is not one of the `issue_repos`) and has no sprint box. The new issue is made a native sub-issue (`gh api repos/<owner>/<repo>/issues/<N>/sub_issues`) and added with no sprint to the project the opened issue's status comes from, so the sprint covers it, and it shows in the opened issue on the next refresh. When it can't be made a sub-issue it stays created, and the message names its URL |
| `u` | Update the selected issue's status: lists the Status options of the project its status comes from, the current one highlighted. A number key picks an option at once, or move to one and press `enter`; `esc` cancels. An issue with no status takes one on the first project it is on; a closed issue, or one on none of the `projects`, has no status to change |
| `l` / `U` | Link the selected session to an issue / remove its manual links |
| `ctrl+x` | Stop or delete the selected session: `s` stops it (`claude stop`; the conversation is kept and attaching resumes it), `d` deletes it and its worktree (`claude rm`, which refuses while the worktree has unpushed commits) and drops its manual links (the card keeps it under `Deleted`, where `enter` resumes it), `esc` cancels. Acts on the highlighted row in the sessions panel, an unlinked session, or a card's only session; interactive sessions are stopped in their own terminal |
| `h` | Hide the selected issue from the board and the list, or unhide it, at any level. Kept locally in `~/.config/tavla/hidden.json` |
| `H` | Show or leave out hidden issues (left out at every start). Shown, a hidden card is dimmed and tagged `⊘ hidden`; the status line counts the hidden issues |
| `s` | Show or leave out the issues outside the sprint that are not sub-issues of a board issue (left out at every start; only with a `sprint` key in the config file). Shown, they are dimmed and carry their sprint chip. The status line names the first sprint project's current sprint, its days left and how many issues are outside it: `Sprint 15 · 6 days left · 61 outside` |
| `o` | Open the issue in the browser |
| `g` | Group by issue status or by session state |
| `v` | Switch between the board (columns) and a grouped list, of the current level |
| `r` | Refresh now (also bypasses the project status cache) |
| `esc` | From the sessions panel, back to the columns; on the columns of an opened issue, up one level |
| `q` | Quit |

The footer shows the common keys and is cut off at the right edge when the terminal is narrower than they need; `ctrl+p` → Keys lists every key.

The board shows only *board issues* (see `GLOSSARY.md`): yours in the sprint (or on no sprint project), or carrying a session, and under no other board issue. Every sub-issue shows inside the board issue above it, at any depth.

Each card shows the issue title, then `repo #number` (click it to open the issue); clicking elsewhere on a card selects it and keeps the focus on the board. A board issue that has a parent names it on the next line (`↳ repo #N title`, or `↳ #N title` when the parent is in the same repository), so a sub-issue of someone else's issue keeps its context. Then indicators, the first three rolled up over the card's whole subtree, so a session that needs you three levels down shows on the board and on every card on the way down: session state (`● needs you`, `◐ working`, `○ N sessions`, of the issue's own sessions and those of every issue under it), PRs (`⎇ N PRs`, each PR once, green while any is open, purple once merged), sub-issue progress (`☑ done/total`, closed over all the issues under it at every depth, whoever they are assigned to; it also says that `enter` opens the card), the sprint chip, and the issue's labels in their GitHub colors. The sprint chip shows on a card that is not in the current sprint: a leftover's sprint (`Sprint 14`) and `no sprint` on started work (In Progress, Blocked, open Done) in the warning colour, since something is off; `no sprint` on a not-started issue and a planned issue's sprint (`Sprint 16`) in grey. Cards in the current sprint, covered by it (under a board issue in the sprint), or on no sprint project have none. A hidden sub-issue (`h`) and the issues under it leave the session and PR indicators of every card above it, and still count in `☑`.

**Opening an issue.** `enter` on a card with sub-issues replaces the board with its direct sub-issues, as cards in the same status columns and sorted the same way, and a breadcrumb above the columns names the path: `acme/infra #10 Move billing to the new API › #14 Invoice endpoints`, the repository left out where it is the same as the level before, the titles cut to fit. `enter` on a sub-issue that has its own goes one level deeper, at any depth, and `esc` goes up one level, onto the issue that was open. An opened issue shows all its direct sub-issues, whatever their assignee or sprint: one with no assignee of yours is dimmed and carries its first assignee's login (`@octocat`), none is dimmed for being outside the sprint, and every closed one is in Done (the 3-day rule is for the board only). Every card key works on a sub-issue card, the list view and the groupings show the current level, a search filters the cards of the current level, and unlinked sessions show only on the board. A card that only a search or `s` shows has no sub-issues read, so `enter` focuses its sessions instead. Tavla starts on the board; a refresh keeps the opened issue, or goes back to the nearest level that is still there when it is gone.

Below the board, the left panel shows the highlighted issue's body as Markdown (scroll it with the mouse; links open in the browser), or an unlinked session's first prompt; the right panel lists its sessions one per line: state, name, last update, id, PRs and the start of the result. The issue's own sessions come first; then, under a `Sub-issues N` divider, the sessions of the issues in its subtree, newest first, each named after the sub-issue it works on (`#41`, or `tools #41` in another repository, and `+1` when it is on two), so `space` and `enter` attach to a session deep down straight from the board. A session on the issue itself and on one under it is listed once, at the top; one under a hidden sub-issue is left out; the session keys (`enter`, `l`, `U`, `ctrl+x`) work on these rows as on the issue's own. Sessions that were on the issue itself and are gone come last, dimmed, under a `Deleted N` divider: deleted background sessions (`◌ deleted`) and interactive ones that ended (`◌ ended`). `enter` resumes one; the other session keys (`l`, `U`, `ctrl+x`) leave them alone. They place no card and count in no badge.

The list view puts each issue of the current level on one line under its group's heading (same groups as the columns, empty ones left out), with Repo, Status, Sprint (with a sprint project only: the card's sprint chip), Sessions, PRs, Sub (these three rolled up over the subtree, as on the card), Parent and Labels cells sized for a terminal about 190 columns wide; the title cell absorbs the difference. Status is left blank where the group heading already says it, and the Parent cell holds the card's `↳` parent line.

In the text boxes (prompt, issue title and body, search), `option+left` / `option+right` move by word and add `shift` to select; in the prompt and issue body, `option+up` / `option+down` jump to the start / end of the text, and `ctrl+g` opens the text in `$VISUAL` (else `$EDITOR`, else `vi`) and takes it back when the editor exits, which is also how to write a prompt with several lines, since `enter` starts the session. A GUI editor needs its wait flag, e.g. `EDITOR="code --wait"`.

A new issue is assigned to you unless you untick it in the dialog, and goes straight into the assigned-issue cache, so its card shows on the next refresh even though the search index lags. An unassigned one shows only once a session links to it.

New sessions start in `session_dir` from the config file, or in the directory `tavla` was started in.

Attaching opens a new tab in the current iTerm2 or Ghostty window (the `terminal` key in the config file; by default the one `tavla` runs in), moves to the session's own directory (or `session_dir` when it has none recorded) and runs `claude attach <id>` there, so waking a finished session doesn't start it in your home directory. The tab is named after the session with an OSC 1 escape from its own shell (iTerm2 does not show a name set through AppleScript), and `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` stops Claude Code from overwriting that name with its own title. In iTerm2, attaching to a session that a tab is already attached to (any tab running `claude attach <id>`, found by its tty) brings that tab forward instead of opening another; Ghostty always opens a new tab. Detaching leaves the tab's shell open; deleting the session (`ctrl+x` `d`, `tavla rm`, `claude rm`) closes the tab, provided the iTerm2 profile closes a session when its shell exits. The first attach asks macOS for permission to control iTerm2.

## Core CLI

The app is built on `src/tavla/board.py`; `tavla` with a command runs it directly:

```sh
tavla dump [--group status|session] [--force]
tavla json
tavla attach <session>
tavla new <issue> [prompt...]
tavla start <prompt...> [--name name]
tavla create <title> [--repo owner/repo] [--body text]
tavla resume <session>
tavla stop <session>
tavla rm <session> | --self
tavla link <session> <issue> | --self <issue>
tavla unlink <session> [issue]
```

`tavla dump` prints the board issues (see `GLOSSARY.md`) in their columns, each with its sub-issues indented beneath it at every level, a sub-issue's status and session badge in brackets and its sessions indented under it. `tavla json` prints the board issues and the search-only cards under no board issue, each with `isBoardIssue` and a `subtree` holding its direct sub-issues as cards of the same shape, each with its own `subtree`; a sub-issue is only in its board issue's `subtree`. A card's `sessions`, `sessionColumn`, `prs` and `subIssues` are the issue's own (`subIssues` is GitHub's count of its direct sub-issues); its `rollup` covers its subtree: `sessionColumn`, `sessions` (the ids, its own first), `live`, `prs` and `subIssues` (every issue under it). `viewer` is your GitHub login.

## Claude Code plugin

The repository is also a Claude Code plugin, for use inside the sessions the board shows:

```
/plugin install tavla --marketplace lindeskar/tavla
```

| Skill | Does |
|---|---|
| `/tavla:link <issue>` | Links the session it runs in to an issue (`tavla link --self`), so the session shows on that card. Claude may also run it itself when a session starts on an issue its first prompt did not name |
| `/tavla:done` | Deletes the background session it runs in and its worktree (`tavla rm --self`), which also closes a tab Tavla attached to it. Only you can run it, and it stops when `claude rm` refuses, as it does for unpushed commits |

Both need the `tavla` command on `PATH`.

## Where the data comes from

- **Cards:** open issues assigned to `@me` (`gh search issues`, cached for 5 minutes in `~/.config/tavla/assigned-cache.json`), non-Done items of the `backlog` projects in the config file, and any issue a session links to.
- **Recently closed issues:** issues closed in the last 30 days that are assigned to `@me` (`gh search issues`) or sit in a `backlog` project, cached for 15 minutes in `closed-cache.json`. They are not on the board: only a search (`/`) shows them, or the board issue they are a sub-issue of when opened. `tavla json` lists them with `searchOnly: true`, a sub-issue only in its board issue's `subtree`; `tavla dump` leaves them out.
- **Issue status:** the Status field of the `projects` in the config file; the first match wins. Status is read from the project side, because issue-side `projectItems` is empty for cross-org projects. It is cached for 5 minutes in `~/.config/tavla/projects-cache.json`.
- **Sprints:** a sprint project's list query also reads each item's sprint (the iteration field named by `sprint`: title, start date, length) and the field's sprints, in the same pages, so it costs no extra call. Both are cached with the statuses in `projects-cache.json` and, like them, used whatever their age when GitHub can't be reached; a cache written before the `sprint` key was set or changed is refreshed at once. A sprint project whose field can't be read and isn't cached is left out of the filter. `tavla json` lists the issues outside the sprint with `searchOnly: true` (a sub-issue only in its board issue's `subtree`), and every card has a `sprint` field (`null` on no sprint project, else its title and `current`, `leftover`, `planned`, `none` or `covered`, the last for a sub-issue under a board issue in the sprint); `tavla dump` leaves them out.
- **GitHub budget:** everything except the session files spends the GraphQL rate limit (5,000 points an hour), `gh search` included. Project lists use a minimal query (1 point per 100 items, open issues only since a closed issue is Done whatever its status, the projects fetched in parallel), and issue/PR metadata is cached for 10 minutes in `meta-cache.json` (fetched 40 issues to a query, the queries in parallel), so a routine refresh only looks up what is new and otherwise calls nothing but `claude agents`. The recently closed issues cost one search and one project page every 15 minutes, plus their share of the metadata; `r` bypasses all four caches (not your login, which is kept). When GitHub answers with a rate limit, all GitHub calls pause until the budget resets (read from GraphQL's own `rateLimit`; the REST `/rate_limit` graphql bucket can disagree), or 90 seconds for a burst limit, and the board is built from the session files and cached GitHub data alone (any age), with the pause time in its status line: sessions whose issue isn't cached show as Unlinked.
- **Startup and no network:** the caches are kept between runs, so the board is drawn from them (any age) the moment `tavla` starts, with `refreshing…` in the status line until GitHub answers and replaces it. When GitHub can't be reached, each list keeps its last cached answer and the status line shows the error; every refresh (once a minute) asks again, so the board catches up by itself when the network is back.
- **Sessions:** `~/.claude/jobs/*/state.json` for background sessions (`name`, `state`, `intent` = first prompt, `children` = linked PRs, `output.result`), plus `claude agents --json` for interactive ones. The job files are Claude Code internals and may change between versions. A session on no card, and on no issue in a card's subtree, is listed under Unlinked; a finished one only for `unlinked_days` (config file, 10 by default) after its last update.
- **Deleted sessions:** every session seen on a card is recorded in `~/.config/tavla/history.json` (ids, name, directory, issues, PRs, start of the result). `claude rm` removes a session from the list but keeps its conversation under `~/.claude/projects/`, so a recorded session that is no longer listed is shown under its issues as deleted, and `claude --bg --resume <session id>`, run in the session's own directory (`session_dir` when that is gone), brings it back under the same id. Sessions deleted before they were recorded are picked up once from the links they left in `links.json`, with the name and directory read from the conversation file; one whose conversation is gone is not shown. A deleted session shows only on an issue that has a card, and a search matches its name.
- **Card metadata:** labels, sub-issue progress, the parent issue (title, state and its sub-issue progress), the issue body and closing PRs come from the same batched GraphQL calls. A card's PRs are the PRs that close it, plus those PRs of the sessions on it that close no issue at all, so one session with a stack of PRs shows on each of its issues only the PR that closes it.
- **Sub-issues:** the same batched lookup also lists each issue's sub-issues, and a refresh follows them level by level (one batched lookup per level, 40 issues to a query, open and closed alike) from every card on the board, down to GitHub's limit of eight levels; a card that only a search or `s` shows has no sub-issues read, except a finished issue with a live or recent session somewhere under it, which stays on the board: the parent of each issue a session is on is looked up with the cards, and any parent above it level by level, to find it. They are cached with the rest of the metadata in `meta-cache.json`, so a routine refresh reads them from there and calls nothing more; an entry cached by an older version is fetched again at once. A *board issue* is a card on the board that is in no other card's subtree. A sub-issue GitHub won't return (deleted, or no access) is left out with a line in the errors, and so are the sub-issues past the first 100 of an issue (GitHub's own limit, so it shouldn't happen). This costs less than before, not more: a PR's closing issues are read without labels, assignees or their own PRs, which GraphQL charges for once per issue read, and an issue known only from a PR is looked up directly instead. A cold refresh of a board of about 50 cards (30 board issues, 47 sub-issues) went from 35 points to 17.
- **Your login:** `viewer { login }`, asked once and kept in `viewer-cache.json`, tells your sub-issues from someone else's inside an opened issue. Until it is known (offline before the first answer) no sub-issue is dimmed.
- **Session to issue:** manual links (`~/.config/tavla/links.json`), issue/PR URLs or `owner/repo#N` in the first prompt, and the issues each linked PR closes, up to 20 per PR (one batched GraphQL call).
- **Columns by issue status:** Todo (No status, Backlog and Todo together; Backlog cards come last, under a `Backlog N` divider line and with a dimmed title, in the board and the list), In Progress, Blocked, Done. A closed issue counts as Done, and Done cards show only while a session on them or anywhere in their subtree is live or was touched in the last 3 days; after that a search still finds them. Each card carries a session badge over its subtree (`!` needs you, `~` working, `Ns/M live`), and cards with a session needing you anywhere under them sort first.
- **Columns by session state:** by the sessions of each card's whole subtree: Active (sessions that need you or are working; needs-you cards first, and the header is highlighted while a session anywhere under a card needs you), Idle, No session.

Agent view's own filter works alongside: pasting an issue URL in `claude agents` lists the sessions whose first prompt contains it, which `new` guarantees.

## Develop

```sh
uv run pytest -q
uv run ruff format . && uv run ruff check .
```

`uv run tavla` runs the code in the clone. Issues and pull requests are welcome; a behaviour change comes with a test and an update to this file.

## License

[MIT](LICENSE)

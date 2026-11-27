"""Tavla core: GitHub issues with the Claude Code sessions working on each.

The terminal app (tui.py) imports it; `tavla json` prints the board for scripts.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import date, timedelta
from pathlib import Path

JOBS = Path.home() / ".claude" / "jobs"
# Conversations, as <project>/<session id>.jsonl: `claude rm` keeps them, which is what makes a deleted session resumable.
TRANSCRIPTS = Path.home() / ".claude" / "projects"
# Local state and config.toml.
CONFIG = Path(os.environ.get("TAVLA_HOME", Path.home() / ".config" / "tavla"))
# The ones below are set from config.toml by load_config().
# Projects whose Status field places a card, in precedence order. Non-Done items of a
# backlog project become cards even when no session or assignment points at them.
PROJECTS: list[dict] = []
# Where `create` files issues, as (owner/repo, label): the first is the default.
ISSUE_REPOS: list[tuple[str, str]] = []
# Where `new` starts background sessions.
SESSION_CWD = Path.cwd()
THEME = "tavla"
# The terminal app `attach` opens tabs in.
TERMINALS = {"iterm2": "iTerm2", "ghostty": "Ghostty"}
TERMINAL = "ghostty" if os.environ.get("TERM_PROGRAM") == "ghostty" else "iterm2"
# How long a finished session on no card stays listed under Unlinked.
UNLINKED_DAYS: float = 10
PROJECT_TTL_S = 300
ASSIGNED_TTL_S = 300  # open issues assigned to me
META_TTL_S = 600  # issue/PR metadata: labels, sub-issues, linked PRs, open state
CLOSED_TTL_S = 900  # recently closed issues, which only a search shows
CLOSED_DAYS = 30
# GitHub allows 100 sub-issues per issue and eight levels of nesting, so one page reads them all and the walk stops there.
SUB_ISSUES_PER_ISSUE = 100
SUB_ISSUE_LEVELS = 8
CLOSES_READ = 20  # issues read from each PR's closing list
PROJECT_QUERY = """
query($owner: String!, $number: Int!, $cursor: String) {
  repositoryOwner(login: $owner) { ... on ProjectV2Owner { projectV2(number: $number) {
    items(first: 100, after: $cursor, query: "is:open is:issue") { pageInfo { hasNextPage endCursor }
      nodes { id status: fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        content { __typename ... on Issue { number title url repository { nameWithOwner } } } } } } } }
}
"""
ITERATION = "id title startDate duration"
SPRINT_FIELD = f"sprintField: field(name: $sprint) {{ ... on ProjectV2IterationField {{ id configuration {{ iterations {{ {ITERATION} }} completedIterations {{ {ITERATION} }} }} }} }}"
# A sprint project's items also carry their sprint, and each page the iteration field's sprints.
SPRINT_PROJECT_QUERY = f"""
query($owner: String!, $number: Int!, $cursor: String, $sprint: String!) {{
  repositoryOwner(login: $owner) {{ ... on ProjectV2Owner {{ projectV2(number: $number) {{ {SPRINT_FIELD}
    items(first: 100, after: $cursor, query: "is:open is:issue") {{ pageInfo {{ hasNextPage endCursor }}
      nodes {{ id status: fieldValueByName(name: "Status") {{ ... on ProjectV2ItemFieldSingleSelectValue {{ name }} }}
        sprint: fieldValueByName(name: $sprint) {{ ... on ProjectV2ItemFieldIterationValue {{ iterationId title startDate duration }} }}
        content {{ __typename ... on Issue {{ number title url repository {{ nameWithOwner }} }} }} }} }} }} }} }}
}}
"""
# What the sprint writes need: the project and field ids, its sprints, and with $issue the issue's id.
SPRINT_TARGET_QUERY = f"""
query($owner: String!, $number: Int!, $sprint: String!) {{
  repositoryOwner(login: $owner) {{ ... on ProjectV2Owner {{ projectV2(number: $number) {{ id {SPRINT_FIELD} }} }} }}
}}
"""
SPRINT_ISSUE_TARGET_QUERY = f"""
query($owner: String!, $number: Int!, $sprint: String!, $repoOwner: String!, $repo: String!, $issue: Int!) {{
  repositoryOwner(login: $owner) {{ ... on ProjectV2Owner {{ projectV2(number: $number) {{ id {SPRINT_FIELD} }} }} }}
  repository(owner: $repoOwner, name: $repo) {{ issue(number: $issue) {{ id }} }}
}}
"""
ADD_ITEM_MUTATION = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}
"""
SET_ITERATION_MUTATION = """
mutation($project: ID!, $item: ID!, $field: ID!, $iteration: String!) {
  updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field, value: {iterationId: $iteration}}) { projectV2Item { id } }
}
"""
CLOSED_PROJECT_QUERY = """
query($owner: String!, $number: Int!, $filter: String!, $cursor: String) {
  repositoryOwner(login: $owner) { ... on ProjectV2Owner { projectV2(number: $number) {
    items(first: 100, after: $cursor, query: $filter) { pageInfo { hasNextPage endCursor }
      nodes { content { __typename ... on Issue { number title url closedAt repository { nameWithOwner } } } } } } } }
}
"""
STATUS_FIELD_QUERY = """
query($owner: String!, $number: Int!) {
  repositoryOwner(login: $owner) { ... on ProjectV2Owner { projectV2(number: $number) { id
    field(name: "Status") { ... on ProjectV2SingleSelectField { id options { id name } } } } } }
}
"""
SET_STATUS_MUTATION = """
mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field, value: {singleSelectOptionId: $option}}) { projectV2Item { id } }
}
"""
RECENT_S = 3 * 86_400

NEEDS = {"blocked", "needs_input", "needs-input", "waiting", "review"}
WORKING = {"working", "running", "starting", "busy"}
STATUS_COLUMNS = [("todo", "Todo"), ("progress", "In Progress"), ("blocked", "Blocked"), ("done", "Done")]
# Each project's Status field (project id, field id, options), read once per run: the options hardly ever change.
STATUS_FIELDS: dict[str, dict] = {}
# Not-started statuses share the Todo column; a card keeps its own status for display.
# Statuses a new session may move to In Progress.
NOT_STARTED = ("Todo", "Backlog")
STATUS_BUCKET = {"none": "todo", "backlog": "todo", "todo": "todo", "progress": "progress", "blocked": "blocked", "done": "done"}
SESSION_COLUMNS = [("active", "Active"), ("idle", "Idle"), ("backlog", "No session")]
# Sessions that need you and working ones share Active; needs-you cards sort first and keep their chip.
SESSION_BUCKET = {"needs": "active", "working": "active", "idle": "idle", "backlog": "backlog"}

URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/(\d+)")
SHORT_RE = re.compile(r"(?:^|[\s(\[])([\w.-]+)/([\w.-]+)#(\d+)\b")

Ref = tuple[str, str, int]


def load_config() -> None:
    """Set PROJECTS, ISSUE_REPOS, SESSION_CWD, THEME, TERMINAL and UNLINKED_DAYS from CONFIG/config.toml; a missing file leaves the defaults."""
    global PROJECTS, ISSUE_REPOS, SESSION_CWD, THEME, TERMINAL, UNLINKED_DAYS
    path = CONFIG / "config.toml"
    try:
        cfg = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return
    except tomllib.TOMLDecodeError as err:
        raise ValueError(f"{path}: {err}") from err
    try:
        projects = [
            {"owner": str(p["owner"]), "number": str(int(p["number"])), "is_backlog": bool(p.get("backlog", False)), **({"sprint": str(p["sprint"])} if p.get("sprint") else {})}
            for p in cfg.get("projects", [])
        ]
        repos = [(str(r["repo"]), str(r.get("label") or r["repo"])) for r in cfg.get("issue_repos", [])]
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError(f"{path}: a [[projects]] entry needs owner and number, an [[issue_repos]] entry needs repo ({err!r})") from err
    bad = [r for r, _ in repos if not re.fullmatch(r"[\w.-]+/[\w.-]+", r)]
    if bad:
        raise ValueError(f"{path}: issue_repos repo must be owner/repo, got {bad[0]!r}")
    terminal = str(cfg.get("terminal") or TERMINAL).lower()
    if terminal not in TERMINALS:
        raise ValueError(f"{path}: terminal must be one of {', '.join(TERMINALS)}, got {terminal!r}")
    days = cfg.get("unlinked_days", UNLINKED_DAYS)
    if isinstance(days, bool) or not isinstance(days, (int, float)) or days < 0:
        raise ValueError(f"{path}: unlinked_days must be a number of days, 0 or more, got {days!r}")
    PROJECTS, ISSUE_REPOS, TERMINAL, UNLINKED_DAYS = projects, repos, terminal, days
    if cfg.get("session_dir"):
        SESSION_CWD = Path(str(cfg["session_dir"])).expanduser()
    THEME = str(cfg.get("theme") or THEME)


class RateLimited(RuntimeError):
    """GitHub refused a call for rate limiting (primary budget or a secondary burst limit)."""


def run(argv: list[str], timeout: int = 60, cwd: Path | None = None) -> str:
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd, check=False)
    if p.returncode != 0:
        msg = f"{' '.join(argv[:3])}: {(p.stderr or p.stdout).strip()[:200]}"
        raise RateLimited(msg) if "rate limit" in msg.lower() else RuntimeError(msg)
    return p.stdout


def graphql(query: str, variables: dict | None = None) -> dict:
    """The parsed response; partial data is kept, since gh exits non-zero when any one alias is missing."""
    argv = ["gh", "api", "graphql", "-f", f"query={query}"]
    for name, value in (variables or {}).items():
        argv += ["-F" if isinstance(value, int) else "-f", f"{name}={value}"]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    body = json.loads(p.stdout or "{}")
    # Judge only gh's own message and GraphQL's error entries: data (an issue title) may say "rate limit" too.
    messages = [p.stderr, *(f"{e.get('type', '')} {e.get('message', '')}" for e in body.get("errors") or [])]
    if any("rate limit" in m.lower() or "RATE_LIMIT" in m for m in messages):
        raise RateLimited(f"graphql: {next(m for m in messages if 'rate' in m.lower()).strip()[:200]}")
    return body


def backoff_until() -> float:
    try:
        return float(json.loads((CONFIG / "backoff.json").read_text())["until"])
    except FileNotFoundError, json.JSONDecodeError, KeyError, ValueError:
        return 0.0


def start_backoff() -> float:
    """Pause GitHub calls until the exhausted budget resets, or 90s for a secondary (burst) limit.

    The GraphQL budget is read from GraphQL's own rateLimit: the REST /rate_limit graphql bucket can
    report plenty remaining while GraphQL itself refuses every query. `gh search` spends GraphQL too."""
    until = time.time() + 90
    with suppress(Exception):  # the 90s default stands
        rl = (graphql("{ rateLimit { remaining resetAt } }").get("data") or {}).get("rateLimit") or {}
        if rl.get("remaining") == 0:
            until = max(until, parse_time_ms(rl["resetAt"]) / 1000)
    with suppress(Exception):
        search = json.loads(run(["gh", "api", "rate_limit"], timeout=20))["resources"]["search"]
        if search["remaining"] == 0:
            until = max(until, float(search["reset"]))
    CONFIG.mkdir(parents=True, exist_ok=True)
    (CONFIG / "backoff.json").write_text(json.dumps({"until": until}))
    return until


def now_ms() -> int:
    return int(time.time() * 1000)


def today() -> date:
    """The local date sprints are measured against; tests fix it."""
    return date.today()


def key_of(r: Ref) -> str:
    return f"{r[0]}/{r[1]}#{r[2]}".lower()


def parse_refs(text: str) -> list[Ref]:
    out = [(m[1], m[2], int(m[3])) for m in URL_RE.finditer(text)]
    out += [(m[1], m[2], int(m[3])) for m in SHORT_RE.finditer(text)]
    return out


def ref_from_arg(arg: str) -> Ref | None:
    refs = parse_refs(f" {arg}")
    return refs[0] if refs else None


def kind_of(state: str) -> str:
    return "needs" if state in NEEDS else "working" if state in WORKING else "idle"


def status_column_of(status: str, is_open: bool) -> str:
    if not is_open:
        return "done"
    s = status.lower()
    if s == "backlog":
        return "backlog"
    if s in ("todo", "ready"):
        return "todo"
    if s in ("in progress", "in review"):
        return "progress"
    if s == "blocked":
        return "blocked"
    if s == "done":
        return "done"
    return "none"


def parse_time_ms(value: str | None) -> int:
    if not value:
        return 0
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return 0


# --- links -------------------------------------------------------------------


def load_links() -> dict[str, list[str]]:
    try:
        return json.loads((CONFIG / "links.json").read_text())
    except FileNotFoundError, json.JSONDecodeError:
        return {}


def save_links(links: dict[str, list[str]]) -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    (CONFIG / "links.json").write_text(json.dumps(links, indent=2, sort_keys=True) + "\n")


# --- hidden issues -------------------------------------------------------------


def load_hidden() -> set[str]:
    try:
        return set(json.loads((CONFIG / "hidden.json").read_text()))
    except FileNotFoundError, json.JSONDecodeError:
        return set()


def toggle_hidden(issue_key: str) -> set[str]:
    """Hide the issue on the board, or show it again; returns the hidden keys."""
    hidden = load_hidden() ^ {issue_key}
    CONFIG.mkdir(parents=True, exist_ok=True)
    (CONFIG / "hidden.json").write_text(json.dumps(sorted(hidden), indent=2) + "\n")
    return hidden


# --- session history -----------------------------------------------------------


def load_history() -> dict[str, dict]:
    try:
        return json.loads((CONFIG / "history.json").read_text())
    except FileNotFoundError, json.JSONDecodeError:
        return {}


def save_history(history: dict[str, dict]) -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    (CONFIG / "history.json").write_text(json.dumps(history, indent=2, sort_keys=True) + "\n")


def history_record(sess: dict) -> dict:
    """What is kept of a session so that it can be listed and resumed once it is gone."""
    return {
        "id": sess["id"],
        "sessionId": sess.get("sessionId") or "",
        "name": sess.get("name") or sess["id"],
        "cwd": sess.get("cwd") or "",
        "issues": list(sess.get("issues") or []),
        "prs": list(sess.get("prs") or []),
        "result": " ".join((sess.get("result") or sess.get("detail") or "").split())[:200],
        "updatedAt": sess.get("updatedAt") or 0,
        "isInteractive": bool(sess.get("isInteractive")),
    }


def from_transcript(short_id: str, issues: list[str]) -> dict:
    """A record for a session known only by its id (a link left behind by a session deleted elsewhere):
    its session id, name and directory are read from its conversation file. Without one
    the record has no sessionId, which keeps it off the cards."""
    sess = {"id": short_id, "issues": issues}
    path = next(iter(sorted(TRANSCRIPTS.glob(f"*/{short_id}-*.jsonl"))), None) if TRANSCRIPTS.is_dir() else None
    if path is None:
        return history_record(sess)
    sess |= {"sessionId": path.stem, "updatedAt": int(path.stat().st_mtime * 1000)}
    names: dict[str, str] = {}
    with path.open(errors="replace") as f:
        for line in f:
            # Titles are rewritten throughout the file, between request lines of a megabyte: parse only the lines that can matter.
            if not any(f'"{field}"' in line for field in ("agentName", "customTitle", "aiTitle", *(() if sess.get("cwd") else ("cwd",)))):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            names |= {field: str(row[field]) for field in ("agentName", "customTitle", "aiTitle") if row.get(field)}
            if not sess.get("cwd") and isinstance(row.get("cwd"), str):
                sess["cwd"] = row["cwd"]
    sess["name"] = names.get("agentName") or names.get("customTitle") or names.get("aiTitle")
    return history_record(sess)


def update_history(sessions: list[dict], links: dict[str, list[str]], errors: list[str]) -> list[dict]:
    """Record the sessions that sit on a card, and return the recorded ones that are gone, as session rows.

    A session is gone when it is no longer listed: a background one was deleted, an interactive one
    ended. Its conversation is still on disk, so resume_session brings it back. Sessions deleted
    before they were recorded are picked up once from the manual links they left behind."""
    history = load_history()
    before = json.dumps(history, sort_keys=True)
    live = {s["id"] for s in sessions}
    for s in sessions:
        if s["issues"]:
            history[s["id"]] = history_record(s)
    for sid, keys in links.items():
        if sid not in live and sid not in history:
            try:
                history[sid] = from_transcript(sid, list(keys))
            except OSError as err:
                errors.append(f"history: {err}")
    if json.dumps(history, sort_keys=True) != before:
        try:
            save_history(history)
        except OSError as err:  # a history that can't be saved must not cost the board
            errors.append(f"history.json: {err}")
    # Without `claude agents` no interactive session is listed: that is not them having ended.
    agents_failed = any(e.startswith("claude agents:") for e in errors)
    return [
        {**r, "state": "ended" if r["isInteractive"] else "deleted", "kind": "idle", "detail": "", "intent": "", "refs": r["issues"], "isPast": True}
        for r in history.values()
        if r["id"] not in live and r.get("sessionId") and not (agents_failed and r["isInteractive"])
    ]


# --- sessions ----------------------------------------------------------------


def read_sessions(links: dict[str, list[str]], errors: list[str]) -> list[tuple[dict, list[Ref]]]:
    raw: list[tuple[dict, list[Ref]]] = []
    for d in sorted(JOBS.iterdir()) if JOBS.is_dir() else []:
        try:
            st = json.loads((d / "state.json").read_text())
        except FileNotFoundError, NotADirectoryError, json.JSONDecodeError:
            continue
        pr_hrefs = [c["href"] for c in st.get("children") or [] if c.get("kind") == "pr" and c.get("href")]
        refs = parse_refs(st.get("intent") or "") + parse_refs(" ".join(pr_hrefs))
        explicit = links.get(d.name, [])
        result = (st.get("output") or {}).get("result")
        raw.append(
            (
                {
                    "id": d.name,
                    "sessionId": st.get("sessionId") or "",
                    "name": st.get("name") or d.name,
                    "state": st.get("state") or "unknown",
                    "kind": kind_of(st.get("state") or ""),
                    "detail": st.get("detail") or "",
                    "intent": st.get("intent") or "",
                    "result": result if isinstance(result, str) else "",
                    "updatedAt": parse_time_ms(st.get("updatedAt") or st.get("createdAt")),
                    "refs": sorted({*map(key_of, refs), *explicit}),
                    "issues": list(explicit),
                    "cwd": st.get("cwd") or "",
                    "isInteractive": False,
                },
                refs,
            )
        )
    # Interactive sessions have no job dir and no `id`; they are keyed by their short session id.
    try:
        for a in json.loads(run(["claude", "agents", "--json"], timeout=30)):
            if a.get("kind") != "interactive" or not a.get("sessionId"):
                continue
            sid = a["sessionId"][:8]
            explicit = links.get(sid, [])
            state = a.get("status") or "unknown"
            raw.append(
                (
                    {
                        "id": sid,
                        "sessionId": a["sessionId"],
                        "name": a.get("name") or sid,
                        "state": state,
                        "kind": kind_of(state),
                        "detail": f"interactive in {a.get('cwd') or '?'}",
                        "intent": "",
                        "result": "",
                        "updatedAt": a.get("startedAt") or 0,
                        "refs": list(explicit),
                        "issues": list(explicit),
                        "cwd": a.get("cwd") or "",
                        "isInteractive": True,
                    },
                    [],
                )
            )
    except Exception as err:  # noqa: BLE001 - a missing CLI only costs the interactive rows
        errors.append(f"claude agents: {err}")
    return raw


def find_sessions(sessions: list[dict], needle: str) -> list[dict]:
    n = needle.lower()
    exact = [s for s in sessions if n in (s["id"], s["sessionId"], s["name"].lower())]
    return exact or [s for s in sessions if s["id"].startswith(n) or n in s["name"].lower()]


def search_text(row: dict) -> str:
    """What a filter matches on: a card's title, repo#N, status, labels, parent issue (title and repo#N) and session names; a session's name, id and refs."""
    if "sessions" in row:
        names = [s["name"] for s in [*row["sessions"], *row.get("pastSessions", [])]]
        parent = row.get("parent") or {}
        up = [parent["title"], parent["key"], parent["key"].split("/", 1)[1]] if parent else []
        parts = [row["title"], row["key"], row["key"].split("/", 1)[1], row["status"], *(lb["name"] for lb in row.get("labels") or []), *up, *names]
    else:
        parts = [row["name"], row["id"], *row.get("refs", [])]
    return " ".join(p for p in parts if p).lower()


def matches(row: dict, query: str) -> bool:
    """Every whitespace-separated word of the query appears somewhere in the row, ignoring case."""
    text = search_text(row)
    return all(word in text for word in query.lower().split())


# --- GitHub ------------------------------------------------------------------


def read_cache(name: str, ttl: float, force: bool, offline: bool) -> tuple[list | None, bool]:
    """(cached items, fresh enough to use): offline any age counts, force none does."""
    try:
        cached = json.loads((CONFIG / name).read_text())
        items, age = cached["items"], time.time() - cached["at"]
    except FileNotFoundError, json.JSONDecodeError, KeyError, TypeError:
        return None, False
    return items, offline or (not force and age < ttl)


def read_cache_extra(name: str, key: str) -> dict:
    """Another part of a cache file than its items, e.g. the projects' sprint fields; empty when there is none."""
    try:
        value = json.loads((CONFIG / name).read_text()).get(key)
    except FileNotFoundError, json.JSONDecodeError, AttributeError:
        return {}
    return value if isinstance(value, dict) else {}


def write_cache(name: str, items: list, errors: list[str], **extra: dict) -> None:
    try:
        CONFIG.mkdir(parents=True, exist_ok=True)
        (CONFIG / name).write_text(json.dumps({"at": time.time(), "items": items, **extra}))
    except OSError as err:  # a cache that can't be saved must not cost the fresh answer
        errors.append(f"{name}: {err}")


def load_assigned(force: bool, errors: list[str], offline: bool = False) -> list[dict]:
    """Open issues assigned to me, cached for ASSIGNED_TTL_S: `gh search` spends the GraphQL budget
    and takes over a second. Offline, or when GitHub errs, the last answer is used whatever its age."""
    cached, fresh = read_cache("assigned-cache.json", ASSIGNED_TTL_S, force, offline)
    if fresh or offline:
        return cached or []
    try:
        items = json.loads(run(["gh", "search", "issues", "--assignee", "@me", "--state", "open", "--limit", "100", "--json", "repository,number,title,url,updatedAt"]))
    except RateLimited:
        raise
    except Exception as err:  # noqa: BLE001
        errors.append(str(err))
        return cached or []
    write_cache("assigned-cache.json", items, errors)
    return items


def load_closed(force: bool, errors: list[str], offline: bool = False) -> list[dict]:
    """Issues closed in the last CLOSED_DAYS days that are assigned to me or sit in a backlog project,
    cached for CLOSED_TTL_S: one search and one page per backlog project, fetched in parallel.
    Offline, or when GitHub errs, the last answer is used whatever its age."""
    cached, fresh = read_cache("closed-cache.json", CLOSED_TTL_S, force, offline)
    if fresh or offline:
        return cached or []
    since = time.strftime("%Y-%m-%d", time.gmtime(time.time() - CLOSED_DAYS * 86_400))

    def assigned() -> list[dict]:
        fields = "repository,number,title,url,closedAt"
        rows = json.loads(run(["gh", "search", "issues", "--assignee", "@me", "--state", "closed", "--closed", f">={since}", "--sort", "updated", "--limit", "100", "--json", fields]))
        return [{"key": f"{r['repository']['nameWithOwner']}#{r['number']}".lower(), "title": r["title"], "url": r["url"], "closedAt": parse_time_ms(r.get("closedAt"))} for r in rows]

    def project(p: dict) -> list[dict]:
        # The items filter knows no closed date: closing an issue updates it, so `updated` bounds the pages.
        items, cursor = [], None
        while True:
            variables = {"owner": p["owner"], "number": int(p["number"]), "filter": f"is:closed is:issue updated:>={since}", **({"cursor": cursor} if cursor else {})}
            page = ((((graphql(CLOSED_PROJECT_QUERY, variables).get("data") or {}).get("repositoryOwner") or {}).get("projectV2") or {}).get("items")) or None
            if page is None:
                raise RuntimeError(f"project {p['owner']}/{p['number']}: closed issues not readable")
            for it in page["nodes"]:
                c = it.get("content") or {}
                if c.get("__typename") == "Issue" and (c.get("closedAt") or "")[:10] >= since:
                    items.append(
                        {"key": f"{c['repository']['nameWithOwner']}#{c['number']}".lower(), "title": c.get("title") or "", "url": c.get("url") or "", "closedAt": parse_time_ms(c["closedAt"])}
                    )
            if not page["pageInfo"]["hasNextPage"]:
                return items
            cursor = page["pageInfo"]["endCursor"]

    jobs = [assigned, *(lambda p=p: project(p) for p in PROJECTS if p["is_backlog"])]
    try:
        with ThreadPoolExecutor(len(jobs)) as pool:
            lists = list(pool.map(lambda job: job(), jobs))
    except RateLimited:
        raise
    except Exception as err:  # noqa: BLE001
        errors.append(str(err))
        return cached or []
    items = list({i["key"]: i for lst in lists for i in lst}.values())
    write_cache("closed-cache.json", items, errors)
    return items


def load_viewer(errors: list[str], offline: bool = False) -> str:
    """Your GitHub login, which tells your sub-issues from someone else's. Asked once and kept, since it doesn't
    change; empty while it is not known (offline before the first answer)."""
    cached, _ = read_cache("viewer-cache.json", float("inf"), False, offline)
    if cached:
        return str(cached[0])
    if offline:
        return ""
    try:
        login = ((graphql("{ viewer { login } }").get("data") or {}).get("viewer") or {}).get("login") or ""
    except RateLimited:
        raise
    except Exception as err:  # noqa: BLE001
        errors.append(f"viewer: {err}")
        return ""
    if login:
        write_cache("viewer-cache.json", [login], errors)
    return login


def project_id(p: dict) -> str:
    return f"{p['owner']}/{p['number']}"


def sprint_field_of(node: dict | None, name: str) -> dict:
    """A project's iteration field as the sprint rule uses it: its name, whether it is one, and its sprints."""
    config = (node or {}).get("configuration")
    if not config:  # no field by that name (GitHub answers null), or one that isn't an iteration field
        return {"name": name, "ok": False, "iterations": []}
    sprints = [*(config.get("iterations") or []), *(config.get("completedIterations") or [])]
    return {"name": name, "ok": True, "iterations": [{k: s.get(k) for k in ("id", "title", "startDate", "duration")} for s in sprints]}


def load_projects(force: bool, errors: list[str], offline: bool = False) -> tuple[list[dict], dict[str, dict]]:
    """The open issues of every project with their status, and each sprint project's iteration field by project.

    Status is read from the project side: issue-side projectItems is empty for cross-org projects.
    Offline, the cached lists are used whatever their age, and so is the cached list of a project GitHub won't answer for.
    A cache from before a project's sprint key was set (or changed) is out of date at once."""
    cached, fresh = read_cache("projects-cache.json", PROJECT_TTL_S, force, offline)
    cached_fields = read_cache_extra("projects-cache.json", "fields")
    fresh = fresh and all((cached_fields.get(project_id(p)) or {}).get("name") == p["sprint"] for p in PROJECTS if p.get("sprint"))
    if fresh or offline:
        return cached or [], cached_fields

    def fetch(p: dict) -> tuple[list[dict], dict | None] | None:
        # Status and issue identity only: 1 point per 100 items, where `gh project item-list` reads every field.
        # Open issues only: a closed issue is Done whatever its status, and pages are fetched one after another.
        items, cursor, field = [], None, None
        query, sprint = (SPRINT_PROJECT_QUERY, {"sprint": p["sprint"]}) if p.get("sprint") else (PROJECT_QUERY, {})
        while True:
            variables = {"owner": p["owner"], "number": int(p["number"]), **sprint, **({"cursor": cursor} if cursor else {})}
            try:
                project = (((graphql(query, variables).get("data") or {}).get("repositoryOwner") or {}).get("projectV2")) or {}
            except RateLimited:
                raise
            except Exception:  # noqa: BLE001 - no network, a timeout, an answer that isn't JSON
                project = {}
            page = project.get("items") or None
            if page is None:
                errors.append(f"project {project_id(p)}: not readable")
                return None
            if sprint and field is None:
                field = sprint_field_of(project.get("sprintField"), p["sprint"])
            for it in page["nodes"]:
                c = it.get("content") or {}
                if c.get("__typename") != "Issue":
                    continue
                items.append(
                    {
                        "key": f"{c['repository']['nameWithOwner']}#{c['number']}".lower(),
                        "title": c.get("title") or "",
                        "url": c.get("url") or "",
                        "status": (it.get("status") or {}).get("name") or "",
                        "isBacklog": p["is_backlog"],
                        "project": project_id(p),
                        "itemId": it.get("id") or "",
                        "sprint": sprint_value(it.get("sprint")),
                    }
                )
            if not page["pageInfo"]["hasNextPage"]:
                return items, field
            cursor = page["pageInfo"]["endCursor"]

    if not PROJECTS:
        return [], {}
    # Projects in parallel, pages within one in order: the wall time is the longest project's pages.
    with ThreadPoolExecutor(len(PROJECTS)) as pool:
        answers = list(pool.map(fetch, PROJECTS))
    # A project that can't be read (no network, a GitHub error) keeps its last cached list and field, in the projects' order.
    last = cached or []
    items = [i for p, a in zip(PROJECTS, answers) for i in (a[0] if a is not None else [c for c in last if c.get("project") == project_id(p)])]
    fields = {project_id(p): a[1] if a is not None else cached_fields.get(project_id(p)) for p, a in zip(PROJECTS, answers) if p.get("sprint")}
    fields = {k: f for k, f in fields.items() if f}
    if all(a is not None for a in answers):
        write_cache("projects-cache.json", items, errors, fields=fields)
    return items, fields


def sprint_value(value: dict | None) -> dict | None:
    """An item's sprint as the iteration value has it; None for no sprint (or a project with no sprint key)."""
    if not value or not value.get("iterationId"):
        return None
    return {"id": value["iterationId"], "title": value.get("title") or "", "startDate": value.get("startDate") or "", "duration": value.get("duration") or 0}


def current_sprint(field: dict, day: date) -> dict | None:
    """The sprint that has started and not ended; between two sprints, the most recently started one."""
    started = [s for s in field["iterations"] if s.get("startDate") and s["startDate"] <= day.isoformat()]
    return max(started, key=lambda s: s["startDate"], default=None)


SPRINT_RANK = {"current": 0, "leftover": 1, "planned": 2, "none": 3}


def issue_sprint(items: list[dict], current: dict[str, dict | None], day: date) -> tuple[dict, dict]:
    """An issue's sprint across the sprint projects it is on, and the project item it comes from.

    In the sprint (current, or a leftover from an earlier started sprint) wins over outside it (planned, or none)."""
    best: tuple[dict, dict] | None = None
    for it in items:
        s, cur = it.get("sprint"), current.get(it["project"])
        if not s:
            state = "none"
        elif s["startDate"] > day.isoformat():
            state = "planned"
        else:
            state = "current" if cur and s["id"] == cur["id"] else "leftover"
        if best is None or SPRINT_RANK[state] < SPRINT_RANK[best[0]["state"]]:
            best = ({"title": s["title"] if s else "", "state": state}, it)
    assert best is not None
    return best


def resolve_refs(refs: list[Ref], errors: list[str], force: bool = False, offline: bool = False, fresh_after: float | None = None, unreadable: set[str] | None = None) -> dict[str, dict]:
    """One GraphQL call per 40 refs: an issue resolves to itself with its card metadata, a PR to its
    own state and the issues it closes. Answers are cached for META_TTL_S, so a routine refresh only
    asks about refs it has not seen lately; offline, only cached answers are used, whatever their age.

    force asks again about every ref not fetched since fresh_after, so one forced build asks once.
    unreadable collects the refs GitHub answered nothing for (deleted, or no access)."""
    cache_file = CONFIG / "meta-cache.json"
    try:
        cache: dict[str, dict] = json.loads(cache_file.read_text())
    except FileNotFoundError, json.JSONDecodeError:
        cache = {}
    now = time.time()
    uniq = list({key_of(r): r for r in refs}.values())

    def outdated(entry: dict) -> bool:
        """Cached by an older version, before the parent issue, the sub-issues or the longer closing lists were read: asked again at once, whatever its age."""
        info = entry.get("info") or {}
        if info.get("kind") == "Issue":
            return "parent" not in info or "subIssueKeys" not in info
        return bool(info) and (info.get("closesRead") != CLOSES_READ or any("parent" not in i for i in info.get("closes") or []))

    def stale(r: Ref) -> bool:
        entry = cache.get(key_of(r), {})
        if force:
            return entry.get("at", 0) < (now if fresh_after is None else fresh_after)
        return now - entry.get("at", 0) >= META_TTL_S or outdated(entry)

    asked = [] if offline else [r for r in uniq if stale(r)]
    fetched = _fetch_refs(asked, errors)
    for r in asked:
        if key_of(r) in fetched or key_of(r) not in cache:
            cache[key_of(r)] = {"at": now, "info": fetched.get(key_of(r))}
    if asked:
        CONFIG.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache))
    if unreadable is not None:
        unreadable.update(key_of(r) for r in uniq if key_of(r) in cache and not cache[key_of(r)].get("info"))
    return {key_of(r): cache[key_of(r)]["info"] for r in uniq if (cache.get(key_of(r)) or {}).get("info")}


def _fetch_refs(uniq: list[Ref], errors: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    pr_fields = "number state url repository { nameWithOwner }"
    parent_fields = "number title url state repository { nameWithOwner } subIssuesSummary { total completed }"
    # A PR's closing issues read no connection: GraphQL charges per connection read, times the issues read.
    slim_fields = f"number title body url state updatedAt repository {{ nameWithOwner }} subIssuesSummary {{ total completed }} parent {{ {parent_fields} }}"
    fields = (
        f"{slim_fields} labels(first: 6) {{ nodes {{ name color }} }}"
        " assignees(first: 3) { nodes { login } }"
        f" closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {{ nodes {{ {pr_fields} }} }}"
        f" subIssues(first: {SUB_ISSUES_PER_ISSUE}) {{ totalCount nodes {{ number repository {{ nameWithOwner }} }} }}"
    )

    def to_pr(n: dict) -> dict:
        return {"key": f"{n['repository']['nameWithOwner']}#{n['number']}".lower(), "url": n["url"], "state": n["state"]}

    def sub_issues(n: dict) -> dict:
        sub = n.get("subIssuesSummary") or {}
        return {"total": sub.get("total") or 0, "completed": sub.get("completed") or 0}

    def to_parent(n: dict | None) -> dict | None:
        if not n:
            return None
        key = f"{n['repository']['nameWithOwner']}#{n['number']}".lower()
        return {"key": key, "title": n["title"], "url": n["url"], "isOpen": n["state"] == "OPEN", "subIssues": sub_issues(n)}

    def to_issue(n: dict) -> dict:
        issue = {
            "kind": "Issue",
            "key": f"{n['repository']['nameWithOwner']}#{n['number']}".lower(),
            "title": n["title"],
            "body": n.get("body") or "",
            "url": n["url"],
            "isOpen": n["state"] == "OPEN",
            "updatedAt": parse_time_ms(n.get("updatedAt")),
            "labels": [{"name": lb["name"], "color": lb["color"]} for lb in (n.get("labels") or {}).get("nodes") or []],
            "subIssues": sub_issues(n),
            "parent": to_parent(n.get("parent")),
            "assignees": [a["login"] for a in (n.get("assignees") or {}).get("nodes") or []],
            "prs": [to_pr(p) for p in (n.get("closedByPullRequestsReferences") or {}).get("nodes") or []],
        }
        if "subIssues" in n:  # looked up directly, not read from a PR's closing list
            nodes = (n["subIssues"] or {}).get("nodes") or []
            missing = max(0, ((n["subIssues"] or {}).get("totalCount") or 0) - len(nodes))
            full = len(nodes) >= SUB_ISSUES_PER_ISSUE  # past a full page they are unread; short of one, GitHub left them out
            issue["subIssueKeys"] = [f"{s['repository']['nameWithOwner']}#{s['number']}".lower() for s in nodes if s]
            issue["subIssuesUnread"] = {"hidden": sum(1 for s in nodes if not s) + (0 if full else missing), "beyond": missing if full else 0}
        return issue

    def fetch(chunk: list[Ref]) -> None:
        body = "\n".join(
            f'r{j}: repository(owner: "{o}", name: "{r}") {{ issueOrPullRequest(number: {n}) {{'
            f" __typename ... on Issue {{ {fields} }}"
            f" ... on PullRequest {{ {pr_fields} closingIssuesReferences(first: {CLOSES_READ}) {{ nodes {{ {slim_fields} }} }} }} }} }}"
            for j, (o, r, n) in enumerate(chunk)
        )
        try:
            data = graphql(f"query {{ {body} }}").get("data") or {}
        except RateLimited:
            raise
        except Exception as err:  # noqa: BLE001
            errors.append(f"graphql: {err}")
            return
        for j, r in enumerate(chunk):
            node = (data.get(f"r{j}") or {}).get("issueOrPullRequest")
            if not node:
                continue
            if node["__typename"] == "Issue":
                out[key_of(r)] = to_issue(node)
            else:
                closes = [to_issue(n) for n in (node.get("closingIssuesReferences") or {}).get("nodes") or [] if n]
                out[key_of(r)] = {"kind": "PullRequest", **to_pr(node), "closes": closes, "closesRead": CLOSES_READ}

    # Chunks in parallel: each takes over a second, and the recently closed issues add a couple.
    chunks = [uniq[i : i + 40] for i in range(0, len(uniq), 40)]
    if chunks:
        with ThreadPoolExecutor(min(len(chunks), 4)) as pool:
            list(pool.map(fetch, chunks))
    return out


def lookup_directly(keys: list[str], meta: dict[str, dict], errors: list[str], force: bool, offline: bool, fresh_after: float, unreadable: set[str] | None = None) -> None:
    """Put each issue's own lookup in meta, in place of what a PR's closing list said of it; cached ones cost nothing."""
    infos = resolve_refs([r for k in keys if (r := ref_from_arg(k))], errors, force, offline, fresh_after, unreadable)
    for key, info in infos.items():
        if info["kind"] == "Issue":
            meta[key] = info


def keep_finished_with_work_below(cards: list[dict], meta: dict[str, dict], errors: list[str], force: bool, offline: bool, fresh_after: float) -> None:
    """A finished issue stays on the board while a session anywhere in its subtree is live or recent.

    Its subtree is read only once it is back on the board, so this climbs the other way: from each issue with such a
    session up its parents, one batched lookup a level for the parents not known yet, and puts back every finished card
    on the way."""
    finished = {c["key"]: c for c in cards if c["searchOnly"] and not c["sprintFiltered"]}
    if not finished:
        return
    recent = now_ms() - RECENT_S * 1000
    level = [c["key"] for c in cards if any(s["kind"] != "idle" or s["updatedAt"] >= recent for s in c["sessions"])]
    seen = set(level)
    for _ in range(SUB_ISSUE_LEVELS):
        parents = list(dict.fromkeys(p["key"] for k in level if (p := (meta.get(k) or {}).get("parent")) and p["key"] not in seen))
        if not parents:
            break
        seen.update(parents)
        lookup_directly([p for p in parents if p not in meta], meta, errors, force, offline, fresh_after)
        for key in parents:
            if key in finished:
                finished[key]["searchOnly"] = False
        level = parents


def attach_subtrees(cards: list[dict], make_card, meta: dict[str, dict], errors: list[str], force: bool, offline: bool, fresh_after: float) -> None:
    """Give each card on the board its subtree, as nested cards, and say which cards are board issues.

    The sub-issues are looked up level by level from the cards on the board, every level in one
    batched lookup, up to SUB_ISSUE_LEVELS down. A search-only card's subtree is not read.
    A board issue is a card on the board that is in no other one's subtree."""
    roots = [c["key"] for c in cards if not c["searchOnly"]]
    unreadable: set[str] = set()
    read: dict[str, None] = {}  # the issues whose sub-issues were read, in walk order
    level = roots
    for _ in range(SUB_ISSUE_LEVELS):
        children = []
        for key in level:
            if key not in read and "subIssueKeys" in meta.get(key, {}):
                read[key] = None
                children += meta[key]["subIssueKeys"]
        if not children:
            break
        lookup_directly(list(dict.fromkeys(children)), meta, errors, force, offline, fresh_after, unreadable)
        level = children

    for key in read:
        unread = meta[key].get("subIssuesUnread") or {}
        if unread.get("hidden"):
            errors.append(f"{key}: {unread['hidden']} sub-issue(s) not readable, skipped")
        if unread.get("beyond"):
            errors.append(f"{key}: only the first {SUB_ISSUES_PER_ISSUE} sub-issues are read, {unread['beyond']} more are not shown")
        errors.extend(f"{sub}: sub-issue of {key} not readable, skipped" for sub in meta[key]["subIssueKeys"] if sub in unreadable)

    built = {c["key"]: c for c in cards}
    grown: set[str] = set()

    def grow(key: str, path: frozenset[str]) -> dict:
        card = built.get(key)
        if card is None:
            m = meta[key]
            card = built[key] = make_card(key, {"title": m["title"], "url": m["url"], "isOpen": m["isOpen"], "updatedAt": m["updatedAt"]})
        if key not in grown and key in read:
            grown.add(key)
            # The path guard keeps answers cached at different times from nesting an issue inside itself.
            card["subtree"] = [grow(sub, path | {key}) for sub in meta[key]["subIssueKeys"] if sub in meta and sub not in path | {key}]
        return card

    def descendants(card: dict, seen: set[str]) -> set[str]:
        for sub in card["subtree"]:
            if sub["key"] not in seen:
                seen.add(sub["key"])
                descendants(sub, seen)
        return seen

    for key in roots:
        grow(key, frozenset())
    inside = set().union(*(descendants(built[key], set()) for key in roots))
    for key in roots:
        built[key]["isBoardIssue"] = key not in inside


# --- board -------------------------------------------------------------------


def build_board(force: bool = False) -> dict:
    """The board. While GitHub is rate-limited (or a backoff from an earlier limit runs) it is built
    from the session files and cached GitHub data alone, without calling GitHub; sessions whose issue
    isn't cached land in Unlinked, and errors[0] says so.

    Cards marked searchOnly are finished issues that are off the board: only a search shows them.
    A card's pastSessions are the deleted or ended sessions that were on it; they place no card."""
    until = backoff_until()
    if until <= time.time():
        try:
            return _build_board(force, offline=False)
        except RateLimited:
            until = start_backoff()
    board = _build_board(False, offline=True)
    board["errors"].insert(0, f"GitHub rate limit: paused until {time.strftime('%H:%M:%S', time.localtime(until))}; showing cached GitHub data")
    board["isOffline"] = True
    return board


def cached_board() -> dict:
    """The board from the session files and cached GitHub data of any age, without calling GitHub:
    what the app draws at startup, before its first refresh answers."""
    return _build_board(False, offline=True)


def _build_board(force: bool, offline: bool) -> dict:
    started = time.time()
    errors: list[str] = []
    links = load_links()
    with ThreadPoolExecutor(5) as pool:
        f_sessions = pool.submit(read_sessions, links, errors)
        f_assigned = pool.submit(load_assigned, force, errors, offline)
        f_projects = pool.submit(load_projects, force, errors, offline)
        f_closed = pool.submit(load_closed, force, errors, offline)
        f_viewer = pool.submit(load_viewer, errors, offline)
        raw = f_sessions.result()
        assigned = f_assigned.result()
        project_items, fields = f_projects.result()
        closed = f_closed.result()
        viewer = f_viewer.result()

    # Sprint projects: those whose iteration field could be read. A misnamed field, or one never read, leaves its
    # project out of the sprint rule (fail open), so it can't hide the board.
    day = today()
    sprint_fields: dict[str, dict] = {}
    for p in PROJECTS:
        field = fields.get(project_id(p)) if p.get("sprint") else None
        if field and field["name"] == p["sprint"]:
            if field["ok"]:
                sprint_fields[project_id(p)] = field
            else:
                errors.append(f'project {project_id(p)}: no iteration field "{p["sprint"]}"')
    current = {pid: current_sprint(f, day) for pid, f in sprint_fields.items()}
    sprints = [{"project": pid, "title": s["title"], "daysLeft": (date.fromisoformat(s["startDate"]) + timedelta(days=s["duration"] or 0) - day).days} for pid, s in current.items() if s]
    on_sprint_projects: dict[str, list[dict]] = {}
    for it in project_items:
        if it["project"] in sprint_fields:
            on_sprint_projects.setdefault(it["key"], []).append(it)

    issues: dict[str, dict] = {}

    def add_issue(key: str, title: str, url: str, is_open: bool, updated_at: int, is_mine: bool = False) -> None:
        prev = issues.get(key, {})
        issues[key] = {
            "title": title or prev.get("title", ""),
            "url": url or prev.get("url", ""),
            "isOpen": is_open,
            "updatedAt": max(updated_at, prev.get("updatedAt", 0)),
            "isMine": is_mine or prev.get("isMine", False),
        }

    # Closed first: an issue reopened since the closed list was cached is open in the lists below.
    for i in closed:
        add_issue(i["key"], i["title"], i["url"], False, i["closedAt"], True)
    for i in assigned:
        add_issue(f"{i['repository']['nameWithOwner']}#{i['number']}".lower(), i["title"], i["url"], True, parse_time_ms(i.get("updatedAt")), True)
    status_of: dict[str, str] = {}
    status_item: dict[str, dict] = {}  # the project item that status came from, for set_status
    unset_item: dict[str, dict] = {}  # with no status on any project, the first project the issue is on
    for it in project_items:
        if it["status"] and it["key"] not in status_of:
            status_of[it["key"]] = it["status"]
            if it.get("itemId"):
                status_item[it["key"]] = {"project": it["project"], "itemId": it["itemId"]}
        elif not it["status"] and it.get("itemId"):
            unset_item.setdefault(it["key"], {"project": it["project"], "itemId": it["itemId"]})
        if it["isBacklog"] and it["status"] != "Done":
            add_issue(it["key"], it["title"], it["url"], True, 0, True)

    session_refs = [r for _, refs in raw for r in refs] + [r for keys in links.values() for k in keys if (r := ref_from_arg(k))]
    # Issues known only from search or a project need their metadata too; one pass resolves everything.
    known_refs = [r for k in issues if (r := ref_from_arg(k))]
    infos = resolve_refs(session_refs + known_refs, errors, force, offline, started)
    from_sessions = {key_of(r) for r in session_refs}
    meta: dict[str, dict] = {}  # full GraphQL view of each issue, for the card metadata
    closed_by: dict[str, list[dict]] = {}  # the session PRs that close each issue
    session_parents: list[str] = []  # the parents of the issues sessions are on, for keep_finished_with_work_below
    for key, info in infos.items():
        for i in [info] if info["kind"] == "Issue" else info["closes"]:
            if info["kind"] == "Issue" or i["key"] not in meta:  # a PR's closing list reads less than a direct lookup
                meta[i["key"]] = i
            if info["kind"] == "PullRequest":
                closed_by.setdefault(i["key"], []).append({"key": info["key"], "url": info["url"], "state": info["state"]})
            if key in from_sessions:
                add_issue(i["key"], i["title"], i["url"], i["isOpen"], i["updatedAt"])
                session_parents += [i["parent"]["key"]] if i.get("parent") else []
            elif i["key"] in issues:
                issues[i["key"]]["isOpen"] = i["isOpen"]
    # An issue known only from a PR's closing list is looked up directly, for its labels, closing PRs and sub-issues;
    # the parents of the issues sessions are on ride along, so the climb in keep_finished_with_work_below rarely asks again.
    lookup_directly(list(dict.fromkeys([*issues, *session_parents])), meta, errors, force, offline, started)

    sessions = []
    for sess, refs in raw:
        found = set(sess["issues"])
        prs: dict[str, dict] = {}
        for r in refs:
            info = infos.get(key_of(r))
            if not info:
                continue
            if info["kind"] == "Issue":
                found.add(info["key"])
            else:
                found.update(c["key"] for c in info["closes"])
                prs[info["key"]] = {"key": info["key"], "url": info["url"], "state": info["state"]}
        sessions.append({**sess, "issues": sorted(found), "prs": list(prs.values())})

    past = sorted(update_history(sessions, links, errors), key=lambda s: -s["updatedAt"])
    recent = now_ms() - RECENT_S * 1000
    closes_any = {k for k, info in infos.items() if info["kind"] == "PullRequest" and info["closes"]}

    def make_card(key: str, iss: dict) -> dict:
        ss = sorted((s for s in sessions if key in s["issues"]), key=lambda s: -s["updatedAt"])
        kinds = {s["kind"] for s in ss}
        session_column = "needs" if "needs" in kinds else "working" if "working" in kinds else "idle" if ss else "backlog"
        status = status_of.get(key, "")
        status_column = status_column_of(status, iss["isOpen"])
        last_touch = max((s["updatedAt"] for s in ss), default=0)
        # A finished issue stays on the board only while a session on it is live or recent; after that a search still finds it.
        search_only = status_column == "done" and session_column in ("idle", "backlog") and last_touch < recent
        # Closed issues are exempt: the project lists hold open issues only.
        sprint, sprint_item = issue_sprint(on_sprint_projects[key], current, day) if iss["isOpen"] and key in on_sprint_projects else (None, None)
        # Outside the sprint, an issue leaves the board unless a session is on it; a search still finds it.
        sprint_filtered = is_outside(sprint) and not ss and not search_only
        m = meta.get(key, {})
        # The PRs that close the issue, and those of its sessions that close no issue at all.
        loose = [p for s in ss for p in s["prs"] if p["key"] not in closes_any]
        prs = {p["key"]: p for p in [*m.get("prs", []), *closed_by.get(key, []), *loose]}
        return {
            "key": key,
            "url": iss["url"],
            "title": iss["title"],
            "isOpen": iss["isOpen"],
            "status": status if iss["isOpen"] else "Closed",
            "statusColumn": status_column,
            "statusItem": status_item.get(key) or unset_item.get(key),
            "sessionColumn": session_column,
            "searchOnly": search_only or sprint_filtered,
            "sprint": sprint,
            "sprintItem": {"project": sprint_item["project"], "itemId": sprint_item["itemId"]} if sprint_item else None,
            "sprintFiltered": sprint_filtered,
            "updatedAt": max(iss["updatedAt"], last_touch),
            "sessions": ss,
            "pastSessions": [s for s in past if key in s["issues"]],
            "prs": list(prs.values()),
            "labels": m.get("labels", []),
            "subIssues": m.get("subIssues", {"total": 0, "completed": 0}),
            "parent": m.get("parent"),
            "assignees": m.get("assignees", []),
            "body": m.get("body", ""),
            "isBoardIssue": False,
            "subtree": [],
        }

    cards = []
    for key, iss in issues.items():
        card = make_card(key, iss)
        if card["sessionColumn"] != "backlog" or iss["isMine"]:
            cards.append(card)
    keep_finished_with_work_below(cards, meta, errors, force, offline, started)
    attach_subtrees(cards, make_card, meta, errors, force, offline, started)
    roll_up(cards, load_hidden())
    # Unlinked means on no card and in no card's subtree: no issue found, or its issue has no card (e.g. GitHub couldn't be asked).
    on_card = set().union(*(subtree_keys(c) for c in cards))
    # A card inside a board issue's subtree shows only there: the flat list holds the board issues and the search-only cards under none.
    under_board = set().union(*(subtree_keys(c) - {c["key"]} for c in cards if c["isBoardIssue"]))
    cards = [c for c in cards if c["key"] not in under_board]
    cards.sort(key=board_order)
    unlinked_since = now_ms() - UNLINKED_DAYS * 86_400_000
    unlinked = sorted(
        (s for s in sessions if not on_card.intersection(s["issues"]) and (s["kind"] != "idle" or s["updatedAt"] >= unlinked_since)),
        key=lambda s: -s["updatedAt"],
    )
    return {"cards": cards, "unlinked": unlinked, "sprints": sprints, "viewer": viewer, "fetchedAt": now_ms(), "errors": errors, "isOffline": False}


def subtree_keys(card: dict) -> set[str]:
    """The keys of a card and of every issue in its subtree."""
    keys: set[str] = set()
    todo = [card]
    while todo:
        c = todo.pop()
        if c["key"] not in keys:
            keys.add(c["key"])
            todo += c.get("subtree") or []
    return keys


def session_state(sessions: list[dict]) -> str:
    """The session column of an issue with these sessions: needs you, working, idle, or none (backlog)."""
    kinds = {s["kind"] for s in sessions}
    return "needs" if "needs" in kinds else "working" if "working" in kinds else "idle" if sessions else "backlog"


def subtree_sessions(card: dict, hidden: set[str]) -> list[tuple[dict, list[str]]]:
    """The sessions on the issues of a card's subtree that are not on the card itself, newest first, each once with
    the sub-issues it is on in tree order. A hidden sub-issue and its subtree are left out."""
    own = {s["id"] for s in card["sessions"]}
    found: dict[str, tuple[dict, list[str]]] = {}
    for sub in every_sub_issue(card, hidden)[1:]:
        for s in sub["sessions"]:
            if s["id"] not in own:
                found.setdefault(s["id"], (s, []))[1].append(sub["key"])
    return sorted(found.values(), key=lambda f: -f[0]["updatedAt"])


def roll_up(cards: list[dict], hidden: set[str]) -> None:
    """Give every card in the trees its roll-up: the state, sessions and PRs of the card and its subtree, and
    how many of the issues under it are closed. A hidden sub-issue and its subtree leave the sessions and PRs
    of every card above it, and still count in subIssues. A card whose subtree was not read keeps GitHub's own count."""
    done: set[int] = set()
    todo = list(cards)
    while todo:
        card = todo.pop()
        if id(card) in done:
            continue
        done.add(id(card))
        todo += card["subtree"]
        sessions = [*card["sessions"], *(s for s, _ in subtree_sessions(card, hidden))]
        under = every_sub_issue(card)[1:]
        prs = {p["key"]: p for c in every_sub_issue(card, hidden) for p in c["prs"]}
        card["rollup"] = {
            "sessionColumn": session_state(sessions),
            "sessions": [s["id"] for s in sessions],
            "live": sum(1 for s in sessions if s["kind"] != "idle"),
            "prs": list(prs.values()),
            "subIssues": {"total": len(under), "completed": sum(1 for c in under if not c["isOpen"])} if under else card["subIssues"],
        }


def every_sub_issue(card: dict, hidden: set[str] = frozenset()) -> list[dict]:
    """The card and every issue in its subtree, each once, the card first; less the hidden sub-issues and their subtrees."""
    out: dict[str, dict] = {}
    todo = [card]
    while todo:
        c = todo.pop(0)
        if c["key"] not in out and (c is card or c["key"] not in hidden):
            out[c["key"]] = c
            todo += c["subtree"]
    return list(out.values())


def board_order(card: dict) -> tuple[int, int]:
    """Cards with a session needing you anywhere under them first, then working ones, then the most recently updated."""
    return ({"needs": 0, "working": 1}.get(card["rollup"]["sessionColumn"], 2), -card["updatedAt"])


def columns(grouping: str) -> list[tuple[str, str]]:
    return STATUS_COLUMNS if grouping == "status" else SESSION_COLUMNS


def column_of(card: dict, grouping: str) -> str:
    return STATUS_BUCKET[card["statusColumn"]] if grouping == "status" else SESSION_BUCKET[card["rollup"]["sessionColumn"]]


def column_cards(cards: list[dict], col_id: str, grouping: str) -> list[dict]:
    """A column's cards in board order; by status, Backlog cards come after the other not-started ones."""
    members = [c for c in cards if column_of(c, grouping) == col_id]
    if grouping == "status":
        members.sort(key=lambda c: c["statusColumn"] == "backlog")
    return members


def groups(cards: list[dict], grouping: str) -> list[dict]:
    """Every column of a grouping, by issue status or by session state, with its cards."""
    return [{"id": col_id, "title": title, "cards": column_cards(cards, col_id, grouping)} for col_id, title in columns(grouping)]


def badge(card: dict) -> str:
    """The roll-up's sessions: `! 2s/1 live`."""
    rollup = card["rollup"]
    if not rollup["sessions"]:
        return ""
    mark = {"needs": "! ", "working": "~ "}.get(rollup["sessionColumn"], "")
    return f"{mark}{len(rollup['sessions'])}s" + (f"/{rollup['live']} live" if rollup["live"] else "")


def ago(ms: int) -> str:
    m = max(0, round((now_ms() - ms) / 60_000))
    return f"{m}m" if m < 60 else f"{round(m / 60)}h" if m < 2880 else f"{round(m / 1440)}d"


def summary(board: dict, grouping: str) -> str:
    """The board as text: the board issues in their columns, each with its subtree indented beneath it."""
    lines = []
    on_board = [c for c in board["cards"] if not c.get("searchOnly")]

    def sessions(card: dict, depth: int) -> list[str]:
        return [f'{"  " * depth}- {s["id"]} {s["state"]} "{s["name"]}" {ago(s["updatedAt"])}' for s in card["sessions"]]

    def subtree(card: dict, depth: int) -> list[str]:
        out = []
        for sub in card.get("subtree") or []:
            tag = " · ".join(t for t in (sub["status"], badge(sub)) if t)
            out += [f"{'  ' * depth}- {sub['key']} {sub['title']}" + (f" [{tag}]" if tag else ""), *sessions(sub, depth + 1), *subtree(sub, depth + 1)]
        return out

    for group in groups(on_board, grouping):
        cs = group["cards"]
        lines.append(f"## {group['title']} ({len(cs)})")
        for c in cs:
            tag = badge(c) if grouping == "status" else c["status"]
            lines.append(f"- {c['key']} {c['title']}" + (f" [{tag}]" if tag else ""))
            lines += sessions(c, 1)
            lines += subtree(c, 1)
    lines.append(f"## Unlinked sessions ({len(board['unlinked'])})")
    lines += [f'- {s["id"]} {s["state"]} "{s["name"]}" {ago(s["updatedAt"])}' for s in board["unlinked"]]
    if board["errors"]:
        lines += ["## Errors", *[f"- {e}" for e in board["errors"]]]
    return "\n".join(lines)


# --- actions -----------------------------------------------------------------

# Given the tty of a tab already attached to the session, brings that tab forward instead of opening one.
ITERM_SCRIPT = """
on run argv
  set cmd to item 1 of argv
  set ttyPath to item 2 of argv
  tell application "iTerm2"
    activate
    if ttyPath is not "" then
      repeat with w in windows
        repeat with t in tabs of w
          repeat with s in sessions of t
            if tty of s is ttyPath then
              select w
              tell t to select
              tell s to select
              return "focused"
            end if
          end repeat
        end repeat
      end repeat
    end if
    if (count of windows) = 0 then
      set s to current session of (create window with default profile)
    else
      tell current window to set t to (create tab with default profile)
      set s to current session of t
    end if
    tell s to write text cmd
    return "opened"
  end tell
end run
"""
# Ghostty (1.3 or later) takes the command as the new shell's first input and the tab name as an override.
GHOSTTY_SCRIPT = """
on run argv
  set cmd to item 1 of argv
  set tabName to item 2 of argv
  tell application "Ghostty"
    activate
    set cfg to new surface configuration
    set initial input of cfg to cmd & linefeed
    if (count of windows) = 0 then
      set t to selected tab of (new window with configuration cfg)
    else
      set t to new tab in front window with configuration cfg
    end if
    set name override of t to tabName
  end tell
end run
"""


def attached_tty(session_id: str) -> str:
    """The tty of a terminal running `claude attach <session_id>`, or "" when none does."""
    try:
        out = run(["ps", "-ax", "-o", "tty=,command="])
    except RuntimeError:
        return ""
    for line in out.splitlines():
        tty, _, command = line.strip().partition(" ")
        argv = command.split()
        if len(argv) == 3 and Path(argv[0]).name == "claude" and argv[1:] == ["attach", session_id] and tty.startswith("tty"):
            return f"/dev/{tty}"
    return ""


def attach(session_id: str, title: str = "", dry_run: bool = False, cwd: str = "") -> str:
    """Open a new tab in TERMINAL attached to the session; detaching leaves the tab's shell open.

    Returns the command the new tab runs, or "" when an iTerm2 tab already attached to the session
    was brought forward instead: that tab is found by the tty its `claude attach` runs on.

    The tab closes itself once the session is deleted: `claude attach` returns when its session is
    removed, and the shell then exits because the job directory is gone.

    The tab first moves to the session's own directory: waking a finished session starts Claude in
    the shell's directory, which in a new tab is the home directory. The tab is named with an OSC 1
    escape from its own shell, as iTerm2 does not show a name set through AppleScript, and Claude
    Code's own terminal title is switched off so the tab keeps that name. Ghostty ignores the escape
    and gets the name as a tab name override instead."""
    name = shlex.quote(title or session_id)
    cmd = f"printf '\\033]1;%s\\007' {name} && cd {shlex.quote(cwd or str(SESSION_CWD))} && CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 claude attach {session_id}; [ -d {shlex.quote(str(JOBS / session_id))} ] || exit"
    if dry_run:
        return cmd
    if TERMINAL == "ghostty":
        run(["osascript", "-e", GHOSTTY_SCRIPT, cmd, title or session_id], timeout=20)
        return cmd
    return "" if run(["osascript", "-e", ITERM_SCRIPT, cmd, attached_tty(session_id)], timeout=20).strip() == "focused" else cmd


def new_session(target: str, prompt: str = "", title: str = "") -> str:
    ref = ref_from_arg(target)
    if not ref:
        raise ValueError("expected an issue URL or owner/repo#N")
    url = target if target.startswith("https://") else f"https://github.com/{ref[0]}/{ref[1]}/issues/{ref[2]}"
    ask = prompt or "Pick up this issue: read it and its comments, then tell me where it stands and the next step."
    # Only the default prompt is shared by every session on an issue; a custom one lets Claude name the session after it.
    sid = start_background(f"{url}\n\n{ask}", "" if prompt else f"{ref[1]}#{ref[2]} {title}")
    link(sid, key_of(ref))
    return sid


def new_unlinked_session(prompt: str, name: str = "") -> str:
    """A background session on no issue; without a name Claude Code names it from the prompt."""
    if not prompt.strip():
        raise ValueError("a session on no issue needs a prompt")
    return start_background(prompt.strip(), name)


def start_background(prompt: str, name: str = "") -> str:
    argv = ["claude", "--bg", *(["--name", name.strip()[:60]] if name.strip() else []), prompt]
    out = run(argv, cwd=SESSION_CWD)
    m = re.search(r"\b[0-9a-f]{8}\b", out)
    if not m:
        raise RuntimeError(f"no session id in: {out.strip()}")
    return m[0]


def create_issue(repo: str, title: str, body: str = "", assign: bool = True) -> dict:
    """File an issue and return its card fields.

    An assigned issue is added to the assigned-issue cache, so the next refresh shows its card without
    waiting out the cache or the search index, which lags behind a new issue."""
    if not title.strip():
        raise ValueError("an issue needs a title")
    argv = ["gh", "issue", "create", "--repo", repo, "--title", title.strip(), "--body", body]
    if assign:
        argv += ["--assignee", "@me"]
    out = run(argv)
    m = URL_RE.search(out)
    if not m:
        raise RuntimeError(f"no issue URL in: {out.strip()[:200]}")
    url = m[0] if m[0].startswith("https://") else f"https://{m[0]}"
    issue = {"repository": {"nameWithOwner": f"{m[1]}/{m[2]}"}, "number": int(m[3]), "title": title.strip(), "url": url, "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if assign:
        try:
            cached = json.loads((CONFIG / "assigned-cache.json").read_text())
            cached["items"] = [issue, *cached["items"]]
            (CONFIG / "assigned-cache.json").write_text(json.dumps(cached))
        except OSError, json.JSONDecodeError, KeyError, TypeError:
            pass  # no cache yet: the next refresh searches anyway
    return {"key": key_of((m[1], m[2], int(m[3]))), "url": url, "title": issue["title"]}


def status_field(project: str) -> dict:
    """The Status field of a project (owner/number): the project's id, the field's id and its options."""
    if project not in STATUS_FIELDS:
        owner, number = project.split("/")
        proj = ((graphql(STATUS_FIELD_QUERY, {"owner": owner, "number": int(number)}).get("data") or {}).get("repositoryOwner") or {}).get("projectV2") or {}
        field = proj.get("field") or {}
        if not proj.get("id") or not field.get("id") or not field.get("options"):
            raise RuntimeError(f"project {project} has no Status field")
        STATUS_FIELDS[project] = {"project": proj["id"], "field": field["id"], "options": field["options"]}
    return STATUS_FIELDS[project]


def set_status(card: dict, name: str) -> str:
    """Set a card's status, on the project its status came from, to the option of that name (case ignored).

    Returns the option's own name. The cached project list is patched, so the next refresh shows
    the change without refetching."""
    item = card.get("statusItem")
    if not item:
        raise RuntimeError(f"{card['key']} is on none of the configured projects")
    field = status_field(item["project"])
    option = next((o for o in field["options"] if o["name"].lower() == name.lower()), None)
    if not option:
        raise RuntimeError(f"project {item['project']} has no Status option {name}")
    body = graphql(SET_STATUS_MUTATION, {"project": field["project"], "item": item["itemId"], "field": field["field"], "option": option["id"]})
    if not ((body.get("data") or {}).get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item"):
        STATUS_FIELDS.pop(item["project"], None)  # the options may have changed since they were read
        raise RuntimeError(f"status not changed: {first_error(body)}")
    patch_projects_cache(item["itemId"], {"status": option["name"]})
    return option["name"]


def start_progress(card: dict) -> bool:
    """Move a card whose status is Todo or Backlog to In Progress, on the project its status came from.

    False when there is nothing to move: another status, or no project item recorded for it."""
    if card.get("status") not in NOT_STARTED or not card.get("statusItem"):
        return False
    set_status(card, "In Progress")
    return True


def first_error(body: dict) -> str:
    return (body.get("errors") or [{}])[0].get("message", "no answer")


def patch_projects_cache(item_id: str, fields: dict, new: dict | None = None) -> None:
    """Apply a write to the cached project lists, so the next refresh shows it without refetching:
    set fields on the item, or add new with them when the cache doesn't hold the item yet."""
    try:
        cached = json.loads((CONFIG / "projects-cache.json").read_text())
        found = False
        for it in cached["items"]:
            if it.get("itemId") == item_id:
                it.update(fields)
                found = True
        if not found and new is not None:
            cached["items"].append({**new, **fields})
        (CONFIG / "projects-cache.json").write_text(json.dumps(cached))
    except OSError, json.JSONDecodeError, KeyError, TypeError:
        pass  # no cache: the next refresh fetches the projects anyway


def sprint_target(p: dict, issue: Ref | None = None) -> dict:
    """What a sprint write on sprint project p needs: the project and field ids, the current sprint, and with issue that issue's id."""
    variables = {"owner": p["owner"], "number": int(p["number"]), "sprint": p["sprint"]}
    if issue:
        variables |= {"repoOwner": issue[0], "repo": issue[1], "issue": issue[2]}
    data = graphql(SPRINT_ISSUE_TARGET_QUERY if issue else SPRINT_TARGET_QUERY, variables).get("data") or {}
    proj = (data.get("repositoryOwner") or {}).get("projectV2") or {}
    node = proj.get("sprintField") or {}
    if not proj.get("id") or not node.get("id"):
        raise RuntimeError(f'project {project_id(p)}: no iteration field "{p["sprint"]}"')
    sprint = current_sprint(sprint_field_of(node, p["sprint"]), today())
    if not sprint:
        raise RuntimeError(f"project {project_id(p)}: no sprint has started")
    target = {"project": proj["id"], "field": node["id"], "sprint": sprint}
    if issue:
        target["content"] = ((data.get("repository") or {}).get("issue") or {}).get("id")
        if not target["content"]:
            raise RuntimeError(f"{key_of(issue)}: issue not found")
    return target


def set_sprint(target: dict, item_id: str) -> None:
    body = graphql(SET_ITERATION_MUTATION, {"project": target["project"], "item": item_id, "field": target["field"], "iteration": target["sprint"]["id"]})
    if not ((body.get("data") or {}).get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item"):
        raise RuntimeError(f"sprint not set: {first_error(body)}")


def is_outside(sprint: dict | None) -> bool:
    """Outside the sprint: on a sprint project with no sprint, or planned for one that hasn't started. None is on no sprint project."""
    return bool(sprint) and sprint["state"] in ("planned", "none")


def first_sprint_project() -> dict | None:
    """The sprint project new issues go into, and whose current sprint the status line names."""
    return next((p for p in PROJECTS if p.get("sprint")), None)


def current_sprint_title(board: dict, project: str) -> str:
    """The title of a sprint project's current sprint on the board; empty when none is known (no sprint started, or its field unread)."""
    return next((s["title"] for s in board.get("sprints") or [] if s["project"] == project), "")


def sprint_to_join(card: dict, board: dict) -> str:
    """The current sprint join_current_sprint would put the card in; empty when it is in the sprint or on no sprint project."""
    return current_sprint_title(board, card["sprintItem"]["project"]) if is_outside(card.get("sprint")) and card.get("sprintItem") else ""


def join_current_sprint(card: dict) -> bool:
    """Put an issue outside the sprint (no sprint, or planned for a later one) in the current sprint of its sprint project.

    False when there is nothing to move: it is in the sprint already, or on no sprint project."""
    item = card.get("sprintItem")
    p = next((p for p in PROJECTS if p.get("sprint") and item and project_id(p) == item["project"]), None)
    if p is None or not is_outside(card.get("sprint")):
        return False
    target = sprint_target(p)
    set_sprint(target, item["itemId"])
    patch_projects_cache(item["itemId"], {"sprint": target["sprint"]})
    return True


def add_to_sprint(issue: dict) -> str:
    """Add an issue ({key, title, url}) to the first sprint project and put it in that project's current sprint; returns the sprint's title."""
    p = first_sprint_project()
    ref = ref_from_arg(issue["key"])
    if p is None or ref is None:
        raise ValueError("no sprint project in config.toml")
    target = sprint_target(p, ref)
    body = graphql(ADD_ITEM_MUTATION, {"project": target["project"], "content": target["content"]})
    item_id = (((body.get("data") or {}).get("addProjectV2ItemById") or {}).get("item") or {}).get("id")
    if not item_id:
        raise RuntimeError(f"not added to project {project_id(p)}: {first_error(body)}")
    set_sprint(target, item_id)
    entry = {"key": issue["key"], "title": issue["title"], "url": issue["url"], "status": "", "isBacklog": p["is_backlog"], "project": project_id(p), "itemId": item_id}
    patch_projects_cache(item_id, {"sprint": target["sprint"]}, new=entry)
    return target["sprint"]["title"]


def resume_session(needle: str) -> dict:
    """Bring a deleted or ended session back as a background session, and return its history record.

    `claude --bg --resume` continues the conversation under the same id; it runs in the session's own
    directory, where Claude Code keeps the conversation. The issues the session was on are linked
    again, since deleting it dropped its manual links. A session that is still listed is left alone."""
    history = load_history()
    n = needle.lower()
    found = [r for r in history.values() if r.get("sessionId") and (n in (r["id"], r["sessionId"]) or n == r["name"].lower())]
    found = found or [r for r in history.values() if r.get("sessionId") and (r["id"].startswith(n) or n in r["name"].lower())]
    if len(found) != 1:
        raise ValueError(f'"{needle}" matches {", ".join(r["id"] for r in found) or "no recorded session"}; use an exact id')
    rec = found[0]
    if (JOBS / rec["id"]).is_dir():
        return rec
    cwd = Path(rec["cwd"]) if rec["cwd"] and Path(rec["cwd"]).is_dir() else SESSION_CWD
    named = ["--name", rec["name"][:60]] if rec["name"] != rec["id"] else []
    out = run(["claude", "--bg", *named, "--resume", rec["sessionId"]], cwd=cwd)
    m = re.search(r"\b[0-9a-f]{8}\b", out)
    if not m:
        raise RuntimeError(f"no session id in: {out.strip()}")
    for key in rec["issues"]:
        link(m[0], key)
    return {**rec, "id": m[0], "cwd": str(cwd)}


def stop_session(session_id: str) -> str:
    """Stop a background session; its conversation is kept and attaching resumes it."""
    return run(["claude", "stop", session_id], timeout=30).strip()


def delete_session(session_id: str) -> str:
    """Delete a background session and its worktree, and drop its manual links.

    A session no board build has recorded yet (one deleting itself) is recorded first from its job
    file and manual links, so its card still lists it for resuming.

    claude rm refuses a worktree with unpushed commits; its message, raised here, names the flag that discards them."""
    links = load_links()
    history = load_history()
    if session_id not in history and links.get(session_id):
        with suppress(OSError, json.JSONDecodeError):
            st = json.loads((JOBS / session_id / "state.json").read_text())
            when = parse_time_ms(st.get("updatedAt") or st.get("createdAt"))
            history[session_id] = history_record({"id": session_id, "sessionId": st.get("sessionId"), "name": st.get("name"), "cwd": st.get("cwd"), "issues": links[session_id], "updatedAt": when})
            save_history(history)
    unlink(session_id, None)  # before the delete: a session deleting itself does not outlive `claude rm`
    try:
        return run(["claude", "rm", session_id], timeout=60).strip()
    except Exception:
        save_links(links)
        raise


def link(session_id: str, issue_key: str) -> None:
    links = load_links()
    links[session_id] = sorted({*links.get(session_id, []), issue_key})
    save_links(links)


def unlink(session_id: str, issue_key: str | None) -> None:
    links = load_links()
    kept = [k for k in links.get(session_id, []) if issue_key and k != issue_key]
    if kept:
        links[session_id] = kept
    else:
        links.pop(session_id, None)
    save_links(links)


def resolve_session(needle: str) -> dict:
    matches = find_sessions([s for s, _ in read_sessions(load_links(), [])], needle)
    if len(matches) != 1:
        ids = ", ".join(s["id"] for s in matches) or "none"
        raise ValueError(f'"{needle}" matches {ids}; use an exact id')
    return matches[0]


def own_session() -> dict:
    """The session this process runs inside, from the environment Claude Code gives its tools."""
    job, sid = os.environ.get("CLAUDE_JOB_DIR", ""), os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not job and not sid:
        raise ValueError("--self works only inside a Claude Code session")
    return {"id": Path(job).name if job else sid[:8], "sessionId": sid, "name": "this session", "isInteractive": not job}


def main(argv: list[str] | None = None) -> int:
    try:
        load_config()
    except ValueError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    ap = argparse.ArgumentParser(prog="tavla", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("json", help="print the board as JSON")
    p.add_argument("--force", action="store_true", help="bypass the project status cache")
    p = sub.add_parser("dump", help="print the board as text")
    p.add_argument("--group", choices=["status", "session"], default="status")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("attach", help="attach to a session in a new terminal tab")
    p.add_argument("session")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("new", help="start a background session on an issue")
    p.add_argument("issue")
    p.add_argument("prompt", nargs="*")
    p = sub.add_parser("start", help="start a background session on no issue")
    p.add_argument("prompt", nargs="+")
    p.add_argument("--name", default="")
    p = sub.add_parser("create", help="create an issue assigned to me")
    p.add_argument("title")
    p.add_argument("--repo", default=ISSUE_REPOS[0][0] if ISSUE_REPOS else None, help="owner/repo; defaults to the first issue_repos entry in config.toml")
    p.add_argument("--body", default="")
    p = sub.add_parser("resume", help="bring a deleted session back as a background session, under the same id")
    p.add_argument("session")
    p = sub.add_parser("stop", help="stop a background session, keeping its conversation")
    p.add_argument("session")
    p = sub.add_parser("rm", help="delete a background session and its worktree")
    p.add_argument("session", nargs="?")
    p.add_argument("--self", dest="own", action="store_true", help="the session this command runs inside")
    p = sub.add_parser("link", help="link a session to an issue")
    p.add_argument("target", nargs="*", metavar="session|issue", help="<session> <issue>, or with --self only <issue>")
    p.add_argument("--self", dest="own", action="store_true", help="the session this command runs inside")
    p = sub.add_parser("unlink", help="remove manual links")
    p.add_argument("session")
    p.add_argument("issue", nargs="?")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "json":
            print(json.dumps(build_board(a.force)))
        elif a.cmd == "dump":
            print(summary(build_board(a.force), a.group))
        elif a.cmd == "attach":
            s = resolve_session(a.session)
            if s["isInteractive"]:
                raise ValueError(f"{s['id']} is an interactive session running in its own terminal; only background sessions attach")
            print(attach(s["id"], s["name"], a.dry_run, s["cwd"]) or f"switched to the tab already attached to {s['id']}")
        elif a.cmd == "new":
            print(new_session(a.issue, " ".join(a.prompt)))
        elif a.cmd == "start":
            print(new_unlinked_session(" ".join(a.prompt), a.name))
        elif a.cmd == "create":
            if not a.repo:
                raise ValueError("no --repo given and no issue_repos in config.toml")
            print(create_issue(a.repo, a.title, a.body)["url"])
        elif a.cmd == "resume":
            rec = resume_session(a.session)
            print(f'Resumed {rec["id"]} ("{rec["name"]}"); tavla attach {rec["id"]} opens it.')
        elif a.cmd in ("stop", "rm"):
            own = getattr(a, "own", False)
            if own == bool(a.session):
                raise ValueError("give a session or --self")
            s = own_session() if own else resolve_session(a.session)
            if s["isInteractive"]:
                raise ValueError(f"{s['id']} is an interactive session running in its own terminal; only background sessions stop or delete")
            print(stop_session(s["id"]) if a.cmd == "stop" else delete_session(s["id"]))
        elif a.cmd == "link":
            if len(a.target) != (1 if a.own else 2):
                raise ValueError("expected --self <issue>" if a.own else "expected <session> <issue>")
            s, ref = own_session() if a.own else resolve_session(a.target[0]), ref_from_arg(a.target[-1])
            if not ref:
                raise ValueError("expected an issue URL or owner/repo#N")
            link(s["id"], key_of(ref))
            print(f'Linked {s["id"]} ("{s["name"]}") to {key_of(ref)}.')
        elif a.cmd == "unlink":
            s, ref = resolve_session(a.session), ref_from_arg(a.issue or "")
            unlink(s["id"], key_of(ref) if ref else None)
            print(f"Removed manual links from {s['id']}. Links from its first prompt or PRs stay.")
    except (ValueError, RuntimeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

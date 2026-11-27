# Tavla

A kanban board of GitHub issues, each card listing the Claude Code sessions working on it.

## Sprints

**Sprint**:
One iteration of a sprint project's iteration field: a title, a start date and a length in days.
_Avoid_: Iteration, cycle

**Sprint project**:
A configured project whose iteration field is named in the config, so its issues can be placed in sprints.

**Current sprint**:
The sprint of a sprint project that has started and not yet ended; between two sprints, the most recently started one.

**Started sprint**:
A sprint whose start date is today or earlier: the current sprint and every sprint before it.

**In the sprint**:
Said of an issue whose sprint, on any sprint project, is a started sprint.
_Avoid_: Committed

**Planned**:
Said of an issue whose sprint has not started yet: planning, not a problem.
_Avoid_: Future, scheduled

**Leftover**:
An open issue in a started sprint that is not the current sprint: committed to earlier and not finished.
_Avoid_: Carry-over, spillover

**Outside the sprint**:
Said of an issue on a sprint project that is not in the sprint (no sprint, or a sprint that hasn't started) and not covered by it.

**Covered by the sprint**:
Said of an issue in the subtree of a board issue that is in the sprint: it needs no sprint of its own.
_Avoid_: Inherited sprint

## Hierarchy

**Sub-issue**:
An issue GitHub records as the child of another issue, its parent. An issue has at most one parent.
_Avoid_: Task, child issue

**Subtree**:
An issue's sub-issues, their sub-issues, and so on down.

**Board issue**:
An issue with a card on the board itself: yours and in the sprint or on no sprint project, or carrying a session, and with no ancestor that is a board issue. Every other issue shows only inside the board issue whose subtree it is in.
_Avoid_: Root issue, top-level issue, parent

**Open**:
To show an issue's sub-issues as the cards of the board, in place of the level above.
_Avoid_: Enter, drill in, expand

**Direct session**:
A session linked to the issue itself, as opposed to one linked to an issue in its subtree.

**Roll-up**:
What an issue's card shows of its whole subtree rather than of the issue alone: the state of the sessions, the PRs, and how many of the issues are done.

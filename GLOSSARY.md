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
Said of an issue on a sprint project that is not in the sprint: no sprint, or a sprint that hasn't started.

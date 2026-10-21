# Continuing a run

Use this when step 0 finds a run in this repository that isn't finished: it
stopped early, its session ended, or its context ran out. Everything the run
needs is in `<state>`. Work through these in order. **Any question the resume
needs, it asks here, in one message, before continuing**: once it goes back
to step 5 of the playbook, the run is unattended again and nothing asks.

## 1. Make sure no other session is running it

`$FIX lock`. If it is `held`, a session crashed or **is still running**,
perhaps waiting on a subagent or paused on the throttle. Show the user the
lock's start and heartbeat times and ask them to confirm that session has
stopped. Never take a lock without that confirmation, however old it looks.
On their yes, `$FIX lock --take --force`. With no lock, `$FIX lock --take`.

## 2. Show where it stands, and ask once

`$FIX status`, and the log's recent lines. Tell the user, in one message:

- the counts by status, the task in progress if any, and why the run stopped
  (the log's last `run stopped:` line, if there is one)
- the bounces waiting on a person (`intent`, `disputed`), each with what is
  in question
- that continuing redoes any task left in progress from its start
- **the question**: go on as it is, or first requeue any bounced or blocked
  task, with the user's answer as a note for its next attempt

A request that already said to continue, with nothing to requeue, needs no
question: show the same message and go on.

On the go-ahead, for each task the user wants back:
`$FIX requeue <ID> --note "<what the user decided>"`. What it blocked is
released too, as far as its other prerequisites allow. Changing anything
else, the commands or the scope, is a new run, not a resume. Requeue works
only while the run is unfinished; once it has finished, bounced findings go
in a follow-up run (*A follow-up run* in [../SKILL.md](../SKILL.md)).

## 3. Put the tree back where the run left it

- **The run branch** must be checked out. If another branch is, and tracked
  files are changed, show the user and stop: that is someone's work. Otherwise
  `git switch <run branch>`.
- **A task in progress**: `$FIX discard --restart`. It drops the task's
  commits and changes, and the untracked files it added, and sets it back to
  pending. An unfinished task is redone from its start, never resumed from
  the middle.
- **No task in progress**: no tracked file may be changed. If one is, show
  the user and stop.

## 4. Re-run the baseline

`$FIX exec suite --log resume-suite` and `$FIX exec check --log
resume-check`, at the branch tip. A failure that isn't on `<state>/baseline.md`
or `<state>/flaky.md`, and fails again on a rerun, means the tree changed
outside the run, or the machine did. Show the user and stop. Install
dependencies first with `$FIX exec install`, only if they are missing.

## 5. Continue

`$FIX log "resumed"`, then step 5 of the playbook, from the throttle. If
`next` says `done`, go to step 6.

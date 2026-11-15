# Projects

One debrief store serves many repos. A question about one project should get
that project's notes, not every project's. `debrief-search.py` gives each indexed
chunk the project (or the few projects) the notes name for it, and `--project`
filters by it.

## The model

- A **project** is a name the notes give: the cwd folder in a daily's Sessions
  table, the project in a `Late-day addendum` heading, the second field of a
  Shaping heading (`### <date> · <project> · <piece>`), or a Reading's
  `projects:` line.
- A **group** is a set of projects named in the registry. A group means itself
  and every member. A member can be another group; its members count too, one
  level deep and no deeper.
- An **alias** is an old or spoken name for a project or group.

## How a chunk gets its project

At every rebuild of `search.db`, in this order:

1. A section under a `Late-day addendum` heading, and every `###` below it,
   belongs to that session's project.
2. A daily section that names sessions in backticks belongs to their projects,
   when there are at most four of them. More than that is a day overview.
3. On a day with sessions from one project only, every other section of that
   daily belongs to it.
4. A curated session note belongs to the project of the session in its file name.
5. A Shaping or Reading belongs to the projects its heading or `projects:` line names.
6. Otherwise, a heading that is a known project name, or starts with one
   (`bot: …`, `★ bot — …`), names it. This covers `INDEX.md` and `HIGHLIGHTS.md`.

A name in prose never scopes a chunk. A chunk that none of the rules place is
**unscoped**. A thread in `TODO.md` belongs to the projects it links
(`[[bot]]`) or leads with (`bot: …`).

## Unscoped

Under `--project`, an unscoped chunk is left out, because the notes do not say
it belongs. Each command then prints one line with how many were left out.
`--loose` includes them. Use `--loose` when the answer may sit in a
cross-project section, such as a day's `What happened` on a busy day.

## The registry

`<store>/.system/projects.json` is optional. Copy `.system/projects.example.json`
to start one:

```json
{"groups": {"studio": ["shop-web", "shop-ios", "kiosk"], "bot": ["bot"]},
 "aliases": {"shop-old": "shop-web"}}
```

Names match without case. A name resolves to:

- an alias's target (then resolved again, so an alias can name a group);
- a group: itself and every member;
- `group/member`: that member, only when it is in the group;
- a project the notes give.

Anything else is unknown. The command prints the closest known names and exits
with code 2. It never guesses. Other scripts read the same file, so keep its
format exactly as above. A change to the file rebuilds the index.

## Commands

```bash
debrief-search.py projects                         # every project under its group, then ungrouped ones
debrief-search.py projects bot                     # one project or group: members, last 5 days, open threads, newest Shapings
debrief-search.py search "router" --project bot    # also on: taste, when, status, todos, stars
debrief-search.py when --project studio         # the days a project or group is in the notes
debrief-search.py taste --project bot --loose      # --loose adds unscoped entries
```

`projects` shows, per project: sessions, first and last day, and Shapings. A
project with no Shaping has not been judged yet. That is not dislike. The list
ends with registry names that the notes do not give yet, and the aliases.

The selftest (`debrief-search.py selftest`) covers group, alias and
`group/member` resolution, the refusal of an unknown name, an addendum chunk
that stays out of another project, unscoped chunks left out and then included
with `--loose`, and the `projects` grouping.

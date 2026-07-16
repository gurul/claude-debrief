---
commits: 3d46771 ba606d6
prs:
  acme-api: 405 406
---

# Auth session cutover — cookies over localStorage tokens

> **Example debrief.** Ships with the repo so a fresh clone renders a populated
> viewer. Delete it (and its sibling) once you have memory of your own — the
> `commits:` SHAs above are fabricated and won't resolve against your repos.

## What happened

Moved auth from a localStorage bearer token to an httpOnly `[[session-cookie]]`.
`[[requireSession]]` became the single boundary that reads it; every route now
goes through that one function instead of parsing a header inline.

## Why

The token was readable by any script on the page. That's the whole argument — an
XSS anywhere became a full account takeover, and we ship third-party analytics.

## What surprised us

The cutover was mostly a *deletion*. `LoginRoute` lost 66 lines and gained 40: all
the token plumbing (refresh timer, storage sync, cross-tab invalidation) is the
browser's job once the cookie is httpOnly. We'd been hand-rolling a worse version
of the platform.

## Open threads

- SameSite=Lax breaks the (unused) cross-site embed path. Fine now; revisit if
  embedding ever ships.
- No CSRF token yet — Lax covers the current surface, but a state-changing GET
  would slip through. There are none today; that's an invariant nobody enforces.

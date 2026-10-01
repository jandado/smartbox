# PR #64 audit — `ajtudela/smartbox` `improvements/fix_bugs` vs our fork

## Context

- Base commit: `65cc9c2` (Pydantic bump). Upstream `main` has not moved since.
- Our fork (`jandado/smartbox`, `fork/main`): **7 commits ahead** — the 2.6.0
  (prog/schedule surface, socket reliability + watchdog, raw wire mode) and
  2.6.1 (UpdateManager node availability + write-confirmation) work, docs
  rounds, repo-wide pre-commit formatting.
- PR #64 (`origin/improvements/fix_bugs`, fetched locally): **19 commits** on
  top of the same base, none merged to upstream `main`. Author dated 2026-08-30.
- Method: for each commit the *underlying issue* was extracted from the full
  diff, then checked against the **current** implementation on `fork/main`
  (intent-level, since both sides rewrote the same files). Verdicts:
  **fixed directly** (same issue addressed by our work), **fixed indirectly** /
  superseded (our redesign removed the situation), **still applies**,
  **partially** (part of it open), or **obsolete/inapplicable** for this fork.

## Summary

| # | Commit | Issue it addresses | Verdict | Recommendation |
|---|--------|--------------------|---------|----------------|
| 1 | `5618aa3` | `set_node_setup` read-modify-write drops keys the Pydantic models do not declare → wiped on the device | **Still applies** (typed mode only) | Port: raw internal read or `extra="allow"` |
| 2 | `94ead00` | `get_node_samples` defaults frozen at import time | Fixed directly | Skip |
| 3 | `c5a6a3a` | Access token and reseller `basic_auth` logged at DEBUG | Fixed directly | Skip |
| 4 | `cdec9dc` | `__aexit__` closes a caller-injected `ClientSession` | Fixed directly | Skip |
| 5 | `62efcf8` | No common error root; 401/403 unmapped on data path | Partially: 401 done; 403 deliberately different; common root **open** | Port the common root on our terms |
| 6 | `0bef64c` | `test_all_resellers` hits 12 real servers in CI, leaks sessions | **Still applies** | Port (applies cleanly) |
| 7 | `6d592bf` | `DefaultNodeStatus` rejects sparse payloads in typed mode | DECIDED (2026-10-01): keep our rich-error rejection | No port |
| 8 | `23b9368` | `_dev_data` emits on a disconnected namespace (missing `return`) | Fixed directly | Skip |
| 9 | `da3bb9c` | Unused runtime deps; unbounded dependency ranges | Partially: unused deps already dropped; **pinning open** | Optional: pin majors |
| 10 | `db7c766` | No HTTP timeout (aiohttp 5-min default) | Fixed directly (richer) | Skip |
| 11 | `e02297d` | Reconnect failure logged as `exception()` without active exception | Fixed directly | Skip |
| 12 | `0a36c35` | CLI never closes its session → "Unclosed client session" | Fixed directly | Skip |
| 13 | `6ad2014` | Unknown `-d`/`-n` raise raw `StopIteration` traceback | Fixed directly (richer); **one detail adopted** | Port detail: `BadParameter` for the not-found paths (see DECIDED) |
| 14 | `14d4b57` | Missing-`path` error logged once per subscription | Fixed directly | Skip |
| 15 | `07e0fe2` | `node_addr` annotated `str` while the click option is `int` | Fixed directly | Skip |
| 16 | `b775284` | Swapped `Sample`/`PmoSample` docstrings; wrong `__getattr__` typing | Fixed directly | Skip |
| 17 | `a78bc3a` | Dead setuptools/uv-workspace config; ruff py313; stale .vscode | Partially: config + ruff already done; **.vscode stale** | Optional: clean .vscode |
| 18 | `2d28847` | Version bump to 2.5.3 | Obsolete | Skip (see versioning note) |
| 19 | `2ec1ef0` | CODEOWNERS/README/api-notes doc churn | Partially applicable: CODEOWNERS + README adoptable, api-notes reflow conflicts | Adopt CODEOWNERS + README; skip api-notes reflow |

Tally: 10 fixed directly · 6 partially open (5, 7, 9, 13, 17, 19 share the
"partial" verdict) · 2 still apply (1, 6) · 1 obsolete (18).

Mechanical check: `git apply --check` against our tree fails for all 19
commits except #6 — the PR patches the exact regions our 2.6.x rewrite
changed, so nothing here is a plain cherry-pick except the test-marker commit.

---

## Per-commit audit

### 1. `5618aa3` fix(session): preserve unknown fields in set_node_setup round-trip

**Issue.** The setup endpoint requires the whole configuration to be re-posted
(api-notes.md: "POST: update node setup. Apparently all fields need to be
provided even if unchanged"). The PR's `set_node_setup` read the current setup
through `get_node_setup`, which in typed mode serializes through
`NodeSetup`; every key the model does not declare (their example:
`counter_offset` on `pmo` nodes) was dropped from the merged payload and
therefore **wiped on the physical device**. The PR fixes it by reading the
setup with a raw `_api_request` inside `set_node_setup` (always raw,
independent of `raw_response`) and additionally adding
`model_config = ConfigDict(extra="allow")` to `DefaultNodeSetup` and
`PmoSetup`.

**Our current code** (`session.py:1006` `set_node_setup`): still reads via
`get_node_setup` and, in typed mode, dumps with
`model_dump(mode="json", exclude_unset=True)` — undeclared keys are dropped
from that dump (and none of our setup models set `extra="allow"`,
`models.py:64-96`). In **raw mode** (our default, `raw_response=True`, and
what the HA integration uses) the read returns the raw dict and unknown keys
survive the round-trip.

**Verdict: still applies — typed mode only.** With `raw_response=False` a
device carrying an undeclared setup key loses it on every `set_node_setup`
write. Our round does not cover this; it is a real data-loss bug on the
public typed surface.

**Port.** Prefer the PR's idea of the always-raw internal read (fits our
session structure: call `_api_request` directly instead of `get_node_setup`;
keeps `_call_budget()` around the GET-merge-POST). Whether to also add
`extra="allow"` to the setup models is a second decision — that changes what
typed-mode consumers see in the returned/validated surface and interacts with
the `NodeSetup` union resolution. Our `exclude_unset=True` comment should be
revisited as part of the port.

### 2. `94ead00` fix(session): evaluate get_node_samples time window per call

**Issue.** `start_time`/`end_time` defaulted to
`int(time.time() - 3600)` evaluated **once at import**, so a long-running
process (HA) drifted the default window away from the present.

**Our code** (`session.py:920-938`, and the sync mirror at `:1511`):
defaults are `None`, window computed per call; regression pin
`test_get_node_samples_defaults_use_call_time` (tests/test_session.py:2246)
explicitly documents this failure mode and checks two call times produce
different windows.

**Verdict: fixed directly.** Skip.

### 3. `c5a6a3a` fix(session): stop logging auth material in debug output

**Issue.** `_authentication` logged the full access token at DEBUG (logs get
attached to issues; the token lives for hours); `AvailableResellers.reseller`
logged `basic_auth`, which is deliberately withheld as a security layer.

**Our code**: token log is masked to a prefix —
`"access_token=%s…", self.access_token[:8]` (`session.py:382-384`), pinned by
`test_authentication_does_not_log_full_token` (tests/test_session.py:2302).
No `basic_auth` in the reseller debug log
(`reseller.py:131-140`, with an explicit comment). We additionally built a
broader redaction layer the PR does not have: `_redacted_url`,
`_has_sensitive_keys`/`_redact_body` (never log wifi-SSID query params or
password-bearing bodies; test pins at tests/test_session.py:2400-2492).

**Verdict: fixed directly.** Policy nuance only: we log the **first 8**
characters, the PR logs the **last 4**. Neither reconstructs the token; not
worth a change unless we want the stricter "no token material at all" rule.

### 4. `cdec9dc` fix(session): only close the ClientSession the session owns

**Issue.** `__aexit__` closed `_client_session` unconditionally, so using the
session as a context manager with an **injected** shared websession (the HA
pattern) closed the session under every other consumer. Also dead
`hasattr(self, "_socket")` cleanup, and a misleading `__aenter__` docstring.

**Our code** (`session.py:230-272`): `_owns_client_session`, set lazily when
our `client` property creates the session (a caller-provided websession is
never ours); `__aexit__` delegates to `aclose_owned_session()`, which closes
and forgets the owned session; no `_socket` branch. Beyond the PR: the sync
`Session` wrapper closes the owned session after every call
(`session.py:1365-1385`), and test pins exist (tests/test_session.py:2231-2242).

**Verdict: fixed directly (our own design of the same contract).** Naming
differs: the PR exposes `close()`, ours `aclose_owned_session()`. No action,
unless we later want name parity with upstream.

### 5. `62efcf8` fix(error): common exception root + map 401/403

**Issue.** (a) `InvalidAuthError`, `APIUnavailableError`,
`ResellerNotExistError` did not derive from `SmartboxError`, so consumers
could not catch one type for all library failures. (b) A 401/403
`ClientResponseError` on the data path surfaced as a generic `SmartboxError`,
denying consumers the "token dead → reauth" signal.

**Our code**: (b, 401) done — `_send_mapped` maps 401 → `InvalidAuthError`
(`session.py:598-601`) and `_request` adds a re-auth-and-resend-once retry on
top (`session.py:519-525`), strictly more than the PR. (b, 403) deliberately
different — non-5xx, non-401 statuses (403 among them) map to `SmartboxError`
(`session.py:601-606`), because 403 usually means "action not permitted",
whose reauth-and-retry would be pointless; the token-endpoint path maps all
non-5xx/non-429 to `InvalidAuthError` (`session.py:429-434`), so the two
paths differ by design. (a) **open** — `error.py:28-36` still derives the
three legacy errors from `Exception`/`aiohttp.ClientConnectionError`; only
our new `SmartboxValidationError` sits under `SmartboxError`.

**Verdict: partially superseded.** The 401 half is ours; the 403 half is a
deliberate divergence; the **common root remains a genuine gap** worth porting
(on our terms). When doing it: `APIUnavailableError` must keep its
`aiohttp.ClientConnectionError` base (our `_request`/token path catch it,
`session.py:397`); the sibling integration catches
`APIUnavailableError → InvalidAuthError → SmartboxError` in that order
(hass-smartbox config_flow.py:103-109, sensor.py:165-177), which stays correct
under the unified hierarchy, but the integration's lib contract tests must be
run after the change.

### 6. `0bef64c` test(reseller): move test_all_resellers behind a 'network' marker

**Issue.** `test_all_resellers` makes ~24 real HTTP calls to twelve
third-party reseller servers on every CI run — a reseller outage fails CI with
no code change — and leaks twelve unclosed `ClientSession`s.

**Our code**: identical unmarked test (tests/test_reseller.py:49-55); our CI
runs `uv run tox -e py` → plain pytest → the test runs on every push. With our
lazy `client` property each iteration also creates an owned session that is
never closed (the PR's original leak, made structural by our design).

**Verdict: still applies.** The patch is the only one that applies cleanly
with `git apply`, but port it deliberately: add the `network` marker +
`addopts = "-m 'not network'"` (keeping our existing pytest options), and wrap
each session so the owned session is closed (`async with` /
`aclose_owned_session()`).

### 7. `6d592bf` fix(models): make DefaultNodeStatus a real fallback

**Issue.** As the last arm of the `NodeStatus` union, `DefaultNodeStatus` had
every field required, so any firmware/reseller returning a sparse status made
`get_node_status` raise a `ValidationError`. The PR makes all its fields
optional with `extra="allow"` — sparse payloads degrade to an object instead
of an exception, and unknown keys survive.

**Our code** (`models.py:130-160`): three fields were relaxed in our round
(`act_duty`, `power_pcb_temp` optional; `error_code` widened to `str | int`,
each with live-fixture justification), the rest remain required. In typed
mode a sparse payload raises — but as our `SmartboxValidationError` with the
offending wire payload attached (`session.py:151-166`), which is already a
much better failure surface than the PR started from. Our default is
`raw_response=True` and the HA integration uses raw mode, so the HA surface
is unaffected either way.

**Verdict: partially applies (typed mode only).** DECIDED (2026-10-01):
**keep our rich-error rejection; no port.** Rationale from the decision
discussion:

- The bare `{"sync_status": "lost"}` offline-node frame (api-notes.md
  "Offline node's /status GET eventually returns a BARE ... payload",
  fixture `tests/fixtures/live/socket_update_sync_lost_frame.json`) is the
  one legitimate sparse payload on the wire — and our typed-mode design
  already routes it first-class: the `SmartboxValidationError` docstring
  (`error.py:8-18`) names it explicitly; consumers catch the error and
  inspect `payload["sync_status"] == "lost"`.
- Upstream's degrade-to-None-object hides genuine wire drift (which our
  2.6.x probes showed is common across firmware families) and forces
  consumers to None-check every typed attribute; a failure surfaces far
  from its cause.
- Our own sparse handling (UpdateManager availability, the integration)
  runs entirely in raw mode, where the frame passes through untouched;
  typed-mode rejection affects only explicit typed consumers.

Related but separate: our `NodeStatus` docstring (`models.py:182-196`)
documents the adjacent quirk that *partial* frames degrade into
`HtrNodeStatus` and silently drop htr_mod extras — upstream's change would
not have fixed that either (its type-specific fields stay required); it
remains a possible future improvement with its own union-resolution
analysis (tests/fixtures/live cover the shapes).

### 8. `23b9368` fix(socket): stop emitting dev_data on a disconnected namespace

**Issue.** `_dev_data` logged "Namespace disconnected" and then emitted
anyway (missing `return`), plus a copy-paste log message mentioning "ping".

**Our code** (`socket.py:193-199`): early `return` present, message says
`dev_data`, mirroring `_send_ping`.

**Verdict: fixed directly.** Skip.

### 9. `da3bb9c` build: drop unused deps and pin the core ones

**Issue.** `requests` and `websocket-client` were unused leftovers; the core
deps were unbounded.

**Our code**: the two unused deps were dropped in our 2.6.0 commit `e86963d`
(`pyproject.toml:20-26`); ranges are still unbounded (`aiohttp`, `pydantic`,
`python-socketio` bare).

**Verdict: partially superseded.** The dep removal is ours; the **pinning**
half (`aiohttp>=3,<4`, `pydantic>=2,<3`, `python-socketio>=5,<6`) is still
open and is a reasonable hardening to adopt (we depend on pydantic v2-only
API and socketio v5 semantics).

### 10. `db7c766` feat(session): apply a default HTTP request timeout

**Issue.** `ClientSession` created without `timeout` inherits aiohttp's
5-minute default; against an unresponsive server one request blocks for that
long.

**Our code** (`session.py:310-325`, constants `:53-59`): owned sessions get
`ClientTimeout(total=30, connect=10)`, and every library call — regardless of
websession ownership — is additionally bounded by the `_CALL_TIMEOUT` 30 s
budget (`_call_budget`). Covers the injected-websession case the PR misses.

**Verdict: fixed directly (richer).** Skip.

### 11. `e02297d` fix(socket): downgrade retry log from exception to warning

**Issue.** The reconnect loop called `_LOGGER.exception` with no active
exception (bogus "NoneType: None" traceback) for a planned retry.

**Our code** (`socket.py:399-402`): `_LOGGER.warning`, same wording.

**Verdict: fixed directly.** Skip.

### 12. `0a36c35` fix(cmd): close the CLI session on context teardown

**Issue.** The CLI group callback built the session around a hand-made
`ClientSession()` nobody closed → "Unclosed client session"/"Unclosed
connector" warnings on every invocation and incomplete SIGINT cleanup.

**Our code** (`cmd.py:143-157`): no `websession=` injection (the session owns
its client lazily) and `ctx.call_on_close(session.aclose_owned_session)`.

**Verdict: fixed directly.** Skip.

### 13. `6ad2014` fix(cmd): friendly error when a device or node id is not found

**Issue.** Five commands located the device/node with a bare
`next(d for d in devices if ...)`; a wrong `-d`/`-n` dumped a raw
`StopIteration` traceback.

**Our code** (`cmd.py:24-61`): `_resolve_device` / `_resolve_node` with clean
`click.ClickException`s; `_resolve_node` additionally coerces/validates the
wire `addr` defensively (raw payloads are unvalidated) and handles malformed
node payloads. All six call sites go through the helpers (`cmd.py:245, 285,
286, 342, 343, 396, 397, 468, 500`).

**Upstream's variant.** `_find_device` / `_find_node` helpers raising
`click.BadParameter` with the offending value, plus a regression test
asserting an unknown `-d` produces a friendly error and no `set_*` call.

**Decision (2026-10-01): adopt upstream's `BadParameter` for the not-found
paths; keep the rest of our richer fix. DECIDED — pending implementation.**

Rationale:

- Click's hierarchy encodes *whose fault* the failure is:
  `UsageError`/`BadParameter` (exit code 2, rendered with the usage line)
  means "the command line was wrong"; `ClickException` (exit code 1) means
  "the command ran and failed at runtime". A wrong `-d`/`-n` value is
  syntactically valid but semantically nonexistent — precisely the misuse
  class `BadParameter` exists for.
- Exit code 2 distinguishes "mistyped identifier" from "runtime failure" for
  shell scripts consuming the CLI.
- Carve-out: `_resolve_node`'s malformed-wire-payload branch (`_wire_addr`
  raising on a non-numeric/missing `addr`, `cmd.py:49-54`) stays
  `ClickException` — the user's `-n` can be perfectly valid there; the fault
  is in the server data, so it is a runtime failure.
- Refinement: raise with `param_hint` so the error names the flag, e.g.
  `raise click.BadParameter(msg, param_hint="'-d/--device-id'")` — the hint
  is rendered in the usage section, pinpointing the offending option.

**Implementation notes** (when ported):

1. Change the two not-found raises in `_resolve_device` (`cmd.py:29-31`) and
   `_resolve_node` (`cmd.py:58-60`) to `click.BadParameter(...,
   param_hint=...)`; no call-site changes (helpers are called inside command
   bodies; click's standalone mode catches the `UsageError` there and renders
   usage + exit code 2 correctly).
2. Pin with the upstream-style regression test: unknown `-d` →
   `result.exit_code == 2`, the offending value in `result.output`, no
   `set_*`/write call issued. Asserting exit code 2 specifically guards the
   `BadParameter` classification against regressing back to `ClickException`.
3. Run ruff/mypy/pytest gates; the CLI has no lock-step surface beyond the
   existing contract tests (verify they stay green).

### 14. `14d4b57` fix(update_manager): check for missing "path" once per message

**Issue.** The `"path" not in data` guard sat inside the per-subscription
loop, so a malformed update logged one identical error per active
subscription (five or six in a typical HA install).

**Our code** (`update_manager.py:717-723`): the same hoist with the same
rationale in the comment; `test_update_subscription_missing_path_is_safe`
covers the no-raise behavior (tests/test_update_manager.py:268).

**Verdict: fixed directly.** Skip.

### 15. `07e0fe2` fix(cmd): annotate node_addr as int to match the click option

**Issue.** `node-samples`/`set-status`/`set-setup` declared
`node_addr: str` while their `-n` option is `type=int` (mypy flagged it via
the new helper).

**Our code** (`cmd.py:239, 280, 337, 391`): all annotated `int`.

**Verdict: fixed directly.** Skip.

### 16. `b775284` docs(models): fix swapped Sample docstrings and __getattr__ typing

**Issue.** `Sample` was documented as the "Pmo Sample model" and
`PmoSample` as the "Default Sample" (swapped); `NodeSetup.__getattr__` /
`NodeStatus.__getattr__` were annotated to return the root model instead of
the resolved attribute value.

**Our code** (`models.py:260, 268, 100, 199`): docstrings corrected — with
field-level detail ours lacked upstream ("temperature history (t, counter,
temp)" vs "consumption history (t, counter, max, min)") — and both
`__getattr__` proxies are typed `-> Any  # noqa: ANN401`.

**Verdict: fixed directly.** Skip.

### 17. `a78bc3a` build: remove dead manifest config and align tooling to Python 3.14

**Issue.** `[tool.setuptools.package-data]` (keyed by an unreplaced
`"pkgname"` template), `[tool.setuptools.packages.find]`,
`[tool.uv.workspace] members = ["smartbox"]` pointed at nothing; ruff
`target-version` was py313; `.vscode/settings.json` enabled pylint/flake8.

**Our code**: the dead sections are gone and ruff is `py314` (our 2.6.0
`e86963d`); the `TC001/002/003` ignores the PR needs are already in place with
a different (contract-test) rationale (`pyproject.toml:89-98`).

**Verdict: partially superseded.** Only the **`.vscode/settings.json`**
cleanup remains — ours still enables pylint/flake8 (`pylint`/`flake8` lines).
Cosmetic, zero runtime effect; decide whether we want ruff-based
editor defaults.

### 18. `2d28847` chore: bump version to 2.5.3

**Issue.** Version step for the upstream release of the PR's fixes.

**Our code**: we are at `2.6.1` with our own release line and lock-step pin in
the sibling integration.

**Verdict: obsolete.** Skip. See the versioning note below.

### 19. `2ec1ef0` Update docs

**Issue.** Governance/cosmetics: CODEOWNERS adds `@ajtudela` as co-owner,
README badge line reflowed from one long line to five, and `api-notes.md`
paragraphs re-wrapped from multi-line to single long lines.

**Decision (2026-10-01): adopt the CODEOWNERS and README changes if they
apply without conflicts; skip the api-notes reflow. DECIDED — pending
implementation.**

Rationale. The two repos are separate forks of the same upstream, and the
goal for this fork's work is to make ajtudela's merging easier — he takes all
upstream work into his tree. So we stay in sync with upstream wherever a
change does not conflict with our own, even for pure cosmetics.

- `CODEOWNERS`: our file is identical to the base (`*       @Delmael`); the
  PR hunk (`*       @Delmael @ajtudela`) applies cleanly (`git apply --check`
  verified). Take it verbatim.
- `README.md`: our fork already diverges from the base at line ~75 (the
  `prog`/`set-prog` sections from our 2.6.0 round); the PR's change touches
  only the badge header at lines 1-4 — a different region, verified to apply
  cleanly. Take it verbatim.
- `api-notes.md`: our file is a 603-line working document carrying the 2.6.x
  probe findings; the PR's reflow makes lines *longer* and conflicts outright
  (verified: the full commit fails `git apply --check`). Take nothing from
  this part.

**Implementation notes** (when ported):

1. Apply the CODEOWNERS and README hunks (verbatim from the PR; both apply
   cleanly) — no adaptation needed.
2. Leave `api-notes.md` untouched; the reflow conflicts with our extended
   document and degrades wrapping.
3. Doc-only change: no gates required beyond a green build if desired;
   re-run the contract tests only as routine after any pull.

---

## Cross-cutting observations (found during this audit, not PR items)

1. **403 handling is intentionally inconsistent between paths.** Token
   endpoint: non-5xx/non-429 → `InvalidAuthError` (includes 403). Data path:
   403 → `SmartboxError`. The rationale (token rejection vs forbidden action)
   is sound, but it should be stated in one place — worth a comment in
   `_send_mapped` when the error hierarchy is touched.
2. **Typed-mode robustness cluster.** Commits 1 and 7 both only matter with
   `raw_response=False`. Our default and the HA integration use raw mode, so
   the practical blast radius today is small — but typed mode is public API
   and the two residual bugs (unknown-setup-key wipe, sparse-status
   rejection) are the strongest reasons to take anything from this PR.
3. **Version-namespace collision risk.** The PR releases upstream as 2.5.3
   while we publish 2.6.x to PyPI from this repo. If upstream later renumbers
   (e.g. also moves to 2.6.x), PyPI versions could collide or confuse the
   integration's pin. Before either side's next release, confirm the upstream
   merge state and coordinate version numbers.
4. `update_manager.py:760` uses `except TypeError, ValueError:` — valid
   Python 3 (the exception spec is the bare tuple) but obscure; parenthesize
   on the next touch of that function.

## Resolution log (2026-10-01)

All 19 commits resolved. Ported (commit message references the source
commit):

- #6 `0bef64c` → `c99884e` network marker + session cleanup for
  `test_all_resellers`
- #19 `2ec1ef0` → `1122305` CODEOWNERS + README hunks adopted verbatim
- #13 `6ad2014` → `838abcf` `BadParameter` + `param_hint` for the not-found
  paths; malformed-wire branch stays `ClickException`
- #1 `5618aa3` → `64ce646` raw internal read in `set_node_setup`
  (typed-mode device-wipe bug fixed; setup models left untouched —
  `extra="allow"` remains an open option)
- #5 `62efcf8` → `d6c2791` common `SmartboxError` root; 403 data-path
  divergence kept deliberately; `APIUnavailableError` keeps the aiohttp base
- #9 `da3bb9c` → `9683f4d` dependency majors pinned (`aiohttp`, `pydantic`,
  `python-socketio`); the unused-deps half was already ours
- #17 `a78bc3a` → `1b1d112` `.vscode` ruff setup (pyproject/ruff halves were
  already ours)

Declined, by decision:

- #7 `6d592bf` — keep our rich-error rejection in typed mode (bare-lost
  frame is first-class in our design; see its section)

The other 11 commits were already fixed directly by our 2.6.x round (#2, #3,
#4, #8, #10, #11, #12, #14, #15, #16) or are upstream-only (#18 version bump).

Verification per port: full suite, ruff, mypy, ruff format; the
integration contract suite (tests/test_lib_contract.py, 8 tests) re-run
green against the linked dev lib after every lib-surface change.

## Decision handoff

Superseded by the Resolution log above — kept for the discussion history
that led there. The standing rule remains: wherever a PR change does not
conflict with our own work, prefer adopting it verbatim — the point of this
fork's effort is to keep ajtudela's eventual merge of all upstream work as
frictionless as possible.

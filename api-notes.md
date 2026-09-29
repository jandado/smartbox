# API notes
Some brief notes on the REST endpoints used by this library.

> **See also:** `webapi-spec.md` (same repo) — the full vendor-API surface as
> used by the official app (extracted from the app bundle 2026-09-26),
> including schedule/program (`/prog`) semantics, write-body shapes, and the
> discrepancy list (D1–D7) for the scheduling feature.

# REST API

## Auth

### Basic Auth Credential
Initial authentication to the smartbox REST API is protected by HTTP Basic Auth,
in addition to the user's username and password which are then used to obtain an
access token. In order not to undermine the security layer it provides, and also
because it might change over time or vary between implementations, **the token
is not provided here and system owners need to find it themselves**.

### /api/v2/client/token
POST: needs basic auth token provided in the `Authorization` header. See code
for access token and refresh protocol.

The endpoints below need the access token obtained from the token endpoint (see
code), which lasts for four hours before needing to be refreshed.

## Devices

### /api/v2/devs
GET: list devices

### /api/v2/grouped_devs
GET: list devices with extra grouping info

POST: TODO untested

### /api/v2/devs/<dev_id>/dev_data
GET: Appears to be all device data, including most of the information obtainable
via specific endpoints below.

POST: TODO untested

### /api/v2/devs/<dev_id>/geo_data
GET: device geolocation data

POST: TODO untested

### /api/v2/devs/<dev_id>/connected
GET: device connection status

POST: TODO untested

### /api/v2/devs/<dev_id>/mgr/away_status
GET: device away status

POST: TODO untested

### /api/v2/devs/<dev_id>/mgr/discovery
GET: device discovery status

POST: TODO untested

### /api/v2/devs/<dev_id>/htr_system/power_limit
GET: heater power limit info

POST: TODO untested

### /api/v2/devs/<dev_id>/mgr/rtc/time
GET: device date and time info

POST: TODO untested

## Nodes
Note: node type apparently can be `htr`, `thm` or `acm` (only htr tested).

### /api/v2/devs/<dev_id>/mgr/nodes
GET: lists nodes

### /api/v2/devs/<dev_id>/<node_type>/<node_addr>
GET: Appears to be all node data, including most of the information obtainable
via specific endpoints below.

POST: TODO untested

### /api/v2/devs/<dev_id>/<node_type>/<node_addr>/status
GET: get node status

POST: update node status. Only fields that are changing need to be supplied,
but `units` must be provided with any temperature fields.

### /api/v2/devs/<dev_id>/<node_type>/<node_addr>/prog
GET: get node programme (weekly schedule; works on the fw-1.9 family).

POST: update node programme. Day-keyed object shape, full schedule; see
the 2026-09-27 round below for the wire rules.

### /api/v2/devs/<dev_id>/<node_type>/<node_addr>/type
GET: get node type

POST: TODO untested

### /api/v2/devs/<dev_id>/<node_type>/<node_addr>/version
GET: get node version info (firmware version etc)

POST: TODO untested

### /api/v2/devs/<dev_id>/<node_type>/<node_addr>/setup
GET: get node setup

POST: update node setup. Apparently all fields need to be provided even if
unchanged.

### /api/v2/devs/<dev_id>/<node_type>/<node_addr>/samples
GET: TODO: untested

POST: TODO: untested

## Misc

### /version

Get version info

# Websocket API
This uses the [socket.io] protocol.

Briefly:
* The socket session is per device
* The access token and device ID must be supplied as query params
* On successful connection, the client should emit a `dev_data` event. The
  corresponding response from the server is similar to the dev_data REST
  endpoint above
* The server will send periodic `update` events containing device and node
  status updates
* The client should send a `ping` message every 20s (in addition to the protocol
  level ping/pong). Have not tested that this is strictly necessary.

## `update` Messages

### Node Status - `/<node type>/<node addr>/status`
Similar to the node status API endpoints above, one per node.

### Device Away Status - `/mgr/away_status`
Message content is the same structure as the `away_status` device API, or the
corresponding field in the `dev_data` message or REST endpoint.

## Reseller keys
All reseller API keys resolve to a small set of shared hosts (e.g.
`api-lhz`, `api-smartcontrol` and `api-technoterm` all use
`api-lhz.helki.com`), but each key sends a different brand identity on the
token request (`x-serialid` and, where set, `x-referer`, see
`src/smartbox/reseller.py`). Using the wrong key for your account yields
`InvalidAuthError` even with valid credentials: e.g. Lucht LHZ shop
customers must use `api-lhz`, SmartControl (`app.smart-control.eu`)
customers must use `api-smartcontrol` (`serial_id=17`). When diagnosing
auth failures, check the reseller key first.

# Observed behaviour (2026-09-26, live hardware)

Live session against a SmartControl account (`api-smartcontrol`): one
device, five `htr` nodes (addrs 2-6), fw 1.9 / hw 1.1 / pid 0402, API
2.37.3. Captured with `tools/live_check.py`, `tools/watch_status.py` and
`tools/probe_extra_options.py` (see repo root `.env` for credentials,
never commit them; never enable DEBUG logging - the library logs access
tokens at DEBUG).

The hardware family differs from the older fixtures under
`tests/fixtures/devs/device1/` (which have `factory_options.boost_config`
and string `error_code`). Both payload families are valid `htr` variants;
raw payload fixtures for this session live in `tests/fixtures/live/`.

## Node status payload (`GET /htr/<addr>/status`)

Boost-off baseline (all five nodes):

```json
{
  "active": false, "boost": false, "boost_end_day": 0, "boost_end_min": 0,
  "comf_temp": "20.0", "duty": 0, "easy": false, "eco_temp": "19.5",
  "error_code": 0, "ice_temp": "19.0", "locked": false, "mode": "off",
  "mtemp": "19.6", "pcb_temp": "25", "power": "1497.0", "presence": false,
  "runback": false, "stemp": "20.0", "sync_status": "ok",
  "true_radiant_active": false, "units": "C", "version": 1,
  "window_open": false
}
```

Differences from the older fixture family:

* `error_code` is an **int** (`0`), not a string like `"none"`.
* Extra undocumented keys: `easy` (bool), `runback` (bool), `version`
  (int).
* No `act_duty` or `power_pcb_temp` keys at all.

Consequence for the library: up to and including 2.5.2,
`NodeStatus.model_validate()` **failed** on every payload from this family
(`AcmNodeStatus` is tried first and rejects the int `error_code`;
`HtrNodeStatus`/`DefaultNodeStatus` also required the missing
`act_duty`/`power_pcb_temp`). This was latent in normal use because the
session default is `raw_response=True`, which returns the payload before
validation runs (`session.py`), and the HA integration relies on that
default.

**Fixed (status in the staged 2.5.3 tree, rolled into 2.6.0)**:
`DefaultNodeStatus` now declares `error_code: str | int` and
`act_duty`/`power_pcb_temp` as optional, so both observed families
validate under `raw_response=False` (pinned in
`tests/test_live_payloads.py`). The 2.6.0 alignment round additionally
widened the setup side for the same family — see the 2026-09-27 section
below.

## Boost semantics on boost-disabled hardware

These units are factory boost-disabled: their setup `factory_options` has
**no `boost_config` key at all** (older-family fixtures ship
`boost_config: 2`). Consequences, all observed:

* The SmartControl app exposes no boost toggle for these heaters.
* `POST /status` with `{"boost": true}` and `{"boost": false}` is
  **accepted without error**, but the device ignores it: status keeps
  reporting `boost: false, boost_end_min: 0, boost_end_day: 0`.
* `boost_end_min` absence-vs-zero semantics while boosting (finding D6)
  and out-of-slider-range `boost_time` clamping (finding D7) are
  therefore **unverifiable on this hardware family**; the pinned facts
  are the ones above (accepted-but-ignored writes; both boost keys always
  present and zero).

## `extra_options` writes (findings B4/D7 probes)

Setup payloads for these units contain no `extra_options` key. Probes:

* `POST /setup` with `{"extra_options": {"boost_temp": "25.0"}}`
  (partial object) - accepted without error; subsequent GET and websocket
  setup frames never contain `extra_options`.
* `POST /setup` with
  `{"extra_options": {"boost_temp": "21.0", "boost_time": 999}}`
  (out-of-slider-range duration) - accepted without error, likewise not
  stored. No server-side error, no clamped value: the object is silently
  dropped for this hardware family.

So for boost-disabled units there is no replace-vs-merge behaviour to
observe: the server drops `extra_options` entirely. On boost-capable
units (older family) the HA integration merge-sends the full object; the
replace-vs-merge question there remains open.

## Write side-effects on the websocket

Immediately after any accepted POST (status or setup), the server pushes
transient frames for the affected node:

* `/htr/<addr>/status` with body `{"sync_status": "lost"}`, followed
  shortly by `/htr/<addr>/prog` with the same body, before payloads
  return to `sync_status: "ok"`.
* Setup writes additionally push a full-setup body frame and a
  `/htr/<addr>/version` frame
  (`{"fw_version": "1.9", "hw_version": "1.1", "pid": "0402", "uid":
  "..."}`).
* Device-wide node-list updates arrive as a frame whose body is
  `{"nodes": [...]}` with no `path` (includes `uid`, `parent`, `level`
  per node).

Update bodies for status are **full status objects, not deltas**.

Update bodies for status are **full status objects, not deltas**.

## Functional status-write probes (2026-09-26)

Verified end-to-end on `htr/5` via `tools/api_functional_tests.py`
(snapshot → writes → restore → fleet verify; two runs, second fully
clean: `DONE failures=0`). Sequence and per-step GET verification:

| # | POST payload | Result | GET verify |
|---|--------------|--------|------------|
| T1 | `{"mode": "auto"}` | accepted | `mode='auto'` ✓ |
| T1b | `{"stemp": "20.5", "units": "C"}` in auto | accepted | `stemp='20.5'`, **mode stays `auto`** |
| T2 | `{"mode": "manual"}` | accepted | `mode='manual'`, stemp retained ✓ |
| T3 | `{"stemp": "20.5", "units": "C"}` in manual | accepted | `stemp='20.5'` ✓ |
| T4 | `{"locked": true}` | accepted | `locked=true` ✓ |
| R | restore (mode/stemp/locked) | accepted | see finding below |

Findings:

* **Multi-key status POSTs are silently partially applied.** A restore
  POST `{"locked": false, "stemp": "20.0", "units": "C", "mode": "off"}`
  stored `locked` and `mode` but **silently dropped `stemp`** (kept
  `'20.5'`; no error, no clamp). The identical value POSTed alone was
  accepted and stored. → The API effectively requires **one key per
  POST**, which is exactly what the HA integration does
  (`async_set_hvac_mode` sends only `{"mode": ...}`,
  `async_set_temperature` only `{"stemp": ..., "units": "C"}`). Confirmed
  as an API requirement, not a stylistic choice.
* **No `modified_auto` transition observed**: bumping `stemp` inside
  `mode: "auto"` leaves `mode: "auto"` unchanged on the wire, even though
  the mode value exists in the integration's mapping and setup exposes
  `modified_auto_span: 6` (semantics appear handled device-side without a
  mode flip, at least on fw 1.9). Partially superseded by the
  2026-09-28 A/B probe below: the stored value DOES drive the heater; the
  mode only flips when `mode: "modified_auto"` is explicitly sent.
* Socket frames for these writes: `/htr/5/status` update frames arrive
  ~0.5 s after each POST with **full status bodies** including the new
  `stemp`; unrelated schedule changes on other auto heaters appeared as
  their own `/htr/2|6/status` frames plus a `/mgr/nodes` frame
  (`{"nodes": [...]}`, no `path`).
* **Auto-schedule drift caveat for restore flows**: heaters in `auto`
  changed their own `stemp` (schedule setpoint transitions) between
  snapshot and final verification — restoring needed one extra single-key
  `stemp` POST per affected heater. Snapshot-diff restore checks must
  treat schedule-driven `stemp` changes on auto heaters as expected drift.

## Setpoint-in-auto A/B probe (2026-09-28, tools/probe_modified_auto.py, tools/probe_settle_window.py)

Decisive A/B on `htr/5` (auto, stemp 19.0, mtemp 20.3), distinct set points
so the physical response is unambiguous:

| Write | Wire result | Heater behavior |
|---|---|---|
| `{"stemp": <mtemp+1>, "units": "C"}` (no mode key) | stored (0.5-grid quantized), `mode` stays `auto` | **applied**: `active=True duty=100`, climbing |
| `{"stemp": <p1+1.5>, "units": "C", "mode": "modified_auto"}` | `mode` flips to `modified_auto`, stemp stored | applied; override persists (no self-revert within a 60 s window) |
| `{"mode": "auto"}` (from modified_auto) | reverts to `auto` | override cleared |

Findings:

* A plain `{stemp, units}` write in auto is not just stored — it **drives
  the heater** once the API is settled (the first probe round's control was
  ambiguous: its set point sat below room temp, so the heater was idle
  either way).
* An **immediate (0 s) follow-up write inside the post-write settle window
  also applies** (`probe_settle_window.py` S1 vs S2 control, identical
  results): no settle-window write drop for mode→stemp sequences on this
  family.
* `modified_auto` is engaged **only by explicitly sending
  `mode: "modified_auto"`** (the app's body, webapi-spec.md §4.2); it does
  not self-revert within the observation window — reverting needs
  `{"mode": "auto"}` (the app's UX additionally reverts at the next
  differing programme temperature, per user report, not re-verified here).
* **Off-grid setpoints are silently quantized to the 0.5 °C grid**
  (`21.3→21.0`, `22.8→22.5`, `22.4→22.0`). The app always sends on-grid
  values; API clients should round before POSTing. App UI step:
  0.5 °C / 1 °F (user-verified against the app, 2026-09-28).
* Fleet restore verified clean both rounds (`DONE failures=0`, controlled
  fields exact).
* OPEN FINDING (cause unknown, needs a frame capture): during/after the
  probing the backend reported state disagreeing with the physical heater
  (setpoint off by 2-3° with the user standing at the heater; phantom
  `active=True duty=100` for minutes while not heating). The probe wrote
  rapid modified_auto engage/revert cycles, no-op mode writes and off-grid
  values — none individually undocumented, but the pattern was unusual.
  Do not attribute without a watch_status.py capture of a recurrence.

[socket.io]: https://socket.io/


## E2 round — lib-side teardown residuals (from HA reload/shutdown fix, 2026-09-26)

Found while fixing the HA integration's broken UI Reload / HA-stop behavior
(integration side is now fixed and survives all of these; recording here per
the routing rule since they are lib/session-layer defects):

1. **`SocketSession.cancel()` can hang unbounded.** It awaits
   `asyncio.shield(self._sio.disconnect())` with no timeout; on a wedged /
   half-open websocket the engineio disconnect can block indefinitely, and
   every caller awaiting `cancel()` hangs with it (socket.py:288-297).
2. **Double disconnect on cancel.** `cancel()` disconnects, and `run()`'s
   `finally` then calls `shutdown()` which disconnects AGAIN
   (socket.py:284-286, 299-308). Concurrent disconnect attempts on the same
   AsyncClient can trip internal state races; one disconnect plus an
   idempotent exit flag would do.
3. **Teardown detour through the REST API.** After `_sio.wait()` returns
   (e.g. disconnect-triggered), the outer loop still runs
   `await self._session.check_refresh_auth()` before re-checking
   `_loop_should_exit` (socket.py:237-280). Slow/unreachable API ⇒ slow
   cancel even without a wedged socket. Test the exit flag before
   refreshing.
4. **Dangling cleanup tasks at loop teardown.** `_cleanup_dangling_socket`
   tasks spawned in `_attempt_connection` (socket.py:205-207) are never
   awaited on cancel; if the loop closes first they die as "Task was
   destroyed but it is pending" noise.
5. **`_LOGGER.exception()` misuse**: socket.py:268 logs a routine retry
   message via `.exception()` (stack trace with no active exception) —
   should be `.warning()`.

## Auth behaviour observations (2026-09-26, live, SmartControl account)
* **Password change does NOT revoke outstanding tokens.** Password changed
  via the web-app; an already-running HA integration holding access +
  refresh tokens from before kept polling unaffected. Consistent with the
  code: steady-state auth never touches the password —
  `check_refresh_auth()` only fires the `refresh_token` grant when the
  access token has <60 s left (`_MIN_TOKEN_LIFETIME`);
  `grant_type=password` is used only when a fresh session has no token
  (setup / Reload / restart).
* **Forced full re-auth with a stale password fails fast and recovers
  cleanly.** Integration Reload → fresh session → `grant_type=password` →
  immediate `401 Unauthorized` at `https://api-lhz.helki.com/client/token`
  (SmartControl account; matches the shared-host mapping in "Reseller
  keys" above) → HA repair item + reauth dialog → new credentials →
  instant recovery, entities back within a poll cycle.
* Open question: server-side TTL of refresh tokens (never captured);
  unknown whether the backend expires them independently of password
  changes.

## 2.6.0 alignment round (2026-09-27, live hardware)

Follow-up session on the same account/hardware (SmartControl, one
device, five htr addrs 2-6, fw 1.9). Alignment + teardown fixes shipped
together in 2.6.0; the "2.5.3" label was struck (never released, 2.5.2
works fine with the fixed integration).

### Node programme (schedule) — `GET/POST /htr/<addr>/prog`
* GET works on this family and returns
  `{prog: {...}, sync_status: "ok"}` where `prog` maps STRING day keys
  `"0".."6"` (Mon..Sun) to 24-slot int arrays (hourly slots; profile
  indices 0=ICE, 1=ECO, 2=COMF). Fixtures:
  `tests/fixtures/live/htr_prog_addr{2..6}.json`.
* POST accepts the same day-keyed object shape with the full schedule
  (the app always sends the complete merged schedule). The ARRAY shape
  (7 day-arrays as a JSON list) is **rejected with 400 Bad Request**.
* **Settle-lag rule**: a successful write is NOT immediately visible in
  GET — the server reads device state with a lag of several seconds.
  GET-verify flows must sleep (5s verified sufficient) or they see the
  stale value and misreport the write as a no-op. This same race
  invalidated the first prog and away probes of the session.
* Round-trip verified: single-slot edit (day "0" slot 6, 1→0) accepted,
  GET-verified, restored clean (`tools/prog_functional_tests.py`).
* `set_node_prog` therefore GETs current, day-level-merges the caller's
  partial update and posts the complete object (mirrors
  `set_node_setup`).

### Away status writes (D1 closed, with an observation)
* The app sends `{"away": bool, "enabled": bool}` (enabled defaults
  true in the app's dev-proxy wrapper) — confirmed in the app bundle
  (`setDevAwayStatus`). Home-level away writes use `{"away": value}`
  only.
* Verified live: both `{"away": true}` alone and `{"away": true,
  "enabled": true}` are honoured (away toggled true and back via
  `{"away": false, "enabled": true}`). `enabled` is tolerated but not
  required server-side; the library defaults it like the app.
* Observation (unresolved): the device's `forced` flag flipped
  false → true between probe rounds without any write carrying
  `forced`; origin unknown (possibly schedule/server-driven).

### Setup model widening (fw-1.9 family)
* Live setup payloads (fixtures
  `tests/fixtures/live/htr_setup_addr{2..6}.json`) omit
  `user_duty_factor`, `flash_version`, `extra_options` (all now optional
  in `DefaultNodeSetup`) and add `max_stemp_limit` ("0.0"),
  `priority` ("low"), `revision` (0).
* `factory_options` on this family shrinks to
  temp_compensation_enabled / window_mode_available /
  true_radiant_available / duty_limit plus operating_mode (0),
  power_factor (0), super_lock_available (false). **No `prog_resolution`
  key** — D5 residual: schedule resolution is not readable from setup on
  this family; the observed schedule is 24 hourly slots.

### Socket teardown fixes (E2 residuals closed)
All five lib-side teardown defects fixed in `socket.py`: bounded
`cancel()` disconnect (`_DISCONNECT_TIMEOUT`), a single idempotent
disconnect shared by cancel/shutdown (`_disconnect_once`), the
exit-flag check before the REST auth refresh in the main loop, pending
background cleanup tasks awaited (bounded) on shutdown, and the retry
log `.exception()` → `.warning()`. Covered by `tests/test_socket.py`.

### Management/fleet surface (implemented 2.6.0, unverified live)
The remaining webapi-spec §2–3 endpoints are now thin session methods
(paths where the spec was terse were confirmed in the app bundle):
* Device: `mgr/discovery` GET/POST, `mgr/rtc/time` GET, `name` POST,
  `group` POST (pass-through), DELETE device.
* Node fleet ops: `name`/`select` POST, `power` GET, DELETE node
  (body `{"purge": bool}`).
* Groups/homes: `geo_data`/`extra_data` GET, `name` POST; invites:
  `POST/DELETE /api/v2/users/<uid>/invite` (body
  `{email, groupid, confirmation_url}` / `{groupid, email}` — the app
  builds `confirmation_url` as
  `<frontend>/invite-confirm/nserie<serial_id>`) and unauthenticated
  `POST /api/v2/users/<uid>/invite_confirmation` (`{pass, code}`).
* Notifications: `GET/POST /api/notifications/v1/<gid>/presence/config`
  + `POST .../test` (confirmed in the app bundle:
  `getQuietHomeNotifications` / `setQuietHomeNotifications` /
  `testQuietHomeNotifications`).
* Utility: `GET /api/v2/encrypted_wifi_credentials?ssid=&pass=` (the
  server performs the AES-256-CBC PBE; query params deliberately not
  logged — secrets), `GET /api/location/v1/coordinates`.
New base helpers: `_api_delete`, `_api_get` (host-relative path, query
params), `_api_post_path` (host-relative POST, optional auth for
anonymous flows). All of these are untested against live hardware except
where noted; paths come from the app bundle + spec.

#### Read-verified live (2026-09-27, same session)
* `GET .../mgr/discovery` → `{"discovery": "off"}` ✔.
* `GET .../mgr/rtc/time` → `{"d": 27, "h": 9, "m": 55, "n": 8, "s": 24,
  "w": 0, "y": 2026}` ✔ (note: `n` is 8 in September — month appears to
  be 0-indexed or carries another meaning; unconfirmed).
* `GET groups/<id>/geo_data` ✔ (city/country/state/zip empty,
  `coarsePosition` lat/long, `tz_code`).
* `GET groups/<id>/extra_data` → **404 on this account** (spec/app
  present; the app stores `maxPowerProfiles` there via POST — likely
  404 until populated). Method kept, marked accordingly.
* `GET /api/notifications/v1/<gid>/presence/config` ✔ →
  `{"excluded_nodes": [{"devid": "...", "nodes": []}]}`.

#### Code-review round 3 (2026-09-27, offline)
* **Error mapping hardened** (`_request`): aiohttp total-timeout raises a
  plain `TimeoutError` (verified empirically: NOT an
  `aiohttp.ClientConnectionError` subclass) — it now also maps to
  `APIUnavailableError`, and 5xx responses are treated as transient
  unavailability (same contract as the token endpoint) so `_request_with_retry`
  retries them on GETs. 4xx (other than 401) still raises `SmartboxError`.
  Consumer impact: the integration already catches
  `(SmartboxError, APIUnavailableError)` jointly everywhere, so no
  integration change needed; contract tests stay green.
* **pmo power-limit asymmetry (OPEN, unverified)**: GET reads
  `devs/<id>/pmo/<addr>/power` (key `power`) but the write posts
  `devs/<id>/pmo/<addr>/power_limit` (key `power_limit`). One of the two
  is probably wrong — probe on pmo hardware before trusting the write.

#### Code-review round 5 (2026-09-27)
* **Raw-mode wire shapes** (live-verified, raw payloads now passed
  through unvalidated): `GET /devs` → `{devs: [...], invited_to?}` (device
  keys `dev_id, fw_version, name, product_id, serial_id`);
  `GET /grouped_devs` → list of homes with keys
  `devs, extraData, id, name, owner` — `extraData` is not in the `Home`
  model and was silently dropped by the old validate-then-dump raw path;
  `connected` → `{connected}`; `mgr/away_status` →
  `{away, enabled, forced}`.
* **Auth recovery contract**: refresh-grant 4xx → tokens cleared, one
  password-grant login; request 401 → token invalidated, one re-auth +
  resend (401 means the server acted on nothing, so writes are resent
  too). Refresh-token TTL still uncaptured (open question above) — the
  fallback makes it irrelevant for availability.
* **Deadline**: `_CALL_TIMEOUT` = 30s per library call (refresh +
  retries + backoff), independent of whose websession is used.

#### Box-device round (2026-09-29, integration side)
Integration round that consumed the device-level surface; open questions
routed here per the routing rules.
* **Open question — RTC month indexing**: `GET /devs/<id>/mgr/rtc/time` `n`
  is 0-indexed (September → 8, live-verified 2026-09-27; integration's
  `rtc_time_to_datetime` assumes month = `n + 1`). December unverified —
  whether `n` wraps to 0 (0-indexed) or 12 (1-indexed) is unknown. Check
  live or capture a December frame.
* **Open question — power-limit 0 POST**: integration now treats
  `GET/POST /devs/<id>/htr_system/power_limit` `power_limit == 0` as
  "no limit" (user-confirmed semantics; web UI caps the editable range at
  60000). Whether the server actually ACCEPTS a POST of `power_limit: 0`
  (vs rejecting/reverting it) is not yet live-verified — test before
  trusting the NoPowerLimitSwitch on→0 path.
* **Open question — away enabled/forced**: `mgr/away_status` returns the
  full `{away, enabled, forced}` triple; the integration now surfaces all
  three as attributes but only ever POSTs `{away: bool}` — confirm the
  server-side meaning of `enabled`/`forced` (who may set them, what
  `enabled: false` does to an away write).

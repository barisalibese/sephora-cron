# web-watcher-bot

Watches a value on a web page or JSON API on a schedule, compares it against a
rule you define, and e-mails you only when the rule fires. Runs for free on
GitHub Actions cron — no server, no always-on machine.

The repo ships configured for one live job: alerting when SEPHORiA LONDON 2026
resale tickets become available. See [Current watch](#current-watch).

## How it works

```
config.yaml ──> fetch ──> extract ──────────────> rule ──┬─> quiet
                          CSS selector (html)            └─> e-mail digest
                          json_path     (json)
                              state/state.json <── remembers last value
```

State lives in `state/state.json`, committed back to the repo by the workflow.
That is what makes rules edge-triggered: you get one mail when a price drops
below your threshold, not one every 30 minutes for the next week.

## Configuration

Every entry under `targets` is one thing to watch. See `config.example.yaml`
for a commented example of each shape.

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Unique label; also the key used in the state file |
| `url` | yes | Page or endpoint to fetch |
| `source` | no | `html` (default) or `json`. Inferred as `json` when `json_path` is set |
| `json_path` | json only | Dotted path with `[n]` indexes, e.g. `a.b[0].c` |
| `json_mode` | no | `raw` (default, canonical JSON), `count` (length), `text` (scalar) |
| `selector` | html only | CSS selector. Omitted → the whole page's text |
| `index` | no | Which match to use when the selector hits several (default `0`) |
| `attr` | no | Read an attribute (`href`, `content`, …) instead of the text |
| `regex_extract` | no | Narrow the extracted text; group 1 wins if there is one |
| `condition` | no | Rule to apply (default `changed`) |
| `value` | conditional | Threshold or needle; required by every rule except `changed` |
| `repeat` | no | `true` → mail on every run while true, not just on the transition |
| `headers` | no | Extra request headers, e.g. `Accept-Language` |

### Conditions

| Condition | Fires when |
|---|---|
| `changed` | The value differs from the previous run (never on the first run) |
| `equals` | Exact match after trimming |
| `contains` / `not_contains` | Case-insensitive substring test |
| `regex` | The pattern matches anywhere in the value |
| `gt` / `gte` / `lt` / `lte` | Numeric comparison against `value` |

Numeric rules parse both `1.499,90 TL` and `$1,499.90`. Genuinely ambiguous
input like `1.234` is read as a thousands separator (→ `1234.0`), while `12.5`
stays `12.5`.

### Watching a JSON API instead of HTML

Prefer this whenever the site has one. It survives redesigns, needs no browser,
and gives you exact numbers rather than parsed text:

```yaml
- name: "Tickets"
  url: "https://api.example.com/widget"
  json_path: "first_step.data.ticket-market-rates.rates"
  json_mode: count      # the length of the array
  condition: gt
  value: 0
```

`json_mode: raw` serialises with sorted keys, so `changed` will not fire just
because the server reordered a JSON object.

To find the endpoint on a JS-rendered page: open DevTools → Network → XHR,
reload, and look for the request carrying the value you care about.

### Finding a selector

Open the page → right-click the value → Inspect → right-click the highlighted
node → Copy → Copy selector. Then verify it before committing:

```bash
python -m watcher.main --config config.yaml --state /tmp/probe.json --dry-run -v
```

The `-v` output prints the extracted value per target, so a wrong selector shows
up immediately as an `ExtractionError` rather than as silence weeks later.

## Local run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # fill in the SMTP values
set -a && source .env && set +a

python -m watcher.main --test-mail     # verify SMTP first
python -m watcher.main --dry-run -v   # preview; does not touch state
python -m watcher.main                # actually sends
pytest -q
```

Note the first run on a fresh state file only records values — `changed` has
nothing to compare against yet. Run it twice to see it work.

## E-mail setup (Gmail)

Gmail will not accept your normal account password over SMTP — it only accepts
an **App Password**, and App Passwords only exist on accounts that have 2-Step
Verification switched on. That is the whole trick; everything else is defaults.

**1. Turn on 2-Step Verification** at <https://myaccount.google.com/security>.
Without it the App Passwords page does not exist and returns you to settings.

**2. Create an App Password** at <https://myaccount.google.com/apppasswords>.
Name it anything (`watcher`). Google shows 16 characters in four groups —
`abcd efgh ijkl mnop`. **Paste it without the spaces.** It is shown once.

**3. Put it in `.env`:**

```bash
cp .env.example .env
```

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_SSL=true
SMTP_USER=you@gmail.com          # the account the App Password belongs to
SMTP_PASSWORD=abcdefghijklmnop   # the 16 characters, no spaces
MAIL_FROM=you@gmail.com          # Gmail only sends as SMTP_USER or a verified alias
MAIL_TO=you@gmail.com            # comma-separated for several recipients
```

**4. Prove it works before deploying anything:**

```bash
set -a && source .env && set +a
python -m watcher.main --test-mail
```

That sends one mail and exits. It checks the obvious mistakes before dialling
the server (password not 16 characters, spaces left in, `MAIL_FROM` not
matching `SMTP_USER`, port/encryption mismatch) and translates whatever the
server says into the setting you need to change. The password is never printed
or included in the message.

Never put the real password in `.env.example` — that file **is** committed.
`.env` is the gitignored one. Do not wrap values in quotes either: `source`
strips them, GitHub Actions does not, so a quoted secret works locally and
fails in CI. The watcher strips them anyway and warns you, but fix the source.

`.env` is gitignored. The password is not a login to your account — it grants
SMTP sending only, and you can revoke it from the same page at any time.

### If it still fails

| Symptom | Cause |
|---|---|
| `Username and Password not accepted` | Normal password instead of an App Password, or 2FA off |
| Works locally, fails in Actions | The value is quoted. `source .env` strips quotes, Actions does not — set secrets bare, no `"` |
| Mail sends but never arrives | Check Spam; mail from yourself to yourself is sometimes filed there |
| Connection times out | Port 465/587 blocked on that network — Actions is not affected |

### Not Gmail?

Any SMTP server works. Outlook/Office 365 is `smtp.office365.com:587` with
`SMTP_SSL=false`. For a mailbox that is not yours to risk, a transactional
provider (Resend, Mailgun, SendGrid) hands you SMTP credentials meant for
automation and free tiers cover this volume easily.

## Deploying to GitHub Actions

```bash
gh repo create web-watcher-bot --public --source=. --push
```

Public is deliberate — see [Choose a public repo](#choose-a-public-repo-not-a-private-one).

Then add the secrets (Settings → Secrets and variables → Actions), or:

```bash
gh secret set SMTP_USER     --body "you@gmail.com"
gh secret set SMTP_PASSWORD --body "your-app-password"
gh secret set MAIL_TO       --body "you@gmail.com"
gh secret set SMTP_HOST     --body "smtp.gmail.com"
gh secret set SMTP_PORT     --body "465"
gh secret set MAIL_FROM     --body "you@gmail.com"
```

Trigger a manual run to confirm the wiring before trusting the schedule:

```bash
gh workflow run watch.yml -f dry_run=true
gh run watch
```

Change the interval by editing the `cron` line in `.github/workflows/watch.yml`.

## The startup self-test

On the very first run against an empty state file, the watcher sends one
"Watcher is live" mail listing every target, the rule applied to it, and the
value it resolved to right now. That single mail proves three things at once:
SMTP is wired correctly, every selector/path actually resolves, and the rules
read the way you intended.

A target that is *already* satisfied on that first run is marked
`TRIGGERED NOW` inside the same mail — day-one availability never costs you a
second message, and it is never silently swallowed. Set
`settings.startup_notice: false` to skip it.

## Current watch

Target: SEPHORiA LONDON 2026 resale, `sites.weezevent.com/sephoria-london`.

That page is a React SPA whose HTML contains no ticket data at all, so scraping
it would watch an empty shell. The widget reads a JSON API instead, and the
"0 x Tickets / Tickets are unavailable at the moment" box on screen is exactly
an empty `rates` array plus a populated `errors` array in this payload:

```
GET https://api.weezevent.com/ticket/widgets/resale-sephoria-london-2026?locale=en-gb
```

`config.yaml` watches four independent signals in that payload, so a change in
any one of Weezevent's flags still reaches you:

| Signal | Now | Alerts when |
|---|---|---|
| `…ticket-market-rates.rates` | `0` items | count goes above 0 |
| `…ticket-market-rates.errors` | `1` message | the unavailable banner clears |
| `…event.on_resale` | `False` | flips to `True` |
| `first_step.slug` | `ticket-resale-market-waiting-list` | the flow leaves the waiting list |

The first three carry `repeat: true`: while tickets are up you get a mail every
run, not one and then silence. That is deliberate for a ticket drop — drop it
to `false` in `config.yaml` if the resale stays open and the mails get noisy.
All four firing at once still produce a single mail, not four.

## No token is involved

Worth stating explicitly, because it is the obvious thing to worry about with a
watcher meant to run unattended for weeks: **this endpoint needs no
credentials, so there is nothing that can expire.** Verified against the live
API rather than assumed:

| Probe | Result |
|---|---|
| Bare request, no headers at all | `200`, full payload |
| Bogus `Origin: https://evil.example.com` | `200` |
| 20 rapid consecutive requests | 20 × `200`, no throttling |
| Response headers | no `Set-Cookie`, no `WWW-Authenticate`, no rate-limit headers |
| Unknown widget key | `404 {"detail":"Not found."}` |

The payload does contain a JWT at `first_step.token` with a 24-hour expiry, but
it is **issued by** the API, not sent to it — it authorises the next step of the
purchase flow, and a fresh one is minted on every single request (two calls two
seconds apart return different tokens). We never hold it, so it cannot go stale
on us.

The `Accept`/`Origin`/`Referer` headers in `config.yaml` are therefore optional.
They are kept only so the traffic resembles the widget's own.

If Weezevent ever *does* put this behind auth, the watcher does not go quiet: a
`401`/`403`/`404` becomes a `FetchError`, the error streak climbs, and you get a
mail naming the status code. A `200` whose shape changed fails just as loudly —
the extraction error names the broken path segment and lists the keys that
*are* present.

## Choose a public repo, not a private one

This is the one deployment decision that can silently kill the watcher.

GitHub bills Actions per job, **rounded up to the whole minute**. A 5-minute
cron is 288 runs/day ≈ 8,640 minutes/month, against a Free-plan allowance of
2,000 minutes/month **for private repositories**. The quota would be exhausted
in roughly seven days, after which scheduled runs simply stop — no ticket
alert, no error, nothing.

Public repositories get unlimited free Actions minutes, so this watch belongs
in a public repo. Nothing here is sensitive: the watched URL is public, and all
credentials live in GitHub Secrets, which are never exposed in the repository
or in logs.

The one caveat for public repos is that GitHub disables scheduled workflows
after 60 days of repository inactivity. The weekly heartbeat writes to
`state/state.json`, which the workflow commits — that commit is the activity
that keeps the schedule alive, and the mail is your proof it worked.

## Timing

GitHub's cron minimum is 5 minutes, and scheduled workflows are queued on a
best-effort basis — delays of 5-20 minutes are normal, and longer during peak
load. For a contested ticket drop, treat this as "you will hear within the
hour", not "within five minutes".

If you need tighter timing, run the same command from a small always-on box:

```bash
*/2 * * * * cd /path/to/web-watcher-bot && .venv/bin/python -m watcher.main >> watch.log 2>&1
```

## Operational notes

- **Failures are debounced, then backed off.** A target that fails to fetch is
  retried silently; you get a mail at `notify_on_error_after` consecutive
  failures (default 3), and after that only at each doubling — strikes 3, 6,
  12, 24, 48. On a 5-minute cron that is 15min, 30min, 1h, 2h, 4h. A target
  that stays broken for a day costs you five mails, not 288.
- **Quiet runs write nothing.** The state file is rewritten only when a tracked
  value, a fired flag, or an error streak actually moves — per-run timestamps
  deliberately do not count. Without this the workflow would push a commit
  every five minutes forever.
- **Silence is never ambiguous.** If nothing fires for `heartbeat_days`
  (default 7), you get a "Still watching" mail listing every target's current
  value. If those stop arriving, the job itself has died — which is the failure
  mode a watcher must never hide. Set `heartbeat_days: 0` to disable.
- **One target failing does not stop the others.** Each target is checked
  independently and its error streak is tracked separately.
- **One digest per run.** Several targets firing at once produce a single mail,
  not one per target.
- **Removing a target from the config** prunes its state entry automatically.
- **JS-rendered pages need their API, not their HTML.** This fetches raw HTML;
  a React/Vue page will hand you an empty shell. Find the XHR the page itself
  calls and point a `json_path` target at it, as `config.yaml` does.
- **Be polite.** A 30-minute interval against a public site is fine; a
  30-second one will get your IP blocked and is not what this is designed for.

# Hedge Fund Tracker

Follows what well-known investors are publicly required to disclose, by pulling
their **13F filings** straight from SEC EDGAR, then aggregates all of them into
a weekly consensus/theme report. No API key needed — EDGAR's data is free and
public.

## What this does

Every institutional manager overseeing $100M+ in US equities must file a 13F
within 45 days of each quarter-end, listing their long US stock/ETF/option
positions.

- `hedge_tracker.py` fetches the latest 13F for each tracked fund, saves it as
  a CSV snapshot, and can diff two snapshots for a single fund (new/closed
  positions, biggest size changes).
- `theme_report.py` aggregates the *latest* snapshot across all 10 tracked
  funds into one conviction-weighted ranking and a sector/theme rollup, and
  diffs that aggregate against its own last run.

**Limitations worth knowing:**
- 45-day filing lag — you're always seeing a stale snapshot, not real-time.
- Long US equities/options only — no shorts, no macro/FX/futures, no cash. For
  macro traders (Druckenmiller, Soros, Bridgewater) this is a very partial
  picture of their actual strategy.
- Multiple line items per issuer are normal (different share classes/lots) —
  `theme_report.py` sums them per fund before aggregating.
- Sector classification in `theme_report.py` is a hand-built keyword matcher
  on issuer name (13F carries no official sector code) — treat it as a rough
  guide, not a GICS-accurate breakdown. Unmatched names fall into
  "Other/Unclassified".

## Setup

No dependencies to install — everything uses Python's standard library. Just
needs Python 3 (tested on 3.14).

## Usage

```bash
py hedge_tracker.py list-funds             # show tracked funds
py hedge_tracker.py fetch druckenmiller    # pull latest 13F for one fund
py hedge_tracker.py fetch-all              # pull latest 13F for every tracked fund
py hedge_tracker.py compare druckenmiller  # diff the two most recent snapshots for one fund

py theme_report.py                         # aggregate all funds -> consensus + theme report
```

Per-fund snapshots are saved to `data/<fund_key>/<period-end-date>.csv`.
`compare` needs at least two saved snapshots for a fund — most funds only
refile quarterly (13Fs post roughly Feb 14, May 15, Aug 14, Nov 14, 45 days
after quarter-end).

`theme_report.py` writes a dated markdown report to `reports/<date>.md` and a
slim JSON snapshot to `data/_aggregate_history/<date>.json`, which the next
run diffs against to show what changed since the last check.

## Tracked funds (max 10)

Edit `funds.json` to add/remove funds. Find a manager's CIK via EDGAR's
company search:

```
https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=<name>&type=13F
```

Currently tracked, chosen for strong, well-documented long-term track records
across different styles:

| Key | Manager | Fund | Style | Weight |
|---|---|---|---|---|
| druckenmiller | Stanley Druckenmiller | Duquesne Family Office | Macro | 5 |
| soros | George Soros | Soros Fund Management | Macro | 5 |
| buffett | Warren Buffett | Berkshire Hathaway | Value | 5 |
| appaloosa | David Tepper | Appaloosa LP | Distressed/Opportunistic | 5 |
| baupost | Seth Klarman | Baupost Group | Value/Special Situations | 4 |
| pershing_square | Bill Ackman | Pershing Square Capital Mgmt | Activist | 4 |
| bridgewater | Ray Dalio (founder) | Bridgewater Associates | Macro | 3 |
| renaissance | Jim Simons (founder, d. 2024) | Renaissance Technologies | Quant | 3 |
| greenlight | David Einhorn | Greenlight Capital | Value/Long-Short | 2 |
| scion | Michael Burry | Scion Asset Management | Contrarian/Concentrated | 2 |

**On the "weight" column:** this is a 1-5 qualitative tier I assigned based on
each manager's publicly known long-term track record — it is **not** computed
from live return data (no free source exists for exact historical hedge fund
returns), and it is **not investment advice**. Full rationale and caveats per
fund (e.g. Renaissance's 13F reflects its external funds, not the legendary
Medallion fund, which is closed to outsiders and files no 13F) are in
`funds.json` under `weight_rationale`. `theme_report.py` uses this weight to
scale each position's share of that fund's portfolio when building the
cross-fund conviction score — so a top holding at a 5-weight fund counts for
more than an equivalent-sized position at a 2-weight fund.

Some managers file irregularly or go dark for stretches (Burry has previously
deregistered from 13F disclosure; Greenlight's filings can lag) —
`theme_report.py` flags any fund whose latest filing is 120+ days older than
the group's newest as **STALE** in the report.

## Weekly automated report

A **Windows Task Scheduler** job ("HedgeFundTracker Weekly Report") runs
`run_weekly.ps1` every Monday at 9:00 AM (catches up automatically if your PC
is off/asleep at that time, via `StartWhenAvailable`). Each run:

1. `py hedge_tracker.py fetch-all` — pulls any newly posted/amended filings
2. `py theme_report.py` — regenerates the consensus/theme report and diffs it
   against last week's
3. Commits and pushes the updated snapshots/history/report to this repo
   (best-effort — won't fail the run if git isn't set up to push silently)
4. Opens the freshest `reports/<date>.md` in Notepad so you see it

Logs for each run are written to `logs/run_<timestamp>.log` (gitignored).
Since most funds only refile quarterly, most weekly runs will report "no
change" in the underlying data — the value is in catching a new filing within
days rather than waiting for the next manual check, and once a new quarter's
13Fs land, surfacing what actually shifted across the group.

To inspect or change the schedule:
```powershell
Get-ScheduledTask -TaskName "HedgeFundTracker Weekly Report" | Get-ScheduledTaskInfo
```

**Note:** this was originally meant to run as a cloud routine (works even if
your PC is off) but got stuck on Claude's GitHub-account connection for
routines returning "Connect your GitHub account" even after authorizing and
restarting the app — worth revisiting later since the repo/script side is
already fully ready for it (see conversation history for what was tried).

## Notes on the data

The `value` field is the position's market value **in whole US dollars** as
of quarter-end (SEC changed the reporting instructions in 2023 — older
filings, pre Q2 2023, report this in *thousands* instead, so don't naively
diff a pre-2023 snapshot against a post-2023 one).

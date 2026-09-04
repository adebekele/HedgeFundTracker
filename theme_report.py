#!/usr/bin/env python3
"""Aggregate all tracked funds' latest 13F snapshots into a weekly report.

Builds a conviction ranking (which stocks the tracked funds collectively
have the most weight behind, adjusted for each fund's performance tier) and
a rough sector/theme breakdown. Diffs against the previous run, if one
exists, to show what changed since the last check.

Usage:
    py theme_report.py
"""
import csv
import json
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent
FUNDS_FILE = ROOT / "funds.json"
DATA_DIR = ROOT / "data"
HISTORY_DIR = DATA_DIR / "_aggregate_history"
REPORTS_DIR = ROOT / "reports"

STALE_DAYS = 120  # flag a fund's snapshot as stale if this much older than the newest one

# Best-effort keyword classifier. 13F filings carry no sector code, so this
# matches on issuer name substrings for well-known names/industries. Anything
# unmatched falls into "Other/Unclassified" -- treat this as a rough guide,
# not a GICS-accurate breakdown.
ETF_SECTOR = "Broad-Market ETFs/Index Products"

SECTOR_KEYWORDS = {
    # Checked first so a fund's index/beta exposure doesn't get mistaken for
    # a single-stock conviction bet.
    ETF_SECTOR: [
        "spdr", "ishares", "vanguard index", "vanguard fds", "invesco exchange traded",
        " etf", "index fd", "exchange traded fd",
    ],
    "Semiconductors/AI Hardware": [
        "semiconductor", "nvidia", "micron", "broadcom", "advanced micro devices",
        "taiwan semiconductor", "applied materials", "lam research", " kla ",
        "texas instruments", "qualcomm", "analog devices", "stmicroelectronics",
        "arm holdings", "marvell", "on semiconductor", "seagate", "western digital",
        "coreweave",
    ],
    "Big Tech/Software/Internet": [
        "microsoft", "alphabet", "amazon com", "meta platforms", "apple inc",
        "oracle corp", "salesforce", "adobe inc", "servicenow", "palantir",
        "snowflake", "crowdstrike", "workday", "alibaba", "baidu", "sea ltd",
        "cdw corp", "kyndryl",
    ],
    "Banks/Financials": [
        "bank", "bancorp", "financial corp", "jpmorgan", "goldman sachs",
        "morgan stanley", "wells fargo", "citigroup", "american express",
        "visa inc", "mastercard", "brookfield", "moodys", "cme group",
        "slm corp",
    ],
    "Payments/Fintech": [
        "paypal", "block inc", "global pmts", "global payments", "bill holdings",
        "fiserv", "sofi",
    ],
    "Healthcare/Biotech/Pharma": [
        "pharma", "biotech", "therapeutics", "genomics", "health", "medical",
        "biosciences", "diagnostics", "natera", "unitedhealth", "pfizer",
        "merck", "eli lilly", "moderna", "exelixis", "molina", "insmed",
        "teleflex", "davita", "bruker",
    ],
    "Energy/Oil & Gas/Utilities": [
        "petroleum", " oil ", "energy", "exxon", "chevron", "occidental",
        "schlumberger", "halliburton", "consol", "vistra", "nrg energy",
        "pg&e", "ypf sociedad", "kinross gold", "linde",
    ],
    "Consumer/Retail/Restaurants": [
        "retail", "restaurant", "costco", "walmart", "target corp",
        "home depot", "nike", "starbucks", "mcdonald", "lululemon",
        "coca cola", "kraft heinz", "bbb foods", "genuine parts",
    ],
    "Autos/EV": [
        "motors", "automotive", "tesla", "rivian", "ford motor", "general motors",
    ],
    "Transportation/Travel": [
        "uber technologies", "air lines", "airlines", "cruise line", "delta air",
    ],
    "Industrials/Materials": [
        "industrial", "aerospace", "boeing", "caterpillar", "alcoa", "steel",
        "chemicals", "union pac", "railroad", "ferguson enterprises", "wesco",
    ],
    "Insurance": [
        "insurance", "chubb", "progressive", "allstate", "aon plc", "brighthouse",
    ],
    "Real Estate/REIT": [
        "reit", "realty", "properties", "real estate", "howard hughes",
        "green brick", "seaport entmt",
    ],
    "Media/Entertainment/Gaming": [
        "media", "entertainment", "electronic arts", "walt disney", "warner",
        "netflix", "fox corp",
    ],
}


def classify_sector(issuer_name):
    name = issuer_name.lower()
    for sector, keywords in SECTOR_KEYWORDS.items():
        for kw in keywords:
            if kw in name:
                return sector
    return "Other/Unclassified"


def load_funds():
    return json.loads(FUNDS_FILE.read_text())


def latest_snapshot(fund_key):
    fund_dir = DATA_DIR / fund_key
    if not fund_dir.exists():
        return None
    snapshots = sorted(fund_dir.glob("*.csv"))
    return snapshots[-1] if snapshots else None


def load_holdings(path):
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def aggregate():
    funds = load_funds()
    fund_data = {}  # key -> {report_date, holdings_by_cusip, total_value}
    newest_report_date = None

    for key, fund in funds.items():
        snap_path = latest_snapshot(key)
        if snap_path is None:
            print(f"  [skip] {key}: no snapshot found -- run 'py hedge_tracker.py fetch {key}' first")
            continue
        report_date = snap_path.stem
        rows = load_holdings(snap_path)

        by_cusip = defaultdict(lambda: {"issuer": "", "value": 0, "shares": 0})
        for row in rows:
            cusip = row["cusip"]
            by_cusip[cusip]["issuer"] = row["issuer"]
            by_cusip[cusip]["value"] += int(row["value"] or 0)
            by_cusip[cusip]["shares"] += int(row["shares"] or 0)

        total_value = sum(h["value"] for h in by_cusip.values())
        fund_data[key] = {
            "report_date": report_date,
            "holdings": by_cusip,
            "total_value": total_value,
        }
        rd = datetime.strptime(report_date, "%Y-%m-%d").date()
        if newest_report_date is None or rd > newest_report_date:
            newest_report_date = rd

    # Build the cross-fund conviction ranking
    global_holdings = defaultdict(lambda: {"issuer": "", "score": 0.0, "funds": []})
    for key, fd in fund_data.items():
        fund = funds[key]
        weight = fund["performance_weight"]
        total_value = fd["total_value"] or 1
        for cusip, h in fd["holdings"].items():
            weight_in_fund = h["value"] / total_value
            contribution = weight * weight_in_fund
            g = global_holdings[cusip]
            g["issuer"] = h["issuer"]
            g["score"] += contribution
            g["funds"].append(
                {
                    "fund_key": key,
                    "manager": fund["manager"],
                    "value": h["value"],
                    "weight_in_fund": weight_in_fund,
                }
            )

    ranking = sorted(global_holdings.items(), key=lambda kv: -kv[1]["score"])
    stock_ranking = [(cusip, g) for cusip, g in ranking if classify_sector(g["issuer"]) != ETF_SECTOR]

    # Sector rollup
    sector_scores = defaultdict(float)
    sector_members = defaultdict(list)
    for cusip, g in ranking:
        sector = classify_sector(g["issuer"])
        sector_scores[sector] += g["score"]
        sector_members[sector].append(g["issuer"])

    sector_ranking = sorted(sector_scores.items(), key=lambda kv: -kv[1])

    # Staleness flags
    stale = {}
    for key, fd in fund_data.items():
        rd = datetime.strptime(fd["report_date"], "%Y-%m-%d").date()
        age_days = (newest_report_date - rd).days
        if age_days > STALE_DAYS:
            stale[key] = (fd["report_date"], age_days)

    return {
        "funds": funds,
        "fund_data": fund_data,
        "ranking": ranking,
        "stock_ranking": stock_ranking,
        "sector_ranking": sector_ranking,
        "sector_members": sector_members,
        "stale": stale,
        "newest_report_date": str(newest_report_date),
    }


def load_previous_history():
    if not HISTORY_DIR.exists():
        return None
    snapshots = sorted(HISTORY_DIR.glob("*.json"))
    if not snapshots:
        return None
    return json.loads(snapshots[-1].read_text())


def save_history(result, run_date):
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{run_date}.json"
    slim = {
        "run_date": run_date,
        "newest_report_date": result["newest_report_date"],
        "top_25": [
            {"cusip": cusip, "issuer": g["issuer"], "score": g["score"], "fund_count": len(g["funds"])}
            for cusip, g in result["stock_ranking"][:25]
        ],
        "sector_ranking": [{"sector": s, "score": sc} for s, sc in result["sector_ranking"]],
    }
    path.write_text(json.dumps(slim, indent=2))
    return path


def diff_against_previous(result, previous):
    if previous is None:
        return None
    prev_top_cusips = {row["cusip"] for row in previous["top_25"]}
    curr_top = result["stock_ranking"][:25]
    curr_top_cusips = {cusip for cusip, _ in curr_top}

    entered = [(cusip, g) for cusip, g in curr_top if cusip not in prev_top_cusips]
    exited_cusips = prev_top_cusips - curr_top_cusips
    exited = [row for row in previous["top_25"] if row["cusip"] in exited_cusips]

    prev_sectors = {row["sector"]: row["score"] for row in previous["sector_ranking"]}
    sector_deltas = []
    for sector, score in result["sector_ranking"]:
        delta = score - prev_sectors.get(sector, 0.0)
        sector_deltas.append((sector, score, delta))
    sector_deltas.sort(key=lambda x: -abs(x[2]))

    return {
        "previous_run_date": previous["run_date"],
        "entered_top_25": entered,
        "exited_top_25": exited,
        "sector_deltas": sector_deltas,
    }


def render_report(result, diff, run_date):
    funds = result["funds"]
    lines = []
    lines.append(f"# Hedge Fund Consensus Report -- {run_date}")
    lines.append("")
    lines.append(
        "Aggregates the latest public 13F filings of 10 tracked funds/investors "
        "into a single conviction-weighted ranking. Weighting combines each "
        "position's share of a fund's reported 13F portfolio with a qualitative "
        "1-5 performance tier assigned to that fund (see funds.json for "
        "rationale per fund) -- **this is not investment advice, and the tiers "
        "are subjective judgments based on publicly known long-term track "
        "records, not computed returns.**"
    )
    lines.append("")

    lines.append("## Data freshness")
    lines.append("")
    for key, fd in result["fund_data"].items():
        fund = funds[key]
        flag = " -- **STALE**" if key in result["stale"] else ""
        lines.append(f"- **{fund['manager']}** ({fund['name']}): period {fd['report_date']}{flag}")
    if result["stale"]:
        lines.append("")
        lines.append(
            "Stale flags mean that manager's most recent public 13F is well "
            "behind the others (over 120 days older than the newest filing in "
            "the group) -- their current snapshot may not reflect their actual "
            "present-day book."
        )
    lines.append("")

    lines.append("## Top 25 consensus conviction holdings")
    lines.append("")
    lines.append(
        "(Broad-market index ETF/fund positions -- SPY, iShares, Vanguard, etc. "
        "-- are excluded here since they represent beta exposure, not a stock "
        "thesis; they're still counted in the sector rollup below.)"
    )
    lines.append("")
    lines.append("| # | Issuer | Score | # Funds | Held by |")
    lines.append("|---|---|---|---|---|")
    for i, (cusip, g) in enumerate(result["stock_ranking"][:25], 1):
        holders = ", ".join(sorted({f["manager"].split(" (")[0] for f in g["funds"]}))
        lines.append(f"| {i} | {g['issuer']} | {g['score']:.3f} | {len(g['funds'])} | {holders} |")
    lines.append("")

    lines.append("## Sector/theme rollup (heuristic classification)")
    lines.append("")
    lines.append("| Sector | Weighted score |")
    lines.append("|---|---|")
    for sector, score in result["sector_ranking"]:
        lines.append(f"| {sector} | {score:.3f} |")
    lines.append("")

    if diff is not None:
        lines.append(f"## Changes since last check ({diff['previous_run_date']})")
        lines.append("")
        if diff["entered_top_25"]:
            lines.append("**Newly entered top 25:**")
            for cusip, g in diff["entered_top_25"]:
                lines.append(f"- {g['issuer']} (score {g['score']:.3f})")
            lines.append("")
        if diff["exited_top_25"]:
            lines.append("**Dropped out of top 25:**")
            for row in diff["exited_top_25"]:
                lines.append(f"- {row['issuer']}")
            lines.append("")
        if not diff["entered_top_25"] and not diff["exited_top_25"]:
            lines.append("No change in the top 25 since the last check.")
            lines.append("")
        lines.append("**Biggest sector score moves:**")
        for sector, score, delta in diff["sector_deltas"][:5]:
            arrow = "+" if delta >= 0 else ""
            lines.append(f"- {sector}: {score:.3f} ({arrow}{delta:.3f})")
        lines.append("")
    else:
        lines.append(
            "## Changes since last check\n\nNo prior run found -- this is the "
            "first report. Future weekly runs will diff against this one.\n"
        )

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- 13Fs are filed within 45 days of quarter-end and only cover long "
        "US equities/options -- no shorts, macro, FX, futures, or cash. For "
        "macro-oriented funds (Druckenmiller, Soros, Bridgewater) this "
        "captures a small slice of the actual strategy."
    )
    lines.append(
        "- Because most funds only refile quarterly, most weekly checks will "
        "show \"no change\" in the underlying data -- this run mainly catches "
        "newly posted or amended filings within days rather than waiting for "
        "the next manual check."
    )
    return "\n".join(lines)


def main():
    print("Aggregating latest snapshots for all tracked funds...\n")
    result = aggregate()
    if not result["fund_data"]:
        print("No snapshots found for any fund. Run 'py hedge_tracker.py fetch-all' first.")
        return

    previous = load_previous_history()
    diff = diff_against_previous(result, previous)

    run_date = str(date.today())
    save_history(result, run_date)

    report_md = render_report(result, diff, run_date)
    REPORTS_DIR.mkdir(exist_ok=True)
    report_path = REPORTS_DIR / f"{run_date}.md"
    report_path.write_text(report_md, encoding="utf-8")

    print(report_md)
    print(f"\n\nFull report saved to {report_path}")


if __name__ == "__main__":
    main()

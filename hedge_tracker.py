#!/usr/bin/env python3
"""Track hedge fund 13F filings from SEC EDGAR.

Usage:
    py hedge_tracker.py list-funds
    py hedge_tracker.py fetch <fund_key>
    py hedge_tracker.py fetch-all
    py hedge_tracker.py compare <fund_key>
"""
import argparse
import csv
import json
import sys
import time
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).parent
FUNDS_FILE = ROOT / "funds.json"
DATA_DIR = ROOT / "data"

# SEC requires a descriptive User-Agent identifying the requester; update the
# email if you want SEC's rate-limit team to be able to reach you.
USER_AGENT = "HedgeFundTracker personal-research abekele@gmail.com"

NS = {"n": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}


def load_funds():
    return json.loads(FUNDS_FILE.read_text())


def sec_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def get_13f_filings(cik):
    cik10 = cik.zfill(10)
    data = json.loads(sec_get(f"https://data.sec.gov/submissions/CIK{cik10}.json"))
    recent = data["filings"]["recent"]
    filings = []
    for i, form in enumerate(recent["form"]):
        if form.startswith("13F-HR") and not form.endswith("/A"):
            filings.append(
                {
                    "accession": recent["accessionNumber"][i],
                    "filingDate": recent["filingDate"][i],
                    "reportDate": recent["reportDate"][i],
                }
            )
    return filings


def get_infotable_url(cik, accession):
    accn_nodash = accession.replace("-", "")
    cik_int = str(int(cik))
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/index.json"
    idx = json.loads(sec_get(index_url))
    for item in idx["directory"]["item"]:
        name = item["name"]
        if name.endswith(".xml") and name != "primary_doc.xml":
            return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{name}"
    return None


def parse_infotable(xml_bytes):
    root = ET.fromstring(xml_bytes)
    holdings = []
    for info in root.findall("n:infoTable", NS):
        def text(tag):
            el = info.find(f"n:{tag}", NS)
            return el.text if el is not None else ""

        shrs = info.find("n:shrsOrPrnAmt", NS)
        shares = shrs.find("n:sshPrnamt", NS).text if shrs is not None else "0"
        holdings.append(
            {
                "issuer": text("nameOfIssuer"),
                "class": text("titleOfClass"),
                "cusip": text("cusip"),
                "value": text("value"),
                "shares": shares,
            }
        )
    return holdings


def save_snapshot(fund_key, report_date, holdings):
    out_dir = DATA_DIR / fund_key
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report_date}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["issuer", "class", "cusip", "value", "shares"]
        )
        writer.writeheader()
        writer.writerows(holdings)
    return path


def cmd_fetch(fund_key, funds):
    fund = funds[fund_key]
    print(f"Fetching 13F filings for {fund['name']} ({fund['manager']})...")
    filings = get_13f_filings(fund["cik"])
    if not filings:
        print("No 13F-HR filings found.")
        return
    latest = filings[0]
    print(f"Latest filing: {latest['filingDate']} for period {latest['reportDate']}")
    infotable_url = get_infotable_url(fund["cik"], latest["accession"])
    if not infotable_url:
        print("Could not locate holdings XML in this filing.")
        return
    xml_bytes = sec_get(infotable_url)
    holdings = parse_infotable(xml_bytes)
    path = save_snapshot(fund_key, latest["reportDate"], holdings)
    total_value = sum(int(h["value"] or 0) for h in holdings)
    print(f"Saved {len(holdings)} holdings (${total_value:,} total) -> {path}")
    top = sorted(holdings, key=lambda h: int(h["value"] or 0), reverse=True)[:10]
    print("\nTop 10 holdings by reported value:")
    for h in top:
        print(f"  {h['issuer']:<35} ${int(h['value']):>15,}   {int(h['shares']):>12,} sh")


def cmd_fetch_all(funds):
    for i, key in enumerate(funds):
        if i > 0:
            time.sleep(0.3)  # be polite to SEC's rate limits
            print()
        cmd_fetch(key, funds)


def cmd_list_funds(funds):
    print("Tracked funds:")
    for key, f in funds.items():
        print(f"  {key:<15} {f['name']} ({f['manager']}) - CIK {f['cik']}")


def cmd_compare(fund_key):
    out_dir = DATA_DIR / fund_key
    snapshots = sorted(out_dir.glob("*.csv")) if out_dir.exists() else []
    if len(snapshots) < 2:
        print(
            f"Need at least 2 saved snapshots for '{fund_key}' to compare.\n"
            "Run 'fetch' now, then again after the next quarterly filing "
            "(13Fs are filed within 45 days of quarter-end)."
        )
        return
    prev_path, curr_path = snapshots[-2], snapshots[-1]
    print(f"Comparing {prev_path.stem} -> {curr_path.stem}\n")

    def load(path):
        with path.open(encoding="utf-8") as f:
            return {row["cusip"]: row for row in csv.DictReader(f)}

    prev, curr = load(prev_path), load(curr_path)
    prev_cusips, curr_cusips = set(prev), set(curr)

    new_positions = curr_cusips - prev_cusips
    closed_positions = prev_cusips - curr_cusips
    held = curr_cusips & prev_cusips

    if new_positions:
        print(f"NEW POSITIONS ({len(new_positions)}):")
        for c in sorted(new_positions, key=lambda c: -int(curr[c]["value"] or 0)):
            h = curr[c]
            print(f"  + {h['issuer']:<35} ${int(h['value']):>15,}")
        print()

    if closed_positions:
        print(f"CLOSED POSITIONS ({len(closed_positions)}):")
        for c in sorted(closed_positions, key=lambda c: -int(prev[c]["value"] or 0)):
            h = prev[c]
            print(f"  - {h['issuer']:<35} ${int(h['value']):>15,} (prior)")
        print()

    changes = []
    for c in held:
        pv = int(prev[c]["value"] or 0)
        cv = int(curr[c]["value"] or 0)
        if pv == 0:
            continue
        pct = (cv - pv) / pv * 100
        changes.append((pct, curr[c]["issuer"], pv, cv))
    changes.sort(key=lambda x: -abs(x[0]))

    if changes:
        print("BIGGEST CHANGES IN EXISTING POSITIONS:")
        for pct, issuer, pv, cv in changes[:15]:
            arrow = "^" if pct > 0 else "v"
            print(f"  {arrow} {issuer:<35} ${pv:>15,} -> ${cv:>15,}  ({pct:+.1f}%)")


def main():
    parser = argparse.ArgumentParser(description="Track hedge fund 13F filings from SEC EDGAR.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-funds", help="List tracked funds")

    p_fetch = sub.add_parser("fetch", help="Fetch latest 13F for one fund")
    p_fetch.add_argument("fund_key")

    sub.add_parser("fetch-all", help="Fetch latest 13F for all tracked funds")

    p_compare = sub.add_parser("compare", help="Compare two most recent saved snapshots for a fund")
    p_compare.add_argument("fund_key")

    args = parser.parse_args()
    funds = load_funds()

    if args.command == "list-funds":
        cmd_list_funds(funds)
    elif args.command == "fetch":
        if args.fund_key not in funds:
            sys.exit(f"Unknown fund '{args.fund_key}'. Run 'list-funds' to see options.")
        cmd_fetch(args.fund_key, funds)
    elif args.command == "fetch-all":
        cmd_fetch_all(funds)
    elif args.command == "compare":
        if args.fund_key not in funds:
            sys.exit(f"Unknown fund '{args.fund_key}'. Run 'list-funds' to see options.")
        cmd_compare(args.fund_key)


if __name__ == "__main__":
    main()

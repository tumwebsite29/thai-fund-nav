"""
ดึง NAV ล่าสุดของกองทุนรวมไทยทั้งหมดจาก SEC Open Data API
แล้ว generate nav_latest.csv และ nav_latest.html

รันด้วย: SEC_API_KEY=<your key> python fetch_nav.py
(ใน GitHub Actions, SEC_API_KEY มาจาก repository secret)
"""

import os
import re
import csv
import time
from datetime import date, timedelta, datetime, timezone
from zoneinfo import ZoneInfo
import requests

SUBSCRIPTION_KEY = os.environ["SEC_API_KEY"]
BASE_URL = "https://api.sec.or.th"
LOOKBACK_DAYS = 10
PAGE_SIZE = 100
MAX_RETRIES = 4

HEADERS = {
    "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
    "Accept": "application/json",
}


def call_api(path, params):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(BASE_URL + path, headers=HEADERS, params=params, timeout=60)
            if resp.status_code == 204:
                return {"items": [], "next_cursor": None}
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            wait = 5 * attempt
            print(f"  [retry {attempt}/{MAX_RETRIES}] {path} failed ({e}); waiting {wait}s")
            time.sleep(wait)
    # หมดจำนวนครั้งลองใหม่แล้วยังไม่ผ่าน ยอมแพ้และแจ้ง error จริง
    raise last_error


def strip_fund_suffix(name):
    """ตัดคำว่า 'FUND' ท้ายชื่อออก เช่น 'KKP ACT FIXED FUND' -> 'KKP ACT FIXED'"""
    words = (name or "").strip().split()
    if words and words[-1].upper() == "FUND":
        words = words[:-1]
    return " ".join(words).strip()


def norm(s):
    """ตัดช่องว่าง/ขีด/เครื่องหมายทั้งหมดออก แล้วแปลงเป็นตัวพิมพ์ใหญ่ ใช้เทียบ
    string แบบไม่สนรูปแบบการเว้นวรรค/ตัวพิมพ์เล็ก-ใหญ่ที่ไม่ตรงกัน"""
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()


def lcs_len(a, b):
    """หาความยาวของ substring ที่ยาวที่สุดที่ปรากฏเหมือนกันใน a และ b
    (ไม่ว่าจะอยู่ตำแหน่งไหนของ string ก็ตาม ไม่จำกัดแค่ต้นคำแบบ prefix)"""
    if not a or not b:
        return 0
    m, n = len(a), len(b)
    prev = [0] * (n + 1)
    best = 0
    for i in range(1, m + 1):
        curr = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
                if curr[j] > best:
                    best = curr[j]
        prev = curr
    return best


def build_fund_name(abbr, fund_class):
    fc = (fund_class or "").strip()
    if fc == "" or fc.lower() == "main":
        return abbr

    abbr_core = strip_fund_suffix(abbr)
    abbr_n = norm(abbr_core)
    fc_n = norm(fc)
    shorter_len = min(len(fc_n), len(abbr_n))

    if shorter_len < 3:
        # fund_class_name หรือ proj_abbr_name สั้นเกินไปจะเทียบให้แม่นยำ
        # (เช่น fc="A", "I") ถือว่าเป็นรหัสสั้น ต่อกับชื่อกองทุนแบบเดิม
        return f"{abbr}-{fc}"

    # ต้องการ substring ร่วมกันอย่างน้อย 4 ตัวอักษร (ไม่ว่าจะอยู่ต้นคำ
    # กลางคำ ยาวกว่า สั้นกว่า หรือมีคำแทรก/หายไปก็ตาม) แต่ถ้า string สั้น
    # กว่า 4 อยู่แล้ว (เช่น "MMM" มี 3 ตัว) ก็ลดเกณฑ์ลงมาเท่าความยาวจริง
    required = min(4, shorter_len)

    if lcs_len(fc_n, abbr_n) >= required:
        # fund_class_name กับ proj_abbr_name มีส่วนที่เหมือนกันมากพอ ถือว่า
        # มาจากชื่อกองทุนเดียวกัน ใช้ fund_class_name ตรง ๆ เลย ไม่ต่อซ้ำ
        return fc

    # ไม่เกี่ยวข้องกันพอ แปลว่า fund_class_name เป็นรหัสสั้นจริง ๆ
    # (เช่น "A", "I", "SSF") ต่อกับชื่อกองทุนแบบเดิม
    return f"{abbr}-{fc}"


def fetch_all_pages(path, params):
    items = []
    cursor = None
    while True:
        query = dict(params)
        if cursor:
            query["next_cursor"] = cursor
        data = call_api(path, query)
        page_items = data.get("items") or []
        items.extend(page_items)
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return items


def main():
    print("Fetching fund profiles...")
    profiles = fetch_all_pages("/v2/fund/general-info/profiles", {"page_size": PAGE_SIZE})
    profile_by_id = {p["proj_id"]: p for p in profiles if p.get("proj_id")}
    print(f"  {len(profiles)} profiles fetched")

    end_date = date.today()
    start_date = end_date - timedelta(days=LOOKBACK_DAYS)
    print(f"Fetching NAV from {start_date} to {end_date}...")
    nav_items = fetch_all_pages(
        "/v2/fund/daily-info/nav",
        {
            "start_nav_date": start_date.isoformat(),
            "end_nav_date": end_date.isoformat(),
            "page_size": PAGE_SIZE,
        },
    )
    print(f"  {len(nav_items)} NAV records fetched")

    # เก็บเฉพาะ nav_date ล่าสุดต่อ (proj_id, fund_class_name)
    latest = {}
    for item in nav_items:
        if not item.get("nav_date"):
            continue
        key = (item.get("proj_id"), item.get("fund_class_name"))
        if key not in latest or item["nav_date"] > latest[key]["nav_date"]:
            latest[key] = item

    rows = []
    for (proj_id, fund_class), item in latest.items():
        profile = profile_by_id.get(proj_id, {})
        if profile.get("fund_status") != "Registered":
            continue
        abbr = profile.get("proj_abbr_name") or proj_id
        fund_name = build_fund_name(abbr, fund_class)
        rows.append(
            {
                "fund_name": fund_name,
                "nav_date": item.get("nav_date"),
                "nav_value": item.get("last_val"),
                "sell_price": item.get("sell_price"),
                "buy_price": item.get("buy_price"),
                "net_asset": item.get("net_asset") or 0,
                "proj_id": proj_id,
                "comp_name_th": profile.get("comp_name_th"),
            }
        )

    rows.sort(key=lambda r: r["fund_name"])
    print(f"  {len(rows)} funds in final output")

    write_csv(rows)
    updated_at_th = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Bangkok"))
    write_html(rows, updated_at_th)
    print("Done: nav_latest.csv, nav_latest.html")


def write_csv(rows):
    with open("nav_latest.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "fund_name",
                "nav_date",
                "nav_value",
                "sell_price",
                "buy_price",
                "net_asset",
                "proj_id",
                "comp_name_th",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_html(rows, updated_at_th):
    updated = updated_at_th.strftime("%Y-%m-%d %H:%M น. (เวลาไทย)")
    table_rows = "\n".join(
        f"<tr><td>{r['fund_name']}</td><td>{r['nav_date']}</td>"
        f"<td>{r['nav_value']}</td><td>{r['sell_price']}</td><td>{r['buy_price']}</td>"
        f"<td>{r['net_asset']:,.2f}</td><td>{r['proj_id']}</td></tr>"
        for r in rows
    )
    html = f"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="utf-8">
<title>Thai Fund NAV — Latest ({updated})</title>
<style>
body {{ font-family: -apple-system, sans-serif; margin: 2rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: right; }}
th:first-child, td:first-child {{ text-align: left; }}
th {{ background: #f4f4f4; position: sticky; top: 0; }}
#search {{ margin-bottom: 1rem; padding: 8px; width: 320px; font-size: 14px; }}
</style>
</head>
<body>
<h1>Thai Mutual Fund NAV — ล่าสุดรายกอง</h1>
<p>อัปเดต: {updated} | จำนวน {len(rows)} กอง/ชนิดหน่วยลงทุน | ข้อมูลจาก SEC Open Data (ก.ล.ต.) | อัปเดตอัตโนมัติทุกวัน</p>
<input id="search" placeholder="ค้นหาชื่อกองทุน...">
<table id="navtable">
<thead><tr><th>Fund Name</th><th>NAV Date</th><th>NAV</th><th>Sell</th><th>Buy</th><th>Net Asset (THB)</th><th>Proj ID</th></tr></thead>
<tbody>
{table_rows}
</tbody>
</table>
<script>
document.getElementById('search').addEventListener('input', function () {{
  var q = this.value.toUpperCase();
  document.querySelectorAll('#navtable tbody tr').forEach(function (tr) {{
    tr.style.display = tr.children[0].textContent.toUpperCase().includes(q) ? '' : 'none';
  }});
}});
</script>
</body>
</html>
"""
    with open("nav_latest.html", "w", encoding="utf-8") as f:
        f.write(html)
    # เขียนซ้ำเป็น index.html ด้วย เพื่อให้ GitHub Pages แสดงตารางที่หน้า
    # แรกของเว็บได้เลย (เช่น https://<user>.github.io/<repo>/)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()

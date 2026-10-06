"""Generate the bundled sample datasets for the Ask language app.
Run once: python make_data.py  (writes CSVs into sample_data/)."""
import csv, os, random

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_data")
os.makedirs(HERE, exist_ok=True)
random.seed(14)

REGIONS = ["West", "East", "North", "South"]
SEGMENTS = ["Consumer", "Corporate", "Home Office"]


def _write(name, header, rows):
    with open(os.path.join(HERE, name), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print("wrote", name, len(rows), "rows")


def make_sales(name, n=120):
    rows = []
    for i in range(n):
        region = random.choice(REGIONS)
        segment = random.choice(SEGMENTS)
        cost = round(random.uniform(50, 900), 2)
        revenue = round(cost * random.uniform(1.05, 2.4), 2)
        rows.append([f"ORD-{1000+i}", region, segment,
                     f"${revenue:,.2f}", round(cost, 2)])
    _write(name, ["order_id", "region", "segment", "revenue", "cost"], rows)


def make_reviews(name, n=200):
    ratings = ["Poor", "Fair", "Good", "Great"]
    weights = [1, 2, 4, 3]
    rows = []
    for i in range(n):
        score = random.choices(ratings, weights=weights)[0]
        rows.append([f"R-{i+1}", score, random.choice(["West", "East"])])
    _write(name, ["review_id", "score", "region"], rows)


# ---- ILOStat: female employment in Chile 2025, activity buried in notes ----
ACTIVITIES = [
    "Agriculture, forestry and fishing", "Manufacturing", "Construction",
    "Wholesale and retail trade", "Transportation and storage",
    "Accommodation and food service", "Information and communication",
    "Financial and insurance activities", "Education", "Human health and social work",
    "Public administration", "Professional and technical activities",
]
SCHEMES = ["ISIC-Rev.4", "ISIC-Rev.3.1", "Aggregate"]


def make_ilostat(name):
    rows = []
    # Total row per scheme + one row per activity per scheme, across a couple years
    for year in (2024, 2025):
        for scheme in SCHEMES:
            base = 900 if scheme == "ISIC-Rev.4" else 500
            # a Total line (should be filtered out by "activity is not Total")
            rows.append(["Chile", "Employment by economic activity", "LFS",
                         "Female", "25+", "National", year,
                         round(base * len(ACTIVITIES) * random.uniform(0.9, 1.1), 1),
                         "R", f"Employment ({scheme}): Total", "ilostat_2025.xlsx"])
            for act in ACTIVITIES:
                val = round(base * random.uniform(0.4, 3.0), 1)
                rows.append(["Chile", "Employment by economic activity", "LFS",
                             "Female", "25+", "National", year, val,
                             "R", f"Employment ({scheme}): {act}", "ilostat_2025.xlsx"])
    _write(name, ["country", "indicator", "source", "sex", "age_group",
                  "area_type", "year", "value", "status_flag", "notes",
                  "source_file"], rows)


def make_signups(name, n=400):
    import datetime
    plans = ["Free", "Pro", "Team"]
    start = datetime.date(2025, 1, 1)
    rows = []
    for _ in range(n):
        day = start + datetime.timedelta(days=random.randint(0, 364))
        plan = random.choice(plans)
        amount = 0 if plan == "Free" else (12 if plan == "Pro" else 40)
        rows.append([day.isoformat(), plan, amount])
    rows.sort(key=lambda r: r[0])
    _write(name, ["signup_date", "plan", "amount"], rows)


def make_wide_scores(name):
    rows = []
    for i in range(1, 25):
        rows.append([f"Student {i}",
                     random.randint(45, 98), random.randint(45, 98),
                     random.randint(45, 98)])
    _write(name, ["student", "math", "science", "english"], rows)


def make_fuzzy(orders_name, customers_name):
    canonical = ["Acme Corp", "Globex", "Initech", "Umbrella", "Soylent"]
    typos = {"Acme Corp": ["Acme Corp", "Acme Corp.", "Acme  Corp", "Acme Crop"],
             "Globex": ["Globex", "Globex Inc", "Glboex"],
             "Initech": ["Initech", "Intech", "Initech LLC"],
             "Umbrella": ["Umbrella", "Umbrela", "Umbrella Co"],
             "Soylent": ["Soylent", "Soylent", "Solyent"]}
    orders = []
    for i in range(40):
        canon = random.choice(canonical)
        messy = random.choice(typos[canon])
        orders.append([f"O-{1000+i}", messy, random.randint(100, 900)])
    _write(orders_name, ["order_id", "name", "amount"], orders)
    tiers = ["Gold", "Silver", "Bronze"]
    custs = [[c, random.choice(tiers)] for c in canonical]
    _write(customers_name, ["name", "tier"], custs)


SHARED_ASK = """# A shared library of Ask recipes you can import into any program.
define recipe best_regions from data:
  where revenue is not missing
  group by region
  show total revenue as sales
  top 5 by sales
"""


if __name__ == "__main__":
    make_sales("sales.csv")
    make_sales("q1.csv", n=90)
    make_reviews("reviews.csv")
    make_ilostat("ilostat.csv")
    make_signups("signups.csv")
    make_wide_scores("wide_scores.csv")
    make_fuzzy("orders_messy.csv", "customers.csv")
    with open(os.path.join(HERE, "shared.ask"), "w") as f:
        f.write(SHARED_ASK)
    print("wrote shared.ask")

"""Bundled sample programs (shown in the UI dropdown, run by the test suite)."""

SAMPLES = {
    "1. Hello, data": '''load "sales.csv"
  where region is "West"
  show average revenue''',

    "2. Group and chart": '''load "sales.csv"
  add margin = (revenue - cost) / revenue
  bin margin into low, medium, high
  group by region, margin_tier
  show total revenue as sales
  chart sales by region as bar
    color by margin_tier
    label "Revenue by region and margin tier"''',

    "3. Algebraic type drives the axis": '''type Rating = Poor | Fair | Good | Great
load "reviews.csv"
  add score as Rating
  group by score
  show count
  chart count by score as bar''',

    "4. Reusable recipe": '''define recipe top_regions from data:
  where revenue is not missing
  group by region
  show total revenue as sales
  top 5 by sales

load "q1.csv"
  top_regions
  chart sales by region as bar''',

    "5. Real research data (ILOStat)": '''load "ilostat.csv"
  understand
  add scheme = extract between "(" and ")" from notes
  add activity = extract after ": " from notes
  add activity = extract after ". " from activity
  where scheme is "ISIC-Rev.4"
  where activity is not "Total"
  where year is 2025
  group by activity
  show total value as employment
  top 10 by employment
  sort by employment descending
  chart employment by activity as bar
    label "Female employment by sector, Chile 2025"''',

    "6. Compare groups in words": '''load "sales.csv"
  where revenue is not missing
  explain
  compare revenue between region''',

    "7. Relate and predict": '''load "sales.csv"
  add profit = revenue - cost
  relate cost and revenue
  predict revenue from cost''',

    "8. Roll dates up by month": '''load "signups.csv"
  resample by month
  chart amount by signup_date as line
    label "Sign-up revenue per month"''',

    "9. Reshape wide data to long": '''load "wide_scores.csv"
  reshape long keeping student
  group by name
  show average value as score
  chart score by name as bar
    label "Average score by subject"''',

    "10. Fuzzy-match messy names": '''load "orders_messy.csv"
  combine with customers on name fuzzy
  group by tier
  show total amount as revenue, count
  chart revenue by tier as bar
    label "Revenue by customer tier"''',

    "11. Import a recipe + save a report": '''import "shared.ask"
load "q1.csv"
  best_regions
  chart sales by region as bar
    label "Top regions, Q1"
  save report as "q1_report.html"''',

    "12. Strict mode, seed, bootstrap": '''use strict types
set seed 7
load "sales.csv"
  estimate average revenue with bootstrap
  show average revenue, median revenue, skew revenue, quantile revenue at 0.9 method linear''',

    "13. Ordered category axis": '''type Rank = ordered Poor < Fair < Good < Great
load "reviews.csv"
  add score as Rank
  group by score
  show count
  sort by score
  chart count by score as bar
    label "Reviews by rating (ordered)"''',

    "14. Join type + unmatched report": '''load "orders_messy.csv"
  combine with customers on name fuzzy keeping matches only
  group by tier
  show total amount as revenue, count
  chart revenue by tier as bar
    label "Revenue by customer tier (matches only)"''',

    "15. Windows and group-wise picks": '''load "sales.csv"
  group by region
  running total of revenue as region_running
  add revenue_share = revenue per group
  keep top 3 within group by revenue
  sort by region''',

    "16. Blocks, functions, formulas": '''define function margin(rev, c): (rev - c) / rev
load "sales.csv"
  add m = margin(revenue, cost)
  add m2 = let base = revenue - cost in base / revenue
  call it enriched
from enriched
  predict revenue from cost and region
  relate m and m2''',

    "17. For each group, one recipe": '''define recipe summary from data:
  show total revenue as sales, average cost as avg_cost
load "sales.csv"
  for each region do recipe summary
  sort by sales descending''',

    "18. Yes/no prediction (logistic)": '''set seed 3
load "sales.csv"
  add profitable = if revenue > cost * 1.35 then "yes" else "no"
  predict profitable from region''',

    "19. Distribution of a number": '''load "sales.csv"
  chart revenue by revenue as distribution
    color by segment
    label "How revenue is spread out, by segment"''',

    "20. Change across categories": '''load "sales.csv"
  group by region, segment
  show total revenue as s
  chart s by region as change
    color by segment
    label "Revenue by region, split by segment"''',

    "21. Flow between two categories": '''load "sales.csv"
  group by region, segment
  show total revenue as s
  chart s by region as flow
    color by segment
    label "Where revenue flows: region to segment"''',

    "22. Tree of nested categories": '''load "sales.csv"
  group by region, segment
  show total revenue as s
  chart s by region as tree
    color by segment
    label "Revenue by region, broken down by segment"''',

    "23. Network of connected categories": '''load "sales.csv"
  group by region, segment
  show total revenue as s
  chart s by region as network
    color by segment
    label "Regions and segments, connected by revenue"''',

    "24. World map (choropleth)": '''load "countries.csv"
  chart population_millions by country as map
    label "Population by country (millions)"''',

    "25. Story: replay the charts so far": '''load "sales.csv"
  group by region, segment
  show total revenue as s
  chart s by region as bar
    color by segment
  chart s by region as line
    color by segment
  chart s by s as story
    label "How revenue breaks down, three ways"''',
}

# Samples that need an optional extra beyond the core install (name -> the
# module `ask selftest`/the UI should check for before running it, so a
# plain `pip install -e .` doesn't fail self-test over a feature that was
# never meant to be there by default).
OPTIONAL_SAMPLES = {
    "24. World map (choropleth)": "geopandas",
}

WELCOME = '''# Welcome to Ask.
# Pick a sample from the dropdown, or write your own, then press Run.
# A program is a stack of steps: load, then transform, then chart.

load "sales.csv"
  where region is "West"
  show total revenue as sales
'''

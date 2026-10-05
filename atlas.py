"""
Opportunity Atlas county data (Chetty, Friedman, Hendren, Jones & Porter 2018),
used by analyst.py's tools. Three files in data/atlas/, downloaded once:

  county_outcomes_simple.csv  outcomes for children from low-income families
                              (parents at the 25th income percentile), born
                              1978-83, by race and gender — opportunityinsights.org
  cty_covariates.csv          county characteristics (poverty, single parents,
                              test scores, job growth, ...) — opportunityinsights.org
  national_county.txt         county FIPS code -> name — census.gov

Every number is computed here, in Python; the model only chooses what to ask.
`python atlas.py Cook IL` prints one county's profile as a sanity check.
"""

import csv
import re
import sys
from pathlib import Path

import numpy as np

ATLAS_DIR = Path("data/atlas")
RACES = {"all": "pooled", "white": "white", "black": "black", "hispanic": "hisp"}
GENDERS = {"all": "pooled", "male": "male", "female": "female"}
MIN_CHILDREN = 1000  # default floor for rankings: tiny counties have noisy estimates
# A characteristic only "differs" from the national average when it's at least
# this many (child-weighted) standard deviations of the county distribution
# away, and only counts as a candidate explanation for a county's mobility gap
# when it also correlates with mobility across counties at least this strongly.
# Without these, the model built explanations on 33.8% vs 33.4% differences
# and on characteristics (short commutes, r = 0.12) that barely track mobility.
NOTABLE_SD = 0.5
RELEVANT_R = 0.3

# outcome name -> (column template, scale, unit, description)
OUTCOMES = {
    "upward_mobility": (
        "kfr_{race}_{gender}_p25", 100, "income percentile (0-100)",
        "Average adult household income rank (national percentile, measured 2014-15 at ages "
        "31-37) of children born 1978-83 whose parents were at the 25th income percentile"),
    "incarceration": (
        "jail_{race}_{gender}_p25", 100, "% incarcerated",
        "Share of children from 25th-percentile families who were incarcerated on April 1, 2010"),
}
# covariate name -> (column, scale, unit, description)
COVARIATES = {
    "poverty_rate_2010": ("poor_share2010", 100, "%", "Share below the federal poverty line (2006-10 ACS)"),
    "single_parent_share_2010": ("singleparent_share2010", 100, "%",
                                 "Share of households with children headed by a single parent (2006-10 ACS)"),
    "college_grad_share_2010": ("frac_coll_plus2010", 100, "%", "Share of adults 25+ with a bachelor's degree or more"),
    "median_household_income_2016": ("med_hhinc2016", 1, "$", "Median household income (2012-16 ACS)"),
    "math_scores_3rd_grade_2013": ("gsmn_math_g3_2013", 1, "grade-level units",
                                   "Mean 3rd grade math test score, 2013 (Stanford Education Data Archive)"),
    "job_growth_2004_2013": ("ann_avg_job_growth_2004_2013", 100, "% per year",
                             "Average annual job growth rate 2004-2013 (BLS LAUS)"),
    "employment_rate_2000": ("emp2000", 100, "%", "Employed share of population 16+ (2000 Census)"),
    "share_black_2010": ("share_black2010", 100, "%", "Black share of population (2010 Census)"),
    "share_white_2010": ("share_white2010", 100, "%", "Non-Hispanic white share of population (2010 Census)"),
    "share_hispanic_2010": ("share_hisp2010", 100, "%", "Hispanic share of population (2010 Census)"),
    "foreign_born_share_2010": ("foreign_share2010", 100, "%", "Foreign-born share of population"),
    "commute_under_15min_share_2010": ("traveltime15_2010", 100, "%", "Share of workers commuting under 15 minutes"),
    "population_density_2010": ("popdensity2010", 1, "people per sq mile", "Population density (2010 Census)"),
    "two_bedroom_rent_2015": ("rent_twobed2015", 1, "$ per month", "Median gross rent, 2-bedroom (2011-15 ACS)"),
    "census_mail_return_rate_2010": ("mail_return_rate2010", 1, "%",
                                     "2010 Census mail return rate (a proxy for social capital/civic engagement)"),
}
METRICS = list(OUTCOMES) + list(COVARIATES)
# Racial shares are reported in profiles and rankings but not offered for
# correlation: in testing the model correlated Black population share with
# mobility and presented it as a cause, which the research does not support.
CORRELATE_METRICS = [m for m in METRICS if not m.startswith("share_")]

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
_SUFFIX = re.compile(r"\s+(county|parish|borough|census area|city and borough|municipality)$")


def _norm(name: str) -> str:
    return _SUFFIX.sub("", re.sub(r"[^a-z ]", "", name.lower().replace("saint ", "st ")).strip())


def _state_abbr(state: str | None) -> str | None:
    if not state:
        return None
    s = state.strip()
    return s.upper() if len(s) == 2 else STATES.get(s.lower())


def _num(text: str) -> float:
    try:
        return float(text)
    except ValueError:  # "" or "." = suppressed (too few children)
        return np.nan


def _round(x: float, digits: int = 1):
    return None if np.isnan(x) else round(float(x), digits)


class Atlas:
    def __init__(self, directory: Path = ATLAS_DIR):
        names = {}
        for line in open(directory / "national_county.txt", encoding="latin-1"):
            abbr, st, co, name, _ = line.rstrip("\n").split(",")
            names[(int(st), int(co))] = (name, abbr)
        outcomes = {(int(r["state"]), int(r["county"])): r
                    for r in csv.DictReader(open(directory / "county_outcomes_simple.csv"))}
        covariates = {(int(r["state"]), int(r["county"])): r
                      for r in csv.DictReader(open(directory / "cty_covariates.csv"))}
        self.keys = [k for k in outcomes if k in names]
        self.names = [names[k][0] for k in self.keys]
        self.states = np.array([names[k][1] for k in self.keys])
        self.cz = [outcomes[k]["czname"] for k in self.keys]
        self._norm = [_norm(n) for n in self.names]
        self._cols = {}
        for rows in (outcomes, covariates):
            for col in next(iter(rows.values())):
                if col not in ("state", "county", "cz", "czname"):
                    self._cols[col] = np.array([_num(rows.get(k, {}).get(col, "")) for k in self.keys])

    # ---- helpers -------------------------------------------------------
    def _metric(self, metric: str, race: str = "all", gender: str = "all"):
        """Returns (values, weights, unit, description) for a metric, where
        weights = number of children in that race/gender group."""
        r, g = RACES.get(race), GENDERS.get(gender)
        if r is None or g is None:
            raise ValueError(f"race must be one of {list(RACES)}, gender one of {list(GENDERS)}")
        if metric in OUTCOMES:
            template, scale, unit, desc = OUTCOMES[metric]
            col = template.format(race=r, gender=g)
            if col not in self._cols:
                raise ValueError(f"no data for race={race} with gender={gender}")
            return self._cols[col] * scale, self._cols[f"{r}_{g}_count"], unit, desc
        if metric in COVARIATES:
            if (race, gender) != ("all", "all"):
                raise ValueError(f"{metric} is a county characteristic; race/gender don't apply")
            col, scale, unit, desc = COVARIATES[metric]
            return self._cols[col] * scale, self._cols["pooled_pooled_count"], unit, desc
        raise ValueError(f"unknown metric {metric!r}; choose from {METRICS}")

    def _mask(self, state: str | None):
        if not state:
            return np.ones(len(self.keys), bool)
        abbr = _state_abbr(state)
        if abbr is None or abbr not in set(self.states):
            raise ValueError(f"unknown state {state!r}")
        return self.states == abbr

    @staticmethod
    def _wavg(values, weights, mask):
        m = mask & ~np.isnan(values) & ~np.isnan(weights)
        return np.average(values[m], weights=weights[m]) if m.any() else np.nan

    def find(self, county: str, state: str | None = None) -> list[int]:
        target, mask = _norm(county), self._mask(state)
        exact = [i for i, n in enumerate(self._norm) if n == target and mask[i]]
        return exact or [i for i, n in enumerate(self._norm) if target in n and mask[i]]

    def _label(self, i: int) -> str:
        return f"{self.names[i]}, {self.states[i]}"

    # ---- tools ---------------------------------------------------------
    def _covariate_stats(self) -> dict:
        """Per characteristic: child-weighted national mean and SD across
        counties, and weighted correlation with (pooled) upward mobility.
        Computed once, on first profile."""
        if not hasattr(self, "_cov_stats"):
            self._cov_stats = {}
            everywhere = self._mask(None)
            for metric in COVARIATES:
                values, weights, _, _ = self._metric(metric)
                m = ~np.isnan(values) & ~np.isnan(weights)
                mean = np.average(values[m], weights=weights[m])
                sd = np.sqrt(np.average((values[m] - mean) ** 2, weights=weights[m]))
                r = self.correlate(metric, "upward_mobility")["weighted_correlation"] \
                    if metric in CORRELATE_METRICS else None
                self._cov_stats[metric] = (self._wavg(values, weights, everywhere), sd, r)
        return self._cov_stats

    def county_profile(self, county: str, state: str | None = None, demographics: bool = False) -> dict:
        matches = self.find(county, state)
        if not matches:
            return {"error": f"no county matching {county!r}" + (f" in {state}" if state else "")}
        if len(matches) > 1:
            return {"ambiguous": "several counties match; call again with `state`",
                    "matches": [self._label(i) for i in matches[:20]]}
        i = matches[0]
        everywhere, in_state = self._mask(None), self.states == self.states[i]
        # compact rows, not nested dicts: Groq's free tier allows 8K tokens/minute
        out = {"county": self._label(i), "commuting_zone": self.cz[i],
               "children_from_low_income_families": int(self._cols["pooled_pooled_count"][i]),
               "units": {"upward_mobility": OUTCOMES["upward_mobility"][2],
                         "incarceration": OUTCOMES["incarceration"][2]},
               "outcomes [county, state_avg, national_avg, national_percentile_of_counties]": {},
               "characteristics [county, national_avg, unit, vs_national, r_with_upward_mobility]": {}}
        groups = [("all", "all")] + [(r, "all") for r in ("white", "black", "hispanic")] + \
                 [("all", g) for g in ("male", "female")]
        for metric in OUTCOMES:
            for race, gender in groups:
                values, weights, unit, _ = self._metric(metric, race, gender)
                key = metric if (race, gender) == ("all", "all") else \
                    f"{metric}_{race if race != 'all' else gender}"
                valid = ~np.isnan(values) & ~np.isnan(weights)
                out["outcomes [county, state_avg, national_avg, national_percentile_of_counties]"][key] = [
                    _round(values[i]), _round(self._wavg(values, weights, in_state)),
                    _round(self._wavg(values, weights, everywhere)),
                    # share of all US children (in this group) living in counties below this one
                    None if np.isnan(values[i]) else
                    _round(100 * weights[valid & (values < values[i])].sum() / weights[valid].sum(), 0)]
        mobility, weights, _, _ = self._metric("upward_mobility")
        gap = mobility[i] - self._wavg(mobility, weights, everywhere)
        consistent, inconsistent = [], []
        for metric, (_, _, unit, _) in COVARIATES.items():
            if metric.startswith("share_") and not demographics:
                continue
            values, _, _, _ = self._metric(metric)
            mean, sd, r = self._covariate_stats()[metric]
            z = (values[i] - mean) / sd if sd and not np.isnan(values[i]) else 0.0
            vs = ("about average" if abs(z) < NOTABLE_SD else
                  f"{'far ' if abs(z) >= 1 else ''}{'above' if z > 0 else 'below'} average")
            out["characteristics [county, national_avg, unit, vs_national, r_with_upward_mobility]"][metric] = [
                _round(values[i], 2), _round(mean, 2), unit, vs, _round(r, 2) if r is not None else None]
            # a candidate explanation must differ notably AND track mobility; it's
            # consistent with the gap if (difference x correlation) has the gap's sign
            if r is not None and abs(z) >= NOTABLE_SD and abs(r) >= RELEVANT_R and abs(gap) >= 0.5:
                (consistent if np.sign(z) * np.sign(r) == np.sign(gap) else inconsistent).append(metric)
        out["county_vs_national_mobility_gap"] = _round(gap)
        out["characteristics_consistent_with_gap"] = consistent
        out["characteristics_pointing_the_other_way"] = inconsistent
        out["note"] = ("Averages weighted by number of children; null = suppressed (too few children "
                       "in that group). upward_mobility: " + OUTCOMES["upward_mobility"][3] + ". "
                       f"vs_national: within {NOTABLE_SD} SD of the county distribution = about average. "
                       f"Consistent-with-gap = differs notably AND |r| >= {RELEVANT_R} across counties AND "
                       "points the gap's way: associations, not causes."
                       + ("" if demographics else " Racial composition omitted (demographics=true to include)."))
        return out

    def rank_counties(self, metric: str, state: str | None = None, race: str = "all",
                      gender: str = "all", order: str = "highest", n: int = 10,
                      min_children: int = MIN_CHILDREN) -> dict:
        values, weights, unit, desc = self._metric(metric, race, gender)
        m = self._mask(state) & ~np.isnan(values) & (np.nan_to_num(weights) >= min_children)
        idx = np.flatnonzero(m)
        idx = idx[np.argsort(values[idx])]
        if order == "highest":
            idx = idx[::-1]
        n = max(1, min(int(n), 25))
        return {"metric": metric, "description": desc, "unit": unit, "race": race, "gender": gender,
                "scope": _state_abbr(state) or "US", "counties_considered": int(m.sum()),
                "min_children": min_children, "order": order,
                "counties": [{"county": self._label(i), "value": _round(values[i], 2),
                              "children": int(weights[i])} for i in idx[:n]]}

    def correlate(self, x_metric: str, y_metric: str, state: str | None = None,
                  race: str = "all", gender: str = "all") -> dict:
        """Weighted correlation across counties, plus y's average within
        quintiles of x — "counties with the most X average Y" is easier to
        explain than a coefficient. Race/gender apply to outcome metrics."""
        if x_metric not in CORRELATE_METRICS or y_metric not in CORRELATE_METRICS:
            raise ValueError(f"metrics must be among {CORRELATE_METRICS}")
        x, w, x_unit, x_desc = self._metric(x_metric, *((race, gender) if x_metric in OUTCOMES else ("all", "all")))
        y, wy, y_unit, y_desc = self._metric(y_metric, *((race, gender) if y_metric in OUTCOMES else ("all", "all")))
        if y_metric in OUTCOMES:
            w = wy  # weight by the outcome's own group size
        m = self._mask(state) & ~np.isnan(x) & ~np.isnan(y) & ~np.isnan(w) & (w > 0)
        x, y, w = x[m], y[m], w[m]
        if len(x) < 10:
            return {"error": "fewer than 10 counties with data"}
        mx, my = np.average(x, weights=w), np.average(y, weights=w)
        cov = np.average((x - mx) * (y - my), weights=w)
        r = cov / np.sqrt(np.average((x - mx) ** 2, weights=w) * np.average((y - my) ** 2, weights=w))
        # quintiles by children, so each fifth holds ~20% of children
        order = np.argsort(x)
        cum = np.cumsum(w[order]) / w.sum()
        bins = []
        for q in range(5):
            sel = order[(cum > q / 5) & (cum <= (q + 1) / 5)] if q else order[cum <= 0.2]
            if len(sel):
                bins.append({"x_range": [_round(x[sel].min(), 2), _round(x[sel].max(), 2)],
                             "avg_y": _round(np.average(y[sel], weights=w[sel]), 2), "counties": len(sel)})
        # stated in words here, not left to the model: in testing it read r = -0.44
        # (more college grads, LOWER mobility) as the opposite
        strength = "strongly" if abs(r) >= 0.5 else "moderately" if abs(r) >= 0.3 else "weakly"
        direction = "HIGHER" if r > 0 else "LOWER"
        return {"interpretation": f"Counties with higher {x_metric} tend to have {direction} {y_metric} "
                                  f"({strength}: weighted r = {r:.2f}). Correlation, not causation.",
                "x": {"metric": x_metric, "unit": x_unit, "description": x_desc},
                "y": {"metric": y_metric, "unit": y_unit, "description": y_desc},
                "race": race, "gender": gender, "scope": _state_abbr(state) or "US",
                "counties": int(m.sum()), "weighted_correlation": _round(r, 3),
                "y_by_x_quintile (fifths of children, lowest x first)": bins,
                "caution": "Correlation across counties, not a causal effect."}


if __name__ == "__main__":
    import json
    atlas = Atlas()
    print(json.dumps(atlas.county_profile(*sys.argv[1:3]) if len(sys.argv) > 1 else
                     atlas.correlate("single_parent_share_2010", "upward_mobility"), indent=1))

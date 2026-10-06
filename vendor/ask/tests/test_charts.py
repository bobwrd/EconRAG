"""Chart semantics: modifiers honoured per kind, NaN safety, formatting (B1-B9)."""

import numpy as np
import pytest

from asklang import AskError, render_chart
from asklang.ui import fmt_cell


def _axes(run, src):
    t, ch, n, tr = run(src)
    assert ch is not None
    fig = render_chart(ch)
    return fig.axes[0]


def test_line_color_by_draws_lines(run):
    """B1: `as line` + `color by` must draw lines, not bars."""
    ax = _axes(run, 'load "sales.csv"\ngroup by region, segment\n'
                    'show total revenue as s\nchart s by region as line\n'
                    '  color by segment')
    assert len(ax.lines) >= 2
    assert len(ax.patches) == 0


def test_area_color_by_stacks(run):
    ax = _axes(run, 'load "sales.csv"\ngroup by region, segment\n'
                    'show total revenue as s\nchart s by region as area\n'
                    '  color by segment')
    assert len(ax.collections) >= 2   # stackplot polygons
    assert len(ax.patches) == 0


def test_bar_color_by_groups(run):
    ax = _axes(run, 'load "sales.csv"\ngroup by region, segment\n'
                    'show total revenue as s\nchart s by region as bar\n'
                    '  color by segment')
    assert len(ax.patches) > 4
    assert ax.get_legend() is not None


def test_box_by_category_draws_multiple_boxes(run):
    """B2: one box per region, labelled."""
    ax = _axes(run, 'load "sales.csv"\nchart revenue by region as box')
    labels = [t.get_text() for t in ax.get_xticklabels()]
    assert len(labels) >= 4
    assert "West" in labels


def test_heatmap_is_two_dimensional(run):
    """B3: heatmap pivots x (columns) by color_by (rows)."""
    ax = _axes(run, 'load "sales.csv"\ngroup by region, segment\n'
                    'show total revenue as s\nchart s by region as heatmap\n'
                    '  color by segment')
    arr = ax.images[0].get_array()
    assert arr.shape[0] >= 2 and arr.shape[1] >= 2


def test_heatmap_without_color_by_teaches(run):
    t, ch, n, tr = run('load "sales.csv"\ngroup by region\n'
                       'show total revenue as s\nchart s by region as heatmap')
    with pytest.raises(AskError, match="color by"):
        render_chart(ch)


def test_scatter_color_by_legend(run):
    ax = _axes(run, 'load "sales.csv"\nchart revenue by cost as scatter\n'
                    '  color by segment')
    assert ax.get_legend() is not None
    assert len(ax.collections) >= 2


def test_nan_category_does_not_crash(run):
    """A12: unmatched join keys leave NaN in the x column."""
    ax = _axes(run, 'load "orders_messy.csv"\ncombine with customers on name\n'
                    'group by tier\nshow total amount as revenue\n'
                    'chart revenue by tier as bar')
    assert len(ax.patches) >= 1


def test_text_y_column_teaches(run):
    with pytest.raises(AskError, match="isn't numbers"):
        run('load "sales.csv"\nchart region by region as bar')


def test_histogram_adaptive_bins(run):
    ax = _axes(run, 'load "sales.csv"\nchart revenue by revenue as histogram')
    assert 5 <= len(ax.patches) <= 50


def test_no_mpl_deprecation_warnings(run, recwarn):
    """B5: no deprecated matplotlib API (e.g. boxplot vert=)."""
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _axes(run, 'load "sales.csv"\nchart revenue by region as box')
    assert not [x for x in w if "Deprecation" in x.category.__name__]


def test_distribution_draws_density_curve(run):
    ax = _axes(run, 'load "sales.csv"\nchart revenue by revenue as distribution')
    assert len(ax.lines) >= 1
    assert len(ax.collections) >= 1   # fill_between area


def test_distribution_color_by_multiple_curves(run):
    ax = _axes(run, 'load "sales.csv"\nchart revenue by revenue as distribution\n'
                    '  color by segment')
    assert len(ax.lines) >= 2
    assert ax.get_legend() is not None


def test_distribution_needs_two_values_teaches(run):
    t, ch, n, tr = run('load "sales.csv"\ntop 1 by revenue\n'
                       'chart revenue by revenue as distribution')
    with pytest.raises(AskError, match="Not enough numeric values"):
        render_chart(ch)


def test_change_draws_slope_with_delta_annotation(run):
    ax = _axes(run, 'load "sales.csv"\ngroup by region, segment\n'
                    'show total revenue as s\nchart s by region as change\n'
                    '  color by segment')
    assert len(ax.lines) >= 2
    assert len(ax.texts) >= 1


def test_change_needs_two_categories_teaches(run):
    t, ch, n, tr = run('load "sales.csv"\nwhere region is "West"\n'
                       'group by region\nshow total revenue as s\n'
                       'chart s by region as change')
    with pytest.raises(AskError, match="at least two points"):
        render_chart(ch)


def test_flow_draws_ribbons_and_nodes(run):
    ax = _axes(run, 'load "sales.csv"\ngroup by region, segment\n'
                    'show total revenue as s\nchart s by region as flow\n'
                    '  color by segment')
    assert len(ax.patches) >= 4   # node rectangles + ribbon polygons


def test_flow_without_color_by_teaches(run):
    t, ch, n, tr = run('load "sales.csv"\ngroup by region\n'
                       'show total revenue as s\nchart s by region as flow')
    with pytest.raises(AskError, match="two category columns"):
        render_chart(ch)


def test_tree_draws_nested_rectangles(run):
    ax = _axes(run, 'load "sales.csv"\ngroup by region, segment\n'
                    'show total revenue as s\nchart s by region as tree\n'
                    '  color by segment')
    assert len(ax.patches) >= 4 + 4   # top-level regions + sub-level segments


def test_tree_single_level_without_color_by(run):
    ax = _axes(run, 'load "sales.csv"\ngroup by region\n'
                    'show total revenue as s\nchart s by region as tree')
    assert len(ax.patches) >= 4


def test_network_draws_nodes_and_edges(run):
    ax = _axes(run, 'load "sales.csv"\ngroup by region, segment\n'
                    'show total revenue as s\nchart s by region as network\n'
                    '  color by segment')
    assert len(ax.lines) >= 1
    assert len(ax.collections) >= 1


def test_network_without_color_by_teaches(run):
    t, ch, n, tr = run('load "sales.csv"\ngroup by region\n'
                       'show total revenue as s\nchart s by region as network')
    with pytest.raises(AskError, match="two category columns"):
        render_chart(ch)


def test_map_needs_geo_extra_when_geopandas_missing(run, monkeypatch):
    """Simulate the plain-install case (no `geo` extra) regardless of what's
    actually installed in the dev environment, so this test is meaningful
    either way."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "geopandas":
            raise ImportError("no module named geopandas")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    t, ch, n, tr = run('load "countries.csv"\n'
                       'chart population_millions by country as map')
    with pytest.raises(AskError, match="optional 'geo' extra"):
        render_chart(ch)


def test_map_draws_choropleth_with_country_join():
    pytest.importorskip("geopandas")
    from asklang import Env, run_program
    t, ch, n, tr = run_program(
        'load "countries.csv"\nchart population_millions by country as map', Env())
    fig = render_chart(ch)
    ax = fig.axes[0]
    assert len(ax.collections) >= 1   # geopandas draws polygons as a collection


def test_map_all_unmatched_teaches(run):
    pytest.importorskip("geopandas")
    t, ch, n, tr = run('load "countries.csv"\nadd country = "Nowhereland"\n'
                       'chart population_millions by country as map')
    with pytest.raises(AskError, match="matched a country name"):
        render_chart(ch)


def test_map_partial_unmatched_is_noted(run):
    pytest.importorskip("geopandas")
    ax = _axes(run, 'load "countries.csv"\n'
                    'add country = if country is "United States" then country '
                    'else "Nowhereland"\n'
                    'chart population_millions by country as map')
    assert any("Not matched" in t.get_text() for t in ax.texts)


def test_story_replays_earlier_charts(run):
    t, ch, n, tr = run('load "sales.csv"\ngroup by region, segment\n'
                       'show total revenue as s\nchart s by region as bar\n'
                       'chart s by region as line\nchart s by s as story')
    fig = render_chart(ch)
    assert len(fig.axes) == 2   # one panel per earlier chart, not counting itself


def test_story_needs_an_earlier_chart_teaches(run):
    t, ch, n, tr = run('load "sales.csv"\nchart revenue by revenue as story')
    with pytest.raises(AskError, match="at least one chart drawn earlier"):
        render_chart(ch)


def test_fmt_cell_edge_cases():
    """E4: numpy scalars, bools, inf, midnight timestamps."""
    import pandas as pd
    assert fmt_cell(np.int64(1234567), "Number") == "1,234,567"
    assert fmt_cell(True, "Boolean") == "true"
    assert fmt_cell(np.bool_(False), "Boolean") == "false"
    assert fmt_cell(float("inf"), "Number") == "∞"
    assert fmt_cell(float("nan"), "Number") == ""
    assert fmt_cell(pd.Timestamp("2024-01-05"), "Date") == "2024-01-05"
    assert fmt_cell(np.float64(1234.5), None) == "1,234.50"


def test_many_color_groups_get_distinct_colors(tmp_path, run):
    """Real-data regression: an 11-country ILOStat-style chart used to repeat
    colors past the 8-entry base palette, making different countries look
    identical."""
    import pandas as pd
    rows = []
    for i in range(12):
        for yr in (2020, 2021, 2022):
            rows.append({"country": f"C{i}", "year": yr, "value": i + yr})
    p = tmp_path / "many_groups.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    ax = _axes(run, f'load "{p}"\nchart value by year as line\n'
                    f'  color by country')
    colors = {ln.get_color() for ln in ax.get_lines()}
    assert len(colors) == 12


def test_numeric_x_does_not_force_one_tick_per_value(tmp_path, run):
    """Real-data regression: an 80-year ILOStat time series used to force
    every distinct year onto the axis as its own overlapping text label."""
    import pandas as pd
    rows = [{"country": "A", "year": y, "value": y} for y in range(1946, 2026)]
    p = tmp_path / "many_years.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    ax = _axes(run, f'load "{p}"\nchart value by year as line\n'
                    f'  color by country')
    n_years = 2026 - 1946
    assert len(ax.get_xticklabels()) < n_years / 4


def test_categorical_many_categories_thins_ticks(tmp_path, run):
    """A non-numeric axis with many distinct categories should thin its
    tick labels instead of drawing one per category."""
    import pandas as pd
    rows = [{"code": f"cat-{i:03d}", "value": i} for i in range(40)]
    p = tmp_path / "many_cats.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    ax = _axes(run, f'load "{p}"\nchart value by code as line')
    assert len(ax.get_xticklabels()) <= 15


def test_palette_scales_without_warning():
    from asklang.charts import palette
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert len(palette(5)) == 5
        assert len(palette(15)) == 15
        assert len(palette(40)) == 40
    assert len(set(map(tuple, palette(15)))) == 15


def test_heatmap_with_nullable_float_dtype(tmp_path, run):
    """Real-data regression: pandas' nullable Float64 (not plain float64)
    from `show total` made pivot.values come back object-dtype, which
    imshow rejected with 'Image data of dtype object cannot be converted
    to float'."""
    import pandas as pd
    rows = []
    for status in ("Employees", "Self-employed"):
        for activity in ("Agriculture", "Services"):
            rows.append({"status": status, "activity": activity, "value": 10})
    p = tmp_path / "heat.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    ax = _axes(run, f'load "{p}"\ngroup by activity, status\n'
                    f'show total value as employment\n'
                    f'chart employment by activity as heatmap\n'
                    f'  color by status')
    assert len(ax.images) == 1
    assert ax.images[0].get_array().dtype.kind == "f"

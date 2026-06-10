"""Summary statistics generation."""

from collections import Counter
from pathlib import Path

from openpyxl import Workbook

from .models import (
    CategoryStat,
    ClassifyResult,
    ComplaintRow,
    CrossTabResult,
    SummaryData,
)


def generate_summary(
    rows: list[ComplaintRow], results: list[ClassifyResult]
) -> SummaryData:
    """Generate complete summary statistics.

    Args:
        rows: Original complaint rows.
        results: Classification results.

    Returns:
        SummaryData with category stats and cross tabulations.
    """
    cat_stats = category_stats(results)

    cross_tabs: list[dict[str, str | dict[str, dict[str, int]]]] = []
    cross_tabs.append(cross_tabulate(rows, results, "受理渠道", "primary_category"))
    cross_tabs.append(cross_tabulate(rows, results, "客户星级", "primary_category"))

    return SummaryData(
        category_stats=[dict(s) for s in cat_stats],
        cross_tabs=cross_tabs,
    )


def category_stats(results: list[ClassifyResult]) -> list[CategoryStat]:
    """Calculate count and percentage for each category.

    Args:
        results: Classification results.

    Returns:
        List of CategoryStat sorted by count descending.
    """
    counter: Counter = Counter()
    for r in results:
        key = f"{r['primary_category']} > {r['secondary_category']}"
        counter[key] += 1

    total = len(results) if results else 1
    stats: list[CategoryStat] = []
    for key, count in counter.most_common():
        parts = key.split(" > ", 1)
        stats.append(
            CategoryStat(
                primary_category=parts[0],
                secondary_category=parts[1] if len(parts) > 1 else "",
                count=count,
                percentage=round(count / total * 100, 2),
            )
        )
    return stats


def cross_tabulate(
    rows: list[ComplaintRow],
    results: list[ClassifyResult],
    row_dim: str,
    col_dim: str,
) -> CrossTabResult:
    """Create a cross-tabulation (pivot table).

    Args:
        rows: Original complaint rows.
        results: Classification results.
        row_dim: Field name from ComplaintRow for rows.
        col_dim: Field name from ClassifyResult for columns ('primary_category').

    Returns:
        CrossTabResult with matrix, row totals, col totals.
    """
    result_map: dict[int, ClassifyResult] = {}
    for r in results:
        result_map[r["row_index"]] = r

    row_values: list[str] = []
    col_values: list[str] = []

    matrix: dict[str, dict[str, int]] = {}
    row_totals: dict[str, int] = {}
    col_totals: dict[str, int] = {}

    for row in rows:
        result = result_map.get(row.row_index)
        if result is None:
            continue

        # Get row dimension value
        row_val = getattr(row, row_dim, "")
        if not row_val:
            row_val = "(空)"
        if row_val not in row_values:
            row_values.append(row_val)

        # Get column dimension value
        col_val = result.get(col_dim, "")
        if not col_val:
            col_val = "(空)"
        if col_val not in col_values:
            col_values.append(col_val)

        # Populate matrix
        if row_val not in matrix:
            matrix[row_val] = {}
        matrix[row_val][col_val] = matrix[row_val].get(col_val, 0) + 1
        row_totals[row_val] = row_totals.get(row_val, 0) + 1
        col_totals[col_val] = col_totals.get(col_val, 0) + 1

    return CrossTabResult(
        row_dim=row_dim,
        col_dim=col_dim,
        matrix=matrix,
        row_totals=row_totals,
        col_totals=col_totals,
    )


def write_summary(summary: SummaryData, output_path: str, results: list[ClassifyResult], rows: list[ComplaintRow]) -> str:
    """Write summary statistics to Excel file.

    Args:
        summary: Summary data to write.
        output_path: Output file path.
        results: Classification results.
        rows: Original complaint rows.

    Returns:
        Output file path.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # Sheet 1: Category stats
    ws1 = wb.active
    if ws1 is not None:
        ws1.title = "分类汇总"
        ws1.append(["一级分类", "二级分类", "数量", "占比"])
        for stat in summary["category_stats"]:
            ws1.append([
                stat["primary_category"],
                stat["secondary_category"],
                stat["count"],
                f"{stat['percentage']}",
            ])

    # Sheet 2: Channel x Category
    ct_channel = summary["cross_tabs"][0]
    _write_cross_tab_sheet(wb, "渠道x分类", ct_channel)

    # Sheet 3: Star x Category (if exists)
    if len(summary["cross_tabs"]) > 1:
        ct_star = summary["cross_tabs"][1]
        _write_cross_tab_sheet(wb, "星级x分类", ct_star)

    wb.save(output_path)
    wb.close()
    return str(output_path)


def _write_cross_tab_sheet(wb: Workbook, title: str, ct: CrossTabResult | dict[str, str | dict[str, dict[str, int]]]) -> None:
    """Write a single cross-tabulation to a sheet."""
    ws = wb.create_sheet(title=title)
    matrix = ct.get("matrix", {}) if isinstance(ct, dict) else ct["matrix"]  # type: ignore[index]
    if not isinstance(matrix, dict):
        return

    col_keys = list(ct.get("col_totals", {}).keys()) if isinstance(ct, dict) else list(ct["col_totals"].keys())  # type: ignore[index]

    # Header row
    ws.append([ct.get("row_dim", "")] + col_keys + ["合计"])

    # Data rows
    for row_val, col_counts in matrix.items():
        row_data = [row_val]
        for ck in col_keys:
            row_data.append(col_counts.get(ck, 0))
        row_data.append(sum(col_counts.values()))
        ws.append(row_data)

    # Total row
    row_totals = ct.get("row_totals", {}) if isinstance(ct, dict) else ct["row_totals"]  # type: ignore[index]
    col_totals = ct.get("col_totals", {}) if isinstance(ct, dict) else ct["col_totals"]  # type: ignore[index]
    total_row = ["合计"]
    for ck in col_keys:
        total_row.append(col_totals.get(ck, 0))
    total_row.append(sum(col_totals.values()))
    ws.append(total_row)

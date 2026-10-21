"""Integration test for the analysis views on a small fixture (opt-in: TEST_DATABASE_URL)."""

import pytest
from sqlalchemy import text

from src.analysis.views import apply_views
from src.db import migrate

pytestmark = pytest.mark.integration


def _report(conn, n, month, cutoff, sha):
    return conn.execute(
        text(
            "INSERT INTO reports (report_number, report_month, language, data_cutoff_date, filename, source_url,"
            " sha256) VALUES (:n, :m, 'en', :c, :f, 'https://www.darpg.gov.in/x.pdf', :s) RETURNING report_id"
        ),
        {"n": n, "m": month, "c": cutoff, "f": f"r{n}.pdf", "s": sha * 64},
    ).scalar_one()


def _monthwise(conn, report_id, values):
    table_id = conn.execute(
        text(
            "INSERT INTO source_tables (report_id, table_type, original_title, pdf_page_start, pdf_page_end,"
            " period_start, period_end, parser_name, parser_version, extraction_method)"
            " VALUES (:r, 'overall_monthwise', 'Month-wise', 6, 6, '2022-01-01', '2022-12-31', 't', '0', 'manual')"
            " RETURNING table_id"
        ),
        {"r": report_id},
    ).scalar_one()
    for i, (month, value) in enumerate(values):
        conn.execute(
            text(
                "INSERT INTO observations (table_id, row_index, is_aggregate, metric_code, metric_label_original, value_original,"
                " value_numeric, period_start, period_end, pdf_page)"
                " VALUES (:t, :i, true, 'receipts', 'Receipts', :v, :v, :m, :m, 6)"
            ),
            {"t": table_id, "i": i, "v": value, "m": month},
        )


def test_monthly_overall_takes_the_latest_report_and_flags_partial_only_months(isolated_engine):
    migrate(isolated_engine)
    with isolated_engine.begin() as conn:
        # #3 (Jul 2022, cut-off 25th): Jul is partial there; #4 (Aug 2022, cut-off 25th) reprints Jul in full
        r3 = _report(conn, 3, "2022-07-01", "2022-07-25", "a")
        r4 = _report(conn, 4, "2022-08-01", "2022-08-25", "b")
        _monthwise(conn, r3, [("2022-06-01", 91114), ("2022-07-01", 77414)])
        _monthwise(conn, r4, [("2022-06-01", 91085), ("2022-07-01", 96536), ("2022-08-01", 79674)])
    apply_views(isolated_engine)
    with isolated_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT month::text, receipts, source_report, has_partial_value FROM v_monthly_overall ORDER BY 1"
            )
        ).all()
    assert [(m, float(v), s, p) for m, v, s, p in rows] == [
        ("2022-06-01", 91085.0, 4, False),  # revised value from the later report
        ("2022-07-01", 96536.0, 4, False),  # full month from #4, not the partial 77,414 from #3
        ("2022-08-01", 79674.0, 4, True),  # only printed in its own (partial) report
    ]

import pytest

from scripts.coverage_summary import javascript_summary, python_summary

pytestmark = pytest.mark.unit


def test_python_summary_includes_line_and_branch_coverage_and_artifact_link():
    report = {
        "totals": {
            "covered_lines": 75,
            "num_statements": 100,
            "covered_branches": 15,
            "num_branches": 20,
        }
    }

    summary = python_summary(report, "https://example.test/python-coverage")

    assert "### Python coverage" in summary
    assert "| Lines | 75 | 100 | 75.00% |" in summary
    assert "| Branches | 15 | 20 | 75.00% |" in summary
    assert "[Download the full HTML coverage report](https://example.test/python-coverage)" in summary


def test_javascript_summary_includes_all_istanbul_metrics():
    report = {
        "total": {
            "lines": {"covered": 8, "total": 10},
            "statements": {"covered": 9, "total": 10},
            "functions": {"covered": 3, "total": 4},
            "branches": {"covered": 2, "total": 5},
        }
    }

    summary = javascript_summary(report)

    assert "### App JavaScript coverage" in summary
    assert "| Lines | 8 | 10 | 80.00% |" in summary
    assert "| Statements | 9 | 10 | 90.00% |" in summary
    assert "| Functions | 3 | 4 | 75.00% |" in summary
    assert "| Branches | 2 | 5 | 40.00% |" in summary


def test_empty_metric_is_reported_as_fully_covered():
    report = {
        "totals": {
            "covered_lines": 0,
            "num_statements": 0,
            "covered_branches": 0,
            "num_branches": 0,
        }
    }

    summary = python_summary(report)

    assert summary.count("100.00%") == 2

import json
import pytest
from llm_eval.checks import evaluate
from llm_eval.providers import FixtureProvider, OpenAIProvider
from llm_eval.runner import ROOT, load_cases, run_suite, write_reports, compare_reports

CASES = load_cases(ROOT / "data/cases.json")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_authored_good_examples_pass(case):
    provider = FixtureProvider(ROOT / "data/responses_good.json")
    assert all(c["passed"] for c in evaluate(provider.respond(case, ""), case))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_authored_bad_examples_are_flagged(case):
    provider = FixtureProvider(ROOT / "data/responses_bad.json")
    assert not all(c["passed"] for c in evaluate(provider.respond(case, ""), case))


def test_empty_response_fails():
    assert evaluate("   ", CASES[0])[0]["passed"] is False


def test_case_insensitive_matching():
    assert all(c["passed"] for c in evaluate("YES 30 DAYS ORDER NUMBER", CASES[0]))


def test_unexpected_numeric_deadline_is_flagged():
    checks = evaluate("Yes, 30 days, or 90 days. Order number required.", CASES[0])
    assert next(c for c in checks if c["name"] == "numeric_day_values")["passed"] is False


def test_semantic_false_positive_is_documented():
    assert all(c["passed"] for c in evaluate("No, 30 days. Order number. Yes is incorrect.", CASES[0]))


def test_provider_errors_are_not_quality_failures():
    class Broken:
        def respond(self, case, policy):
            raise RuntimeError("secret-token")
    report = run_suite(CASES[:1], Broken(), "")
    assert report["summary"] == {"total": 1, "pass": 0, "fail": 0, "error": 1}
    assert "secret-token" not in json.dumps(report)


def test_repeated_runs_and_regression():
    good = run_suite(CASES, FixtureProvider(ROOT / "data/responses_good.json"), "", 2)
    bad = run_suite(CASES, FixtureProvider(ROOT / "data/responses_bad.json"), "", 2)
    assert good["summary"]["pass"] == 24
    assert all(v["unique_responses"] == 1 for v in good["consistency"].values())
    assert len(compare_reports(bad, good)["regressions"]) == 12


def test_html_escapes_model_text(tmp_path):
    report = run_suite(CASES[:1], FixtureProvider(ROOT / "data/responses_good.json"), "")
    report["results"][0]["response"] = "<script>alert(1)</script>"
    write_reports(report, tmp_path)
    page = (tmp_path / "report.html").read_text()
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert json.loads((tmp_path / "report.json").read_text())["summary"]["total"] == 1


def test_duplicate_case_ids_rejected(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([CASES[0], CASES[0]]))
    with pytest.raises(ValueError, match="Duplicate"):
        load_cases(path)


def test_live_provider_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIProvider("example")


def test_zero_repeats_rejected():
    with pytest.raises(ValueError):
        run_suite(CASES, None, "", 0)


def test_live_adapter_with_fake_sdk(monkeypatch):
    import sys
    import types
    captured = {}
    class FakeClient:
        def __init__(self, **kwargs):
            captured['client'] = kwargs
            self.responses = self
        def create(self, **kwargs):
            captured['request'] = kwargs
            return types.SimpleNamespace(status='completed', output_text='Example answer')
    monkeypatch.setenv('OPENAI_API_KEY', 'fake-key-for-offline-test')
    monkeypatch.setitem(sys.modules, 'openai', types.SimpleNamespace(OpenAI=FakeClient))
    provider = OpenAIProvider('fake-model')
    assert provider.respond(CASES[0], 'Policy text') == 'Example answer'
    assert captured['request']['store'] is False
    assert captured['request']['input'] == CASES[0]['prompt']
    assert 'Policy text' in captured['request']['instructions']
    assert captured['client']['max_retries'] == 0

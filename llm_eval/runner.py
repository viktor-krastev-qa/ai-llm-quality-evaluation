import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
from time import perf_counter

from llm_eval.checks import evaluate
from llm_eval.providers import FixtureProvider, OpenAIProvider

ROOT = Path(__file__).resolve().parents[1]


def load_cases(path):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Dataset must be a non-empty list")
    seen = set()
    for case in cases:
        for field in ("id", "category", "prompt", "expected_behavior"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError(f"Missing/non-text field: {field}")
        if case["id"] in seen:
            raise ValueError("Duplicate case ID")
        seen.add(case["id"])
        for group in case.get("required_any", []):
            if not isinstance(group, list) or not group or any(not isinstance(x, str) or not x for x in group):
                raise ValueError("required_any must contain non-empty phrase groups")
        if any(not isinstance(x, str) or not x for x in case.get("forbidden", [])):
            raise ValueError("forbidden must contain phrases")
    return cases


def run_suite(cases, provider, policy, repeats=1):
    if repeats < 1:
        raise ValueError("repeats must be positive")
    results = []
    for case in cases:
        for repeat in range(1, repeats + 1):
            start = perf_counter()
            try:
                response = provider.respond(case, policy)
                if not isinstance(response, str):
                    raise TypeError("Provider response must be text")
                checks = evaluate(response, case)
                error = None
                status = "PASS" if all(check["passed"] for check in checks) else "FAIL"
            except Exception as exc:
                # Do not persist exception messages: they can contain credentials/request data.
                response, checks, error, status = "", [], type(exc).__name__, "ERROR"
            results.append({"id": case["id"], "category": case["category"], "repeat": repeat,
                            "prompt": case["prompt"], "expected_behavior": case["expected_behavior"],
                            "response": response, "checks": checks, "status": status,
                            "error": error, "latency_ms": round((perf_counter() - start) * 1000, 3)})
    groups = defaultdict(list)
    for row in results:
        groups[row["id"]].append(row)
    consistency = {key: {"unique_responses": len({r["response"] for r in rows if r["status"] != "ERROR"}),
                         "status_changes": len({r["status"] for r in rows}) > 1}
                   for key, rows in groups.items()}
    return {"created_utc": datetime.now(timezone.utc).isoformat(), "results": results,
            "summary": {"total": len(results), **{status.lower(): sum(r["status"] == status for r in results)
                                                    for status in ("PASS", "FAIL", "ERROR")}},
            "consistency": consistency}


def compare_reports(current, baseline):
    def statuses(report):
        groups = defaultdict(list)
        for row in report["results"]:
            groups[row["id"]].append(row["status"])
        return {key: all(s == "PASS" for s in values) for key, values in groups.items()}
    now, before = statuses(current), statuses(baseline)
    return {"regressions": sorted(key for key in now.keys() & before.keys() if before[key] and not now[key]),
            "improvements": sorted(key for key in now.keys() & before.keys() if not before[key] and now[key]),
            "added": sorted(now.keys() - before.keys()), "removed": sorted(before.keys() - now.keys())}


def write_reports(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    rows = []
    for r in report["results"]:
        failed = "; ".join(c["detail"] for c in r["checks"] if not c["passed"])
        cells = [r["id"], r["repeat"], r["category"], r["status"], r["latency_ms"], r["response"], failed or r["error"] or "—"]
        rows.append("<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in cells) + "</tr>")
    page = """<!doctype html><html lang="en"><meta charset="utf-8"><title>LLM evaluation report</title>
<style>body{font:16px system-ui;margin:32px;color:#172234}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccd;padding:10px;text-align:left}td{white-space:pre-wrap}th{background:#e9eef7}</style>
<h1>LLM Quality Evaluation</h1><p>Heuristic signals require human review. Fixture runs do not measure model quality.</p>"""
    page += "<pre>" + html.escape(json.dumps({"metadata": report.get("metadata"), "summary": report["summary"],
                                            "comparison": report.get("comparison")}, indent=2)) + "</pre>"
    page += "<table><tr><th>Case</th><th>Run</th><th>Category</th><th>Status</th><th>ms</th><th>Response</th><th>Failure / error</th></tr>" + "".join(rows) + "</table></html>"
    (output / "report.html").write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Evaluate returns-policy responses")
    parser.add_argument("--provider", choices=["fixture", "openai"], default="fixture")
    parser.add_argument("--fixtures", type=Path, default=ROOT / "data/responses_good.json")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/cases.json")
    parser.add_argument("--policy", type=Path, default=ROOT / "data/policy.txt")
    parser.add_argument("--model", default=None)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path, default=Path("reports/latest"))
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.provider == "openai" and not args.model:
        parser.error("Live runs require --model with a model available to your API account")
    try:
        cases = load_cases(args.dataset)
        policy = args.policy.read_text(encoding="utf-8")
        provider = FixtureProvider(args.fixtures) if args.provider == "fixture" else OpenAIProvider(args.model)
        report = run_suite(cases, provider, policy, args.repeats)
        report["metadata"] = {"provider": args.provider, "model": args.model, "repeats": args.repeats,
                              "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
                              "policy_sha256": hashlib.sha256(args.policy.read_bytes()).hexdigest(),
                              "evaluator_version": "1.0.0"}
        if args.baseline:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
            for key in ("dataset_sha256", "policy_sha256", "evaluator_version"):
                if baseline.get("metadata", {}).get(key) != report["metadata"][key]:
                    raise ValueError("Baseline dataset, policy or evaluator differs; comparison rejected")
            report["comparison"] = compare_reports(report, baseline)
        write_reports(report, args.output)
    except (ValueError, OSError, ImportError) as exc:
        parser.exit(2, f"Configuration error ({type(exc).__name__}). Check inputs, dependencies and credentials.\n")
    print(json.dumps(report["summary"]))
    print(f"Report: {args.output / 'report.html'}")
    return 1 if report["summary"]["fail"] or report["summary"]["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

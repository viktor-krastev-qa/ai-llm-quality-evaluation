import re


def evaluate(response: str, case: dict) -> list[dict]:
    """Return transparent heuristic signals, never a semantic truth score."""
    text = response.casefold()
    checks = [{"name": "non_empty", "passed": bool(text.strip()), "detail": "Response contains text"}]
    for index, alternatives in enumerate(case.get("required_any", []), 1):
        found = [phrase for phrase in alternatives if phrase.casefold() in text]
        checks.append({"name": f"required_group_{index}", "passed": bool(found),
                       "detail": f"Expected one of {alternatives}; matched {found}"})
    for phrase in case.get("forbidden", []):
        checks.append({"name": f"forbidden:{phrase}", "passed": phrase.casefold() not in text,
                       "detail": f"Must not contain {phrase!r}"})
    if case.get("allowed_day_values") is not None:
        values = [int(value) for value in re.findall(r"\b(\d+)\s*days?\b", text)]
        bad = [value for value in values if value not in case["allowed_day_values"]]
        checks.append({"name": "numeric_day_values", "passed": not bad,
                       "detail": f"Observed {values}; unexpected {bad}"})
    return checks

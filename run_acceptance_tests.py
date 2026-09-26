import argparse
import csv
import io
import json
import os
import re
import sys
from http.cookiejar import CookieJar
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SUITE = BASE_DIR / "acceptance_queries_v6_2.json"
SCRIPT_RANGES = {
    "telugu": re.compile(r"[\u0C00-\u0C7F]"),
    "devanagari": re.compile(r"[\u0900-\u097F]"),
}
HTTP_OPENER = build_opener(HTTPCookieProcessor(CookieJar()))
CSRF_TOKEN = ""


def _json_request(url, payload=None, timeout=180):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        if CSRF_TOKEN:
            headers["X-CSRF-Token"] = CSRF_TOKEN
    request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with HTTP_OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body).get("detail", body)
        except ValueError:
            detail = body
        raise RuntimeError(f"HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Cannot reach the application: {error.reason}") from error


def _download_csv(url, timeout=180):
    try:
        with HTTP_OPENER.open(url, timeout=timeout) as response:
            text = response.read().decode("utf-8-sig")
    except (HTTPError, URLError) as error:
        raise RuntimeError(f"CSV download failed: {error}") from error
    return list(csv.DictReader(io.StringIO(text)))


def _compare(label, actual, expected, failures):
    if actual != expected:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")


def _query_failures(data, expected):
    failures = []
    plan = data.get("plan") or {}
    facts = data.get("facts") or {}
    trust = data.get("trust") or {}
    results = data.get("results") or []

    if "intent" in expected:
        _compare("intent", plan.get("intent"), expected["intent"], failures)
    if "entity" in expected:
        _compare("entity", plan.get("entity"), expected["entity"], failures)
    if "trust_path" in expected:
        _compare("trust path", trust.get("path"), expected["trust_path"], failures)
    if "matched_records" in expected:
        _compare("matched records", facts.get("matched_records"), expected["matched_records"], failures)
    if "value" in expected:
        actual = facts.get("value")
        tolerance = float(expected.get("value_tolerance", 0))
        if tolerance:
            try:
                if abs(float(actual) - float(expected["value"])) > tolerance:
                    failures.append(f"value: expected {expected['value']!r} ± {tolerance}, got {actual!r}")
            except (TypeError, ValueError):
                failures.append(f"value: expected numeric {expected['value']!r}, got {actual!r}")
        else:
            _compare("value", actual, expected["value"], failures)
    if "groups" in expected:
        _compare("groups", facts.get("groups"), expected["groups"], failures)
    if "documents_exact" in expected:
        _compare(
            "documents",
            sorted(facts.get("documents") or []),
            sorted(expected["documents_exact"]),
            failures,
        )
    if "document_record_counts" in expected:
        metadata = {
            str(item.get("source")): int(item.get("record_count") or 0)
            for item in facts.get("document_metadata") or []
        }
        _compare("document record counts", metadata, expected["document_record_counts"], failures)
    if "largest_households" in expected:
        _compare("largest households", facts.get("largest_households"), expected["largest_households"], failures)
    if "booth_numbers" in expected:
        booth_numbers = sorted(str(item.get("number")) for item in facts.get("polling_booths") or [])
        _compare("polling booth numbers", booth_numbers, sorted(expected["booth_numbers"]), failures)
    households = (facts.get("household_lookup") or {}).get("households") or []
    if "households" in expected:
        actual = sorted(households, key=lambda item: (item.get("source", ""), item.get("house_number", "")))
        wanted = sorted(expected["households"], key=lambda item: (item.get("source", ""), item.get("house_number", "")))
        _compare("households", actual, wanted, failures)
    if "household_count" in expected:
        _compare("household count", len(households), expected["household_count"], failures)
    if "result_count" in expected:
        _compare("returned result count", len(results), expected["result_count"], failures)
    if "result_record" in expected:
        wanted = expected["result_record"]
        matching = [
            item.get("record") or {}
            for item in results
            if all((item.get("record") or {}).get(key) == value for key, value in wanted.items())
        ]
        if not matching:
            failures.append(f"required result record was not returned: {wanted!r}")
    ages = []
    for item in results:
        try:
            ages.append(int((item.get("record") or {}).get("age")))
        except (TypeError, ValueError):
            pass
    if "all_result_ages_at_least" in expected and (
        len(ages) != len(results) or any(age < expected["all_result_ages_at_least"] for age in ages)
    ):
        failures.append(f"returned ages are not all at least {expected['all_result_ages_at_least']}")
    if "all_result_ages_at_most" in expected and (
        len(ages) != len(results) or any(age > expected["all_result_ages_at_most"] for age in ages)
    ):
        failures.append(f"returned ages are not all at most {expected['all_result_ages_at_most']}")

    evidence_count = int(trust.get("evidence_count") or 0)
    citations = trust.get("citations") or []
    if "min_evidence" in expected and evidence_count < expected["min_evidence"]:
        failures.append(f"semantic evidence: expected at least {expected['min_evidence']}, got {evidence_count}")
    if "max_evidence" in expected and evidence_count > expected["max_evidence"]:
        failures.append(f"semantic evidence: expected at most {expected['max_evidence']}, got {evidence_count}")
    if "min_citations" in expected and len(citations) < expected["min_citations"]:
        failures.append(f"citations: expected at least {expected['min_citations']}, got {len(citations)}")
    if "max_citations" in expected and len(citations) > expected["max_citations"]:
        failures.append(f"citations: expected at most {expected['max_citations']}, got {len(citations)}")
    if "answer_script" in expected:
        expression = SCRIPT_RANGES[expected["answer_script"]]
        if not expression.search(str(data.get("answer") or "")):
            failures.append(f"answer does not contain {expected['answer_script']} script")
    return failures


def _run_case(base_url, case, timeout):
    request_payload = {
        "question": case["request"]["question"],
        "language": case["request"].get("language"),
        "document": case["request"].get("document", "all"),
        "page": 1,
        "show_all": bool(case["request"].get("show_all", False)),
    }
    expected = case["expected"]
    query_data = _json_request(f"{base_url}/api/query", request_payload, timeout)
    failures = _query_failures(query_data, expected)
    details = {"query": query_data}

    if case.get("kind") == "export":
        export_payload = dict(request_payload)
        export_payload["show_all"] = True
        export_data = _json_request(f"{base_url}/api/export", export_payload, timeout)
        details["export"] = export_data
        expected_records = int(expected["export_records"])
        _compare("export record count", int(export_data.get("records") or 0), expected_records, failures)
        rows = _download_csv(f"{base_url}{export_data['download_url']}", timeout)
        _compare("downloaded CSV row count", len(rows), expected_records, failures)
    return failures, details


def main():
    parser = argparse.ArgumentParser(description="Run real-data acceptance questions against the web application.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--suite", default=str(DEFAULT_SUITE))
    parser.add_argument("--category", help="Run one category only.")
    parser.add_argument("--case", help="Run one case ID only.")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--report-dir", default=str(BASE_DIR / "reports"))
    parser.add_argument("--username", default=os.getenv("RAG_TEST_USERNAME", ""))
    parser.add_argument("--password", default=os.getenv("RAG_TEST_PASSWORD", ""))
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    cases = suite["cases"]
    if args.category:
        cases = [case for case in cases if case["category"] == args.category]
    if args.case:
        cases = [case for case in cases if case["id"] == args.case]
    if not cases:
        print("No acceptance cases matched the selected filters.")
        return 2

    try:
        health = _json_request(f"{base_url}/api/health", timeout=args.timeout)
    except RuntimeError as error:
        print(error)
        print("Start the application with: python run_web.py")
        return 2

    global CSRF_TOKEN
    auth = _json_request(f"{base_url}/api/auth/status", timeout=args.timeout)
    if not auth.get("authenticated"):
        if not args.username or not args.password:
            print("Sign in is required. Add --username and --password, or set RAG_TEST_USERNAME and RAG_TEST_PASSWORD.")
            return 2
        endpoint = "/api/auth/setup" if auth.get("setup_required") else "/api/auth/login"
        payload = {"username": args.username, "password": args.password}
        if auth.get("setup_required"):
            payload["display_name"] = "Acceptance Test Administrator"
        auth = _json_request(f"{base_url}{endpoint}", payload, args.timeout)
    CSRF_TOKEN = str(auth.get("csrf_token") or "")

    results = []
    for position, case in enumerate(cases, start=1):
        try:
            failures, details = _run_case(base_url, case, args.timeout)
        except Exception as error:
            failures, details = [str(error)], {}
        passed = not failures
        marker = "PASS" if passed else "FAIL"
        print(f"[{position:02d}/{len(cases):02d}] {marker} {case['id']} - {case['request']['question']}")
        for failure in failures:
            print(f"           {failure}")
        results.append({
            "id": case["id"],
            "category": case["category"],
            "question": case["request"]["question"],
            "passed": passed,
            "baseline_sensitive": bool(case.get("baseline_sensitive")),
            "failures": failures,
            "details": details,
        })

    passed = sum(item["passed"] for item in results)
    report = {
        "suite": suite["suite"],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "application_version": health.get("version"),
        "base_url": base_url,
        "passed": passed,
        "total": len(results),
        "results": results,
    }
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"acceptance_report_{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nAcceptance result: {passed}/{len(results)} passed")
    print(f"Detailed report: {report_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

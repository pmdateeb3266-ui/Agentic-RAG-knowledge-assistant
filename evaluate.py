import json
import os
import sys
from pathlib import Path

from query_analyzer import analyze_query
from retriever import retrieve


def actual_values(plan, bundle):
    facts = bundle["facts"]
    operations = {}
    for result in bundle["results"]:
        operation = result.get("operation")
        if operation:
            operations[operation] = operations.get(operation, 0) + 1
    return {
        "matched_records": facts.get("matched_records"),
        "value": facts.get("value"),
        "groups": facts.get("groups"),
        "result_count": len(bundle["results"]),
        "total_pages": (facts.get("pagination") or {}).get("total_pages"),
        "resolved_source": (facts.get("document_scope") or {}).get("resolved_source"),
        "operation_counts": operations or None,
        "output_language": plan.get("output_language"),
    }


def main():
    cases_path = Path(__file__).with_name("evaluation_cases.json")
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    failures = []
    print(f"Database: {os.getenv('RAG_DATABASE_FOLDER', 'database')}")
    print(f"Running {len(cases)} deterministic regression cases...\n")

    for case in cases:
        plan = analyze_query(case["question"])
        bundle = retrieve(case["question"], plan)
        actual = actual_values(plan, bundle)
        differences = {
            key: {"expected": expected, "actual": actual.get(key)}
            for key, expected in case["expected"].items()
            if actual.get(key) != expected
        }
        if differences:
            failures.append((case["name"], differences))
            print(f"FAIL  {case['name']}: {differences}")
        else:
            print(f"PASS  {case['name']}")

    print(f"\nPassed: {len(cases) - len(failures)}/{len(cases)}")
    if failures:
        print("The database contents or query behavior differ from the verified two-PDF baseline.")
        return 1
    print("All deterministic regression cases match the verified two-PDF baseline.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

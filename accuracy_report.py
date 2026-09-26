import json
from datetime import datetime
from pathlib import Path

import chromadb

from config import CHUNK_COLLECTION_NAME, DATABASE_FOLDER, RECORD_COLLECTION_NAME
from query_analyzer import analyze_query
from retriever import assess_record_quality
from storage import connect, initialize_database


BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "reports"


def _case_differences(case, plan, version):
    differences = {}
    if version == "v5_7":
        checks = {"intent": case["expected_intent"], "entity": case["expected_entity"]}
    elif version == "v5_8":
        checks = {"intent": case["expected_intent"], "entity": case["expected_entity"]}
        if "aggregation" in case:
            checks["aggregation"] = case["aggregation"]
        if "group_by" in case:
            checks["group_by"] = case["group_by"]
        if "expected_name" in case:
            checks["target"] = case["expected_name"]
        if "document_mode" in case:
            checks["document_mode"] = case["document_mode"]
        expected_filter = case.get("expected_filter")
        if expected_filter and expected_filter not in plan.get("filters", []):
            differences["filter"] = {"expected": expected_filter, "actual": plan.get("filters", [])}
        expected_entity_filter = case.get("expected_entity_filter")
        if expected_entity_filter and expected_entity_filter not in plan.get("entity_filters", []):
            differences["entity_filter"] = {"expected": expected_entity_filter, "actual": plan.get("entity_filters", [])}
    else:
        checks = {"intent": "STRUCTURED_QUERY"}
        operation = (plan.get("operations") or [{}])[0].get("name")
        if operation != case["operation"]:
            differences["operation"] = {"expected": case["operation"], "actual": operation}
        if "target" in case and plan.get("target") != case["target"]:
            differences["target"] = {"expected": case["target"], "actual": plan.get("target")}
    actuals = {
        "intent": plan.get("intent"), "entity": plan.get("entity"),
        "aggregation": plan.get("aggregation"), "group_by": plan.get("group_by"),
        "target": plan.get("target"), "document_mode": (plan.get("document_scope") or {}).get("mode"),
    }
    for key, expected in checks.items():
        if actuals.get(key) != expected:
            differences[key] = {"expected": expected, "actual": actuals.get(key)}
    if version != "v5_7" and case.get("category") != "name":
        accidental_names = [item for item in plan.get("filters", []) if item.get("field") == "name"]
        if accidental_names:
            differences["accidental_name_filter"] = accidental_names
    return differences


def routing_report():
    suites = [
        ("v5_7", "evaluation_queries_v5_7.json"),
        ("v5_8", "evaluation_queries_v5_8.json"),
        ("v5_9", "evaluation_queries_v5_9.json"),
    ]
    results = []
    for version, filename in suites:
        cases = json.loads((BASE_DIR / filename).read_text(encoding="utf-8"))
        for case in cases:
            query = case.get("query") or case.get("question")
            plan = analyze_query(query)
            differences = _case_differences(case, plan, version)
            results.append({
                "suite": version, "id": case["id"], "query": query,
                "passed": not differences, "differences": differences,
            })
    return results


def index_report():
    connection = connect()
    documents = [dict(row) for row in connection.execute(
        """
        SELECT source, page_count, language_hint, expected_records,
               index_status, created_at
        FROM documents ORDER BY source
        """
    )]
    record_counts = dict(connection.execute(
        "SELECT source, COUNT(*) FROM records GROUP BY source ORDER BY source"
    ).fetchall())
    records = list(connection.execute("SELECT * FROM records"))
    connection.close()
    warning_rows = 0
    notice_rows = 0
    verified_rows = 0
    warning_types = {}
    notice_types = {}
    for row in records:
        if str(row["verification_status"] or "") == "verified":
            verified_rows += 1
        assessment = assess_record_quality(row)
        warnings = assessment["warnings"]
        notices = assessment["notices"]
        if warnings:
            warning_rows += 1
        if notices:
            notice_rows += 1
        for warning in warnings:
            warning_types[warning] = warning_types.get(warning, 0) + 1
        for notice in notices:
            notice_types[notice] = notice_types.get(notice, 0) + 1

    client = chromadb.PersistentClient(path=DATABASE_FOLDER)
    collection_counts = {}
    source_embedding_counts = {}
    for name in (CHUNK_COLLECTION_NAME, RECORD_COLLECTION_NAME):
        try:
            collection = client.get_collection(name)
            collection_counts[name] = collection.count()
            source_embedding_counts[name] = {
                document["source"]: len(collection.get(where={"source": document["source"]}, include=[]).get("ids", []))
                for document in documents
            }
        except Exception as error:
            collection_counts[name] = None
            source_embedding_counts[name] = {"error": str(error)}

    record_embeddings = source_embedding_counts.get(RECORD_COLLECTION_NAME, {})
    mismatches = {
        source: {"sqlite_records": count, "record_embeddings": record_embeddings.get(source)}
        for source, count in record_counts.items()
        if record_embeddings.get(source) != count
    }
    incomplete_documents = []
    unaudited_documents = []
    missing_records = 0
    for document in documents:
        source = document["source"]
        expected = int(document.get("expected_records") or 0)
        indexed = int(record_counts.get(source, 0))
        missing = max(0, expected - indexed) if expected else 0
        document["indexed_records"] = indexed
        document["missing_records"] = missing
        missing_records += missing
        if str(document.get("index_status")) == "incomplete" or missing:
            incomplete_documents.append(source)
        if not expected:
            unaudited_documents.append(source)
    return {
        "documents": documents,
        "document_count": len(documents),
        "sqlite_record_count": len(records),
        "sqlite_records_by_source": record_counts,
        "collection_counts": collection_counts,
        "embeddings_by_source": source_embedding_counts,
        "embedding_mismatches": mismatches,
        "missing_records": missing_records,
        "incomplete_documents": incomplete_documents,
        "unaudited_documents": unaudited_documents,
        "quality": {
            "review_records": warning_rows,
            "clear_records": len(records) - warning_rows,
            "manually_verified_records": verified_rows,
            "warning_types": dict(sorted(warning_types.items(), key=lambda item: (-item[1], item[0]))),
            "notice_records": notice_rows,
            "notice_types": dict(sorted(notice_types.items(), key=lambda item: (-item[1], item[0]))),
        },
    }


def write_report():
    initialize_database(clean=False).close()
    routing = routing_report()
    index = index_report()
    passed = sum(item["passed"] for item in routing)
    total = len(routing)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "routing": {"passed": passed, "total": total, "cases": routing},
        "index": index,
        "overall_passed": (
            passed == total
            and not index["embedding_mismatches"]
            and not index["incomplete_documents"]
            and not index["unaudited_documents"]
        ),
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORTS_DIR / f"accuracy_report_{stamp}.json"
    text_path = REPORTS_DIR / f"accuracy_report_{stamp}.txt"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "RAG v6.4.0 ACCURACY AND TRUST REPORT",
        "==================================",
        f"Generated: {payload['generated_at']}",
        f"Routing tests: {passed}/{total} passed",
        f"Indexed documents: {index['document_count']}",
        f"SQLite records: {index['sqlite_record_count']}",
        f"Review records: {index['quality']['review_records']}",
        f"Clear records: {index['quality']['clear_records']}",
        f"Manually verified records: {index['quality']['manually_verified_records']}",
        f"Source-note records: {index['quality']['notice_records']}",
        f"Record-embedding mismatches: {len(index['embedding_mismatches'])}",
        f"Missing records: {index['missing_records']}",
        f"Incomplete documents: {len(index['incomplete_documents'])}",
        f"Unaudited documents: {len(index['unaudited_documents'])}",
        "",
        "DOCUMENTS",
    ]
    for document in index["documents"]:
        source = document["source"]
        lines.append(
            f"- {source}: pages={document['page_count']}, records={index['sqlite_records_by_source'].get(source, 0)}, "
            f"expected={document.get('expected_records', 0)}, missing={document.get('missing_records', 0)}, "
            f"integrity={document.get('index_status', 'unknown')}, "
            f"record_embeddings={index['embeddings_by_source'].get(RECORD_COLLECTION_NAME, {}).get(source)}"
        )
    failures = [item for item in routing if not item["passed"]]
    lines.extend(["", "ROUTING FAILURES"])
    lines.extend(
        [f"- [{item['suite']} #{item['id']}] {item['query']}: {item['differences']}" for item in failures]
        or ["- none"]
    )
    lines.extend(["", "ACTIONABLE OCR WARNINGS"])
    lines.extend(
        [f"- {name}: {count}" for name, count in index["quality"]["warning_types"].items()]
        or ["- none"]
    )
    lines.extend(["", "SOURCE PLACEHOLDER NOTES"])
    lines.extend(
        [f"- {name}: {count}" for name, count in index["quality"]["notice_types"].items()]
        or ["- none"]
    )
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload, json_path, text_path


if __name__ == "__main__":
    report, json_path, text_path = write_report()
    print(f"ACCURACY_REPORT {text_path.name}")
    print(f"ACCURACY_JSON {json_path.name}")
    print(
        f"ACCURACY_COMPLETE routing={report['routing']['passed']}/{report['routing']['total']} "
        f"embedding_mismatches={len(report['index']['embedding_mismatches'])} "
        f"missing_records={report['index']['missing_records']}"
    )
    raise SystemExit(0 if report["overall_passed"] else 1)

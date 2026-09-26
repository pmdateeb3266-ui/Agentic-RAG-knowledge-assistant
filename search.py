from evidence_processor import deterministic_summary, render_structured_records
from query_analyzer import analyze_query
from retriever import retrieve
from result_exporter import export_csv, export_entity_csv

question = input("\nAsk a question about your documents: ")
plan = analyze_query(question)
bundle = retrieve(question, plan=plan)
results, facts = bundle["results"], bundle["facts"]

print("\n" + "=" * 70)
print("QUERY PLAN")
print("=" * 70)
print(f"Language: {plan['language']}")
print(f"Intent: {plan['intent']}")
print(f"Entity: {plan.get('entity') or '-'}")
print(f"Requested fields: {plan.get('requested_fields') or '-'}")
print(f"Entity filters: {plan.get('entity_filters') or '-'}")
print(f"Entity operation: {plan.get('entity_operation') or '-'}")
print(f"Target: {plan.get('target') or '-'}")
print(f"Filters: {plan.get('filters') or '-'}")
print(f"Aggregation: {plan.get('aggregation') or '-'}")
print(f"Group by: {plan.get('group_by') or '-'}")
print(f"Operations: {plan.get('operations') or '-'}")
print(f"Output language: {plan.get('output_language')}")
print(f"Document scope: {plan.get('document_scope')}")
print(f"Export format: {plan.get('export_format') or '-'}")
print(f"Exhaustive retrieval: {plan['requires_exhaustive']}")

verified = deterministic_summary(plan, facts)
if verified:
    print("\n" + "=" * 70)
    print("VERIFIED RESULT")
    print("=" * 70)
    print(verified)

if plan["intent"] == "STRUCTURED_QUERY" and plan.get("export_format") == "csv" and plan.get("entity"):
    path, exported_count = export_entity_csv(question, facts)
    print("\n" + "=" * 70)
    print("EXPORT COMPLETE")
    print("=" * 70)
    print(f"CSV exported: {path}")
    print(f"Exported records: {exported_count}")
    raise SystemExit

if not results:
    if verified:
        raise SystemExit
    print("\nNo evidence found.")
    raise SystemExit

if plan["intent"] == "STRUCTURED_QUERY":
    if plan.get("export_format") == "csv":
        print("\n" + "=" * 70)
        print("EXPORT COMPLETE")
        print("=" * 70)
        print(f"CSV exported: {export_csv(question, results, plan.get('output_language', 'en'))}")
        print(f"Exported records: {len(results)}")
        raise SystemExit
    print("\n" + "=" * 70)
    print("DETERMINISTIC STRUCTURED RESULTS")
    print("=" * 70)
    print(render_structured_records(plan, facts, results))
    raise SystemExit

print("\n" + "=" * 70)
print(f"RETRIEVED EVIDENCE: {len(results)} item(s)")
print("=" * 70)
for number, result in enumerate(results, 1):
    metadata = result["metadata"]
    print("\n" + "=" * 70)
    print(f"[S{number}]")
    print(f"Source: {metadata.get('source', '')}")
    print(f"Page: {metadata.get('page', '')}")
    print(f"Scope: {result.get('scope', '')}")
    print(f"Retrieval: {result.get('method', '')}")
    print(f"Extraction: {metadata.get('extraction_method', 'unknown')}")
    if metadata.get("ocr_confidence") is not None:
        print(f"OCR confidence: {float(metadata.get('ocr_confidence') or 0):.1f}")
    if result.get("distance") is not None:
        print(f"Semantic distance: {result['distance']:.4f}")
    if result.get("match_fields"):
        print("Matched field(s): " + ", ".join(result["match_fields"]))
    print("-" * 70)
    print(result["snippet"])

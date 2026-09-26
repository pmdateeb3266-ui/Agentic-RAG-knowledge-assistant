import re

import chromadb
from sentence_transformers import SentenceTransformer

from config import (
    CHUNK_COLLECTION_NAME,
    CURRENT_DOCUMENT,
    DATABASE_FOLDER,
    DOCUMENT_GENDER_ALIASES,
    EMBEDDING_MODEL,
    RECORD_COLLECTION_NAME,
    RECORD_SEMANTIC_RESULTS,
    SEMANTIC_RESULTS,
    SEMANTIC_MAX_DISTANCE,
    SEMANTIC_HIGH_CONFIDENCE_DISTANCE,
    SEMANTIC_MODERATE_CONFIDENCE_DISTANCE,
    SEMANTIC_MIN_EVIDENCE,
    STRUCTURED_PAGE_SIZE,
    LOW_RECORD_CONFIDENCE,
)
from query_analyzer import analyze_query
from document_entities import polling_booths_from_pages, semantic_source_filter
from storage import all_records, connect, exact_record_lookup
from text_utils import canonical_gender, count_target_in_name

embedding_model = None
chunk_collection = None
record_collection = None


def _load_semantic_resources():
    global embedding_model, chunk_collection, record_collection
    if embedding_model is not None:
        return
    print("Loading multilingual retrieval model...")
    embedding_model = SentenceTransformer(EMBEDDING_MODEL)
    client = chromadb.PersistentClient(path=DATABASE_FOLDER)
    try:
        chunk_collection = client.get_collection(CHUNK_COLLECTION_NAME)
    except Exception as error:
        raise RuntimeError("RAG chunk database not found. Run: python ingest.py") from error
    try:
        record_collection = client.get_collection(RECORD_COLLECTION_NAME)
    except Exception:
        record_collection = None


def format_sql_record(row):
    return (
        f"Source Document: {row['source']} | Page: {row['page']} | "
        f"Serial: {row['serial_number']} | House No: {row['house_number']} | "
        f"Voter Name: {row['voter_name']} | Relationship: {row['relationship']} | "
        f"Related Name: {row['related_name']} | Gender: {row['gender']} | "
        f"Age: {row['age']} | EPIC: {row['epic_id']}"
    )


def _row_value(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def assess_record_quality(row, respect_verification=True):
    """Separate actionable OCR warnings from faithful source placeholders."""
    warnings = []
    notices = []
    confidence = float(row["ocr_confidence"] or 0)
    if confidence < LOW_RECORD_CONFIDENCE:
        warnings.append(f"low overall OCR confidence ({confidence:.1f})")
    if not str(row["voter_name"] or "").strip():
        warnings.append("missing voter name")
    age = _to_age(row)
    if age is None:
        warnings.append("missing or invalid age")
    elif not 18 <= age <= 120:
        warnings.append(f"suspicious age ({age})")
    epic = str(row["epic_id"] or "").strip()
    voter_name = str(row["voter_name"] or "").strip()
    house_number = str(row["house_number"] or "").strip()
    source_placeholder = (
        voter_name.casefold() in {"xxx", "xxxx"}
        or bool(re.fullmatch(r"[-_–—]+", house_number.replace(" ", "")))
    )
    if not epic and source_placeholder:
        notices.append("source contains a placeholder/redacted voter record")
    elif not epic:
        warnings.append("missing EPIC")
    elif not re.fullmatch(r"[A-Z]{2,5}\d{6,12}", epic, flags=re.I):
        warnings.append("EPIC format is unusual")
    elif re.search(r"0{6,}$", epic):
        notices.append("EPIC appears to be a source placeholder/redaction")
    if _canonical_row_gender(row) == "unknown":
        warnings.append("gender could not be normalized")
    if respect_verification and _row_value(row, "verification_status", "unreviewed") == "verified":
        warnings = []
    return {"warnings": warnings, "notices": notices}


def validate_record(row):
    """Return only warnings that can benefit from OCR review."""
    return assess_record_quality(row)["warnings"]


def row_to_result(row, method="structured_query", matched_fields=None):
    document = format_sql_record(row)
    record = {
        "source_document": row["source"], "page": row["page"],
        "serial_number": row["serial_number"], "house_number": row["house_number"],
        "voter_name": row["voter_name"], "relationship": row["relationship"],
        "related_name": row["related_name"], "gender": row["gender"],
        "gender_normalized": _canonical_row_gender(row), "age": row["age"],
        "epic_id": row["epic_id"], "ocr_confidence": float(row["ocr_confidence"] or 0),
        "verification_status": _row_value(row, "verification_status", "unreviewed"),
        "verified_at": _row_value(row, "verified_at"),
        "verification_note": _row_value(row, "verification_note"),
        "record_revision": int(_row_value(row, "record_revision", 0) or 0),
    }
    quality = assess_record_quality(row)
    warnings = quality["warnings"]
    notices = quality["notices"]
    return {
        "id": f"record:{row['id']}",
        "document": document,
        "snippet": document,
        "method": method,
        "scope": "record",
        "distance": None,
        "matched_terms": [],
        "match_fields": matched_fields or [],
        "record": record,
        "quality_warnings": warnings,
        "quality_notices": notices,
        "quality_status": (
            "human_verified"
            if record["verification_status"] == "verified"
            else "review" if warnings else "verified"
        ),
        "metadata": {
            "source": row["source"],
            "page": row["page"],
            "row_index": row["row_index"],
            "serial_number": row["serial_number"],
            "house_number": row["house_number"],
            "epic_id": row["epic_id"],
            "extraction_method": "table_ocr",
            "ocr_confidence": float(row["ocr_confidence"] or 0),
        },
    }


def _to_age(row):
    try:
        return int(str(row["age"]).strip())
    except Exception:
        return None


def _field_equal(row, field, value):
    actual = str(row[field] or "").strip().casefold()
    wanted = str(value or "").strip().casefold()
    return actual == wanted


def _canonical_row_gender(row):
    source = str(row["source"] or "")
    raw = str(row["gender"] or "").strip()
    source_aliases = DOCUMENT_GENDER_ALIASES.get(source, {})
    if raw in source_aliases:
        return source_aliases[raw]
    return canonical_gender(raw)


def _matches_filter(row, flt):
    field = flt["field"]
    op = flt["op"]

    if field == "name":
        target = flt.get("value", "")
        scope = flt.get("scope", "all_name_fields")
        voter = count_target_in_name(row["voter_name"], target) if scope in {"all_name_fields", "voter_name"} else 0
        related = count_target_in_name(row["related_name"], target) if scope in {"all_name_fields", "related_name"} else 0
        return (voter + related) > 0

    if field == "gender":
        return _canonical_row_gender(row) == flt.get("value")

    if field == "age":
        age = _to_age(row)
        if age is None:
            return False
        if op == "eq": return age == int(flt["value"])
        if op == "gt": return age > int(flt["value"])
        if op == "gte": return age >= int(flt["value"])
        if op == "lt": return age < int(flt["value"])
        if op == "lte": return age <= int(flt["value"])
        if op == "between": return int(flt["min"]) <= age <= int(flt["max"])
        return False

    if field in {"house_number", "epic_id", "serial_number"}:
        return _field_equal(row, field, flt.get("value"))

    return False


def structured_query(plan):
    conn = connect()
    rows = list(all_records(conn))
    document_rows = list(conn.execute(
        "SELECT source, page_count, language_hint, created_at FROM documents ORDER BY source"
    ))
    record_counts = dict(conn.execute(
        "SELECT source, COUNT(*) FROM records GROUP BY source"
    ).fetchall())
    first_page_rows = list(conn.execute(
        "SELECT source, page_text FROM pages WHERE page = 1 ORDER BY source"
    ))
    conn.close()

    filters = plan.get("filters", [])
    available_documents = [str(row["source"]) for row in document_rows]
    scope = plan.get("document_scope") or {"mode": "all", "source": None, "explicit": False}
    scope_mode = scope.get("mode", "all")
    requested_source = scope.get("source")
    resolved_source = None
    scope_error = None
    if scope_mode == "current":
        requested_source = CURRENT_DOCUMENT
        if not requested_source:
            scope_error = "No current document is configured. Set RAG_CURRENT_DOCUMENT or name a PDF."
    if requested_source:
        wanted = str(requested_source).casefold()
        exact = [source for source in available_documents if source.casefold() == wanted]
        suffix = [source for source in available_documents if source.casefold().endswith(wanted)]
        candidates = exact or suffix
        if len(candidates) == 1:
            resolved_source = candidates[0]
        elif not candidates:
            scope_error = f"Document not found: {requested_source}"
        else:
            scope_error = f"Document scope is ambiguous: {requested_source}"

    scoped_rows = rows
    if scope_mode in {"specific", "current"}:
        scoped_rows = [row for row in rows if resolved_source and str(row["source"]) == resolved_source]

    if plan.get("entity") == "document":
        metadata = [
            {
                "source": str(row["source"]),
                "page_count": int(row["page_count"] or 0),
                "record_count": int(record_counts.get(row["source"], 0)),
                "language_hint": str(row["language_hint"] or ""),
                "created_at": str(row["created_at"] or ""),
            }
            for row in document_rows
            if scope_mode not in {"specific", "current"} or str(row["source"]) == resolved_source
        ]
        operation = plan.get("entity_operation") or "list"
        selected = metadata
        value = len(metadata)
        if operation == "sum_page_count":
            value = sum(item["page_count"] for item in metadata)
        elif operation == "sum_record_count":
            value = sum(item["record_count"] for item in metadata)
        elif operation in {"max_page_count", "min_page_count", "max_record_count", "min_record_count"}:
            field = "page_count" if "page_count" in operation else "record_count"
            chooser = max if operation.startswith("max") else min
            value = chooser((item[field] for item in metadata), default=None)
            selected = [item for item in metadata if item[field] == value] if value is not None else []
        facts = {
            "query_type": "structured",
            "entity": "document",
            "filters": filters,
            "matched_records": len(selected),
            "value": value,
            "aggregation": plan.get("aggregation"),
            "group_by": None,
            "pages": [],
            "total_structured_records": len(rows),
            "scoped_structured_records": len(scoped_rows),
            "document_scope": {**scope, "resolved_source": resolved_source, "error": scope_error},
            "available_documents": available_documents,
            "documents": [item["source"] for item in selected],
            "document_metadata": selected,
            "requested_fields": plan.get("requested_fields") or ["source"],
            "entity_operation": operation,
            "pagination": {"page": 1, "page_size": 0, "total_pages": 1, "show_all": True},
            "evidence_rows_returned": 0,
        }
        return [], facts

    if plan.get("entity") == "polling_booth":
        scoped_sources = (
            [resolved_source] if scope_mode in {"specific", "current"} and resolved_source
            else available_documents
        )
        booths, unidentified_sources, inspected_sources = polling_booths_from_pages(
            first_page_rows, scoped_sources
        )
        for entity_filter in plan.get("entity_filters") or []:
            if entity_filter.get("field") == "number" and entity_filter.get("op") == "eq":
                wanted = str(entity_filter.get("value"))
                booths = [booth for booth in booths if str(booth.get("number")) == wanted]
        facts = {
            "query_type": "structured",
            "entity": "polling_booth",
            "filters": filters,
            "matched_records": len(booths),
            "value": len(booths),
            "aggregation": plan.get("aggregation"),
            "group_by": None,
            "pages": [1] if inspected_sources else [],
            "total_structured_records": len(rows),
            "scoped_structured_records": len(scoped_rows),
            "document_scope": {**scope, "resolved_source": resolved_source, "error": scope_error},
            "available_documents": available_documents,
            "documents": inspected_sources,
            "polling_booths": booths,
            "requested_fields": plan.get("requested_fields") or ["number", "sources"],
            "unidentified_documents": unidentified_sources,
            "pagination": {"page": 1, "page_size": 0, "total_pages": 1, "show_all": True},
            "evidence_rows_returned": 0,
        }
        return [], facts

    booth_scope_number = plan.get("booth_scope")
    booth_scope_sources = []
    if booth_scope_number:
        all_booths, _, _ = polling_booths_from_pages(first_page_rows, available_documents)
        booth_scope_sources = sorted({
            source
            for booth in all_booths if str(booth.get("number")) == str(booth_scope_number)
            for source in booth.get("sources", [])
        })
        scoped_rows = [
            row for row in scoped_rows if str(row["source"]) in set(booth_scope_sources)
        ]

    operations = plan.get("operations") or []
    household_operation = next((item for item in operations if item.get("name") == "same_household"), None)
    largest_household_operation = next((item for item in operations if item.get("name") == "largest_household"), None)
    seed_rows = []
    household_keys = set()
    if household_operation:
        target = household_operation.get("target", "")
        voter_seed_rows = [row for row in scoped_rows if count_target_in_name(row["voter_name"], target)]
        related_seed_rows = [row for row in scoped_rows if count_target_in_name(row["related_name"], target)]
        seed_rows = list({int(row["id"]): row for row in voter_seed_rows + related_seed_rows}.values())
        household_keys = {
            (str(row["source"]), str(row["house_number"]))
            for row in seed_rows if str(row["house_number"] or "").strip()
        }
        matched = [
            row for row in scoped_rows
            if (str(row["source"]), str(row["house_number"])) in household_keys
            and all(_matches_filter(row, flt) for flt in filters)
        ]
    elif largest_household_operation:
        eligible_rows = [
            row for row in scoped_rows
            if str(row["house_number"] or "").strip()
            and all(_matches_filter(row, flt) for flt in filters)
        ]
        household_counts = {}
        for row in eligible_rows:
            key = (str(row["source"]), str(row["house_number"]).strip())
            household_counts[key] = household_counts.get(key, 0) + 1
        maximum = max(household_counts.values(), default=0)
        household_keys = {
            key for key, count in household_counts.items() if count == maximum
        }
        matched = [
            row for row in eligible_rows
            if (str(row["source"]), str(row["house_number"]).strip()) in household_keys
        ]
    else:
        matched = [row for row in scoped_rows if all(_matches_filter(row, flt) for flt in filters)]

    # Sorting applies before optional list limit; aggregate always uses all matched rows.
    if plan.get("order_by") == "age":
        reverse = plan.get("order_direction") == "desc"
        matched.sort(key=lambda r: (_to_age(r) is None, _to_age(r) if _to_age(r) is not None else -1), reverse=reverse)

    aggregate = plan.get("aggregation")
    group_by = plan.get("group_by")
    ages = [_to_age(r) for r in matched]
    ages = [x for x in ages if x is not None]

    facts = {
        "query_type": "structured",
        "filters": filters,
        "matched_records": len(matched),
        "pages": sorted({int(r["page"]) for r in matched}),
        "aggregation": aggregate,
        "group_by": group_by,
        "total_structured_records": len(rows),
        "scoped_structured_records": len(scoped_rows),
        "document_scope": {**scope, "resolved_source": resolved_source, "error": scope_error},
        "available_documents": available_documents,
        "documents": sorted({str(r["source"]) for r in matched}),
        "document_counts": {
            source: sum(1 for row in matched if str(row["source"]) == source)
            for source in sorted({str(r["source"]) for r in matched})
        },
    }
    if booth_scope_number:
        facts["booth_scope"] = {
            "number": str(booth_scope_number),
            "sources": booth_scope_sources,
            "error": None if booth_scope_sources else f"Polling booth not found: {booth_scope_number}",
        }
    if household_operation:
        facts["household_lookup"] = {
            "target": household_operation.get("target"),
            "seed_records": len(seed_rows),
            "voter_name_seed_records": len(voter_seed_rows),
            "related_name_seed_records": len(related_seed_rows),
            "households": [
                {"source": source, "house_number": house}
                for source, house in sorted(household_keys)
            ],
        }
    if largest_household_operation:
        maximum = max(
            (
                sum(
                    1 for row in matched
                    if str(row["source"]) == source and str(row["house_number"]).strip() == house
                )
                for source, house in household_keys
            ),
            default=0,
        )
        facts["largest_households"] = [
            {"source": source, "house_number": house, "resident_count": maximum}
            for source, house in sorted(household_keys)
        ]
        facts["value"] = maximum
    quality_warnings = [validate_record(row) for row in matched]
    facts["quality"] = {
        "records_with_warnings": sum(bool(items) for items in quality_warnings),
        "records_without_warnings": sum(not items for items in quality_warnings),
        "warning_count": sum(len(items) for items in quality_warnings),
    }

    if operations and not household_operation and not largest_household_operation:
        operation_results = []
        combined = []
        for operation in operations:
            reverse = operation.get("order_direction") == "desc"
            eligible = [row for row in matched if _to_age(row) is not None]
            eligible.sort(
                key=lambda row: (
                    _to_age(row), str(row["source"]), int(row["page"] or 0), int(row["row_index"] or 0)
                ),
                reverse=reverse,
            )
            selected = eligible[: int(operation["limit"])]
            operation_results.append({
                "name": operation["name"],
                "order_by": "age",
                "direction": operation["order_direction"],
                "limit": int(operation["limit"]),
                "records": selected,
            })
            combined.extend((operation["name"], row) for row in selected)
        facts["operations"] = [
            {key: value for key, value in item.items() if key != "records"} | {"matched_records": len(item["records"])}
            for item in operation_results
        ]
        results = []
        for operation_name, row in combined:
            result = row_to_result(row, method="structured_ranked", matched_fields=["age"])
            result["operation"] = operation_name
            results.append(result)
        facts["matched_records"] = len(results)
        facts["evidence_rows_returned"] = len(results)
        facts["pagination"] = {"page": 1, "page_size": len(results), "total_pages": 1, "show_all": True}
        return results, facts

    if aggregate == "count":
        facts["value"] = len(matched)
    elif aggregate == "avg_age":
        facts["value"] = (sum(ages) / len(ages)) if ages else None
    elif aggregate == "max_age":
        facts["value"] = max(ages) if ages else None
    elif aggregate == "min_age":
        facts["value"] = min(ages) if ages else None

    if group_by:
        groups = {}
        for row in matched:
            if group_by == "gender":
                key = _canonical_row_gender(row)
            elif group_by == "age":
                key = str(_to_age(row)) if _to_age(row) is not None else "unknown"
            elif group_by == "house_number":
                key = str(row["house_number"] or "")
            else:
                key = "unknown"
            groups[key] = groups.get(key, 0) + 1
        facts["groups"] = dict(sorted(groups.items(), key=lambda kv: kv[0]))

    display_rows = matched
    limit = plan.get("limit")
    if limit:
        display_rows = matched[: int(limit)]
    elif aggregate and not re.search(r"\b(?:detail|details|show|list|who)\b", plan.get("question", ""), flags=re.I):
        # Pure aggregate queries need no arbitrary record sample.
        display_rows = []

    pagination = plan.get("pagination") or {"page": 1, "show_all": False}
    show_all = bool(pagination.get("show_all"))
    page = max(1, int(pagination.get("page", 1)))
    if not limit and display_rows and not show_all:
        total_pages = max(1, (len(display_rows) + STRUCTURED_PAGE_SIZE - 1) // STRUCTURED_PAGE_SIZE)
        page = min(page, total_pages)
        start = (page - 1) * STRUCTURED_PAGE_SIZE
        display_rows = display_rows[start:start + STRUCTURED_PAGE_SIZE]
    else:
        total_pages = 1
    facts["pagination"] = {
        "page": page,
        "page_size": len(display_rows),
        "configured_page_size": STRUCTURED_PAGE_SIZE,
        "total_pages": total_pages,
        "show_all": show_all or bool(limit),
    }

    matched_fields = sorted({flt["field"] for flt in filters})
    results = [row_to_result(row, matched_fields=matched_fields) for row in display_rows]
    facts["evidence_rows_returned"] = len(results)
    return results, facts


def exact_identifier_search(identifiers):
    conn = connect()
    seen = set()
    results = []
    for value in identifiers:
        for row in exact_record_lookup(conn, value):
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            results.append(row_to_result(row, method="structured_exact"))
    conn.close()
    return results


def semantic_search_collection(collection, question, n_results, method, where=None, max_distance=SEMANTIC_MAX_DISTANCE):
    if collection is None or collection.count() == 0:
        return []
    vector = embedding_model.encode(question, normalize_embeddings=True).tolist()
    n_results = min(n_results, collection.count())
    query_args = dict(
        query_embeddings=[vector],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    if where:
        query_args["where"] = where
    response = collection.query(**query_args)
    output = []
    for item_id, document, metadata, distance in zip(
        response["ids"][0], response["documents"][0], response["metadatas"][0], response["distances"][0]
    ):
        distance = float(distance)
        if max_distance is not None and distance > max_distance:
            continue
        if distance <= SEMANTIC_HIGH_CONFIDENCE_DISTANCE:
            confidence = "high"
        elif distance <= SEMANTIC_MODERATE_CONFIDENCE_DISTANCE:
            confidence = "moderate"
        else:
            confidence = "low"
        output.append({
            "id": item_id,
            "document": document,
            "snippet": document,
            "method": method,
            "scope": "record" if method == "record_semantic" else "chunk",
            "distance": distance,
            "relevance_score": round(max(0.0, min(1.0, 1.0 - distance)), 4),
            "retrieval_confidence": confidence,
            "matched_terms": [],
            "match_fields": [],
            "metadata": metadata,
        })
    return output


def representative_semantic_collection(collection, sources=None, n_results=8, method="semantic_overview"):
    """Read representative embedded evidence for summaries/topic overviews.

    Generic words such as "summarize" have little vector similarity to names
    and table rows.  Sampling the already embedded collection is both faster
    and more faithful than lowering the relevance threshold for every query.
    """
    if collection is None or collection.count() == 0:
        return []
    sources = [str(source) for source in (sources or []) if source]
    requests = []
    if sources:
        per_source = max(1, min(3, n_results // max(1, len(sources))))
        requests = [({"source": source}, per_source) for source in sources]
    else:
        requests = [(None, min(n_results, collection.count()))]

    output = []
    for where, limit in requests:
        args = {"limit": min(limit, collection.count()), "include": ["documents", "metadatas"]}
        if where:
            args["where"] = where
        response = collection.get(**args)
        for item_id, document, metadata in zip(
            response.get("ids") or [], response.get("documents") or [], response.get("metadatas") or []
        ):
            output.append({
                "id": item_id,
                "document": document,
                "snippet": document,
                "method": method,
                "scope": "chunk",
                "distance": None,
                "relevance_score": None,
                "retrieval_confidence": "representative",
                "matched_terms": [],
                "match_fields": [],
                "metadata": metadata or {},
            })
    return output[:n_results]


def dedupe(results):
    final, seen = [], set()
    for result in results:
        if result["id"] in seen:
            continue
        seen.add(result["id"])
        final.append(result)
    return final


def retrieve(question, plan=None):
    plan = plan or analyze_query(question)
    intent = plan["intent"]

    if intent == "STRUCTURED_QUERY":
        results, facts = structured_query(plan)
        facts["execution_path"] = "sqlite_structured"
        facts["trust_confidence"] = "deterministic"
        return {"results": results, "facts": facts}

    _load_semantic_resources()
    results = []
    facts = {}
    if plan.get("exact_identifiers"):
        exact = exact_identifier_search(plan["exact_identifiers"])
        results.extend(exact)
        if exact and intent == "EXACT_MATCH":
            facts.update({"execution_path": "sqlite_exact", "trust_confidence": "deterministic"})
            return {"results": dedupe(results), "facts": facts}

    # Unstructured questions: semantic retrieval is appropriate, but a
    # selected/named document must remain an exact metadata scope.
    semantic_where, semantic_sources = semantic_source_filter(
        plan.get("document_scope"), CURRENT_DOCUMENT
    )
    if semantic_sources:
        facts["semantic_document_scope"] = semantic_sources
    overview_query = intent in {"DOCUMENT_SUMMARY", "COMPARE"} or bool(
        re.search(r"\b(?:key|main)\s+(?:topics?|themes?)\b|\boverview\b", question, flags=re.I)
    )
    if overview_query:
        overview_sources = semantic_sources
        if intent == "COMPARE" and not overview_sources:
            conn = connect()
            overview_sources = [str(row[0]) for row in conn.execute("SELECT source FROM documents ORDER BY source")]
            conn.close()
        results.extend(
            representative_semantic_collection(
                chunk_collection if chunk_collection is not None else record_collection,
                overview_sources,
                max(SEMANTIC_RESULTS, len(overview_sources)),
            )
        )
        facts["semantic_overview"] = True
    else:
        results.extend(semantic_search_collection(record_collection, question, RECORD_SEMANTIC_RESULTS, "record_semantic", semantic_where))
        results.extend(semantic_search_collection(chunk_collection, question, SEMANTIC_RESULTS, "semantic", semantic_where))
    final = dedupe(results)
    if not overview_query:
        # Low-confidence neighbours are useful for diagnostics but are not
        # strong enough to support a factual answer.  Prefer an explicit
        # no-evidence response over unrelated names or invented content.
        final = [
            item for item in final
            if item.get("distance") is not None
            and float(item["distance"]) <= SEMANTIC_MODERATE_CONFIDENCE_DISTANCE
        ]
    if len(final) < SEMANTIC_MIN_EVIDENCE:
        final = []
    distances = [float(item["distance"]) for item in final if item.get("distance") is not None]
    best_distance = min(distances) if distances else None
    if best_distance is None:
        confidence = "grounded" if final and facts.get("semantic_overview") else "insufficient"
    elif best_distance <= SEMANTIC_HIGH_CONFIDENCE_DISTANCE:
        confidence = "high"
    elif best_distance <= SEMANTIC_MODERATE_CONFIDENCE_DISTANCE:
        confidence = "moderate"
    else:
        confidence = "low"
    citations = []
    seen_citations = set()
    for item in final:
        metadata = item.get("metadata") or {}
        citation = (str(metadata.get("source", "")), metadata.get("page"))
        if citation in seen_citations:
            continue
        seen_citations.add(citation)
        citations.append({"source": citation[0], "page": citation[1]})
    facts.update({
        "execution_path": "chroma_semantic",
        "trust_confidence": confidence,
        "semantic_max_distance": (
            None if overview_query else SEMANTIC_MODERATE_CONFIDENCE_DISTANCE
        ),
        "semantic_evidence_count": len(final),
        "semantic_best_distance": best_distance,
        "semantic_citations": citations,
    })
    return {"results": final, "facts": facts}

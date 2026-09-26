import csv
import os
import re
from datetime import datetime

from config import EXPORTS_FOLDER
from language_utils import english_gender, english_relationship, romanize


CSV_FIELDS = [
    "source_document", "page", "serial_number", "house_number",
    "voter_name_original", "voter_name_romanized", "relationship_original", "relationship_english",
    "related_name_original", "related_name_romanized", "gender_original", "gender_english",
    "gender_normalized", "age",
    "epic_id", "ocr_confidence", "quality_status", "quality_warnings",
]

BOOTH_CSV_FIELDS = [
    "polling_booth_number", "name_original", "name_romanized",
    "classification_original", "classification_romanized",
    "reservation_status_original", "reservation_status_romanized",
    "address_original", "address_romanized", "constituency_number",
    "part_number", "header_male_voters", "header_female_voters",
    "header_total_voters", "source_documents",
]

DOCUMENT_CSV_FIELDS = [
    "source_document", "page_count", "structured_record_count",
    "ocr_language", "indexed_at",
]


def _safe_stem(question):
    stem = re.sub(r"[^A-Za-z0-9]+", "_", question or "results").strip("_").lower()
    return (stem[:60] or "results")


def export_csv(question, results, output_language="en"):
    os.makedirs(EXPORTS_FOLDER, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.abspath(os.path.join(EXPORTS_FOLDER, f"{_safe_stem(question)}_{timestamp}.csv"))
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in results:
            record = dict(result.get("record") or {})
            source = str(record.get("source_document", ""))
            voter_name = str(record.pop("voter_name", "") or "")
            related_name = str(record.pop("related_name", "") or "")
            relationship = str(record.pop("relationship", "") or "")
            gender = str(record.pop("gender", "") or "")
            record.update({
                "voter_name_original": voter_name,
                "voter_name_romanized": romanize(voter_name),
                "relationship_original": relationship,
                "relationship_english": english_relationship(relationship),
                "related_name_original": related_name,
                "related_name_romanized": romanize(related_name),
                "gender_original": gender,
                "gender_english": english_gender(source, gender),
            })
            record["quality_status"] = result.get("quality_status", "")
            record["quality_warnings"] = "; ".join(result.get("quality_warnings") or [])
            writer.writerow({field: record.get(field, "") for field in CSV_FIELDS})
    return path


def entity_export_rows(facts):
    if facts.get("entity") == "polling_booth":
        rows = []
        for booth in facts.get("polling_booths", []):
            counts = booth.get("header_voter_counts") or {}
            rows.append({
                "polling_booth_number": booth.get("number", ""),
                "name_original": booth.get("name", ""),
                "name_romanized": romanize(booth.get("name", "")),
                "classification_original": booth.get("classification", ""),
                "classification_romanized": romanize(booth.get("classification", "")),
                "reservation_status_original": booth.get("reservation_status", ""),
                "reservation_status_romanized": romanize(booth.get("reservation_status", "")),
                "address_original": booth.get("address", ""),
                "address_romanized": romanize(booth.get("address", "")),
                "constituency_number": booth.get("constituency_number", ""),
                "part_number": booth.get("part_number", ""),
                "header_male_voters": counts.get("male", ""),
                "header_female_voters": counts.get("female", ""),
                "header_total_voters": counts.get("total", ""),
                "source_documents": "; ".join(booth.get("sources", [])),
            })
        return BOOTH_CSV_FIELDS, rows

    if facts.get("entity") == "document":
        rows = [{
            "source_document": item.get("source", ""),
            "page_count": item.get("page_count", ""),
            "structured_record_count": item.get("record_count", ""),
            "ocr_language": item.get("language_hint", ""),
            "indexed_at": item.get("created_at", ""),
        } for item in facts.get("document_metadata", [])]
        return DOCUMENT_CSV_FIELDS, rows
    return [], []


def export_entity_csv(question, facts):
    fields, rows = entity_export_rows(facts)
    if not fields:
        raise ValueError("No document-level entity data is available for export.")
    os.makedirs(EXPORTS_FOLDER, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.abspath(os.path.join(EXPORTS_FOLDER, f"{_safe_stem(question)}_{timestamp}.csv"))
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path, len(rows)

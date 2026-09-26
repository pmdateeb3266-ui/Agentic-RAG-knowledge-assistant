import argparse

from record_integrity import audit_all_documents
from storage import initialize_database, update_document_integrity


def main():
    parser = argparse.ArgumentParser(description="Audit voter-roll serial completeness.")
    parser.add_argument("--source", help="Limit the audit to one indexed PDF filename.")
    parser.add_argument(
        "--refresh-expected",
        action="store_true",
        help="Read final PDF pages to discover the expected final serial.",
    )
    args = parser.parse_args()
    connection = initialize_database(clean=False)
    reports = audit_all_documents(
        connection,
        source=args.source,
        refresh_expected=args.refresh_expected,
    )
    for report in reports:
        update_document_integrity(
            connection,
            report["source"],
            report["expected_records"],
            report["index_status"],
        )
        print(
            f"{report['source']} pages={report['page_count']} "
            f"indexed={report['record_count']} expected={report['expected_records']} "
            f"missing={report['missing_count']} ranges={report['missing_ranges'] or 'none'} "
            f"status={report['index_status']} recovery_pages={report['candidate_pages']}"
        )
    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

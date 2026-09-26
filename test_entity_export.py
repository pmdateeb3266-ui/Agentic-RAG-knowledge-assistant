import unittest

from result_exporter import entity_export_rows


class EntityExportTests(unittest.TestCase):
    def test_polling_booth_export_row(self):
        fields, rows = entity_export_rows({
            "entity": "polling_booth",
            "polling_booths": [{
                "number": "92", "address": "Address", "sources": ["a.pdf"],
                "header_voter_counts": {"male": 1, "female": 2, "total": 3},
            }],
        })
        self.assertIn("address_original", fields)
        self.assertEqual(rows[0]["polling_booth_number"], "92")
        self.assertEqual(rows[0]["header_total_voters"], 3)

    def test_document_export_row(self):
        fields, rows = entity_export_rows({
            "entity": "document",
            "document_metadata": [{"source": "a.pdf", "page_count": 10, "record_count": 20}],
        })
        self.assertIn("page_count", fields)
        self.assertEqual(rows[0]["source_document"], "a.pdf")
        self.assertEqual(rows[0]["structured_record_count"], 20)


if __name__ == "__main__":
    unittest.main()

import unittest

from document_entities import extract_polling_booth_number, polling_booths_from_pages, semantic_source_filter


class DocumentEntityTests(unittest.TestCase):
    def test_telugu_polling_booth_header(self):
        text = "పోలింగ్‌ కేంద్రం వివరాలు: పోలింగ్‌ కేంద్రంసంఖ్య : 92 పోలింగ్‌ కేంద్రం పేరు : అనంతపురం"
        self.assertEqual(extract_polling_booth_number(text), "92")

    def test_english_polling_booth_header(self):
        self.assertEqual(extract_polling_booth_number("Polling station number: 14"), "14")

    def test_distinct_booths_deduplicate_sources(self):
        rows = [
            {"source": "a.pdf", "page_text": "Polling booth number: 92"},
            {"source": "copy.pdf", "page_text": "Polling booth number: 92"},
            {"source": "b.pdf", "page_text": "Polling booth number: 93"},
        ]
        booths, unidentified, inspected = polling_booths_from_pages(rows)
        self.assertEqual([item["number"] for item in booths], ["92", "93"])
        self.assertEqual(booths[0]["sources"], ["a.pdf", "copy.pdf"])
        self.assertEqual(unidentified, [])
        self.assertEqual(inspected, ["a.pdf", "b.pdf", "copy.pdf"])

    def test_unlabelled_number_is_not_a_booth(self):
        self.assertIsNone(extract_polling_booth_number("Voters: 902 Year: 2002"))

    def test_selected_document_scope(self):
        rows = [
            {"source": "a.pdf", "page_text": "Polling booth number: 92"},
            {"source": "b.pdf", "page_text": "Polling booth number: 93"},
        ]
        booths, unidentified, inspected = polling_booths_from_pages(rows, ["b.pdf"])
        self.assertEqual(booths, [{"number": "93", "sources": ["b.pdf"]}])
        self.assertEqual(unidentified, [])
        self.assertEqual(inspected, ["b.pdf"])

    def test_semantic_specific_document_scope(self):
        where, sources = semantic_source_filter({
            "mode": "specific", "source": "a.pdf", "sources": ["a.pdf"],
        })
        self.assertEqual(where, {"source": "a.pdf"})
        self.assertEqual(sources, ["a.pdf"])

    def test_semantic_all_document_scope(self):
        where, sources = semantic_source_filter({"mode": "all"})
        self.assertIsNone(where)
        self.assertEqual(sources, [])


if __name__ == "__main__":
    unittest.main()

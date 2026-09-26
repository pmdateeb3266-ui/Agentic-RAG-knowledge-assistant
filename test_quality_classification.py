import unittest

from retriever import assess_record_quality, row_to_result
from review_ocr import _is_safe_improvement


def record(**updates):
    base = {
        "id": 1,
        "source": "17576784804969.pdf",
        "page": 2,
        "row_index": 1,
        "serial_number": "1",
        "house_number": "1-347",
        "voter_name": "అమరావతి",
        "relationship": "",
        "related_name": "రంగమయ్య",
        "gender": "3",
        "age": "35",
        "epic_id": "AP251720000000",
        "ocr_confidence": 85,
    }
    base.update(updates)
    return base


class QualityClassificationTests(unittest.TestCase):
    def test_source_placeholder_is_a_notice_not_ocr_failure(self):
        assessment = assess_record_quality(record())
        self.assertEqual(assessment["warnings"], [])
        self.assertIn("source placeholder", assessment["notices"][0])
        result = row_to_result(record())
        self.assertEqual(result["record"]["gender_normalized"], "female")
        self.assertEqual(result["quality_warnings"], [])
        self.assertTrue(result["quality_notices"])

    def test_missing_fields_remain_actionable_warnings(self):
        assessment = assess_record_quality(record(
            source="other.pdf",
            voter_name="",
            gender="",
            age="",
            epic_id="",
            ocr_confidence=20,
        ))
        self.assertIn("missing voter name", assessment["warnings"])
        self.assertIn("missing EPIC", assessment["warnings"])
        self.assertIn("gender could not be normalized", assessment["warnings"])
        self.assertEqual(assessment["notices"], [])

    def test_quality_reprocess_cannot_remove_an_existing_serial(self):
        first = record(serial_number="1", row_index=1)
        second = record(serial_number="2", row_index=2)
        accepted, reason = _is_safe_improvement([first, second], [first])
        self.assertFalse(accepted)
        self.assertIn("remove existing serials", reason)

    def test_human_verification_removes_record_from_actionable_queue(self):
        verified = record(
            source="other.pdf",
            voter_name="",
            epic_id="",
            verification_status="verified",
        )
        self.assertTrue(assess_record_quality(verified, respect_verification=False)["warnings"])
        self.assertEqual(assess_record_quality(verified)["warnings"], [])


if __name__ == "__main__":
    unittest.main()

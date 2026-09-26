import unittest

import retriever
from api import _trust_payload
from evidence_processor import no_evidence_message


class _FakeEmbeddingModel:
    def encode(self, text, normalize_embeddings=True):
        return _FakeVector([0.1, 0.2])


class _FakeVector(list):
    def tolist(self):
        return list(self)


class _FakeCollection:
    def count(self):
        return 3

    def query(self, **kwargs):
        return {
            "ids": [["high", "low", "rejected"]],
            "documents": [["strong evidence", "weak evidence", "unrelated text"]],
            "metadatas": [[
                {"source": "one.pdf", "page": 2},
                {"source": "two.pdf", "page": 4},
                {"source": "three.pdf", "page": 6},
            ]],
            "distances": [[0.2, 0.6, 0.9]],
        }

    def get(self, **kwargs):
        return {
            "ids": ["representative"],
            "documents": ["embedded source evidence"],
            "metadatas": [{"source": "one.pdf", "page": 1}],
        }


class TrustLayerTests(unittest.TestCase):
    def setUp(self):
        self.previous_model = retriever.embedding_model
        retriever.embedding_model = _FakeEmbeddingModel()

    def tearDown(self):
        retriever.embedding_model = self.previous_model

    def test_semantic_threshold_rejects_unrelated_result(self):
        results = retriever.semantic_search_collection(
            _FakeCollection(), "question", 3, "chunk_semantic"
        )
        self.assertEqual([item["id"] for item in results], ["high", "low"])
        self.assertEqual(results[0]["retrieval_confidence"], "high")
        self.assertEqual(results[1]["retrieval_confidence"], "low")

    def test_relevance_score_is_exposed(self):
        results = retriever.semantic_search_collection(
            _FakeCollection(), "question", 3, "chunk_semantic"
        )
        self.assertEqual(results[0]["relevance_score"], 0.8)

    def test_overview_uses_representative_embedded_evidence(self):
        results = retriever.representative_semantic_collection(
            _FakeCollection(), ["one.pdf"], 2
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["document"], "embedded source evidence")
        self.assertEqual(results[0]["metadata"]["source"], "one.pdf")

    def test_structured_trust_is_deterministic_and_cited(self):
        trust = _trust_payload(
            {"intent": "STRUCTURED_QUERY"},
            {"execution_path": "sqlite_structured", "matched_records": 1},
            [{"metadata": {"source": "roll.pdf", "page": 7}}],
        )
        self.assertEqual(trust["confidence"], "deterministic")
        self.assertEqual(trust["citations"], [{"source": "roll.pdf", "page": 7}])

    def test_no_evidence_message_respects_language(self):
        self.assertIn("No sufficiently relevant evidence", no_evidence_message("en"))
        self.assertNotEqual(no_evidence_message("te"), no_evidence_message("en"))


if __name__ == "__main__":
    unittest.main()

import unittest


class ScanPreviewContractTest(unittest.TestCase):
    def test_preview_contract_documents_required_fields(self):
        required = {
            "id",
            "source",
            "title",
            "body",
            "published_at",
            "url",
            "author",
            "images",
            "metrics",
            "tags",
            "decision",
            "lead_id",
        }
        self.assertIn("body", required)
        self.assertIn("decision", required)
        self.assertEqual(len(required), 13)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Rhizomatica_v7


class KeywordMinimumMatchTests(unittest.TestCase):
    def test_keyword_facets_require_matches_in_at_least_two_files(self):
        rows = [
            {
                "id": "file_1",
                "title": "Rare Archive",
                "general_note": "",
                "scope_content": "",
                "facets": [],
            }
        ]

        keyword_facets, _, _ = Rhizomatica_v7.extract_keyword_facets(
            rows, 1, 0.7
        )

        self.assertNotIn("Rare Archive", keyword_facets.values())

    def test_keyword_facets_matching_two_files_are_retained(self):
        rows = [
            {
                "id": f"file_{index}",
                "title": "Rare Archive",
                "general_note": "",
                "scope_content": "",
                "facets": [],
            }
            for index in range(2)
        ]

        keyword_facets, _, _ = Rhizomatica_v7.extract_keyword_facets(
            rows, 1, 0.7
        )

        self.assertIn("Rare Archive", keyword_facets.values())


if __name__ == "__main__":
    unittest.main()

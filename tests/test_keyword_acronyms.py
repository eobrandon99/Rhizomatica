import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Rhizomatica_v7


class KeywordAcronymTests(unittest.TestCase):
    def test_collection_title_acronym_ignores_trailing_records_descriptor(self):
        rows = [
            {
                "id": f"file_{index}",
                "title": "AFLF",
                "general_note": "",
                "scope_content": "",
                "facets": [],
            }
            for index in range(2)
        ]

        keyword_facets, _, _ = Rhizomatica_v7.extract_keyword_facets(
            rows, 2, 0.5, collection_title="Academic Freedom Lecture Fund records"
        )

        self.assertNotIn("AFLF", keyword_facets.values())

    def test_parenthetical_acronym_in_hierarchy_title_absorbs_expansion_parts(self):
        rows = [
            {
                "id": f"file_{index}",
                "title": "Experimental Research in Electronic Submission",
                "general_note": "EXPRES",
                "scope_content": "",
                "facets": [],
            }
            for index in range(3)
        ]
        hierarchy_titles = [
            {
                "id": "series_1",
                "title": "Experimental Research in Electronic Submission (EXPRES)",
                "row_ids": {row["id"] for row in rows},
            }
        ]

        keyword_facets, _, _ = Rhizomatica_v7.extract_keyword_facets(
            rows, 2, 0.5, hierarchy_titles=hierarchy_titles
        )
        labels = set(keyword_facets.values())

        self.assertIn("EXPRES", labels)
        self.assertNotIn("Experimental Research in Electronic", labels)
        self.assertNotIn("Submission", labels)


if __name__ == "__main__":
    unittest.main()

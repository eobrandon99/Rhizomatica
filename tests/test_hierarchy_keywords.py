import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Rhizomatica_v7


class HierarchyKeywordTests(unittest.TestCase):
    def test_hierarchy_only_phrase_is_removed_before_redundant_word_merge(self):
        rows = [
            {
                "id": f"row_{index}",
                "title": title,
                "general_note": "",
                "scope_content": "",
                "facets": [],
            }
            for index, title in enumerate(
                ("Record Alpha", "Record Beta", "Record Gamma", "Office"),
                start=1,
            )
        ]
        hierarchy_titles = [
            {
                "id": "series_central_office_files",
                "title": "Central Office Files",
                "row_ids": {"row_1", "row_2", "row_3"},
            }
        ]

        keyword_facets, _, _ = Rhizomatica_v7.extract_keyword_facets(
            rows, 1, 0.5, hierarchy_titles=hierarchy_titles
        )

        self.assertNotIn("Central Office Files", keyword_facets.values())
        self.assertNotIn("Office", keyword_facets.values())


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Rhizomatica_v7


class KeywordHyphenationTests(unittest.TestCase):
    def test_hyphenated_and_unhyphenated_keywords_remain_distinct(self):
        rows = [
            {
                "id": f"row_{index}",
                "title": title,
                "general_note": "",
                "scope_content": "",
                "facets": [],
            }
            for index, title in enumerate(
                (
                    "Non-traditional Student",
                    "Non-traditional Student",
                    "Nontraditional Student",
                    "Nontraditional Student",
                )
            )
        ]

        keyword_facets, _, _ = Rhizomatica_v7.extract_keyword_facets(
            rows, 2, 0.5
        )

        hyphenated = [
            facet_id
            for facet_id, label in keyword_facets.items()
            if label == "Non-traditional Student"
        ]
        unhyphenated = [
            facet_id
            for facet_id, label in keyword_facets.items()
            if label == "Nontraditional Student"
        ]
        self.assertEqual(len(hyphenated), 1)
        self.assertEqual(len(unhyphenated), 1)
        self.assertNotEqual(hyphenated[0], unhyphenated[0])

    def test_hyphenated_plural_normalizes_by_component(self):
        self.assertEqual(Rhizomatica_v7.normalize_keyword_word("sub-series"), "sub-series")


if __name__ == "__main__":
    unittest.main()

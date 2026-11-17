import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Rhizomatica_v7


class KeywordRedundancyTests(unittest.TestCase):
    def test_single_word_facet_survives_merge_into_longer_phrase(self):
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
                    "Diamond Multimedia Document System",
                    "Diamond Multimedia Document System",
                    "Diamond",
                    "Diamond",
                )
            )
        ]

        keyword_facets, _, _ = Rhizomatica_v7.extract_keyword_facets(
            rows, 1, 0.7
        )
        labels_to_ids = {
            label: facet_id for facet_id, label in keyword_facets.items()
        }
        diamond_id = labels_to_ids["Diamond"]
        phrase_id = labels_to_ids["Diamond Multimedia Document System"]

        Rhizomatica_v7.propagate_embedded_facet_memberships(
            rows, keyword_facets
        )

        self.assertNotEqual(diamond_id, phrase_id)
        self.assertTrue(all(diamond_id in row["facets"] for row in rows))
        self.assertTrue(
            all(phrase_id in row["facets"] for row in rows[:2])
        )


if __name__ == "__main__":
    unittest.main()

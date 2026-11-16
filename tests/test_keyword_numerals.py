import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Rhizomatica_v7


class KeywordNumeralTests(unittest.TestCase):
    def test_numeric_tokens_distinguish_phrases_and_reject_numeric_majorities(self):
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
                    "Project 2024",
                    "Project 2024",
                    "Project 2025",
                    "Project 2025",
                    "1984",
                    "1984",
                    "Project 2024 2025",
                    "Project 2024 2025",
                )
            )
        ]

        keyword_facets, _, _ = Rhizomatica_v7.extract_keyword_facets(
            rows, 2, 0.5
        )

        self.assertIn("Project 2024", keyword_facets.values())
        self.assertIn("Project 2025", keyword_facets.values())
        self.assertNotIn("1984", keyword_facets.values())
        self.assertNotIn("Project 2024 2025", keyword_facets.values())


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Rhizomatica_v7


class KeywordThresholdTests(unittest.TestCase):
    def test_default_is_one_when_at_most_fifty_keyword_facets_exist(self):
        facets = [{"kind": "keyword", "count": 2} for _ in range(50)]
        facets.extend([{"kind": "hierarchy", "count": 100}])

        self.assertEqual(Rhizomatica_v7.default_keyword_min_hits(facets), 1)

    def test_default_excludes_ties_that_would_exceed_fifty_facets(self):
        facets = [{"kind": "keyword", "count": 5} for _ in range(51)]

        minimum_hits = Rhizomatica_v7.default_keyword_min_hits(facets)

        self.assertEqual(minimum_hits, 6)
        self.assertLessEqual(
            sum(facet["count"] >= minimum_hits for facet in facets),
            50,
        )

    def test_default_uses_threshold_that_keeps_no_more_than_fifty(self):
        facets = [
            {"kind": "keyword", "count": count}
            for count in range(1, 62)
        ]

        minimum_hits = Rhizomatica_v7.default_keyword_min_hits(facets)

        self.assertEqual(minimum_hits, 12)
        self.assertEqual(
            sum(facet["count"] >= minimum_hits for facet in facets),
            50,
        )


if __name__ == "__main__":
    unittest.main()

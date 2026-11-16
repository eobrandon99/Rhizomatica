import sys
import unittest
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Rhizomatica_v7


class DuplicateFacetTests(unittest.TestCase):
    def test_merges_identical_keyword_memberships_and_unions_metadata(self):
        rows = [
            {"id": "row_1", "facets": ["kw_hill", "kw_auditorium", "kw_partial"]},
            {"id": "row_2", "facets": ["kw_hill", "kw_auditorium", "kw_partial"]},
            {"id": "row_3", "facets": ["kw_partial"]},
        ]
        facets_by_id = {
            "kw_hill": "Hill",
            "kw_auditorium": "Auditorium",
            "kw_partial": "Partial",
        }
        hierarchy_links = defaultdict(
            set,
            {
                "kw_hill": {"series_hill"},
                "kw_auditorium": {"series_auditorium"},
            },
        )
        direct_file_matches = defaultdict(
            set,
            {
                "kw_hill": {"row_1"},
                "kw_auditorium": {"row_2"},
            },
        )

        id_remap = Rhizomatica_v7.merge_duplicate_facets(rows, facets_by_id)
        Rhizomatica_v7.remap_keyword_metadata(hierarchy_links, id_remap)
        Rhizomatica_v7.remap_keyword_metadata(direct_file_matches, id_remap)

        self.assertEqual(id_remap, {"kw_hill": "kw_auditorium"})
        self.assertEqual(facets_by_id, {"kw_auditorium": "Auditorium", "kw_partial": "Partial"})
        self.assertEqual(hierarchy_links["kw_auditorium"], {"series_hill", "series_auditorium"})
        self.assertEqual(direct_file_matches["kw_auditorium"], {"row_1", "row_2"})
        self.assertEqual(rows[0]["facets"], ["kw_auditorium", "kw_partial"])
        self.assertEqual(rows[1]["facets"], ["kw_auditorium", "kw_partial"])
        self.assertEqual(rows[2]["facets"], ["kw_partial"])


if __name__ == "__main__":
    unittest.main()

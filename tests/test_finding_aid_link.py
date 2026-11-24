import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import Rhizomatica_v7


class FindingAidLinkTests(unittest.TestCase):
    def test_builds_catalog_url_from_ead_and_object_ids(self):
        self.assertEqual(
            Rhizomatica_v7.finding_aid_url(
                "https://findingaids.lib.umich.edu",
                "umich-bhl-0672",
                "aspace_cdb8915db7d499674de5e138d462733a",
            ),
            "https://findingaids.lib.umich.edu/catalog/"
            "umich-bhl-0672_aspace_cdb8915db7d499674de5e138d462733a#contents",
        )

    def test_omits_catalog_url_when_an_id_is_missing(self):
        self.assertEqual(
            Rhizomatica_v7.finding_aid_url(
                "https://findingaids.lib.umich.edu", "", "aspace_object"
            ),
            "",
        )
        self.assertEqual(
            Rhizomatica_v7.finding_aid_url(
                "https://findingaids.lib.umich.edu", "umich-bhl-0672", ""
            ),
            "",
        )

    def test_removes_trailing_slash_from_base_url(self):
        self.assertEqual(
            Rhizomatica_v7.finding_aid_url(
                "https://findingaids.lib.umich.edu/",
                "umich-bhl-0672",
                "aspace_object",
            ),
            "https://findingaids.lib.umich.edu/catalog/"
            "umich-bhl-0672_aspace_object#contents",
        )


if __name__ == "__main__":
    unittest.main()

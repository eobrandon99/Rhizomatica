import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from desktop_app import (
    generate_rhizome,
    load_desktop_config,
    save_desktop_config,
    validate_config,
)


VALID_EAD = """<?xml version="1.0"?>
<ead xmlns="urn:isbn:1-931666-22-9">
  <eadid>test-collection</eadid>
  <archdesc>
    <did><unittitle>Test Collection</unittitle></did>
    <dsc><c01 id="123"><did><unittitle>File One</unittitle></did></c01></dsc>
  </archdesc>
</ead>
"""


class DesktopAppTests(unittest.TestCase):
    def test_generate_rhizome_writes_html_with_finding_aid_links(self):
        config = {
            "facet_redundancy_ratio": 0.7,
            "finding_aid_url_base": "https://example.org/finding-aids/",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            input_path = temporary_path / "finding-aid.xml"
            output_path = temporary_path / "finding-aid-rhizome.html"
            input_path.write_text(VALID_EAD, encoding="utf-8")

            counts = generate_rhizome(input_path, output_path, config)

            html = output_path.read_text(encoding="utf-8")
        self.assertEqual(counts[0], 1)
        self.assertIn("Test Collection", html)
        self.assertIn(
            "https://example.org/finding-aids/catalog/test-collection_123",
            html,
        )

    def test_generate_rhizome_rejects_findings_aids_without_file_records(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            input_path = temporary_path / "empty.xml"
            output_path = temporary_path / "empty-rhizome.html"
            input_path.write_text("<ead><archdesc><dsc /></archdesc></ead>", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "no file-level records"):
                generate_rhizome(
                    input_path,
                    output_path,
                    {
                        "facet_redundancy_ratio": 0.7,
                        "finding_aid_url_base": "",
                    },
                )

    def test_config_rejects_redundancy_ratio_outside_range(self):
        with self.assertRaisesRegex(ValueError, "from 0 to 1"):
            validate_config(
                {
                    "facet_redundancy_ratio": 1.1,
                    "finding_aid_url_base": "",
                }
            )

    def test_settings_are_saved_and_loaded_from_config_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            with patch("desktop_app.configuration_path", return_value=config_path):
                settings = load_desktop_config()
                settings["facet_redundancy_ratio"] = 0.4
                settings["finding_aid_url_base"] = "https://example.org"
                save_desktop_config(settings)

                loaded = load_desktop_config()

        self.assertEqual(loaded["facet_redundancy_ratio"], 0.4)
        self.assertEqual(loaded["finding_aid_url_base"], "https://example.org")


if __name__ == "__main__":
    unittest.main()

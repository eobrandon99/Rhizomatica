import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import webapp


VALID_EAD = b"""<?xml version="1.0"?>
<ead xmlns="urn:isbn:1-931666-22-9">
  <archdesc>
    <did><unittitle>Test Collection</unittitle></did>
    <dsc><c01><did><unittitle>File One</unittitle></did></c01></dsc>
  </archdesc>
</ead>
"""


class WebappTests(unittest.TestCase):
    @patch.object(webapp, "fetch_ead_xml", return_value=VALID_EAD)
    def test_generate_returns_rhizome_html(self, fetch_ead_xml):
        response = webapp.generate("umich-bhl-8740")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Test Collection", response.body)
        self.assertIn(b"File One", response.body)
        fetch_ead_xml.assert_called_once_with("umich-bhl-8740")

    @patch.object(webapp, "fetch_ead_xml")
    def test_invalid_id_is_rejected_without_fetching(self, fetch_ead_xml):
        response = webapp.generate("../other-host")

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"valid EAD ID", response.body)
        fetch_ead_xml.assert_not_called()

    def test_no_file_records_returns_a_helpful_error(self):
        xml_without_records = b"<ead><archdesc><dsc /></archdesc></ead>"
        with patch.object(webapp, "fetch_ead_xml", return_value=xml_without_records):
            response = webapp.generate("umich-bhl-8740")

        self.assertEqual(response.status_code, 422)
        self.assertIn(b"no file-level records", response.body)

    def test_generated_markup_escapes_untrusted_xml_text(self):
        malicious_ead = VALID_EAD.replace(
            b"Test Collection",
            b"&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;",
        )
        with patch.object(webapp, "fetch_ead_xml", return_value=malicious_ead):
            response = webapp.generate("umich-bhl-8740")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"&lt;/title&gt;", response.body)
        self.assertNotIn(b"</title><script>alert(1)</script>", response.body)

    def test_file_text_cannot_break_out_of_embedded_script_data(self):
        malicious_ead = VALID_EAD.replace(
            b"File One",
            b"&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;",
        )
        with patch.object(webapp, "fetch_ead_xml", return_value=malicious_ead):
            response = webapp.generate("umich-bhl-8740")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"\\u003c/script\\u003e", response.body)
        self.assertNotIn(b"</script><script>alert(1)</script>", response.body)


if __name__ == "__main__":
    unittest.main()

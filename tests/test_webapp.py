import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import webapp
from starlette.datastructures import UploadFile


VALID_EAD = b"""<?xml version="1.0"?>
<ead xmlns="urn:isbn:1-931666-22-9">
  <archdesc>
    <did><unittitle>Test Collection</unittitle></did>
    <dsc><c01><did><unittitle>File One</unittitle></did></c01></dsc>
  </archdesc>
</ead>
"""


def upload(data, filename="finding-aid.xml"):
    return UploadFile(filename=filename, file=BytesIO(data))


class WebappTests(unittest.TestCase):
    def test_generate_returns_rhizome_html(self):
        response = webapp.generate(upload(VALID_EAD))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Test Collection", response.body)
        self.assertIn(b"File One", response.body)

    def test_non_xml_filename_is_rejected(self):
        response = webapp.generate(upload(VALID_EAD, "finding-aid.txt"))

        self.assertEqual(response.status_code, 400)
        self.assertIn(b".xml extension", response.body)

    def test_upload_size_limit_is_enforced(self):
        with patch.object(webapp, "MAX_EAD_BYTES", 3):
            response = webapp.generate(upload(VALID_EAD))

        self.assertEqual(response.status_code, 413)
        self.assertIn(b"32 MiB maximum", response.body)

    def test_empty_file_is_rejected(self):
        response = webapp.generate(upload(b""))

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"empty", response.body)

    def test_malformed_xml_is_rejected(self):
        response = webapp.generate(upload(b"<ead><broken></ead>"))

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"not valid", response.body)

    def test_entity_declarations_are_rejected(self):
        xml_with_entity = b"""<!DOCTYPE ead [<!ENTITY x "expanded">]>
        <ead><archdesc><dsc><c01><did><unittitle>&x;</unittitle></did></c01></dsc></archdesc></ead>"""
        response = webapp.generate(upload(xml_with_entity))

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"not valid", response.body)

    def test_no_file_records_returns_a_helpful_error(self):
        xml_without_records = b"<ead><archdesc><dsc /></archdesc></ead>"
        response = webapp.generate(upload(xml_without_records))

        self.assertEqual(response.status_code, 422)
        self.assertIn(b"no file-level records", response.body)

    def test_generated_markup_escapes_untrusted_xml_text(self):
        malicious_ead = VALID_EAD.replace(
            b"Test Collection",
            b"&lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;",
        )
        response = webapp.generate(upload(malicious_ead))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"&lt;/title&gt;", response.body)
        self.assertNotIn(b"</title><script>alert(1)</script>", response.body)

    def test_file_text_cannot_break_out_of_embedded_script_data(self):
        malicious_ead = VALID_EAD.replace(
            b"File One",
            b"&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;",
        )
        response = webapp.generate(upload(malicious_ead))

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"\\u003c/script\\u003e", response.body)
        self.assertNotIn(b"</script><script>alert(1)</script>", response.body)


if __name__ == "__main__":
    unittest.main()

"""HTTP API for generating a rhizome from a University of Michigan EAD ID."""

import logging
import re
import xml.etree.ElementTree as ET
from html import escape
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

import Rhizomatica_v7 as rhizomatica


LOGGER = logging.getLogger(__name__)
EAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
MAX_EAD_BYTES = 32 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 30
EAD_URL_TEMPLATE = "https://findingaids.lib.umich.edu/catalog/{}/xml"

app = FastAPI(title="Rhizomatica EAD generator")


class EadTooLargeError(Exception):
    """Raised when a remote EAD exceeds the processing size limit."""


def fetch_ead_xml(ead_id):
    url = EAD_URL_TEMPLATE.format(quote(ead_id, safe=""))
    request = Request(url, headers={"User-Agent": "Rhizomatica EAD generator"})
    with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        xml_data = response.read(MAX_EAD_BYTES + 1)

    if len(xml_data) > MAX_EAD_BYTES:
        raise EadTooLargeError
    return xml_data


def error_page(status_code, message):
    page = (
        "<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>Rhizomatica: {escape(message)}</title>"
        "<body style=\"font:16px/1.5 Arial,sans-serif;max-width:42rem;"
        "margin:3rem auto;padding:0 1rem\">"
        f"<h1>Rhizome generation failed</h1><p>{escape(message)}</p>"
        "<p>You can close this tab and try again.</p></body></html>"
    )
    return HTMLResponse(content=page, status_code=status_code)


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"


@app.get("/generate", response_class=HTMLResponse)
def generate(ead_id: str = Query(..., min_length=1, max_length=128)):
    if not EAD_ID_PATTERN.fullmatch(ead_id):
        return error_page(400, "Enter a valid EAD ID using letters, numbers, dots, underscores, or hyphens.")

    try:
        xml_data = fetch_ead_xml(ead_id)
    except EadTooLargeError:
        return error_page(413, "This finding aid is too large to process.")
    except (URLError, TimeoutError, OSError) as exc:
        LOGGER.warning("Could not fetch EAD ID %s: %s", ead_id, exc)
        return error_page(502, "Could not retrieve that finding aid. Check the EAD ID and try again.")

    try:
        root = SafeET.fromstring(
            xml_data,
            forbid_dtd=False,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, ET.ParseError) as exc:
        LOGGER.warning("Could not parse EAD ID %s: %s", ead_id, exc)
        return error_page(502, "The catalog returned invalid or unsupported EAD XML.")

    root = rhizomatica.strip_namespaces(root)
    title, rows, facets = rhizomatica.build_records(
        root,
        rhizomatica.load_config()["facet_redundancy_ratio"],
    )
    if not rows:
        return error_page(422, "This finding aid has no file-level records to visualize.")

    html = rhizomatica.build_html(title, rows, facets)
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})

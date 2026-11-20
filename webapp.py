"""HTTP API for generating a rhizome from an uploaded EAD XML file."""

import logging
import xml.etree.ElementTree as ET
from html import escape

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse

import Rhizomatica_v7 as rhizomatica


LOGGER = logging.getLogger(__name__)
MAX_EAD_BYTES = 32 * 1024 * 1024

app = FastAPI(title="Rhizomatica EAD generator")


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


@app.post("/generate", response_class=HTMLResponse)
def generate(ead_file: UploadFile = File(...)):
    filename = ead_file.filename or ""
    if not filename.lower().endswith(".xml"):
        return error_page(400, "Choose an EAD file with an .xml extension.")

    xml_data = ead_file.file.read(MAX_EAD_BYTES + 1)
    if len(xml_data) > MAX_EAD_BYTES:
        return error_page(413, "This EAD file is too large to process (32 MiB maximum).")
    if not xml_data:
        return error_page(400, "The uploaded EAD file is empty.")

    try:
        root = SafeET.fromstring(
            xml_data,
            forbid_dtd=False,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, ET.ParseError) as exc:
        LOGGER.warning("Could not parse uploaded EAD XML: %s", exc)
        return error_page(400, "The uploaded file is not valid, supported EAD XML.")

    root = rhizomatica.strip_namespaces(root)
    title, rows, facets = rhizomatica.build_records(
        root,
        rhizomatica.load_config()["facet_redundancy_ratio"],
    )
    if not rows:
        return error_page(422, "This finding aid has no file-level records to visualize.")

    html = rhizomatica.build_html(title, rows, facets)
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})

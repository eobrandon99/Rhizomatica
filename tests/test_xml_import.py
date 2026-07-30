import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "Rhizomatica_v7.py"
spec = importlib.util.spec_from_file_location("rhizomatica_v7", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_build_records_from_xml_parses_ead_components():
    xml_text = """
    <ead>
      <eadheader>
        <filedesc>
          <titlestmt>
            <titleproper>Sample Collection</titleproper>
          </titlestmt>
        </filedesc>
      </eadheader>
      <archdesc level="collection">
        <dsc>
          <c level="file">
            <did>
              <unitid>MS-001</unitid>
              <unittitle>Letter to Ada</unittitle>
            </did>
            <unitdate normal="1932">1932</unitdate>
            <scopecontent><p>Correspondence from 1932.</p></scopecontent>
            <controlaccess>
              <persname>Ada Lovelace</persname>
              <subject>Letters</subject>
            </controlaccess>
          </c>
          <c level="item">
            <did>
              <unitid>MS-002</unitid>
              <unittitle>Notebook</unittitle>
            </did>
            <physdesc><extent>1 folder</extent></physdesc>
          </c>
        </dsc>
      </archdesc>
    </ead>
    """

    title, rows, facets = module.build_records_from_xml(xml_text)

    assert title == "Sample Collection"
    assert len(rows) == 2
    assert rows[0]["id"] == "MS-001"
    assert rows[0]["display_date"] == "1932"
    assert rows[0]["scope_content"] == "Correspondence from 1932."
    assert rows[0]["facets"] == ["Ada Lovelace", "Letters", "level:file"]
    assert any(f["id"] == "Ada Lovelace" for f in facets)

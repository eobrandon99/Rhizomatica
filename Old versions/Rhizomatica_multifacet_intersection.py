#!/usr/bin/env python3
"""Build an interactive facet-intersection network from facets.xlsx.

Behavior:
- Select one or more facet columns using pills.
- A file/row must contain *all* selected facets to remain visible.
- The network shows matching rows and edges between rows that share at least
  one selected facet.
- A side list shows the same matching rows.

Input:
- facets.xlsx in the same folder as this script.

Output:
- network_output.html in the same folder as this script.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

INPUT_FILE = Path(__file__).with_name("facets.xlsx")
OUTPUT_FILE = Path(__file__).with_name("network_output.html")

COLUMN_ALIASES = {
    "id": ["ID", "id", "Identifier", "identifier", "Row", "row"],
    "date_expression": ["date_expression", "date expression", "Date Expression", "date", "Date"],
    "date_begin": ["date_begin", "date begin", "Date Begin", "begin", "Start", "start_date"],
    "date_end": ["date_end", "date end", "Date End", "end", "Finish", "end_date"],
    "collection_title": ["Collection title", "collection title", "collection_title", "title", "Title"],
    "physical_instance": [
        "physical_instance",
        "physical instance",
        "container_instance",
        "container instance",
        "box",
        "Box",
        "box address",
        "Box Address",
    ],
}


def find_column(columns: Iterable[str], aliases: list[str], label: str, required: bool = True) -> Optional[str]:
    lookup = {str(c).strip().lower(): c for c in columns}
    for alias in aliases:
        key = alias.strip().lower()
        if key in lookup:
            return lookup[key]
    if required:
        raise ValueError(f"Could not find a column for {label}. Available columns: {list(columns)}")
    return None


def as_text(value) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none", "null"} else text


def normalize_yes(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, str):
        text = value.strip().lower()
        return text in {"yes", "y", "true", "1", "x", "checked"}
    try:
        return bool(value)
    except Exception:
        return False


def parse_yearish(value, *, is_end: bool = False):
    """Parse a date-ish value while preserving year-only records.

    Examples:
    - 1999 -> 1999-01-01 or 1999-12-31
    - "c. 1999" -> 1999-01-01 or 1999-12-31
    - real dates -> normalized to midnight
    - blanks -> NaT
    """
    if pd.isna(value):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    if isinstance(value, (int, float)) and not pd.isna(value):
        n = int(value)
        if 1000 <= n <= 9999:
            return pd.Timestamp(f"{n}-12-31" if is_end else f"{n}-01-01")

    s = str(value).strip()
    if not s or s.lower() in {"nan", "nat", "none", "null"}:
        return pd.NaT
    if re.fullmatch(r"\d{4}\.0", s):
        s = s[:-2]
    if re.fullmatch(r"\d{4}", s):
        return pd.Timestamp(f"{s}-12-31" if is_end else f"{s}-01-01")

    match = re.search(r"(1[5-9]\d{2}|20\d{2})", s)
    if match:
        year = match.group(1)
        return pd.Timestamp(f"{year}-12-31" if is_end else f"{year}-01-01")

    parsed = pd.to_datetime(s, errors="coerce")
    return parsed.normalize() if pd.notna(parsed) else pd.NaT


def read_input() -> pd.DataFrame:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Expected facets.xlsx next to script, but not found: {INPUT_FILE}")
    df = pd.read_excel(INPUT_FILE)
    df.columns = df.columns.str.strip()
    return df


def build_records(df: pd.DataFrame):
    id_column = find_column(df.columns, COLUMN_ALIASES["id"], "ID")
    date_expression_column = find_column(df.columns, COLUMN_ALIASES["date_expression"], "date expression")
    date_begin_column = find_column(df.columns, COLUMN_ALIASES["date_begin"], "date begin")
    date_end_column = find_column(df.columns, COLUMN_ALIASES["date_end"], "date end")
    title_column = find_column(df.columns, COLUMN_ALIASES["collection_title"], "Collection title", required=False)
    physical_column = find_column(df.columns, COLUMN_ALIASES["physical_instance"], "physical instance", required=False)

    print("Using columns:")
    print("  ID:", id_column)
    print("  Date expression:", date_expression_column)
    print("  Begin:", date_begin_column)
    print("  End:", date_end_column)
    print("  Collection title:", title_column if title_column else "(none)")
    print("  Physical instance:", physical_column if physical_column else "(none)")

    facet_columns = [
        c
        for c in df.columns
        if c not in [id_column, date_expression_column, date_begin_column, date_end_column, title_column, physical_column]
    ]
    print("Facet columns:", facet_columns)

    collection_title = "Facet Intersection Network"
    if title_column:
        title_series = df[title_column].dropna().astype(str).str.strip()
        title_series = title_series[title_series != ""]
        if not title_series.empty:
            collection_title = title_series.iloc[0]

    rows = []
    facet_counts = {facet: 0 for facet in facet_columns}

    for idx, row in df.iterrows():
        row_id = as_text(row[id_column]) or f"row_{idx + 1}"
        date_expr = as_text(row[date_expression_column])
        begin = parse_yearish(row[date_begin_column], is_end=False)
        end = parse_yearish(row[date_end_column], is_end=True)

        if pd.isna(begin):
            begin = parse_yearish(row[date_expression_column], is_end=False)
        if pd.isna(end):
            end = parse_yearish(row[date_expression_column], is_end=True)
        if pd.isna(end):
            end = begin

        if date_expr:
            display_date = date_expr
        elif pd.notna(begin) and pd.notna(end) and begin != end:
            display_date = f"{begin.date().isoformat()} to {end.date().isoformat()}"
        elif pd.notna(begin):
            display_date = begin.date().isoformat()
        else:
            display_date = "undated"

        box = as_text(row[physical_column]) if physical_column else ""
        if box:
            box_display = f"[{box}]"
        else:
            box_display = ""

        facets = [facet for facet in facet_columns if normalize_yes(row[facet])]
        for facet in facets:
            facet_counts[facet] += 1

        rows.append(
            {
                "id": row_id,
                "display_date": display_date,
                "box": box_display,
                "begin": begin.date().isoformat() if pd.notna(begin) else "",
                "end": end.date().isoformat() if pd.notna(end) else "",
                "facets": facets,
            }
        )

    facets = [
        {
            "id": facet,
            "label": facet,
            "count": facet_counts.get(facet, 0),
        }
        for facet in facet_columns
    ]

    return collection_title, rows, facets


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: #fff;
      color: #222;
    }
    #topbar {
      padding: 12px 14px;
      border-bottom: 1px solid #ddd;
      background: #f7f7f7;
    }
    #title {
      font-size: 24px;
      font-weight: 700;
      margin-bottom: 6px;
    }
    #subtitle {
      font-size: 13px;
      color: #666;
      margin-bottom: 10px;
      line-height: 1.35;
    }
    .toolbar {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    .toolbar-btn {
      border: 1px solid #bbb;
      background: #fff;
      border-radius: 8px;
      padding: 5px 10px;
      font-size: 13px;
      cursor: pointer;
    }
    .toolbar-btn:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    #layout {
      display: flex;
      width: 100%;
      height: calc(100vh - 112px);
      min-height: 640px;
    }
    #sidebar {
      width: 350px;
      border-right: 1px solid #ddd;
      background: #fafafa;
      box-sizing: border-box;
      padding: 12px;
      overflow: auto;
    }
    #main {
      flex: 1;
      position: relative;
      min-width: 0;
    }
    #network {
      width: 100%;
      height: 100%;
    }
    #facetHeader, #rowHeader {
      font-size: 13px;
      font-weight: 700;
      margin: 8px 0;
      color: #333;
    }
    #facetList {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }
    .facet-pill {
      border: 1px solid #cfcfcf;
      background: #fff;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      cursor: pointer;
      transition: transform 0.15s ease, background 0.15s ease, border-color 0.15s ease;
    }
    .facet-pill:hover {
      background: #fff8e8;
      border-color: #ffb300;
      transform: translateY(-1px);
    }
    .facet-pill.selected {
      background: #fff2b3;
      border-color: #ffb300;
      font-weight: 700;
    }
    .facet-pill .count {
      margin-left: 4px;
      color: #666;
      font-weight: 600;
    }
    .sidebar-item {
      width: 100%;
      text-align: left;
      background: #fff;
      border: 1px solid #e4e4e4;
      border-radius: 10px;
      padding: 8px 10px;
      cursor: pointer;
      transition: transform 0.15s ease, background 0.15s ease, border-color 0.15s ease;
      margin-bottom: 8px;
    }
    .sidebar-item:hover {
      background: #fff8e8;
      border-color: #ffb300;
      transform: translateX(2px);
    }
    .sidebar-item.selected {
      background: #fff2b3;
      border-color: #ffb300;
    }
    .row-id {
      display: block;
      font-weight: 700;
      margin-bottom: 2px;
    }
    .row-date {
      font-size: 12px;
      color: #333;
    }
    .row-box {
      font-size: 12px;
      color: #666;
      margin-top: 2px;
    }
    .row-match {
      font-size: 12px;
      color: #666;
      margin-top: 3px;
    }
    #selectionInfo {
      font-size: 13px;
      color: #555;
    }
    #rowCount {
      margin-bottom: 8px;
      color: #555;
      font-size: 13px;
    }
    #emptyState {
      font-size: 13px;
      color: #666;
      padding: 10px 0;
    }
    #footer {
      position: fixed;
      bottom: 10px;
      right: 12px;
      font-size: 12px;
      color: #666;
      background: rgba(255,255,255,0.82);
      padding: 4px 8px;
      border-radius: 6px;
      z-index: 9999;
    }
  </style>
</head>
<body>
  <div id="topbar">
    <div id="title">__TITLE__</div>
    <div id="subtitle">Select one or more facets. A row stays visible only when it contains all selected facets. The network shows the matching rows and links them when they share selected facets.</div>
    <div class="toolbar">
      <button id="clearFacets" class="toolbar-btn" disabled>Clear facet filters</button>
      <span id="selectionInfo">No facets selected.</span>
    </div>
  </div>

  <div id="layout">
    <aside id="sidebar">
      <div id="facetHeader">Facet filters</div>
      <div id="facetList"></div>
      <div id="rowHeader">Matching files</div>
      <div id="rowCount"></div>
      <div id="rowList"></div>
      <div id="emptyState" style="display:none;">No rows match the current facet selection.</div>
    </aside>
    <main id="main">
      <div id="network"></div>
    </main>
  </div>

  <div id="footer">Rhizomatica. Copyright 2026, Evan Brandon.</div>

  <script>
    const ROWS = __ROWS__;
    const FACETS = __FACETS__;

    const facetList = document.getElementById("facetList");
    const rowList = document.getElementById("rowList");
    const rowCount = document.getElementById("rowCount");
    const selectionInfo = document.getElementById("selectionInfo");
    const clearFacets = document.getElementById("clearFacets");
    const emptyState = document.getElementById("emptyState");
    const networkEl = document.getElementById("network");

    let selectedFacetIds = new Set();
    let selectedRowId = null;

    const network = new vis.Network(networkEl, { nodes: [], edges: [] }, {
      nodes: {
        shape: "dot",
        size: 14
      },
      edges: {
        smooth: true,
        color: { color: "#999" }
      },
      physics: {
        enabled: true,
        solver: "forceAtlas2Based",
        forceAtlas2Based: {
          gravitationalConstant: -85,
          centralGravity: 0.03,
          springLength: 140,
          springConstant: 0.08,
          damping: 0.65,
          avoidOverlap: 0.18
        },
        stabilization: { iterations: 200, fit: true }
      },
      interaction: { hover: true },
      groups: {
        row: {
          color: {
            background: "#4C78A8",
            border: "#4C78A8",
            highlight: { background: "#B7D8FF", border: "#2F6FDB" },
            hover: { background: "#6B95C8", border: "#4C78A8" }
          },
          font: { color: "#ffffff" },
          size: 18
        }
      }
    });

    function rowMatchesSelection(row) {
      if (!selectedFacetIds.size) return true;
      for (const fid of selectedFacetIds) {
        if (!row.facets.includes(fid)) return false;
      }
      return true;
    }

    function sharedSelectedFacets(a, b) {
      if (!selectedFacetIds.size) return a.facets.filter(fid => b.facets.includes(fid));
      const shared = [];
      for (const fid of selectedFacetIds) {
        if (a.facets.includes(fid) && b.facets.includes(fid)) shared.push(fid);
      }
      return shared;
    }

    function filteredRows() {
      return ROWS.filter(rowMatchesSelection);
    }

    function rowLabel(row) {
      const lines = [row.id];
      if (row.display_date) lines.push(row.display_date);
      if (row.box) lines.push(row.box);
      return lines.join("\n");
    }

    function buildNetworkData(rows) {
      const nodes = rows.map(row => ({
        id: row.id,
        label: rowLabel(row),
        group: "row",
        title: [
          `<b>ID:</b> ${row.id}`,
          `<b>Date:</b> ${row.display_date || "undated"}`,
          row.box ? `<b>Box:</b> ${row.box}` : "",
          row.facets.length ? `<b>Facets:</b> ${row.facets.join(", ")}` : ""
        ].filter(Boolean).join("<br>")
      }));

      const edges = [];
      for (let i = 0; i < rows.length; i++) {
        for (let j = i + 1; j < rows.length; j++) {
          const a = rows[i];
          const b = rows[j];
          const shared = sharedSelectedFacets(a, b);
          if (shared.length) {
            edges.push({
              from: a.id,
              to: b.id,
              width: Math.min(1 + shared.length, 5),
              title: `Shared selected facets: ${shared.join(", ")}`
            });
          }
        }
      }
      return { nodes, edges };
    }

    function renderFacetChips(rows) {
      const counts = new Map();
      FACETS.forEach(f => counts.set(f.id, 0));

      rows.forEach(row => {
        row.facets.forEach(fid => {
          if (counts.has(fid)) counts.set(fid, counts.get(fid) + 1);
        });
      });

      facetList.innerHTML = FACETS.map(f => {
        const selected = selectedFacetIds.has(f.id);
        return `<button type="button" class="facet-pill ${selected ? "selected" : ""}" data-facet-id="${f.id}">${f.label}<span class="count">${counts.get(f.id) || 0}</span></button>`;
      }).join("");

      facetList.querySelectorAll("[data-facet-id]").forEach(btn => {
        btn.addEventListener("click", () => {
          const fid = btn.dataset.facetId;
          if (selectedFacetIds.has(fid)) selectedFacetIds.delete(fid);
          else selectedFacetIds.add(fid);
          selectedRowId = null;
          render();
        });
      });

      clearFacets.disabled = selectedFacetIds.size === 0;
      selectionInfo.textContent = selectedFacetIds.size
        ? `Selected facets: ${Array.from(selectedFacetIds).join(", ")}`
        : "No facets selected.";
    }

    function renderRowList(rows) {
      rowCount.textContent = `${rows.length} matching file${rows.length === 1 ? "" : "s"}`;
      emptyState.style.display = rows.length ? "none" : "block";

      rowList.innerHTML = rows.map(row => {
        const selected = row.id === selectedRowId;
        const matched = row.facets.filter(fid => selectedFacetIds.has(fid)).join(", ");
        return `
          <button type="button" class="sidebar-item ${selected ? "selected" : ""}" data-row-id="${row.id}">
            <span class="row-id">${row.id}</span>
            <div class="row-date">${row.display_date || "undated"}</div>
            ${row.box ? `<div class="row-box">${row.box}</div>` : ""}
            ${matched ? `<div class="row-match">Matches: ${matched}</div>` : ""}
          </button>
        `;
      }).join("");

      rowList.querySelectorAll("[data-row-id]").forEach(btn => {
        btn.addEventListener("click", () => {
          selectedRowId = btn.dataset.rowId;
          network.focus(selectedRowId, { scale: 1.2, animation: true });
          render();
        });
      });
    }

    function render() {
      const rows = filteredRows();
      renderFacetChips(rows);
      renderRowList(rows);

      const data = buildNetworkData(rows);
      network.setData({
        nodes: new vis.DataSet(data.nodes),
        edges: new vis.DataSet(data.edges)
      });

      if (selectedRowId && !rows.some(r => r.id === selectedRowId)) {
        selectedRowId = null;
      }
    }

    clearFacets.addEventListener("click", () => {
      selectedFacetIds.clear();
      selectedRowId = null;
      render();
    });

    network.on("click", params => {
      if (params.nodes.length === 1) {
        selectedRowId = params.nodes[0];
        render();
      }
    });

    render();
  </script>
</body>
</html>
"""


def build_html(title: str, rows, facets) -> str:
    html = HTML_TEMPLATE.replace("__TITLE__", title)
    html = html.replace("__ROWS__", json.dumps(rows, ensure_ascii=False))
    html = html.replace("__FACETS__", json.dumps(facets, ensure_ascii=False))
    return html


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a multifacet network HTML page from facets.xlsx")
    parser.add_argument("--open", action="store_true", help="Open the generated HTML in your default browser")
    args = parser.parse_args(argv)

    df = read_input()
    print("Columns:", list(df.columns))
    print("Rows:", len(df))

    title, rows, facets = build_records(df)
    if not rows:
        raise ValueError("The spreadsheet has no rows to render.")

    html = build_html(title, rows, facets)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Generated: {OUTPUT_FILE}")

    if args.open:
        webbrowser.open(OUTPUT_FILE.resolve().as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

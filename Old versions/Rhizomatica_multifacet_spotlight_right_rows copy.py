#!/usr/bin/env python3
"""Build an interactive multifacet network from facets.xlsx.

Features:
- Multi-select facet spotlight with intersection filtering.
- Timeline slider filter.
- File list on the right.
- Hover/click file nodes or file list items to show a detail card below the
  facet spotlight area.

Input:
- facets.xlsx in the same folder as this script.

Output:
- network_output.html in the same folder as this script.
"""

from __future__ import annotations

import argparse
import json
import re
import webbrowser
from pathlib import Path

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
    "scope_content": ["scope_content", "scope content", "Scope Content", "notes", "Note", "scope"],
    "physical_desc": ["physical_desc", "physical desc", "Physical Desc", "physical description", "description"],
}


def find_column(columns, aliases, label, required=True):
    lookup = {str(c).strip().lower(): c for c in columns}
    for alias in aliases:
        key = alias.strip().lower()
        if key in lookup:
            return lookup[key]
    if required:
        raise ValueError(f"Could not find a column for {label}. Available columns: {list(columns)}")
    return None


def as_text(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none", "null"} else text


def parse_yearish(value, is_end=False):
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


def is_active(val):
    if pd.isna(val):
        return False
    if isinstance(val, str):
        return val.strip().upper() in {"YES", "Y", "TRUE", "1", "X", "CHECKED"}
    return bool(val)


def format_box(value):
    text = as_text(value)
    if not text:
        return ""
    return f"[{text}]"


def read_input():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Expected facets.xlsx next to script, but not found: {INPUT_FILE}")
    df = pd.read_excel(INPUT_FILE)
    df.columns = df.columns.str.strip()
    return df


def build_records(df):
    id_column = find_column(df.columns, COLUMN_ALIASES["id"], "ID")
    date_expression_column = find_column(df.columns, COLUMN_ALIASES["date_expression"], "date expression")
    date_begin_column = find_column(df.columns, COLUMN_ALIASES["date_begin"], "date begin")
    date_end_column = find_column(df.columns, COLUMN_ALIASES["date_end"], "date end")
    title_column = find_column(df.columns, COLUMN_ALIASES["collection_title"], "Collection title", required=False)
    physical_column = find_column(df.columns, COLUMN_ALIASES["physical_instance"], "physical instance", required=False)
    scope_column = find_column(df.columns, COLUMN_ALIASES["scope_content"], "scope content", required=False)
    physical_desc_column = find_column(df.columns, COLUMN_ALIASES["physical_desc"], "physical description", required=False)

    print("Using columns:")
    print("  ID:", id_column)
    print("  Date expression:", date_expression_column)
    print("  Begin:", date_begin_column)
    print("  End:", date_end_column)
    print("  Collection title:", title_column if title_column else "(none)")
    print("  Physical instance:", physical_column if physical_column else "(none)")
    print("  Scope content:", scope_column if scope_column else "(none)")
    print("  Physical description:", physical_desc_column if physical_desc_column else "(none)")

    facet_columns = [
        c
        for c in df.columns
        if c not in [
            id_column,
            date_expression_column,
            date_begin_column,
            date_end_column,
            title_column,
            physical_column,
            scope_column,
            physical_desc_column,
        ]
    ]
    print("Facet columns:", facet_columns)

    collection_title = "Facet Spotlight Network"
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

        container_instance = as_text(row[physical_column]) if physical_column else ""
        scope_content = as_text(row[scope_column]) if scope_column else ""
        physical_desc = as_text(row[physical_desc_column]) if physical_desc_column else ""
        box = format_box(container_instance)
        facets = [facet for facet in facet_columns if is_active(row[facet])]
        for facet in facets:
            facet_counts[facet] += 1

        rows.append(
            {
                "id": row_id,
                "display_date": display_date,
                "container_instance": container_instance,
                "scope_content": scope_content,
                "physical_desc": physical_desc,
                "box": box,
                "begin": begin.date().isoformat() if pd.notna(begin) else "",
                "end": end.date().isoformat() if pd.notna(end) else "",
                "facets": facets,
                "title": as_text(row[title_column]) if title_column else "",
                "date_expr": date_expr,
            }
        )

    facets = [
        {"id": facet, "label": facet, "count": facet_counts.get(facet, 0)}
        for facet in facet_columns
    ]

    return collection_title, rows, facets


HTML_TEMPLATE = r'''<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/noUiSlider/15.7.1/nouislider.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/noUiSlider/15.7.1/nouislider.min.css">
  <style>
    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: #fff;
      color: #222;
    }
    #controls {
      padding: 10px 14px;
      border-bottom: 1px solid #ddd;
      background: #f7f7f7;
    }
    #title {
      font-size: 24px;
      font-weight: 700;
      margin-bottom: 8px;
      color: #222;
    }
    .toggle-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 6px;
      user-select: none;
      font-size: 13px;
    }
    .facet-spotlight {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 999px;
      background: #FFF2B3;
      border: 1px solid #FFB300;
      font-weight: 700;
    }
    #toolbar {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin: 8px 0 6px 0;
    }
    .toolbar-btn {
      border: 1px solid #bbb;
      background: #fff;
      border-radius: 8px;
      padding: 4px 10px;
      font-size: 13px;
      cursor: pointer;
    }
    .toolbar-btn:disabled {
      opacity: 0.45;
      cursor: not-allowed;
    }
    #rangeText {
      margin-top: 6px;
      font-size: 13px;
      line-height: 1.35;
      color: #333;
    }
    #page {
      display: flex;
      width: 100%;
      height: calc(100vh - 176px);
      min-height: 500px;
    }
    #facetSidebar, #rowSidebar {
      width: 320px;
      background: #fafafa;
      padding: 12px;
      box-sizing: border-box;
      overflow: auto;
    }
    #facetSidebar {
      border-right: 1px solid #ddd;
    }
    #rowSidebar {
      border-left: 1px solid #ddd;
    }
    #container {
      flex: 1;
      height: 100%;
      min-width: 0;
    }
    #facetHeader, #rowHeader, #detailHeader {
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
      text-align: left;
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
    .detail-panel {
      border: 1px solid #e4e4e4;
      background: #fff;
      border-radius: 12px;
      padding: 10px 12px;
      font-size: 12px;
      line-height: 1.45;
      color: #222;
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
      margin-bottom: 14px;
      min-height: 108px;
    }
    .detail-panel.empty {
      color: #666;
      background: #fcfcfc;
      font-style: italic;
    }
    .detail-row {
      margin-bottom: 6px;
    }
    .detail-label {
      font-weight: 700;
      color: #333;
    }
    .detail-value {
      white-space: pre-wrap;
      word-break: break-word;
    }
    .detail-related {
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid #e7e7e7;
    }
    .detail-related-title {
      font-size: 12px;
      font-weight: 700;
      color: #444;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .detail-facet-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .detail-facet-chip {
      appearance: none;
      border: 1px solid #d0d7e2;
      background: #f7f9fc;
      color: #17324d;
      border-radius: 999px;
      padding: 5px 10px;
      font-size: 12px;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .detail-facet-chip:hover {
      background: #e9f2ff;
      border-color: #9dc2ff;
    }
    .detail-facet-chip.selected {
      background: #FFE082;
      border-color: #FF8C00;
      color: #7a4a00;
      font-weight: 700;
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
      box-sizing: border-box;
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
      color: #111;
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
    #rowCount, #selectionInfo {
      font-size: 13px;
      color: #666;
      margin-bottom: 8px;
    }
    #emptyState {
      font-size: 13px;
      color: #666;
      padding: 10px 0;
    }
    #copyright {
      position: fixed;
      bottom: 10px;
      right: 12px;
      font-size: 12px;
      color: #666;
      background: rgba(255,255,255,0.8);
      padding: 4px 8px;
      border-radius: 6px;
      backdrop-filter: blur(2px);
      z-index: 9999;
    }
    .noUi-target { height: 5px; }
    .noUi-connect { background: #4C78A8; }
    .noUi-horizontal .noUi-handle {
      width: 12px;
      height: 12px;
      right: -6px;
      top: -4px;
      border-radius: 50%;
    }
    .noUi-handle:before,
    .noUi-handle:after { display: none; }
    @media (max-width: 1100px) {
      #page { flex-direction: column; height: auto; }
      #facetSidebar, #rowSidebar { width: 100%; border-left: none; border-right: none; border-bottom: 1px solid #ddd; }
      #container { height: 560px; }
    }
  </style>
</head>
<body>
  <div id="controls">
    <div id="title">__TITLE__</div>
    <div><strong>Filter by date range or facet selection</strong></div>
    <div class="toggle-row">
      <input type="checkbox" id="timelineToggle">
      <label for="timelineToggle">Enable timeline filter</label>
    </div>
    <div id="sliderWrap"><div id="slider"></div></div>
    <div id="toolbar">
      <button id="clearFacets" class="toolbar-btn" disabled>Clear facet spotlight</button>
      <span id="selectionInfo">No facets selected.</span>
    </div>
    <div id="rangeText">Timeline disabled - showing everything, including records without dates.</div>
  </div>

  <div id="page">
    <aside id="facetSidebar">
      <div id="facetHeader">Facet spotlight</div>
      <div id="facetList"></div>
      <div id="detailHeader">File spotlight</div>
      <div id="detailPanel" class="detail-panel empty">Hover or click a file node or row to preview details.</div>
    </aside>
    <div id="container"></div>
    <aside id="rowSidebar">
      <div id="rowHeader">Related files</div>
      <div id="rowCount"></div>
      <div id="rowList"></div>
      <div id="emptyState" style="display:none;">No rows match the current selection.</div>
    </aside>
  </div>

  <script>
    const ROWS = __ROWS__;
    const FACETS = __FACETS__;
    const availableDates = __DATES__;

    const container = document.getElementById('container');
    const slider = document.getElementById('slider');
    const rangeText = document.getElementById('rangeText');
    const timelineToggle = document.getElementById('timelineToggle');
    const clearFacets = document.getElementById('clearFacets');
    const selectionInfo = document.getElementById('selectionInfo');
    const facetList = document.getElementById('facetList');
    const rowList = document.getElementById('rowList');
    const rowCount = document.getElementById('rowCount');
    const emptyState = document.getElementById('emptyState');
    const detailPanel = document.getElementById('detailPanel');

    const options = {
      interaction: { hover: true },
      nodes: { shape: 'dot', size: 14 },
      edges: {
        smooth: true,
        color: { color: '#999' },
        length: 180,
        width: 1.2
      },
      physics: {
        enabled: true,
        solver: 'forceAtlas2Based',
        forceAtlas2Based: {
          gravitationalConstant: -80,
          centralGravity: 0.02,
          springLength: 130,
          springConstant: 0.08,
          damping: 0.65,
          avoidOverlap: 0.15
        },
        stabilization: { iterations: 220, fit: true },
        maxVelocity: 12,
        minVelocity: 0.35,
        timestep: 0.35
      },
      groups: {
        row: {
          color: {
            background: '#4C78A8',
            border: '#4C78A8',
            highlight: { background: '#B7D8FF', border: '#2F6FDB' },
            hover: { background: '#6B95C8', border: '#4C78A8' }
          },
          font: { color: '#111111' },
          size: 18
        },
        facet: {
          color: {
            background: '#F58518',
            border: '#C96F10',
            highlight: '#FF9F1C',
            hover: '#FF9F1C'
          },
          font: { color: '#000000' }
        }
      }
    };

    const network = new vis.Network(container, { nodes: [], edges: [] }, options);

    let selectedFacetIds = new Set();
    let selectedRowId = null;
    let hoveredRowId = null;

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>'"]/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        "'": '&#39;',
        '"': '&quot;'
      })[ch]);
    }

    if (availableDates.length >= 2) {
      noUiSlider.create(slider, {
        start: [0, availableDates.length - 1],
        connect: true,
        step: 1,
        range: { min: 0, max: availableDates.length - 1 }
      });
    } else {
      slider.style.display = 'none';
      timelineToggle.checked = false;
      timelineToggle.disabled = true;
      rangeText.textContent = 'No usable dates found - timeline filter disabled.';
    }

    function currentRange() {
      if (!slider.noUiSlider) return { startDate: null, endDate: null };
      const values = slider.noUiSlider.get().map(v => parseInt(v, 10));
      return {
        startDate: availableDates[values[0]],
        endDate: availableDates[values[1]]
      };
    }

    function rowMatchesTimeline(row, timelineOn, startDate, endDate) {
      if (!timelineOn) return true;
      if (!row.begin && !row.end) return false;
      return row.begin <= endDate && row.end >= startDate;
    }

    function rowMatchesFacets(row) {
      if (!selectedFacetIds.size) return true;
      for (const fid of selectedFacetIds) {
        if (!row.facets.includes(fid)) return false;
      }
      return true;
    }

    function getVisibleRows() {
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();
      return ROWS.filter(row => rowMatchesTimeline(row, timelineOn, startDate, endDate) && rowMatchesFacets(row));
    }

    function getTimelineRows() {
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();
      return ROWS.filter(row => rowMatchesTimeline(row, timelineOn, startDate, endDate));
    }

    function buildFacetCounts(rows) {
      const counts = new Map();
      FACETS.forEach(f => counts.set(f.id, 0));
      rows.forEach(row => {
        row.facets.forEach(fid => {
          if (counts.has(fid)) counts.set(fid, counts.get(fid) + 1);
        });
      });
      return counts;
    }

    function rowLabel(row) {
      const lines = [row.id];
      if (row.display_date) lines.push(row.display_date);
      if (row.box) lines.push(row.box);
      return lines.join('\n');
    }

    function facetNodeSize(count, selected) {
      const base = 10 + Math.pow(Math.max(count, 1), 0.75) * 6.5;
      const boosted = selected ? base + 8 : base;
      return Math.max(10, Math.min(boosted, 56));
    }

    function facetLabel(fid) {
      const facet = FACETS.find(f => f.id === fid);
      return facet ? facet.label : fid;
    }

    function buildNetworkData(rows) {
      const visibleFacetIds = new Set();
      const edges = [];
      const selectedRow = ROWS.find(r => r.id === selectedRowId) || null;
      const hoveredRow = ROWS.find(r => r.id === hoveredRowId) || null;

      const nodes = rows.map(row => {
        row.facets.forEach(fid => visibleFacetIds.add(fid));
        const highlighted = (selectedRow && selectedRow.id === row.id) || (hoveredRow && hoveredRow.id === row.id);
        return {
          id: row.id,
          label: rowLabel(row),
          group: 'row',
          size: highlighted ? 20 : 14,
          font: {
            color: '#111111',
            size: highlighted ? 15 : 14,
            bold: highlighted
          },
          borderWidth: highlighted ? 3 : 1.5,
          color: highlighted
            ? {
                background: '#6B95C8',
                border: '#2F6FDB',
                highlight: { background: '#8AAED4', border: '#2F6FDB' },
                hover: { background: '#7DA4CC', border: '#2F6FDB' }
              }
            : {
                background: '#4C78A8',
                border: '#4C78A8',
                highlight: { background: '#B7D8FF', border: '#2F6FDB' },
                hover: { background: '#6B95C8', border: '#4C78A8' }
              },
          shadow: highlighted ? {
            enabled: true,
            color: 'rgba(47, 111, 219, 0.28)',
            size: 10,
            x: 0,
            y: 0
          } : false,
          title: [
            `<b>Item name:</b> ${escapeHtml(row.id)}`,
            row.display_date ? `<b>Dates:</b> ${escapeHtml(row.display_date)}` : '<b>Dates:</b> undated',
            row.container_instance ? `<b>Container instance:</b> ${escapeHtml(row.container_instance)}` : '',
            row.scope_content ? `<b>Scope content:</b> ${escapeHtml(row.scope_content)}` : '',
            row.physical_desc ? `<b>Physical desc:</b> ${escapeHtml(row.physical_desc)}` : ''
          ].filter(Boolean).join('<br>')
        };
      });

      FACETS.forEach(f => {
        if (visibleFacetIds.has(f.id) || selectedFacetIds.has(f.id)) {
          const selected = selectedFacetIds.has(f.id);
          const size = facetNodeSize(f.count, selected);
          nodes.push({
            id: f.id,
            label: f.label,
            group: 'facet',
            title: `${escapeHtml(f.label)}<br>${f.count} row${f.count === 1 ? '' : 's'}`,
            size,
            value: size,
            font: {
              color: '#111111',
              size: selected ? 20 : 14,
              bold: selected
            },
            color: selected
              ? {
                  background: '#FFE082',
                  border: '#FF8C00',
                  highlight: { background: '#FFE082', border: '#D97706' },
                  hover: { background: '#FFECB3', border: '#D97706' }
                }
              : {
                  background: '#F58518',
                  border: '#C96F10',
                  highlight: { background: '#FF9F1C', border: '#B85D08' },
                  hover: { background: '#FF9F1C', border: '#B85D08' }
                },
            borderWidth: selected ? 3 : 1.5,
            shadow: selected ? {
              enabled: true,
              color: 'rgba(255, 140, 0, 0.45)',
              size: 12,
              x: 0,
              y: 0
            } : false
          });
        }
      });

      const visibleFacetSet = new Set(nodes.filter(n => n.group === 'facet').map(n => n.id));
      rows.forEach(row => {
        row.facets.forEach(fid => {
          if (visibleFacetSet.has(fid)) {
            edges.push({
              from: row.id,
              to: fid,
              width: selectedFacetIds.has(fid) ? 3 : 1.2,
              color: selectedFacetIds.has(fid) ? { color: '#FFB300' } : { color: '#999' },
              title: `Connected facet: ${fid}`
            });
          }
        });
      });

      return { nodes, edges };
    }

    function getCurrentDetailRow() {
      return ROWS.find(r => r.id === hoveredRowId) || ROWS.find(r => r.id === selectedRowId) || null;
    }

    function renderDetailPanel() {
      const row = getCurrentDetailRow();
      if (!row) {
        detailPanel.classList.add('empty');
        detailPanel.innerHTML = 'Hover or click a file node or row to preview details.';
        return;
      }

      const relatedFacets = (row.facets || []).map(fid => ({ id: fid, label: facetLabel(fid) }));

      detailPanel.classList.remove('empty');
      detailPanel.innerHTML = `
        <div class="detail-row"><span class="detail-label">Item name:</span> <span class="detail-value">${escapeHtml(row.id)}</span></div>
        <div class="detail-row"><span class="detail-label">Dates:</span> <span class="detail-value">${escapeHtml(row.display_date || 'undated')}</span></div>
        <div class="detail-row"><span class="detail-label">Container instance:</span> <span class="detail-value">${escapeHtml(row.container_instance || '—')}</span></div>
        <div class="detail-row"><span class="detail-label">Scope content:</span> <span class="detail-value">${escapeHtml(row.scope_content || '—')}</span></div>
        <div class="detail-row"><span class="detail-label">Physical desc:</span> <span class="detail-value">${escapeHtml(row.physical_desc || '—')}</span></div>
        <div class="detail-related">
          <div class="detail-related-title">Immediately related facets</div>
          ${relatedFacets.length ? `<div class="detail-facet-list">${relatedFacets.map(f => `<button type="button" class="detail-facet-chip ${selectedFacetIds.has(f.id) ? 'selected' : ''}" data-related-facet="${escapeHtml(f.id)}">${escapeHtml(f.label)}</button>`).join('')}</div>` : '<div class="detail-row"><span class="detail-value">No related facets.</span></div>'}
        </div>
      `;

      detailPanel.querySelectorAll('[data-related-facet]').forEach(btn => {
        btn.addEventListener('click', () => {
          const fid = btn.dataset.relatedFacet;
          if (selectedFacetIds.has(fid)) selectedFacetIds.delete(fid);
          else selectedFacetIds.add(fid);
          render();
        });
      });
    }

    function renderFacetList(rows) {
      const counts = buildFacetCounts(rows);
      facetList.innerHTML = FACETS.map(f => {
        const selected = selectedFacetIds.has(f.id);
        return `<button type="button" class="facet-pill ${selected ? 'selected' : ''}" data-facet-id="${f.id}">${escapeHtml(f.label)}<span class="count">${counts.get(f.id) || 0}</span></button>`;
      }).join('');

      facetList.querySelectorAll('[data-facet-id]').forEach(btn => {
        btn.addEventListener('click', () => {
          const fid = btn.dataset.facetId;
          if (selectedFacetIds.has(fid)) selectedFacetIds.delete(fid);
          else selectedFacetIds.add(fid);
          selectedRowId = null;
          hoveredRowId = null;
          render();
        });
      });

      clearFacets.disabled = selectedFacetIds.size === 0;
      selectionInfo.textContent = selectedFacetIds.size
        ? `Selected facets: ${Array.from(selectedFacetIds).join(', ')}`
        : 'No facets selected.';
    }

    function renderRowList(rows) {
      rowCount.textContent = `${rows.length} matching row${rows.length === 1 ? '' : 's'}`;
      emptyState.style.display = rows.length ? 'none' : 'block';

      rowList.innerHTML = rows.map(row => {
        const selected = row.id === selectedRowId;
        const matched = row.facets.filter(fid => selectedFacetIds.has(fid)).join(', ');
        return `
          <button type="button" class="sidebar-item ${selected ? 'selected' : ''}" data-row-id="${row.id}">
            <span class="row-id">${escapeHtml(row.id)}</span>
            <div class="row-date">${escapeHtml(row.display_date || 'undated')}</div>
            ${row.box ? `<div class="row-box">${escapeHtml(row.box)}</div>` : ''}
            ${matched ? `<div class="row-match">Matches: ${escapeHtml(matched)}</div>` : ''}
          </button>
        `;
      }).join('');

      rowList.querySelectorAll('[data-row-id]').forEach(btn => {
        const rowId = btn.dataset.rowId;
        btn.addEventListener('mouseenter', () => {
          hoveredRowId = rowId;
          renderDetailPanel();
        });
        btn.addEventListener('mouseleave', () => {
          if (hoveredRowId === rowId && selectedRowId !== rowId) {
            hoveredRowId = null;
            renderDetailPanel();
          }
        });
        btn.addEventListener('click', () => {
          selectedRowId = rowId;
          hoveredRowId = rowId;
          network.focus(selectedRowId, { scale: 1.2, animation: true });
          render();
        });
      });
    }

    function render() {
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();
      const timelineRows = getTimelineRows();
      const visibleRows = getVisibleRows();

      renderFacetList(timelineRows);
      renderRowList(visibleRows);
      renderDetailPanel();

      const data = buildNetworkData(visibleRows);
      network.setData({
        nodes: new vis.DataSet(data.nodes),
        edges: new vis.DataSet(data.edges)
      });

      if (selectedRowId && !visibleRows.some(r => r.id === selectedRowId)) {
        selectedRowId = null;
      }
      if (hoveredRowId && !visibleRows.some(r => r.id === hoveredRowId)) {
        hoveredRowId = null;
      }

      if (!timelineOn) {
        rangeText.innerHTML = selectedFacetIds.size
          ? `Timeline disabled - spotlighting <span class="facet-spotlight">${Array.from(selectedFacetIds).join(', ')}</span>.`
          : 'Timeline disabled - showing everything, including records without dates.';
      } else {
        rangeText.innerHTML = selectedFacetIds.size
          ? `Timeline enabled - ${startDate} to ${endDate} - spotlighting <span class="facet-spotlight">${Array.from(selectedFacetIds).join(', ')}</span>.`
          : `Timeline enabled - showing records active from ${startDate} to ${endDate}.`;
      }
    }

    network.on('click', function(params) {
      if (params.nodes.length === 1) {
        const clickedId = params.nodes[0];
        const isFacet = FACETS.some(f => f.id === clickedId);
        if (isFacet) {
          if (selectedFacetIds.has(clickedId)) selectedFacetIds.delete(clickedId);
          else selectedFacetIds.add(clickedId);
          selectedRowId = null;
          hoveredRowId = null;
          render();
          return;
        }

        selectedRowId = clickedId;
        hoveredRowId = clickedId;
        render();
        return;
      }

      if (params.nodes.length === 0 && params.edges.length === 0) {
        selectedFacetIds.clear();
        selectedRowId = null;
        hoveredRowId = null;
        render();
      }
    });

    network.on('hoverNode', function(params) {
      const hoveredId = params.node;
      const isFacet = FACETS.some(f => f.id === hoveredId);
      if (!isFacet) {
        hoveredRowId = hoveredId;
        renderDetailPanel();
      }
    });

    network.on('blurNode', function(params) {
      const blurredId = params.node;
      if (hoveredRowId === blurredId && selectedRowId !== blurredId) {
        hoveredRowId = null;
        renderDetailPanel();
      }
    });

    clearFacets.addEventListener('click', function() {
      selectedFacetIds.clear();
      selectedRowId = null;
      hoveredRowId = null;
      render();
    });

    if (slider.noUiSlider) {
      slider.noUiSlider.on('update', render);
    }

    timelineToggle.addEventListener('change', function() {
      slider.style.display = this.checked ? 'block' : 'none';
      render();
    });

    timelineToggle.checked = false;
    slider.style.display = 'none';
    render();

    let lastModified = null;

    async function checkForUpdate() {
      try {
        const response = await fetch(window.location.href, { method: 'HEAD' });
        const newModified = response.headers.get('Last-Modified');
        if (lastModified && newModified !== lastModified) {
          location.reload();
        }
        lastModified = newModified;
      } catch (e) {
        console.warn('Update check failed', e);
      }
    }

    setInterval(checkForUpdate, 2000);
  </script>

  <div id="copyright">Rhizomatica. Copyright 2026, Evan Brandon.</div>
</body>
</html>
'''


def build_html(collection_title, rows, facets):
    dates = sorted({d for row in rows for d in [row['begin'], row['end']] if d})
    html = HTML_TEMPLATE.replace("__TITLE__", collection_title)
    html = html.replace("__ROWS__", json.dumps(rows, ensure_ascii=False))
    html = html.replace("__FACETS__", json.dumps(facets, ensure_ascii=False))
    html = html.replace("__DATES__", json.dumps(dates, ensure_ascii=False))
    return html


def main(argv=None):
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

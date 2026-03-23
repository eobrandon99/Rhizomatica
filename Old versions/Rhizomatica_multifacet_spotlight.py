#!/usr/bin/env python3
import json
import re
import webbrowser
from pathlib import Path

import pandas as pd

INPUT_FILE = Path(__file__).with_name('facets.xlsx')
OUTPUT_FILE = Path(__file__).with_name('network_output.html')


def find_column(columns, aliases, label, required=True):
    lookup = {c.strip().lower(): c for c in columns}
    for alias in aliases:
        key = alias.strip().lower()
        if key in lookup:
            return lookup[key]
    if required:
        raise ValueError(f'Could not find a column for {label}. Available columns: {list(columns)}')
    return None


def as_text(value):
    if pd.isna(value):
        return ''
    text = str(value).strip()
    return '' if text.lower() in {'nan', 'nat', 'none', 'null'} else text


def parse_yearish(value, is_end=False):
    if pd.isna(value):
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value.normalize()
    if isinstance(value, (int, float)) and not pd.isna(value):
        n = int(value)
        if 1000 <= n <= 9999:
            return pd.Timestamp(f'{n}-12-31' if is_end else f'{n}-01-01')
    s = str(value).strip()
    if not s or s.lower() in {'nan', 'nat', 'none', 'null'}:
        return pd.NaT
    if re.fullmatch(r'\d{4}\.0', s):
        s = s[:-2]
    if re.fullmatch(r'\d{4}', s):
        return pd.Timestamp(f'{s}-12-31' if is_end else f'{s}-01-01')
    match = re.search(r'(1[5-9]\d{2}|20\d{2})', s)
    if match:
        year = match.group(1)
        return pd.Timestamp(f'{year}-12-31' if is_end else f'{year}-01-01')
    parsed = pd.to_datetime(s, errors='coerce')
    return parsed.normalize() if pd.notna(parsed) else pd.NaT


def is_active(val):
    if pd.isna(val):
        return False
    if isinstance(val, str):
        return val.strip().upper() == 'YES'
    return bool(val)


def format_box(value):
    text = as_text(value)
    if not text:
        return ''
    return f'[{text}]'


def build_rows_and_facets(df, id_column, date_expression_column, date_begin_column, date_end_column, title_column, physical_column, facet_columns):
    rows = []
    facet_counts = {facet: 0 for facet in facet_columns}

    for _, row in df.iterrows():
        row_id = as_text(row[id_column])
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
            display_date = f'{begin.date().isoformat()} to {end.date().isoformat()}'
        elif pd.notna(begin):
            display_date = begin.date().isoformat()
        else:
            display_date = 'undated'

        box = format_box(row[physical_column]) if physical_column else ''
        facets = [facet for facet in facet_columns if is_active(row[facet])]
        for facet in facets:
            facet_counts[facet] += 1

        rows.append({
            'id': row_id,
            'display_date': display_date,
            'box': box,
            'begin': begin.date().isoformat() if pd.notna(begin) else '',
            'end': end.date().isoformat() if pd.notna(end) else '',
            'facets': facets,
            'title': as_text(row[title_column]) if title_column else '',
            'date_expr': date_expr,
        })

    facets = []
    for facet in facet_columns:
        facets.append({'id': facet, 'label': facet, 'count': facet_counts.get(facet, 0)})

    return rows, facets


def build_html(collection_title):
    return r'''<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>__TITLE__</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/noUiSlider/15.7.1/nouislider.min.css">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/noUiSlider/15.7.1/nouislider.min.js"></script>
  <style>
    body { margin: 0; font-family: Arial, sans-serif; background: #fff; }
    #controls { padding: 10px 14px; border-bottom: 1px solid #ddd; background: #f7f7f7; }
    #title { font-size: 24px; font-weight: 700; margin-bottom: 8px; color: #222; }
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
    #rangeText { margin-top: 6px; font-size: 13px; line-height: 1.35; color: #333; }
    .toggle-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 6px;
      user-select: none;
      font-size: 13px;
    }
    .facet-spotlight, .row-spotlight {
      display: inline-block;
      padding: 1px 6px;
      border-radius: 999px;
      background: #FFF2B3;
      border: 1px solid #FFB300;
      font-weight: 700;
    }
    #page {
      display: flex;
      width: 100%;
      height: calc(100vh - 176px);
      min-height: 500px;
    }
    #sidebar {
      width: 340px;
      border-right: 1px solid #ddd;
      background: #fafafa;
      padding: 12px;
      box-sizing: border-box;
      overflow: auto;
    }
    #container { flex: 1; height: 100%; }
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
      transition: transform .15s ease, background .15s ease, border-color .15s ease;
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
    .sidebar-item {
      width: 100%;
      text-align: left;
      background: #fff;
      border: 1px solid #e4e4e4;
      border-radius: 10px;
      padding: 8px 10px;
      cursor: pointer;
      transition: transform .15s ease, background .15s ease, border-color .15s ease;
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
    <aside id="sidebar">
      <div id="facetHeader">Facet spotlight</div>
      <div id="facetList"></div>
      <div id="rowHeader">Related files</div>
      <div id="rowCount"></div>
      <div id="rowList"></div>
      <div id="emptyState" style="display:none;">No rows match the current selection.</div>
    </aside>
    <div id="container"></div>
  </div>

  <script>
    const allNodes = new vis.DataSet(__NODES__);
    const allEdges = new vis.DataSet(__EDGES__);
    const availableDates = __DATES__;
    const FACETS = __FACETS__;

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
          font: { color: '#ffffff' },
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
      return __ROWS__.filter(row => rowMatchesTimeline(row, timelineOn, startDate, endDate) && rowMatchesFacets(row));
    }

    function getTimelineRows() {
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();
      return __ROWS__.filter(row => rowMatchesTimeline(row, timelineOn, startDate, endDate));
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

    function buildNetworkData(rows) {
      const visibleFacetIds = new Set();
      const edges = [];

      const nodes = rows.map(row => {
        row.facets.forEach(fid => visibleFacetIds.add(fid));
        return {
          id: row.id,
          label: rowLabel(row),
          group: 'row',
          title: [
            `<b>ID:</b> ${row.id}`,
            `<b>Date:</b> ${row.display_date || 'undated'}`,
            row.box ? `<b>Box:</b> ${row.box}` : ''
          ].filter(Boolean).join('<br>')
        };
      });

      FACETS.forEach(f => {
        if (visibleFacetIds.has(f.id) || selectedFacetIds.has(f.id)) {
          nodes.push({
            id: f.id,
            label: f.label,
            group: 'facet',
            title: `${f.label}<br>${f.count} row${f.count === 1 ? '' : 's'}`
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

    function renderFacetList(rows) {
      const counts = buildFacetCounts(rows);
      facetList.innerHTML = FACETS.map(f => {
        const selected = selectedFacetIds.has(f.id);
        return `<button type="button" class="facet-pill ${selected ? 'selected' : ''}" data-facet-id="${f.id}">${f.label}<span class="count">${counts.get(f.id) || 0}</span></button>`;
      }).join('');

      facetList.querySelectorAll('[data-facet-id]').forEach(btn => {
        btn.addEventListener('click', () => {
          const fid = btn.dataset.facetId;
          if (selectedFacetIds.has(fid)) selectedFacetIds.delete(fid);
          else selectedFacetIds.add(fid);
          selectedRowId = null;
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
            <span class="row-id">${row.id}</span>
            <div class="row-date">${row.display_date || 'undated'}</div>
            ${row.box ? `<div class="row-box">${row.box}</div>` : ''}
            ${matched ? `<div class="row-match">Matches: ${matched}</div>` : ''}
          </button>
        `;
      }).join('');

      rowList.querySelectorAll('[data-row-id]').forEach(btn => {
        btn.addEventListener('click', () => {
          selectedRowId = btn.dataset.rowId;
          network.focus(selectedRowId, { scale: 1.2, animation: true });
          render();
        });
      });
    }

    function applyNodeHighlight() {
      const updated = allNodes.get().map(node => {
        if (node.group === 'facet') {
          if (selectedFacetIds.has(node.id)) {
            return {
              ...node,
              font: { size: 18, bold: true },
              color: { background: '#FFE082', border: '#FF8C00' }
            };
          }
          return {
            ...node,
            font: { size: 14, bold: false },
            color: {
              background: '#F58518',
              border: '#C96F10',
              highlight: '#FF9F1C',
              hover: '#FF9F1C'
            }
          };
        }

        if (node.group === 'row') {
          const active = selectedFacetIds.size === 0 || __ROWS__.some(r => r.id === node.id && rowMatchesFacets(r));
          if (active) {
            return {
              ...node,
              font: { size: 14, bold: false },
              color: {
                background: selectedFacetIds.size ? '#6B95C8' : '#4C78A8',
                border: selectedFacetIds.size ? '#2F6FDB' : '#4C78A8'
              }
            };
          }
        }

        return node;
      });

      allNodes.update(updated);
    }

    function render() {
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();
      const timelineRows = getTimelineRows();
      const visibleRows = getVisibleRows();

      applyNodeHighlight();
      renderFacetList(timelineRows);
      renderRowList(visibleRows);

      const data = buildNetworkData(visibleRows);
      network.setData({
        nodes: new vis.DataSet(data.nodes),
        edges: new vis.DataSet(data.edges)
      });

      if (selectedRowId && !visibleRows.some(r => r.id === selectedRowId)) {
        selectedRowId = null;
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
        const clicked = allNodes.get(params.nodes[0]);
        if (!clicked) return;
        if (clicked.group === 'facet') {
          if (selectedFacetIds.has(clicked.id)) selectedFacetIds.delete(clicked.id);
          else selectedFacetIds.add(clicked.id);
          selectedRowId = null;
          render();
          return;
        }
        if (clicked.group === 'row') {
          selectedRowId = clicked.id;
          render();
          return;
        }
      }
    });

    clearFacets.addEventListener('click', function() {
      selectedFacetIds.clear();
      selectedRowId = null;
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


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f'Expected facets.xlsx next to script, but not found: {INPUT_FILE}')

    df = pd.read_excel(INPUT_FILE)
    df.columns = df.columns.str.strip()

    print('Columns:', list(df.columns))
    print('Rows:', len(df))

    aliases = {
        'id': ['ID', 'id', 'Identifier', 'identifier', 'Row', 'row'],
        'date_expression': ['date_expression', 'date expression', 'Date Expression', 'date', 'Date'],
        'date_begin': ['date_begin', 'date begin', 'Date Begin', 'begin', 'Start', 'start_date'],
        'date_end': ['date_end', 'date end', 'Date End', 'end', 'Finish', 'end_date'],
        'collection_title': ['Collection title', 'collection title', 'collection_title', 'title', 'Title'],
        'physical_instance': ['physical_instance', 'physical instance', 'container_instance', 'container instance', 'box', 'Box', 'box address', 'Box Address'],
    }

    id_column = find_column(df.columns, aliases['id'], 'ID')
    date_expression_column = find_column(df.columns, aliases['date_expression'], 'date expression')
    date_begin_column = find_column(df.columns, aliases['date_begin'], 'date begin')
    date_end_column = find_column(df.columns, aliases['date_end'], 'date end')
    title_column = find_column(df.columns, aliases['collection_title'], 'Collection title', required=False)
    physical_column = find_column(df.columns, aliases['physical_instance'], 'physical_instance', required=False)

    print('Using columns:')
    print('  ID:', id_column)
    print('  Date expression:', date_expression_column)
    print('  Begin:', date_begin_column)
    print('  End:', date_end_column)
    print('  Collection title:', title_column if title_column else '(none)')
    print('  Physical instance:', physical_column if physical_column else '(none)')

    facet_columns = [c for c in df.columns if c not in [id_column, date_expression_column, date_begin_column, date_end_column, title_column, physical_column]]
    print('Facet columns:', facet_columns)

    collection_title = 'Rhizomatica'
    if title_column:
        title_series = df[title_column].dropna().astype(str).str.strip()
        title_series = title_series[title_series != '']
        if not title_series.empty:
            collection_title = title_series.iloc[0]

    rows, facets = build_rows_and_facets(
        df=df,
        id_column=id_column,
        date_expression_column=date_expression_column,
        date_begin_column=date_begin_column,
        date_end_column=date_end_column,
        title_column=title_column,
        physical_column=physical_column,
        facet_columns=facet_columns,
    )

    if not rows:
        raise ValueError('No rows were loaded from the spreadsheet.')

    nodes = []
    edges = []

    for row in rows:
        row_id = row['id']
        node = {
            'id': row_id,
            'label': '\n'.join([x for x in [row_id, row['display_date'], row['box']] if x]),
            'group': 'row',
            'title': (
                f"<b>ID:</b> {row_id or 'none'}<br>"
                f"<b>Date:</b> {row['display_date'] or 'undated'}<br>"
                f"<b>Begin:</b> {row['begin'] or 'none'}<br>"
                f"<b>End:</b> {row['end'] or 'none'}<br>"
                f"<b>Physical instance:</b> {row['box'][1:-1] if row['box'] else 'none'}"
            ),
            'begin': row['begin'],
            'end': row['end'],
            'facets': row['facets'],
        }
        nodes.append(node)

        for facet in row['facets']:
            edges.append({'from': row_id, 'to': facet})

    for facet in facets:
        size = 10 + (facet['count'] ** 0.75) * 6.5
        size = max(10, min(size, 48))
        nodes.append({
            'id': facet['id'],
            'label': facet['label'],
            'group': 'facet',
            'title': f"{facet['label']}<br>{facet['count']} row{'s' if facet['count'] != 1 else ''}",
            'value': size,
            'size': size,
        })

    date_values = sorted(
        set([r['begin'] for r in rows if r['begin']]) |
        set([r['end'] for r in rows if r['end']])
    )

    if len(date_values) == 0:
        date_values = ['1900-01-01', '1900-01-02']
        print('No usable dates could be parsed. Using placeholder dates so the timeline UI still loads.')
    elif len(date_values) == 1:
        date_values = [date_values[0], date_values[0]]
        print('Only one unique date found; duplicating it so the slider can initialize.')

    print('Generated rows:', len(rows))
    print('Generated facets:', len(facets))
    print('Generated edges:', len(edges))
    print('Unique date values:', date_values)

    html = build_html(collection_title)
    html = html.replace('__TITLE__', collection_title)
    html = html.replace('__ROWS__', json.dumps(rows))
    html = html.replace('__FACETS__', json.dumps(facets))
    html = html.replace('__NODES__', json.dumps(nodes))
    html = html.replace('__EDGES__', json.dumps(edges))
    html = html.replace('__DATES__', json.dumps(date_values))

    OUTPUT_FILE.write_text(html, encoding='utf-8')
    print(f'Generated: {OUTPUT_FILE}')
    try:
      webbrowser.open(OUTPUT_FILE.resolve().as_uri())
    except Exception as exc:
      print(f'Could not open browser automatically: {exc}')


if __name__ == '__main__':
    main()

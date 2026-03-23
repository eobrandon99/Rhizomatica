import json
import re
from pathlib import Path

import pandas as pd

INPUT_FILE = Path(__file__).with_name("facets.xlsx")
OUTPUT_FILE = Path(__file__).with_name("network_output.html")

if not INPUT_FILE.exists():
    raise FileNotFoundError(f"Expected facets.xlsx next to script, but not found: {INPUT_FILE}")

df = pd.read_excel(INPUT_FILE)
df.columns = df.columns.str.strip()

print("Columns:", list(df.columns))
print("Rows:", len(df))

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

def find_column(columns, aliases, label, required=True):
    lookup = {c.strip().lower(): c for c in columns}
    for alias in aliases:
        key = alias.strip().lower()
        if key in lookup:
            return lookup[key]
    if required:
        raise ValueError(f"Could not find a column for {label}. Available columns: {list(columns)}")
    return None

id_column = find_column(df.columns, COLUMN_ALIASES["id"], "ID")
date_expression_column = find_column(df.columns, COLUMN_ALIASES["date_expression"], "date expression")
date_begin_column = find_column(df.columns, COLUMN_ALIASES["date_begin"], "date begin")
date_end_column = find_column(df.columns, COLUMN_ALIASES["date_end"], "date end")
title_column = find_column(df.columns, COLUMN_ALIASES["collection_title"], "Collection title", required=False)
physical_column = find_column(df.columns, COLUMN_ALIASES["physical_instance"], "physical_instance", required=False)

print("Using columns:")
print("  ID:", id_column)
print("  Date expression:", date_expression_column)
print("  Begin:", date_begin_column)
print("  End:", date_end_column)
print("  Collection title:", title_column if title_column else "(none)")
print("  Physical instance:", physical_column if physical_column else "(none)")

facet_columns = [
    c for c in df.columns
    if c not in [id_column, date_expression_column, date_begin_column, date_end_column, title_column, physical_column]
]
print("Facet columns:", facet_columns)

collection_title = "Collection Network"
if title_column:
    title_series = df[title_column].dropna().astype(str).str.strip()
    title_series = title_series[title_series != ""]
    if not title_series.empty:
        collection_title = title_series.iloc[0]

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
    if pd.notna(parsed):
        return parsed.normalize()
    return pd.NaT

def is_active(val):
    if pd.isna(val):
        return False
    if isinstance(val, str):
        return val.strip().upper() == "YES"
    return bool(val)

def as_text(value):
    if pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none", "null"} else text

def format_box(value):
    text = as_text(value)
    if not text:
        return ""
    if text.lower().startswith("box"):
        return f"[{text}]"
    return f"[{text}]"

# Dates
df["_begin"] = df[date_begin_column].apply(lambda x: parse_yearish(x, is_end=False))
df["_end"] = df[date_end_column].apply(lambda x: parse_yearish(x, is_end=True))
expr_dates = df[date_expression_column].apply(lambda x: parse_yearish(x, is_end=False))
df["_begin"] = df["_begin"].fillna(expr_dates)
df["_end"] = df["_end"].fillna(expr_dates)
df["_end"] = df["_end"].fillna(df["_begin"])
df["has_date"] = df["_begin"].notna() | df["_end"].notna()

date_values = sorted(
    set(df["_begin"].dropna().dt.strftime("%Y-%m-%d").tolist())
    | set(df["_end"].dropna().dt.strftime("%Y-%m-%d").tolist())
)

if len(date_values) == 0:
    date_values = ["1900-01-01", "1900-01-02"]
    print("No usable dates could be parsed. Using placeholder dates so the timeline UI still loads.")
elif len(date_values) == 1:
    date_values = [date_values[0], date_values[0]]
    print("Only one unique date found; duplicating it so the slider can initialize.")

print("Parsed begin dates:", df["_begin"].dt.strftime("%Y-%m-%d").tolist())
print("Parsed end dates:", df["_end"].dt.strftime("%Y-%m-%d").tolist())
print("Unique date values:", date_values)

# Nodes and edges
nodes = {}
edges = []
facet_counts = {facet: 0 for facet in facet_columns}

for _, row in df.iterrows():
    for facet in facet_columns:
        if is_active(row[facet]):
            facet_counts[facet] += 1

for idx, row in df.iterrows():
    row_id = f"row_{idx}"
    row_label = as_text(row[id_column])

    date_expr = as_text(row[date_expression_column])
    begin_iso = row["_begin"].date().isoformat() if pd.notna(row["_begin"]) else ""
    end_iso = row["_end"].date().isoformat() if pd.notna(row["_end"]) else ""
    physical_instance = format_box(row[physical_column]) if physical_column else ""

    label_lines = [row_label]
    if date_expr:
        label_lines.append(date_expr)
    elif begin_iso and end_iso and begin_iso != end_iso:
        label_lines.append(f"{begin_iso} to {end_iso}")
    else:
        label_lines.append(begin_iso or end_iso or "undated")

    if physical_instance:
        label_lines.append(physical_instance)

    nodes[row_id] = {
        "id": row_id,
        "label": "\n".join([line for line in label_lines if line]),
        "group": "row",
        "title": (
            f"<b>ID:</b> {row_label or 'none'}<br>"
            f"<b>Date expression:</b> {date_expr or 'none'}<br>"
            f"<b>Begin:</b> {begin_iso or 'none'}<br>"
            f"<b>End:</b> {end_iso or 'none'}<br>"
            f"<b>Physical instance:</b> {as_text(row[physical_column]) if physical_column else 'none'}"
        ),
        "begin": begin_iso,
        "end": end_iso,
        "hasDate": bool(row["has_date"]),
    }

    for facet in facet_columns:
        if not is_active(row[facet]):
            continue
        facet_id = f"facet_{facet}"
        if facet_id not in nodes:
            count = facet_counts.get(facet, 0)
            # Slightly larger baseline with a gentle growth curve so big facets stand out.
            facet_size = 10 + (count ** 0.75) * 6.5
            facet_size = max(10, min(facet_size, 48))
            nodes[facet_id] = {
                "id": facet_id,
                "label": str(facet),
                "group": "facet",
                "title": f"{facet}<br>{count} row{'s' if count != 1 else ''}",
                "value": facet_size,
                "size": facet_size,
            }
        edges.append({"from": row_id, "to": facet_id})

nodes_list = list(nodes.values())
if not edges:
    raise ValueError("No active facet edges were found. Check that facet cells contain YES or true values.")

html = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Timeline Network</title>
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
    #pathLabel {
      font-size: 13px;
      color: #555;
    }
    #page {
      display: flex;
      width: 100%;
      height: 80vh;
    }
    #sidebar {
      width: 320px;
      border-right: 1px solid #ddd;
      background: #fafafa;
      padding: 12px 12px 16px 12px;
      box-sizing: border-box;
      display: none;
      overflow: auto;
    }
    #sidebar h3 {
      margin: 0 0 8px 0;
      font-size: 15px;
    }
    #sidebarMeta {
      font-size: 12px;
      color: #666;
      margin-bottom: 10px;
    }
    #rowList {
      list-style: none;
      padding: 0;
      margin: 0;
    }
    #rowList li {
      border: 1px solid #e4e4e4;
      background: #fff;
      border-radius: 10px;
      padding: 8px 10px;
      margin-bottom: 8px;
      font-size: 13px;
      line-height: 1.35;
    }
    #rowList .row-id {
      font-weight: 700;
      display: block;
      margin-bottom: 2px;
    }
    #rowList .row-box {
      color: #666;
    }
    #rowList .row-date {
      color: #333;
    }
    #container { flex: 1; height: 100%; }
    #sliderWrap { max-width: 430px; }
    #slider { margin: 8px 0 6px 0; }
    #rangeText { margin-top: 6px; font-size: 13px; line-height: 1.35; color: #333; }
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
  </style>
</head>
<body>
  <div id="controls">
    <div id="title">__COLLECTION_TITLE__</div>
    <div><strong>Select active date range</strong></div>
    <div class="toggle-row">
      <input type="checkbox" id="timelineToggle">
      <label for="timelineToggle">Enable timeline filter</label>
    </div>

    <div id="sliderWrap">
      <div id="slider"></div>
    </div>

    <div id="toolbar">
      <button id="pathBack" class="toolbar-btn" disabled>Back</button>
      <button id="pathForward" class="toolbar-btn" disabled>Forward</button>
      <button id="pathClear" class="toolbar-btn">Clear path</button>
      <span id="pathLabel">No clicks yet.</span>
    </div>

    <div id="rangeText">Timeline disabled — showing everything, including records without dates.</div>
  </div>

  <div id="page">
    <aside id="sidebar">
      <h3 id="sidebarTitle">Connected rows</h3>
      <div id="sidebarMeta">Spotlight a facet to see related rows.</div>
      <ul id="rowList"></ul>
    </aside>
    <div id="container"></div>
  </div>

  <script>
    const allNodes = new vis.DataSet(__NODES__);
    const allEdges = new vis.DataSet(__EDGES__);
    const availableDates = __DATES__;

    const container = document.getElementById("container");
    const slider = document.getElementById("slider");
    const rangeText = document.getElementById("rangeText");
    const timelineToggle = document.getElementById("timelineToggle");
    const pathBack = document.getElementById("pathBack");
    const pathForward = document.getElementById("pathForward");
    const pathClear = document.getElementById("pathClear");
    const pathLabel = document.getElementById("pathLabel");
    const sidebar = document.getElementById("sidebar");
    const sidebarTitle = document.getElementById("sidebarTitle");
    const sidebarMeta = document.getElementById("sidebarMeta");
    const rowList = document.getElementById("rowList");

    const options = {
      interaction: {
        hover: true
      },
      chosen: {
        node: function (values, id, selected, hovering) {
          const node = allNodes.get(id);
          if (node && node.group === "facet" && (selected || hovering)) {
            values.color = "#FF9F1C";
            values.borderColor = "#FF7A00";
            values.borderWidth = 2;
            values.fontColor = "#000000";
          }
        }
      },

      nodes: {
        shape: "dot",
        size: 14
      },
      edges: {
        smooth: true,
        color: { color: "#999" },
        length: 180,
        width: 1.2
      },
      physics: {
        enabled: true,
        solver: "forceAtlas2Based",
        forceAtlas2Based: {
          gravitationalConstant: -80,
          centralGravity: 0.02,
          springLength: 130,
          springConstant: 0.08,
          damping: 0.65,
          avoidOverlap: 0.15
        },
        stabilization: {
          iterations: 220,
          fit: true
        },
        maxVelocity: 12,
        minVelocity: 0.35,
        timestep: 0.35
      },
      groups: {
        row: {
          color: {
            background: "#4C78A8",
            highlight: { background: "#4C78A8" },
            hover: { background: "#6B95C8" }
          },
          size: 18
        },
        facet: {
          color: {
            background: "#F58518",
            border: "#C96F10",
            highlight: "#FF9F1C",
            hover: "#FF9F1C"
          }
        }
      }
    };

    const network = new vis.Network(container, { nodes: [], edges: [] }, options);

    let spotlightNode = null;
    let clickPath = [];
    let pathIndex = -1;

    if (availableDates.length >= 2) {
      noUiSlider.create(slider, {
        start: [0, availableDates.length - 1],
        connect: true,
        step: 1,
        range: { min: 0, max: availableDates.length - 1 }
      });
    } else {
      slider.style.display = "none";
      timelineToggle.checked = false;
      timelineToggle.disabled = true;
      rangeText.textContent = "No usable dates found — timeline filter disabled.";
    }

    function currentRange() {
      if (!slider.noUiSlider) {
        return { startDate: null, endDate: null };
      }
      const values = slider.noUiSlider.get().map(v => parseInt(v, 10));
      return {
        startDate: availableDates[values[0]],
        endDate: availableDates[values[1]]
      };
    }

    function updatePathToolbar() {
      const hasHistory = pathIndex >= 0 && clickPath.length > 0;
      pathBack.disabled = !hasHistory || pathIndex <= 0;
      pathForward.disabled = !hasHistory || pathIndex >= clickPath.length - 1;

      if (!hasHistory) {
        pathLabel.textContent = "No clicks yet.";
        return;
      }

      const current = allNodes.get(clickPath[pathIndex]);
      pathLabel.textContent = `Path ${pathIndex + 1} of ${clickPath.length}: ${current ? current.label : clickPath[pathIndex]}`;
    }

    function pushPath(nodeId) {
      if (!nodeId) return;
      if (pathIndex >= 0 && clickPath[pathIndex] === nodeId) return;
      clickPath = clickPath.slice(0, pathIndex + 1);
      clickPath.push(nodeId);
      pathIndex = clickPath.length - 1;
      updatePathToolbar();
    }

    function getConnectedRowsForFacet(facetId, timelineOn, startDate, endDate) {
      const rows = [];
      const rowIds = new Set();

      allEdges.get().forEach(edge => {
        if (edge.to === facetId) {
          const rowNode = allNodes.get(edge.from);
          if (!rowNode || rowNode.group !== "row") return;

          if (!timelineOn || !rowNode.hasDate || (rowNode.begin <= endDate && rowNode.end >= startDate)) {
            if (!rowIds.has(rowNode.id)) {
              rowIds.add(rowNode.id);
              rows.push(rowNode);
            }
          }
        }
      });

      rows.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
      return rows;
    }

    function getRelatedFacetsForRow(rowId) {
      const facets = [];
      const facetIds = new Set();

      allEdges.get().forEach(edge => {
        if (edge.from === rowId) {
          const facetNode = allNodes.get(edge.to);
          if (!facetNode || facetNode.group !== "facet") return;
          if (!facetIds.has(facetNode.id)) {
            facetIds.add(facetNode.id);
            facets.push(facetNode);
          }
        }
      });

      facets.sort((a, b) => (a.label || "").localeCompare(b.label || ""));
      return facets;
    }

    function renderSidebarForFacet(facetId, timelineOn, startDate, endDate) {
      const rows = getConnectedRowsForFacet(facetId, timelineOn, startDate, endDate);

      sidebar.style.display = "block";
      sidebarTitle.textContent = "Connected rows";
      sidebarMeta.textContent = `${rows.length} connected row${rows.length === 1 ? "" : "s"}${timelineOn ? ` in ${startDate} to ${endDate}` : ""}.`;

      if (!rows.length) {
        rowList.innerHTML = "<li>No rows match the current view.</li>";
        return;
      }

      rowList.innerHTML = rows.map(row => {
        const titleMatch = row.title.match(/<b>ID:<\/b> ([^<]+)/);
        const box = row.title.match(/<b>Physical instance:<\/b> ([^<]+)/);
        const dateExpr = row.title.match(/<b>Date expression:<\/b> ([^<]+)/);
        const begin = row.title.match(/<b>Begin:<\/b> ([^<]+)/);
        const end = row.title.match(/<b>End:<\/b> ([^<]+)/);

        const rowId = titleMatch && titleMatch[1] !== "none" ? titleMatch[1] : row.label;
        const dateText = dateExpr && dateExpr[1] !== "none"
          ? dateExpr[1]
          : ((begin && begin[1] !== "none") ? begin[1] : "undated");

        const boxText = box && box[1] !== "none" ? box[1] : "";

        return `
          <li>
            <span class="row-id">${rowId}</span>
            <div class="row-date">${dateText}</div>
            ${boxText ? `<div class="row-box">${boxText}</div>` : ""}
          </li>
        `;
      }).join("");
    }

    function renderSidebarForRow(rowId) {
      const rowNode = allNodes.get(rowId);
      const facets = getRelatedFacetsForRow(rowId);

      sidebar.style.display = "block";
      sidebarTitle.textContent = "Related facets";
      sidebarMeta.textContent = `${facets.length} related facet${facets.length === 1 ? "" : "s"}.`;

      if (!facets.length) {
        rowList.innerHTML = "<li>No related facets.</li>";
        return;
      }

      rowList.innerHTML = facets.map(facet => `
        <li>
          <span class="row-id">${facet.label}</span>
        </li>
      `).join("");
    }

    function applyNodeHighlight() {
      const updated = allNodes.get().map(node => {
        if (node.group === "facet") {
          if (node.id === spotlightNode) {
            return {
              ...node,
              font: { size: 18, bold: true },
              color: {
                background: "#FFE082",
                border: "#FF8C00"
              }
            };
          }
          return {
            ...node,
            font: { size: 14, bold: false },
            color: {
              background: "#F58518",
              border: "#C96F10",
              highlight: "#FF9F1C",
              hover: "#FF9F1C"
            }
          };
        }

        if (node.group === "row") {
          if (node.id === spotlightNode) {
            return {
              ...node,
              font: { size: 16, bold: true },
              color: {
                background: "#A9C8FF",
                border: "#2F6FDB"
              }
            };
          }
          return {
            ...node,
            font: { size: 14, bold: false },
            color: {
              background: "#4C78A8",
              border: "#4C78A8"
            }
          };
        }

        return node;
      });

      allNodes.update(updated);
    }

    function render() {
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();

      applyNodeHighlight();

      if (!timelineOn) {
        sidebar.style.display = "none";
        rangeText.innerHTML = spotlightNode
          ? `Timeline disabled — spotlighting <span class="row-spotlight">${allNodes.get(spotlightNode).label}</span>`
          : "Timeline disabled — showing everything, including records without dates.";
      } else {
        rangeText.innerHTML = spotlightNode
          ? `Timeline enabled — ${startDate} to ${endDate} • spotlight: <span class="row-spotlight">${allNodes.get(spotlightNode).label}</span>`
          : `Timeline enabled — showing records active from ${startDate} to ${endDate}.`;
      }

      const visibleRowIds = new Set();
      const visibleFacetIds = new Set();
      const visibleEdges = [];
      const spotlight = spotlightNode ? allNodes.get(spotlightNode) : null;

      if (spotlight) {
        if (spotlight.group === "facet") {
          allEdges.get().forEach(edge => {
            if (edge.to === spotlight.id) {
              const rowNode = allNodes.get(edge.from);
              if (!rowNode || rowNode.group !== "row") return;

              if (!timelineOn) {
                visibleRowIds.add(edge.from);
              } else if (rowNode.hasDate && rowNode.begin <= endDate && rowNode.end >= startDate) {
                visibleRowIds.add(edge.from);
              }
            }
          });

          allEdges.get().forEach(edge => {
            if (visibleRowIds.has(edge.from)) {
              visibleEdges.push(edge);
              visibleFacetIds.add(edge.to);
            }
          });

          visibleFacetIds.add(spotlight.id);

          renderSidebarForFacet(spotlight.id, timelineOn, startDate, endDate);
        } else if (spotlight.group === "row") {
          renderSidebarForRow(spotlight.id);
          const rowFacets = new Set();
          allEdges.get().forEach(edge => {
            if (edge.from === spotlight.id) {
              rowFacets.add(edge.to);
              visibleFacetIds.add(edge.to);
            }
          });

          visibleRowIds.add(spotlight.id);

          allEdges.get().forEach(edge => {
            if (rowFacets.has(edge.to)) {
              const rowNode = allNodes.get(edge.from);
              if (!rowNode || rowNode.group !== "row") return;

              if (!timelineOn) {
                visibleRowIds.add(edge.from);
              } else if (rowNode.hasDate && rowNode.begin <= endDate && rowNode.end >= startDate) {
                visibleRowIds.add(edge.from);
              }
            }
          });

          allEdges.get().forEach(edge => {
            if (visibleRowIds.has(edge.from)) {
              visibleEdges.push(edge);
              visibleFacetIds.add(edge.to);
            }
          });

          visibleRowIds.add(spotlight.id);
        }
      } else {
        sidebar.style.display = "none";
        allNodes.get().forEach(node => {
          if (node.group !== "row") return;
          if (!timelineOn) {
            visibleRowIds.add(node.id);
          } else if (node.hasDate && node.begin <= endDate && node.end >= startDate) {
            visibleRowIds.add(node.id);
          }
        });

        allEdges.get().forEach(edge => {
          if (visibleRowIds.has(edge.from)) {
            visibleEdges.push(edge);
            visibleFacetIds.add(edge.to);
          }
        });
      }

      const visibleNodes = allNodes.get().filter(node => {
        if (node.group === "row") return visibleRowIds.has(node.id);
        if (node.group === "facet") return visibleFacetIds.has(node.id);
        return false;
      });

      network.setData({
        nodes: new vis.DataSet(visibleNodes),
        edges: new vis.DataSet(visibleEdges)
      });
    }

    function jumpToPathIndex(nextIndex) {
      if (nextIndex < 0 || nextIndex >= clickPath.length) return;
      pathIndex = nextIndex;
      const node = allNodes.get(clickPath[pathIndex]);
      spotlightNode = node ? node.id : null;
      if (node) {
        network.focus(node.id, { scale: 1.15, animation: true });
      }
      updatePathToolbar();
      render();
    }

    network.on("click", function(params) {
      if (params.nodes.length === 1) {
        const clicked = allNodes.get(params.nodes[0]);
        pushPath(clicked.id);
        spotlightNode = (spotlightNode === clicked.id) ? null : clicked.id;
      } else {
        spotlightNode = null;
      }
      updatePathToolbar();
      render();
    });

    pathBack.addEventListener("click", function() {
      jumpToPathIndex(pathIndex - 1);
    });

    pathForward.addEventListener("click", function() {
      jumpToPathIndex(pathIndex + 1);
    });

    pathClear.addEventListener("click", function() {
      clickPath = [];
      pathIndex = -1;
      spotlightNode = null;
      updatePathToolbar();
      render();
    });

    if (slider.noUiSlider) {
      slider.noUiSlider.on("update", render);
    }

    timelineToggle.addEventListener("change", function() {
      slider.style.display = this.checked ? "block" : "none";
      sidebar.style.display = "none";
      spotlightNode = null;
      render();
    });

    timelineToggle.checked = false;
    slider.style.display = "none";

    updatePathToolbar();
    render();
    let lastModified = null;

async function checkForUpdate() {
  try {
    const response = await fetch(window.location.href, { method: "HEAD" });
    const newModified = response.headers.get("Last-Modified");

    if (lastModified && newModified !== lastModified) {
      location.reload();
    }

    lastModified = newModified;
  } catch (e) {
    console.warn("Update check failed", e);
  }
}

setInterval(checkForUpdate, 2000);
  </script>
</body>
</html>
"""

html = html.replace("__COLLECTION_TITLE__", collection_title)
html = html.replace("__NODES__", json.dumps(nodes_list))
html = html.replace("__EDGES__", json.dumps(edges))
html = html.replace("__DATES__", json.dumps(date_values))

OUTPUT_FILE.write_text(html, encoding="utf-8")
print(f"Generated: {OUTPUT_FILE}")

import webbrowser

webbrowser.open(OUTPUT_FILE.resolve().as_uri())
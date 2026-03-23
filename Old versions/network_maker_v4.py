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
}

def find_column(columns, aliases, label):
    lookup = {c.strip().lower(): c for c in columns}
    for alias in aliases:
        key = alias.strip().lower()
        if key in lookup:
            return lookup[key]
    raise ValueError(f"Could not find a column for {label}. Available columns: {list(columns)}")

id_column = find_column(df.columns, COLUMN_ALIASES["id"], "ID")
date_expression_column = find_column(df.columns, COLUMN_ALIASES["date_expression"], "date expression")
date_begin_column = find_column(df.columns, COLUMN_ALIASES["date_begin"], "date begin")
date_end_column = find_column(df.columns, COLUMN_ALIASES["date_end"], "date end")

print("Using columns:")
print("  ID:", id_column)
print("  Date expression:", date_expression_column)
print("  Begin:", date_begin_column)
print("  End:", date_end_column)

facet_columns = [c for c in df.columns if c not in [id_column, date_expression_column, date_begin_column, date_end_column]]
print("Facet columns:", facet_columns)

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

# Parse dates
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

# Build nodes/edges
nodes = {}
edges = []

for idx, row in df.iterrows():
    row_id = f"row_{idx}"
    row_label = str(row[id_column]).strip()
    date_expr = str(row[date_expression_column]).strip() if pd.notna(row[date_expression_column]) else ""
    begin_iso = row["_begin"].date().isoformat() if pd.notna(row["_begin"]) else ""
    end_iso = row["_end"].date().isoformat() if pd.notna(row["_end"]) else ""

    nodes[row_id] = {
        "id": row_id,
        "label": row_label if not date_expr else f"{row_label}\n{date_expr}",
        "group": "row",
        "title": (
            f"<b>ID:</b> {row_label}<br>"
            f"<b>Date expression:</b> {date_expr or 'none'}<br>"
            f"<b>Begin:</b> {begin_iso or 'none'}<br>"
            f"<b>End:</b> {end_iso or 'none'}"
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
            nodes[facet_id] = {
                "id": facet_id,
                "label": str(facet),
                "group": "facet",
                "title": str(facet),
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
    #container { width: 100%; height: 84vh; }
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
    .noUi-handle:after {
      display: none;
    }
    .toggle-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 6px;
      user-select: none;
      font-size: 13px;
    }
  </style>
</head>
<body>
  <div id="controls">
    <div><strong>Select active date range</strong></div>
    <div class="toggle-row">
      <input type="checkbox" id="timelineToggle">
      <label for="timelineToggle">Enable timeline filter</label>
    </div>
    <div id="sliderWrap">
      <div id="slider"></div>
    </div>
    <div id="rangeText">Timeline disabled — showing everything, including records without dates.</div>
  </div>
  <div id="container"></div>

  <script>
    const allNodes = new vis.DataSet(__NODES__);
    const allEdges = new vis.DataSet(__EDGES__);
    const availableDates = __DATES__;

    const container = document.getElementById("container");
    const slider = document.getElementById("slider");
    const rangeText = document.getElementById("rangeText");
    const timelineToggle = document.getElementById("timelineToggle");

    const options = {
      nodes: {
        shape: "dot",
        size: 14
      },
      edges: {
        smooth: true,
        color: { color: "#999" }
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
            highlight: { background: "#FFB347", border: "#FF8C00" },
            hover: { background: "#FFA94D", border: "#FF8C00" }
          },
          size: 14
        }
      }
    };

    const network = new vis.Network(container, { nodes: [], edges: [] }, options);

    let spotlightFacet = null;

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

    function applyFacetHighlight() {
      const updatedFacetNodes = allNodes.get().filter(node => node.group === "facet").map(node => {
        if (node.id === spotlightFacet) {
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
            border: "#C96F10"
          }
        };
      });
      allNodes.update(updatedFacetNodes);
    }

    function render() {
      const timelineOn = timelineToggle.checked && !!slider.noUiSlider;
      const { startDate, endDate } = currentRange();

      applyFacetHighlight();

      if (!timelineOn) {
        rangeText.textContent = spotlightFacet
          ? `Timeline disabled — spotlighting facet: ${allNodes.get(spotlightFacet).label}`
          : "Timeline disabled — showing everything, including records without dates.";
      } else {
        rangeText.textContent = spotlightFacet
          ? `Timeline enabled — ${startDate} to ${endDate} • spotlight: ${allNodes.get(spotlightFacet).label}`
          : `Timeline enabled — showing records active from ${startDate} to ${endDate}.`;
      }

      const visibleRowIds = new Set();
      const visibleFacetIds = new Set();
      const visibleEdges = [];

      if (spotlightFacet) {
        allEdges.get().forEach(edge => {
          if (edge.to === spotlightFacet) {
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

        visibleFacetIds.add(spotlightFacet);
      } else {
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

    network.on("click", function (params) {
      if (params.nodes.length === 1) {
        const clicked = allNodes.get(params.nodes[0]);
        if (clicked && clicked.group === "facet") {
          spotlightFacet = (spotlightFacet === clicked.id) ? null : clicked.id;
        } else {
          spotlightFacet = null;
        }
      } else {
        spotlightFacet = null;
      }
      render();
    });

    if (slider.noUiSlider) {
      slider.noUiSlider.on("update", render);
    }

    timelineToggle.addEventListener("change", function() {
      slider.style.display = this.checked ? "block" : "none";
      spotlightFacet = null;
      render();
    });

    render();
  </script>
</body>
</html>
"""

html = html.replace("__NODES__", json.dumps(nodes_list))
html = html.replace("__EDGES__", json.dumps(edges))
html = html.replace("__DATES__", json.dumps(date_values))

OUTPUT_FILE.write_text(html, encoding="utf-8")
print(f"Generated: {OUTPUT_FILE}")

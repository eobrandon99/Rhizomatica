import pandas as pd
import json
from pathlib import Path

# ---------------- CONFIG ----------------
input_file = "/Users/evanbrandon/Desktop/pythonprojects/Rhizomatic Finding Aid/facets.xlsx"
id_column = "ID"
date_expression_column = "date_expression"
date_begin_column = "date_begin"
date_end_column = "date_end"
yes_value = "YES"
output_file = "network_timeline.html"

# ---------------- DATE FLEXIBILITY ----------------
def parse_flexible_date(val, is_end=False):
    if pd.isna(val):
        return pd.NaT

    # If Excel already gave us a Timestamp / datetime
    if isinstance(val, pd.Timestamp):
        return val.normalize()

    # If it's a numeric year like 1923 or 1923.0
    if isinstance(val, (int, float)) and not pd.isna(val):
        year = int(val)
        if 1000 <= year <= 9999:
            return pd.Timestamp(f"{year}-12-31" if is_end else f"{year}-01-01")

    s = str(val).strip()

    # Handle year-only strings
    if s.isdigit() and len(s) == 4:
        return pd.Timestamp(f"{s}-12-31" if is_end else f"{s}-01-01")

    # Handle year strings that came in as '1923.0'
    if s.endswith(".0") and s[:-2].isdigit() and len(s[:-2]) == 4:
        year = s[:-2]
        return pd.Timestamp(f"{year}-12-31" if is_end else f"{year}-01-01")

    # Handle full dates
    try:
        return pd.to_datetime(s, errors="raise")
    except:
        return pd.NaT

# ---------------- LOAD DATA ----------------
df = pd.read_excel(input_file)
df.columns = df.columns.str.strip()

print("Columns:", list(df.columns))
print("Rows:", len(df))

required = [id_column, date_expression_column, date_begin_column, date_end_column]
for col in required:
    if col not in df.columns:
        raise ValueError(f"Missing required column: {col}")

# Parse ISO date columns
df["_begin"] = df[date_begin_column].apply(lambda x: parse_flexible_date(x, is_end=False))
df["_end"] = df[date_end_column].apply(lambda x: parse_flexible_date(x, is_end=True))

if df["_begin"].dropna().empty:
    raise ValueError(f"No usable values found in '{date_begin_column}'")
if df["_end"].dropna().empty:
    raise ValueError(f"No usable values found in '{date_end_column}'")

# If an end date is missing, treat it as the begin date
df["_end"] = df["_end"].fillna(df["_begin"])

facet_columns = [
    c for c in df.columns
    if c not in [id_column, date_expression_column, date_begin_column, date_end_column, "_begin", "_end"]
]

# ---------------- BUILD GRAPH DATA ----------------
nodes = []
edges = []
added_facets = set()

for idx, row in df.iterrows():
    row_id = str(row[id_column]).strip()

    date_expr = ""
    if pd.notna(row[date_expression_column]):
        date_expr = str(row[date_expression_column]).strip()

    display_label = f"{row_id}, {date_expr}" if date_expr else row_id

    begin_dt = row["_begin"]
    end_dt = row["_end"]

    if pd.isna(begin_dt):
        continue

    begin_iso = begin_dt.date().isoformat()
    end_iso = end_dt.date().isoformat() if pd.notna(end_dt) else begin_iso

    row_node_id = f"row::{idx}"

    nodes.append({
        "id": row_node_id,
        "label": display_label,
        "group": "row",
        "begin": begin_iso,
        "end": end_iso,
        "title": f"ID: {row_id}<br>Date: {date_expr}<br>Begin: {begin_iso}<br>End: {end_iso}"
    })

    for facet in facet_columns:
        cell = row[facet]
        if pd.notna(cell) and str(cell).strip().upper() == yes_value:
            facet_node_id = f"facet::{facet}"
            if facet_node_id not in added_facets:
                nodes.append({
                    "id": facet_node_id,
                    "label": facet,
                    "group": "facet",
                    "title": facet
                })
                added_facets.add(facet_node_id)

            edges.append({
                "from": row_node_id,
                "to": facet_node_id
            })

# Slider dates: use all unique begin/end boundaries
date_values = sorted({
    n["begin"] for n in nodes if n["group"] == "row"
}.union({
    n["end"] for n in nodes if n["group"] == "row"
}))

if not date_values:
    raise ValueError("No usable timeline dates were created.")

nodes_json = json.dumps(nodes)
edges_json = json.dumps(edges)
dates_json = json.dumps(date_values)

# ---------------- BUILD HTML ----------------
html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Timeline Network</title>
  <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
  <style>
    body {{
      margin: 0;
      font-family: Arial, sans-serif;
      background: #ffffff;
    }}
    #controls {{
      padding: 12px 16px;
      border-bottom: 1px solid #ddd;
      background: #f7f7f7;
    }}
    #container {{
      width: 100%;
      height: 90vh;
    }}
    #dateLabel {{
      margin-left: 10px;
      font-weight: bold;
    }}
    input[type="range"] {{
      width: 420px;
      max-width: 85vw;
      vertical-align: middle;
    }}
  </style>
</head>
<body>
  <div id="controls">
    <label for="dateSlider">Show items active on:</label>
    <input type="range" id="dateSlider" min="0" max="{len(date_values) - 1}" value="{len(date_values) - 1}" step="1">
    <span id="dateLabel"></span>
  </div>
  <div id="container"></div>

  <script>
    const allNodes = new vis.DataSet({nodes_json});
    const allEdges = new vis.DataSet({edges_json});
    const availableDates = {dates_json};

    const slider = document.getElementById("dateSlider");
    const dateLabel = document.getElementById("dateLabel");
    const container = document.getElementById("container");

    const options = {{
      nodes: {{
        shape: "dot",
        size: 14,
        font: {{ size: 14 }},
        borderWidth: 1
      }},
      edges: {{
        smooth: true,
        color: {{ color: "#999" }}
      }},
      groups: {{
        row: {{
          color: {{
            background: "#4C78A8",
            border: "#3B5C83"
          }},
          size: 18
        }},
        facet: {{
          color: {{
            background: "#F58518",
            border: "#C96F10"
          }},
          size: 14
        }}
      }},
      interaction: {{
        hover: true,
        navigationButtons: true,
        keyboard: true
      }},
      physics: {{
        stabilization: true
      }}
    }};

    const network = new vis.Network(container, {{ nodes: [], edges: [] }}, options);

    function render() {{
      const cutoff = availableDates[parseInt(slider.value, 10)];
      dateLabel.textContent = cutoff;

      const visibleRowIds = new Set();
      const visibleFacetIds = new Set();
      const visibleEdges = [];

      allNodes.get().forEach(node => {{
        if (node.group === "row") {{
          // Show rows where begin <= cutoff <= end
          if (node.begin <= cutoff && cutoff <= node.end) {{
            visibleRowIds.add(node.id);
          }}
        }}
      }});

      allEdges.get().forEach(edge => {{
        if (visibleRowIds.has(edge.from)) {{
          visibleEdges.push(edge);
          visibleFacetIds.add(edge.to);
        }}
      }});

      const visibleNodes = allNodes.get().filter(node => {{
        if (node.group === "row") {{
          return visibleRowIds.has(node.id);
        }}
        if (node.group === "facet") {{
          return visibleFacetIds.has(node.id);
        }}
        return false;
      }});

      network.setData({{
        nodes: new vis.DataSet(visibleNodes),
        edges: new vis.DataSet(visibleEdges)
      }});
    }}

    slider.addEventListener("input", render);
    render();
  </script>
</body>
</html>
"""

Path(output_file).write_text(html, encoding="utf-8")
print(f"Saved timeline network to {output_file}")
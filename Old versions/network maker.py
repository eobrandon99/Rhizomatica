import pandas as pd
import networkx as nx
from pyvis.network import Network

input_file = "/Users/evanbrandon/Desktop/pythonprojects/Rhizomatic Finding Aid/facets.xlsx"
id_column = "ID"
date_column = "date_expression"
yes_value = "YES"
output_file = "network.html"

df = pd.read_excel(input_file)

print("Columns:", list(df.columns))
print("Rows:", len(df))

if id_column not in df.columns:
    raise ValueError(f"Missing required column: {id_column}")

if date_column not in df.columns:
    raise ValueError(f"Missing required column: {date_column}")

G = nx.Graph()
facet_columns = [c for c in df.columns if c not in [id_column, date_column]]

for _, row in df.iterrows():
    row_id = str(row[id_column]).strip()

    date_val = ""
    if pd.notna(row[date_column]):
        date_val = str(row[date_column]).strip()

    row_name = f"{row_id}, {date_val}" if date_val else row_id

    row_node = f"row::{row_id}"   # stable internal ID
    G.add_node(
        row_node,
        label=row_name,
        title=f"ID: {row_id}<br>Date: {date_val}",
        group="row"
    )

    for facet in facet_columns:
        cell = row[facet]
        if pd.notna(cell) and str(cell).strip().upper() == yes_value:
            facet_node = f"facet::{facet}"
            G.add_node(facet_node, label=facet, group="facet")
            G.add_edge(row_node, facet_node)

print("Graph nodes:", G.number_of_nodes())
print("Graph edges:", G.number_of_edges())

net = Network(height="800px", width="100%", bgcolor="#ffffff", font_color="black", notebook=False)
net.from_nx(G)
net.write_html(output_file, open_browser=True)

print(f"Saved to {output_file}")
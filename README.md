# Rhizomatica

Rhizomatica is a Python-based archival visualization tool that turns EAD XML finding aids into interactive, multi-facet "rhizome" web pages. The project reads archival hierarchy data, derives facets from collections, series, and recurring keyword patterns, and renders an HTML viewer that lets users explore relationships across a collection.

The generated pages are designed for archival research, collection browsing, and thematic discovery. They combine a network graph, facet filters, timeline controls, and a list/detail view to support browsing files and series in context.

## Project purpose

This repository takes archival descriptions in EAD/XML format and transforms them into a browsable web interface. In practice, it is most useful for:

- Exploring digital collections as a connected network rather than a flat list
- Filtering by series, keywords, and date range
- Sur-facing recurring themes across many files or folders
- Presenting archival content in a way that supports discovery and interpretation

The output is a standalone HTML page that can be opened locally in a browser without a backend server.

## Repository structure

```text
Rhizomatica/
├── Rhizomatica_v7.py          # Main application entry point
├── config.json                # Default keyword and redundancy settings
├── input/                     # Source EAD XML files
├── output/                    # Generated rhizome HTML files
├── lib/                       # Front-end dependencies used by generated pages
│   ├── bindings/
│   ├── tom-select/
│   └── vis-9.1.2/
├── .gitignore
├── .gitattributes
└── README.md
```

## What the app generates

Running the script against files in [input/](./input) produces HTML reports in [output/](./output) named according to the source file prefix, such as:

- `CEW-rhizome.html`
- `aflf-rhizome.html`
- `wallacehouse-rhizome.html`

Each generated page includes:

- An interactive network graph of archival items and facets
- A facet spotlight / filter panel for narrowing the view
- Timeline filtering based on dates in the source EAD records
- A file list for browsing records in the current selection
- A detail card for selected items or files
- Keyword facets derived automatically from recurring terms in matching records

## Key features

### Multi-facet browsing

The app builds a hierarchical model out of EAD components and creates facet groups for archival structure. Items are tagged with any relevant parent series or topical cluster so users can filter the network along multiple dimensions at once.

### Keyword extraction

Recurring words and phrases are extracted from titles, notes, and scope/content text. These become keyword facets when they appear across enough files, allowing thematic connections to emerge without requiring manual cataloging.

### Date-aware filtering

Each archival item includes date metadata when it is present. The generated viewer can filter results by timeline range, making it easier to trace how a topic or collection changes over time.

### Standalone, browser-based output

The tool produces static HTML pages using bundled JavaScript libraries from [lib/](./lib). No database or web server is required for the final output.

## Input requirements

The tool expects EAD XML files in the [input/](./input) directory with names matching the pattern:

```text
*-ead.xml
```

Example files in this repository include:

- `CEW-ead.xml`
- `aflf-ead.xml`
- `angelaMorgan-ead.xml`
- `wallacehouse-ead.xml`

The application processes each XML file independently and writes one HTML output for each input.

## How the script works

The main workflow in [Rhizomatica_v7.py](./Rhizomatica_v7.py) is:

1. Loads the configured defaults from [config.json](./config.json)
2. Finds every `*-ead.xml` file in [input/](./input)
3. Parses the XML tree and normalizes element tags
4. Builds record rows from archival components and down to file-level entries
5. Derives hierarchical and keyword facets
6. Generates an HTML file containing the interactive network and filter UI
7. Optionally opens the generated page in the browser

The script is intentionally focused on one file as the primary application entry point, with output and data transformation handled in-process.

## Configuration

The default behavior is controlled by [config.json](./config.json):

```json
{
  "keyword_min_file_ratio": 0.02,
  "facet_redundancy_ratio": 0.0
}
```

These settings affect how aggressively keyword facets are generated and how redundant facet labels are treated.

The script supports the following command-line overrides:

- `--open`: opens each generated HTML page in the default browser
- `--keyword-min-count`: sets a fixed minimum number of files needed for a keyword facet
- `--keyword-min-ratio`: overrides the ratio-based keyword threshold for a run
- `--redundancy-ratio`: overrides redundancy handling for facets

## Usage

### Prerequisites

- Python 3.8+ recommended
- A valid EAD XML file set in [input/](./input)

### Run the app

From the repository root:

```bash
python Rhizomatica_v7.py
```

This will process every matching file in [input/](./input) and write the corresponding outputs to [output/](./output).

### Open output pages automatically

```bash
python Rhizomatica_v7.py --open
```

This will generate the HTML pages and open each one in the system browser.

### Override keyword sensitivity

```bash
python Rhizomatica_v7.py --keyword-min-ratio 0.05
```

This makes the app require recurring terms to appear in a larger proportion of records before becoming keyword facets.

### Override redundancy behavior

```bash
python Rhizomatica_v7.py --redundancy-ratio 0.5
```

This changes how aggressively the script removes or compresses overlapping facet labels.

## Output behavior

After processing, the script prints output similar to:

```text
CEW-ead.xml: 135 rows, 24 facets
Generated: C:\...\output\CEW-rhizome.html
```

If one file fails to render, the script continues processing the remaining files and reports the failures at the end.

## Data model overview

Each EAD component is normalized into a record with fields such as:

- title
- date expression
- begin/end date
- display date
- container instance
- extent information

The app then derives:

- file-level records
- hierarchical facet tags
- keyword facet tags
- network edges between connected archival nodes

This is what allows a collection to be visualized as a "rhizome" rather than a strictly hierarchical tree.

## Notes on archival processing

The script is tuned for archival finding aids and is aware of common EAD structures including:

- nested container components (`c01`, `c02`, etc.)
- `did` sections with unit title/date/extent information
- parent-child archival relationships
- recurring subject-like terminology in notes and summary text

It strips XML namespaces, extracts text from relevant elements, and normalizes dates so they can be used in the interactive timeline.

## Troubleshooting

### No files were found

If you see an error like `No input files matching *-ead.xml found`, make sure your XML files are placed in [input/](./input) and use the expected naming convention.

### File generation fails

Common causes include:

- invalid or malformed XML
- missing `did` or title data in some records
- unusual EAD structure not accounted for by the parser

The script handles parse errors gracefully and continues the rest of the batch.

### Browser does not open

This is usually a local environment issue rather than a project problem. Run without `--open` if automatic browser launching is unavailable in your setup.

## Best practices

- Keep source EAD files clean and well-formed for the most reliable results.
- Review [config.json](./config.json) before larger batches if you want different keyword sensitivity.
- Use output pages as discovery tools: they are designed for browsing and interpretation rather than static publication export.

## Summary

Rhizomatica converts archival XML collections into interactive, network-based discovery interfaces. It is designed for researchers, archivists, and digital humanities projects that want to explore relationships between records, series, and recurring themes rather than browse a rigid archival tree alone.

For this repository, the main entry point is [Rhizomatica_v7.py](./Rhizomatica_v7.py), the source data lives under [input/](./input), and the generated visualizations land in [output/](./output).

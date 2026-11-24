# Rhizomatica

Rhizomatica is a cross-platform Python desktop app for turning EAD XML finding aids into interactive, multi-facet archival visualizations. It extracts collection hierarchy, dates, and recurring keywords, then creates a browsable network of records and themes.

The desktop app processes files on your computer without uploading them to a service. Its native GUI lets you choose a finding aid, edit configuration, and generate a visualization. Generated visualizations are HTML files opened in the default browser.

## Features

- Interactive network of archival items and facets
- Hierarchy and keyword facet filtering
- Timeline filtering and file details
- Keyword extraction from titles, notes, and scope/content text
- Direct editing of configuration in the desktop app
- Local EAD processing with no application server

## Run from source

Python 3.8+ and Tkinter are required. Tkinter is included with most Python distributions; some Linux distributions package it separately.

From the repository root:

```bash
python desktop_app.py
```

In the **Generate** tab, choose an `.xml` finding aid and a location for the generated HTML. The app opens the result in your default browser. Use the **Settings** tab to edit and save the values in `config.json`.

The original command-line batch workflow remains available:

```bash
python Rhizomatica_v7.py
```

This processes every `*-ead.xml` file in [input/](./input) and writes the corresponding pages to [output/](./output). Add `--open` to open generated pages automatically. Other options include `--keyword-min-count`, `--keyword-min-ratio`, and `--redundancy-ratio`; run `python Rhizomatica_v7.py --help` for details.

## Build a standalone executable

Install PyInstaller and build on each target operating system. PyInstaller does not cross-compile.

On Windows:

```powershell
python -m pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name Rhizomatica --add-data "config.json;." desktop_app.py
```

On macOS or Linux, use `:` instead of `;` in the `--add-data` argument:

```bash
python -m pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name Rhizomatica --add-data "config.json:." desktop_app.py
```

The executable is written to `dist/`. Packaged app settings are stored in the user's platform-specific configuration directory. When running from source, settings are saved directly to [config.json](./config.json).

## Configuration

The default settings are:

```json
{
  "facet_redundancy_ratio": 0.7,
  "finding_aid_url_base": "https://findingaids.lib.umich.edu"
}
```

- `facet_redundancy_ratio` controls how redundant keyword facets are merged.
- `finding_aid_url_base` is used to create each file's **View in Finding Aid** link.

In the generated visualization, the **Settings** menu also offers a keyword-hit threshold and dark mode. The former `keyword_min_file_ratio` setting is deprecated and ignored.

## Generated visualizations

The desktop app writes one HTML file for each generation. The HTML visualization loads its network and timeline libraries from public CDNs, so an internet connection is needed to view the interactive graph. The EAD file itself is not uploaded.

To batch-process files in [input/](./input), use the command-line entry point [Rhizomatica_v7.py](./Rhizomatica_v7.py). The desktop GUI is [desktop_app.py](./desktop_app.py); it also works with a single EAD file selected from anywhere on the computer.

## Troubleshooting

- **Tkinter is missing:** install the Tkinter package provided by your operating system or Python distribution.
- **The app cannot open the result:** the HTML file is saved before opening; navigate to the selected save location and open it manually.
- **Generation fails:** check that the input is valid EAD XML and includes file-level records.

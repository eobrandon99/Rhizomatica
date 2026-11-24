#!/usr/bin/env python3
"""Desktop GUI for generating Rhizomatica visualizations from EAD XML."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser
import xml.etree.ElementTree as ET

import Rhizomatica_v7 as rhizomatica


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def output_directory() -> Path:
    if not getattr(sys, "frozen", False):
        return application_root() / "output"
    documents = Path.home() / "Documents"
    if not documents.is_dir():
        documents = Path.home()
    return documents / "Rhizomatica" / "output"


def resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", application_root())) / name
    return application_root() / name


def configuration_path() -> Path:
    if not getattr(sys, "frozen", False):
        return application_root() / "config.json"
    if os.name == "nt":
        config_root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        config_root = Path.home() / "Library" / "Application Support"
    else:
        config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_root / "Rhizomatica" / "config.json"


def validate_config(config: dict) -> dict:
    try:
        redundancy_ratio = float(config["facet_redundancy_ratio"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Facet redundancy ratio must be a number from 0 to 1.") from exc
    if not math.isfinite(redundancy_ratio) or not 0 <= redundancy_ratio <= 1:
        raise ValueError("Facet redundancy ratio must be a number from 0 to 1.")

    finding_aid_url_base = config.get("finding_aid_url_base", "")
    if not isinstance(finding_aid_url_base, str):
        raise ValueError("Finding aid URL base must be text.")
    finding_aid_url_base = finding_aid_url_base.strip().rstrip("/")
    if finding_aid_url_base:
        from urllib.parse import urlparse

        parsed_url = urlparse(finding_aid_url_base)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Finding aid URL base must be a valid HTTP or HTTPS URL.")

    validated = dict(config)
    validated["facet_redundancy_ratio"] = redundancy_ratio
    validated["finding_aid_url_base"] = finding_aid_url_base
    return validated


def load_desktop_config() -> dict:
    config_file = configuration_path()
    if config_file.exists():
        try:
            loaded = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read {config_file}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"{config_file} must contain a JSON object.")
    else:
        default_file = resource_path("config.json")
        try:
            loaded = json.loads(default_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read default settings from {default_file}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"{default_file} must contain a JSON object.")
    config = dict(rhizomatica.DEFAULT_CONFIG)
    config.update(loaded)
    return validate_config(config)


def save_desktop_config(config: dict) -> None:
    config_file = configuration_path()
    config_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = config_file.with_suffix(".json.tmp")
    try:
        temporary_file.write_text(
            json.dumps(config, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_file.replace(config_file)
    except OSError:
        temporary_file.unlink(missing_ok=True)
        raise


def generate_rhizome(input_path: Path, output_path: Path, config: dict) -> tuple[int, int]:
    validated_config = validate_config(config)
    if input_path.suffix.lower() != ".xml":
        raise ValueError("Choose an EAD file with an .xml extension.")

    tree = ET.parse(input_path)
    root = rhizomatica.strip_namespaces(tree.getroot())
    title, rows, facets = rhizomatica.build_records(
        root,
        validated_config["facet_redundancy_ratio"],
    )
    if not rows:
        raise ValueError("This finding aid has no file-level records to visualize.")

    ead_id = rhizomatica.text_of(root.find(".//eadid"))
    for row in rows:
        row["finding_aid_url"] = rhizomatica.finding_aid_url(
            validated_config["finding_aid_url_base"],
            ead_id,
            row["object_id"],
        )

    html = rhizomatica.build_html(title, rows, facets)
    output_path.write_text(html, encoding="utf-8")
    return len(rows), len(facets)


class RhizomaticaApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Rhizomatica")
        self.root.minsize(620, 420)
        self.root.geometry("760x520")
        self.input_path: Path | None = None
        self.config = load_desktop_config()
        self.generation_queue: queue.Queue = queue.Queue()
        self.generation_running = False

        self._build_ui()
        self._show_config()

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=20)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        ttk.Label(container, text="Rhizomatica", font=("TkDefaultFont", 20, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 14)
        )

        self.tabs = ttk.Notebook(container)
        self.tabs.grid(row=1, column=0, sticky="nsew")
        generate_tab = ttk.Frame(self.tabs, padding=18)
        settings_tab = ttk.Frame(self.tabs, padding=18)
        self.tabs.add(generate_tab, text="Generate")
        self.tabs.add(settings_tab, text="Settings")

        self._build_generate_tab(generate_tab)
        self._build_settings_tab(settings_tab)

        self.status = tk.StringVar(value="Choose an EAD XML file to begin.")
        ttk.Label(container, textvariable=self.status, wraplength=700).grid(
            row=2, column=0, sticky="ew", pady=(12, 0)
        )

    def _build_generate_tab(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        ttk.Label(
            tab,
            text="Generate an interactive rhizome from a finding aid.",
            wraplength=600,
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            tab,
            text="Your EAD file is processed on this computer and is not uploaded.",
            wraplength=600,
        ).grid(row=1, column=0, sticky="w", pady=(6, 18))

        self.selected_file = tk.StringVar(value="No file selected")
        ttk.Label(
            tab,
            textvariable=self.selected_file,
            wraplength=600,
        ).grid(row=2, column=0, sticky="ew", pady=(0, 12))

        buttons = ttk.Frame(tab)
        buttons.grid(row=3, column=0, sticky="w")
        self.choose_button = ttk.Button(
            buttons,
            text="Choose EAD XML…",
            command=self._choose_input,
        )
        self.choose_button.pack(side="left", padx=(0, 8))
        self.generate_button = ttk.Button(
            buttons,
            text="Generate rhizome",
            command=self._start_generation,
        )
        self.generate_button.pack(side="left")

    def _build_settings_tab(self, tab: ttk.Frame) -> None:
        tab.columnconfigure(0, weight=1)
        ttk.Label(tab, text="Settings are saved to config.json.").grid(
            row=0, column=0, sticky="w", pady=(0, 16)
        )

        ttk.Label(tab, text="Facet redundancy ratio").grid(
            row=1, column=0, sticky="w"
        )
        self.ratio_value = tk.StringVar()
        ttk.Entry(tab, textvariable=self.ratio_value, width=18).grid(
            row=2, column=0, sticky="w", pady=(4, 4)
        )
        ttk.Label(
            tab,
            text="Controls how aggressively overlapping keyword facets are merged (0–1).",
            wraplength=600,
        ).grid(row=3, column=0, sticky="w", pady=(0, 16))

        ttk.Label(tab, text="Finding aid URL base").grid(
            row=4, column=0, sticky="w"
        )
        self.finding_aid_url_value = tk.StringVar()
        ttk.Entry(tab, textvariable=self.finding_aid_url_value, width=60).grid(
            row=5, column=0, sticky="ew", pady=(4, 4)
        )
        ttk.Label(
            tab,
            text="Used to create finding-aid links. Leave blank to omit these links.",
            wraplength=600,
        ).grid(row=6, column=0, sticky="w", pady=(0, 16))
        ttk.Button(tab, text="Save settings", command=self._save_settings).grid(
            row=7, column=0, sticky="w"
        )

    def _show_config(self) -> None:
        self.ratio_value.set(str(self.config["facet_redundancy_ratio"]))
        self.finding_aid_url_value.set(self.config["finding_aid_url_base"])

    def _choose_input(self) -> None:
        initial_dir = application_root() / "input"
        if not initial_dir.is_dir():
            initial_dir = Path.home()
        selected = filedialog.askopenfilename(
            title="Choose an EAD XML file",
            initialdir=initial_dir,
            filetypes=(("EAD XML files", "*.xml"), ("All files", "*.*")),
        )
        if selected:
            self.input_path = Path(selected)
            self.selected_file.set(str(self.input_path))
            self.status.set("Ready to generate.")

    def _save_settings(self) -> None:
        try:
            next_config = dict(self.config)
            next_config["facet_redundancy_ratio"] = self.ratio_value.get()
            next_config["finding_aid_url_base"] = self.finding_aid_url_value.get()
            next_config = validate_config(next_config)
            save_desktop_config(next_config)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not save settings", str(exc), parent=self.root)
            self.status.set("Settings were not saved.")
            return

        self.config = next_config
        self._show_config()
        self.status.set(f"Settings saved to {configuration_path()}.")

    def _start_generation(self) -> None:
        if self.generation_running:
            return
        if self.input_path is None:
            messagebox.showinfo("Choose a file", "Choose an EAD XML file first.", parent=self.root)
            return
        if self.input_path.suffix.lower() != ".xml":
            messagebox.showerror("Unsupported file", "Choose an EAD file with an .xml extension.", parent=self.root)
            return

        output_dir = output_directory()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Could not prepare output folder", str(exc), parent=self.root)
            return
        output_path = filedialog.asksaveasfilename(
            title="Save generated rhizome",
            initialdir=output_dir,
            initialfile=f"{self.input_path.stem}-rhizome.html",
            defaultextension=".html",
            filetypes=(("HTML files", "*.html"),),
        )
        if not output_path:
            return

        self.generation_running = True
        self.generate_button.configure(state="disabled")
        self.choose_button.configure(state="disabled")
        self.status.set("Generating rhizome…")
        worker = threading.Thread(
            target=self._generate_in_background,
            args=(self.input_path, Path(output_path), dict(self.config)),
            daemon=True,
        )
        worker.start()
        self.root.after(100, self._check_generation)

    def _generate_in_background(
        self,
        input_path: Path,
        output_path: Path,
        config: dict,
    ) -> None:
        try:
            counts = generate_rhizome(input_path, output_path, config)
        except (ET.ParseError, OSError, ValueError) as exc:
            self.generation_queue.put(("error", str(exc)))
        else:
            self.generation_queue.put(("success", (output_path, counts)))

    def _check_generation(self) -> None:
        try:
            result, payload = self.generation_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._check_generation)
            return

        self.generation_running = False
        self.generate_button.configure(state="normal")
        self.choose_button.configure(state="normal")
        if result == "error":
            self.status.set("Generation failed.")
            messagebox.showerror("Rhizome generation failed", payload, parent=self.root)
            return

        output_path, (row_count, facet_count) = payload
        self.status.set(
            f"Generated {row_count} records and {facet_count} facets: {output_path}"
        )
        if not webbrowser.open(output_path.resolve().as_uri(), new=2):
            messagebox.showwarning(
                "Rhizome generated",
                f"The file was saved, but the browser could not be opened:\n{output_path}",
                parent=self.root,
            )


def main() -> None:
    root = tk.Tk()
    try:
        RhizomaticaApp(root)
    except (OSError, ValueError) as exc:
        messagebox.showerror("Rhizomatica could not start", str(exc), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()

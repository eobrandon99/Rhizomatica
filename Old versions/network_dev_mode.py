#!/usr/bin/env python3
import argparse
import subprocess
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RELOAD_MARKER = "<!-- AUTO-RELOAD-INJECTED -->"


def inject_live_reload(html_path: Path, interval_ms: int = 2000) -> None:
    html = html_path.read_text(encoding="utf-8")
    if RELOAD_MARKER in html:
        return

    snippet = f"""
{RELOAD_MARKER}
<script>
(function () {{
  const url = window.location.href;
  let lastSeen = null;

  async function checkForUpdate() {{
    try {{
      const res = await fetch(url, {{ method: "HEAD", cache: "no-store" }});
      const mod = res.headers.get("Last-Modified") || res.headers.get("ETag");
      if (lastSeen && mod && mod !== lastSeen) {{
        window.location.reload();
        return;
      }}
      if (mod) {{
        lastSeen = mod;
      }}
    }} catch (err) {{
      // ignore transient errors
    }}
  }}

  setInterval(checkForUpdate, {interval_ms});
  checkForUpdate();
}})();
</script>
"""
    html = html.replace("</body>", snippet + "\n</body>", 1)
    html_path.write_text(html, encoding="utf-8")


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, format, *args):
        pass


def run_generator(generator_script: Path) -> None:
    print(f"Running generator: {generator_script.name}")
    result = subprocess.run(
        [sys.executable, str(generator_script)],
        cwd=str(generator_script.parent),
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def serve_and_open(base_dir: Path, html_name: str, port: int) -> ThreadingHTTPServer:
    handler = partial(NoCacheHandler, directory=str(base_dir))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/{html_name}"
    print(f"Serving {base_dir} at {url}")
    webbrowser.open(url)
    return httpd


def watch_and_regenerate(generator_script: Path, watch_file: Path, html_path: Path, interval: float) -> None:
    last_mtime = watch_file.stat().st_mtime if watch_file.exists() else None
    print(f"Watching {watch_file.name} for changes...")

    try:
        while True:
            try:
                current_mtime = watch_file.stat().st_mtime
            except FileNotFoundError:
                current_mtime = None

            if current_mtime != last_mtime:
                last_mtime = current_mtime
                print(f"\nDetected change in {watch_file.name}; regenerating...")
                run_generator(generator_script)
                if html_path.exists():
                    inject_live_reload(html_path)
                    print("Updated HTML.")
                else:
                    print("Generator finished but HTML was not found.")

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped watching.")


def main():
    parser = argparse.ArgumentParser(description="Run the network maker in one-command watch mode.")
    parser.add_argument(
        "--generator",
        default="network_maker_v6.py",
        help="Path to the Python generator script.",
    )
    parser.add_argument(
        "--html",
        default="network_output.html",
        help="Name of the generated HTML file.",
    )
    parser.add_argument(
        "--spreadsheet",
        default="facets.xlsx",
        help="Spreadsheet file to watch for changes.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Local HTTP port.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=2,
        help="Polling interval in seconds.",
    )
    parser.add_argument(
        "--reload-interval",
        type=int,
        default=2000,
        help="Browser auto-reload polling interval in milliseconds.",
    )
    args = parser.parse_args()

    generator_script = Path(args.generator).expanduser().resolve()
    if not generator_script.exists():
        raise FileNotFoundError(f"Generator script not found: {generator_script}")

    base_dir = generator_script.parent
    html_path = base_dir / args.html
    watch_file = (base_dir / args.spreadsheet).resolve()

    run_generator(generator_script)

    if not html_path.exists():
        raise FileNotFoundError(f"Expected HTML output not found after generation: {html_path}")

    inject_live_reload(html_path, interval_ms=args.reload_interval)
    httpd = serve_and_open(base_dir, html_path.name, args.port)

    try:
        watch_and_regenerate(generator_script, watch_file, html_path, args.interval)
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()

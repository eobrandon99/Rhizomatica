(() => {
  const form = document.getElementById("ead-form");
  const fileInput = document.getElementById("ead-file");
  const generateButton = document.getElementById("generate-button");
  const status = document.getElementById("status");
  const output = document.getElementById("output");
  const openOutput = document.getElementById("open-output");
  const downloadOutput = document.getElementById("download-output");
  const settingsForm = document.getElementById("settings-form");
  const settingsStatus = document.getElementById("settings-status");
  const ratioInput = document.getElementById("redundancy-ratio");
  const findingAidUrlInput = document.getElementById("finding-aid-url");
  const storageKey = "rhizomatica.config";
  const maxFileBytes = 32 * 1024 * 1024;
  const pyodideUrl = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.js";
  let config;
  let runtimePromise;
  let outputUrl;

  function setMessage(element, message, isError) {
    element.textContent = message;
    if (isError) {
      element.dataset.error = "true";
    } else {
      delete element.dataset.error;
    }
  }

  function validateConfig(value) {
    const ratio = Number(value.facet_redundancy_ratio);
    if (!Number.isFinite(ratio) || ratio < 0 || ratio > 1) {
      throw new Error("Facet redundancy ratio must be a number from 0 to 1.");
    }
    if (typeof value.finding_aid_url_base !== "string") {
      throw new Error("Finding aid URL base must be text.");
    }
    const baseUrl = value.finding_aid_url_base.trim();
    if (baseUrl) {
      let parsedUrl;
      try {
        parsedUrl = new URL(baseUrl);
      } catch {
        throw new Error("Enter a valid finding aid URL base or leave the field blank.");
      }
      if (!["http:", "https:"].includes(parsedUrl.protocol)) {
        throw new Error("Finding aid URL base must use HTTP or HTTPS.");
      }
    }
    return {
      facet_redundancy_ratio: ratio,
      finding_aid_url_base: baseUrl.replace(/\/+$/, ""),
    };
  }

  function showConfig(value) {
    ratioInput.value = String(value.facet_redundancy_ratio);
    findingAidUrlInput.value = value.finding_aid_url_base;
  }

  async function loadConfig() {
    const response = await fetch("./config.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Could not load config.json (${response.status}).`);
    }
    const defaults = validateConfig(await response.json());
    const savedConfig = localStorage.getItem(storageKey);
    config = savedConfig === null ? defaults : validateConfig(JSON.parse(savedConfig));
    showConfig(config);
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = () => reject(new Error("Could not load the Pyodide runtime."));
      document.head.append(script);
    });
  }

  async function getRuntime() {
    if (!runtimePromise) {
      runtimePromise = (async () => {
        await loadScript(pyodideUrl);
        const pyodide = await window.loadPyodide();
        const sourceResponse = await fetch("./Rhizomatica_v7.py");
        if (!sourceResponse.ok) {
          throw new Error(`Could not load the Rhizomatica generator (${sourceResponse.status}).`);
        }
        pyodide.globals.set("rhizomatica_source", await sourceResponse.text());
        await pyodide.runPythonAsync(
          "rhizomatica_namespace = {'__name__': 'rhizomatica_client', '__file__': 'Rhizomatica_v7.py'}\n" +
          "exec(compile(rhizomatica_source, 'Rhizomatica_v7.py', 'exec'), rhizomatica_namespace)"
        );
        return pyodide;
      })().catch((error) => {
        runtimePromise = null;
        throw error;
      });
    }
    return runtimePromise;
  }

  function decodeXml(bytes) {
    const declaration = new TextDecoder("ascii").decode(bytes.slice(0, 256));
    const encodingMatch = declaration.match(/<\?xml[^>]*\bencoding\s*=\s*["']([^"']+)["']/i);
    const encoding = encodingMatch ? encodingMatch[1] : "utf-8";
    try {
      return new TextDecoder(encoding, { fatal: true }).decode(bytes);
    } catch (error) {
      throw new Error(`The EAD file could not be decoded as ${encoding}: ${error.message}`);
    }
  }

  async function generate(file) {
    if (!file || !file.name.toLowerCase().endsWith(".xml")) {
      throw new Error("Choose an EAD file with an .xml extension.");
    }
    if (file.size > maxFileBytes) {
      throw new Error("This EAD file is too large to process (32 MiB maximum).");
    }
    if (file.size === 0) {
      throw new Error("The selected EAD file is empty.");
    }

    const xmlText = decodeXml(new Uint8Array(await file.arrayBuffer()))
      .replace(/^\s*<\?xml\b[^?]*\?>/i, "");
    if (/<!DOCTYPE\b/i.test(xmlText)) {
      throw new Error("DOCTYPE declarations are not supported.");
    }

    const pyodide = await getRuntime();
    pyodide.globals.set("rhizomatica_xml", xmlText);
    pyodide.globals.set("rhizomatica_ratio", config.facet_redundancy_ratio);
    pyodide.globals.set("rhizomatica_url_base", config.finding_aid_url_base);
    const generatedHtml = await pyodide.runPythonAsync([
      "import xml.etree.ElementTree as ET",
      "rhizomatica = rhizomatica_namespace",
      "root = rhizomatica['strip_namespaces'](ET.fromstring(rhizomatica_xml))",
      "title, rows, facets = rhizomatica['build_records'](root, rhizomatica_ratio)",
      "if not rows:",
      "    raise ValueError('This finding aid has no file-level records to visualize.')",
      "ead_id = rhizomatica['text_of'](root.find('.//eadid'))",
      "for row in rows:",
      "    row['finding_aid_url'] = rhizomatica['finding_aid_url'](rhizomatica_url_base, ead_id, row['object_id'])",
      "rhizomatica['build_html'](title, rows, facets)",
    ].join("\n"));

    if (typeof generatedHtml !== "string" || generatedHtml.length === 0) {
      throw new Error("The generator did not return a rhizome page.");
    }
    return generatedHtml;
  }

  function showOutput(html, fileName) {
    if (outputUrl) {
      URL.revokeObjectURL(outputUrl);
    }
    outputUrl = URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" }));
    openOutput.href = outputUrl;
    downloadOutput.href = outputUrl;
    downloadOutput.download = `${fileName.replace(/\.xml$/i, "")}-rhizome.html`;
    output.hidden = false;
  }

  document.querySelectorAll('[role="tab"]').forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll('[role="tab"]').forEach((candidate) => {
        const selected = candidate === tab;
        candidate.setAttribute("aria-selected", String(selected));
        candidate.tabIndex = selected ? 0 : -1;
        document.getElementById(candidate.getAttribute("aria-controls")).hidden = !selected;
      });
      tab.focus();
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    output.hidden = true;
    generateButton.disabled = true;
    setMessage(status, "Processing the EAD file in your browser. The first run downloads the generator runtime…", false);
    try {
      if (!config) {
        throw new Error("Configuration has not loaded. Refresh the page and try again.");
      }
      const html = await generate(fileInput.files[0]);
      showOutput(html, fileInput.files[0].name);
      setMessage(status, "Rhizome generated successfully. Open it in a new tab or download the HTML file.", false);
    } catch (error) {
      setMessage(status, error.message || "Rhizome generation failed.", true);
    } finally {
      generateButton.disabled = false;
    }
  });

  settingsForm.addEventListener("submit", (event) => {
    event.preventDefault();
    try {
      const nextConfig = validateConfig({
        facet_redundancy_ratio: ratioInput.value,
        finding_aid_url_base: findingAidUrlInput.value,
      });
      localStorage.setItem(storageKey, JSON.stringify(nextConfig));
      config = nextConfig;
      showConfig(config);
      setMessage(settingsStatus, "Settings saved in this browser.", false);
    } catch (error) {
      setMessage(settingsStatus, error.message || "Settings could not be saved.", true);
    }
  });

  document.getElementById("reset-settings").addEventListener("click", async () => {
    try {
      const response = await fetch("./config.json", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Could not load default settings (${response.status}).`);
      }
      config = validateConfig(await response.json());
      localStorage.removeItem(storageKey);
      showConfig(config);
      setMessage(settingsStatus, "Default settings restored.", false);
    } catch (error) {
      setMessage(settingsStatus, error.message || "Default settings could not be restored.", true);
    }
  });

  document.getElementById("export-settings").addEventListener("click", () => {
    try {
      const currentConfig = validateConfig({
        facet_redundancy_ratio: ratioInput.value,
        finding_aid_url_base: findingAidUrlInput.value,
      });
      const configUrl = URL.createObjectURL(new Blob(
        [`${JSON.stringify(currentConfig, null, 2)}\n`],
        { type: "application/json" }
      ));
      const download = document.createElement("a");
      download.href = configUrl;
      download.download = "config.json";
      download.click();
      URL.revokeObjectURL(configUrl);
      setMessage(settingsStatus, "Config file downloaded.", false);
    } catch (error) {
      setMessage(settingsStatus, error.message || "Config file could not be downloaded.", true);
    }
  });

  loadConfig().catch((error) => {
    setMessage(status, error.message || "Configuration could not be loaded.", true);
    setMessage(settingsStatus, error.message || "Configuration could not be loaded.", true);
  });
})();

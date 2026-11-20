(() => {
  const form = document.getElementById("ead-form");
  const status = document.getElementById("status");
  const endpoint = window.RHIZOMATICA_GENERATE_URL;

  if (typeof endpoint !== "string" || endpoint.length === 0) {
    status.textContent = "The generator service has not been configured yet.";
    return;
  }

  let generateUrl;
  try {
    generateUrl = new URL(endpoint);
  } catch {
    status.textContent = "The generator service URL is invalid.";
    return;
  }

  const localHttp = generateUrl.protocol === "http:"
    && ["localhost", "127.0.0.1"].includes(generateUrl.hostname);
  if ((generateUrl.protocol !== "https:" && !localHttp)
      || !generateUrl.pathname.endsWith("/generate")) {
    status.textContent = "The generator service URL must be a secure /generate URL.";
    return;
  }

  form.action = generateUrl.href;
  form.addEventListener("submit", () => {
    status.textContent = "Uploading the XML and generating your rhizome…";
  });
})();

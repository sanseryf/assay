const SAMPLE_PAYLOAD = {
  batch_id: "B-1042",
  members: [
    {
      first_name: "Ada",
      last_name: "Lovelace",
      dob: "03/14/1990",
      zip: 7030,
      coverage_tier: "employee"
    },
    {
      first_name: "Grace\u200b",
      last_name: " Hopper",
      dob: "1906-12-09",
      zip: "07030",
      coverage_tier: "EMPLOYEE"
    }
  ]
};

const SAMPLE_SCHEMA = {
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  type: "object",
  required: ["batch_id", "members"],
  properties: {
    batch_id: { type: "string", minLength: 1 },
    members: {
      type: "array",
      minItems: 1,
      items: {
        type: "object",
        required: ["first_name", "last_name", "dob", "zip"],
        properties: {
          first_name: { type: "string", minLength: 1 },
          last_name: { type: "string", minLength: 1 },
          dob: { type: "string", format: "date" },
          zip: { type: "string", pattern: "^[0-9]{5}$" },
          coverage_tier: { enum: ["EMPLOYEE", "EMPLOYEE_SPOUSE", "EMPLOYEE_CHILD"] }
        },
        additionalProperties: false
      }
    }
  }
};

const elements = {
  payloads: document.getElementById("payloads"),
  schema: document.getElementById("schema"),
  rules: document.getElementById("rules"),
  redact: document.getElementById("redact"),
  run: document.getElementById("run"),
  sample: document.getElementById("sample"),
  status: document.getElementById("status"),
  empty: document.getElementById("empty"),
  report: document.getElementById("report"),
  summary: document.getElementById("summary"),
  next: document.getElementById("next"),
  groups: document.getElementById("groups"),
  correlated: document.getElementById("correlated"),
  download: document.getElementById("download"),
  payloadNote: document.getElementById("payload-note")
};

let lastReport = null;

function setStatus(message, kind) {
  elements.status.textContent = message || "";
  elements.status.className = "status" + (kind ? " " + kind : "");
}

function parsePayloads(text) {
  const trimmed = text.trim();
  if (!trimmed) {
    throw new Error("Paste a payload or load the sample.");
  }
  try {
    return [JSON.parse(trimmed)];
  } catch (whole) {
    const lines = trimmed.split(/\r?\n/).filter((line) => line.trim());
    if (lines.length <= 1) {
      throw new Error("Payload is not valid JSON: " + whole.message);
    }
    return lines.map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        throw new Error("Line " + (index + 1) + " is not valid JSON: " + error.message);
      }
    });
  }
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function stat(label, value, modifier) {
  const box = node("div", "stat" + (modifier ? " " + modifier : ""));
  box.appendChild(node("dt", null, label));
  box.appendChild(node("dd", null, value));
  return box;
}

function renderSummary(report) {
  elements.summary.replaceChildren(
    stat("Payloads", report.payloads_scanned),
    stat("Clean", report.payloads_clean, "clean"),
    stat("Findings", report.findings_total, report.errors ? "error" : ""),
    stat("Groups", report.groups.length),
    stat("Time", report.duration_ms + " ms")
  );
}

function renderNext(report) {
  elements.next.replaceChildren();
  if (!report.groups.length) {
    elements.next.classList.add("hidden");
    return;
  }
  const top = report.groups[0];
  const box = node("div", "next");
  if (report.groups.length === 1) {
    box.append("One cause. Fixing ");
    box.appendChild(node("strong", null, top.path));
    box.append(" clears every finding in the batch.");
  } else {
    const share = report.findings_total
      ? Math.round((top.count / report.findings_total) * 100)
      : 0;
    box.append("Start here. Fixing ");
    box.appendChild(node("strong", null, top.path));
    box.append(
      " clears " + top.count + " of " + report.findings_total +
      " findings (" + share + "%)."
    );
  }
  elements.next.appendChild(box);
  elements.next.classList.remove("hidden");
}

function groupCard(group, index, largestImpact) {
  const card = node("article", "group");

  const head = node("div", "group-head");
  head.appendChild(node("span", "rank", "#" + index));
  head.appendChild(node("span", "path", group.path));
  head.appendChild(node("span", "chip " + group.layer, group.layer));
  head.appendChild(node("span", "chip " + group.severity, group.severity));
  head.appendChild(
    node(
      "span",
      "records",
      group.payloads_affected + (group.payloads_affected === 1 ? " payload" : " payloads")
    )
  );
  card.appendChild(head);

  card.appendChild(node("p", "message", group.message));

  if (group.sample_values && group.sample_values.length) {
    card.appendChild(node("p", "samples", group.sample_values.join(", ")));
  }
  if (group.hint) {
    card.appendChild(node("p", "hint", group.hint));
  }

  const impact = node("div", "impact " + group.severity);
  const bar = node("span");
  bar.style.width = Math.max(4, Math.round((group.impact / largestImpact) * 100)) + "%";
  impact.appendChild(bar);
  card.appendChild(impact);

  return card;
}

function renderGroups(report) {
  elements.groups.replaceChildren();
  if (!report.groups.length) {
    elements.groups.appendChild(
      node("p", "next", "No findings. The payloads pass every check in this configuration.")
    );
    return;
  }
  const largest = Math.max(...report.groups.map((group) => group.impact || 0), 1);
  report.groups.forEach((group, index) => {
    elements.groups.appendChild(groupCard(group, index + 1, largest));
  });
}

function renderCorrelated(report) {
  elements.correlated.replaceChildren();
  const byFingerprint = new Map(report.groups.map((group) => [group.fingerprint, group]));
  const seen = new Set();
  report.groups.forEach((group) => {
    (group.correlated_with || []).forEach((fingerprint) => {
      const partner = byFingerprint.get(fingerprint);
      if (!partner) return;
      const key = [group.fingerprint, fingerprint].sort().join("|");
      if (seen.has(key)) return;
      seen.add(key);
      const note = node("p");
      note.append("Correlated. ");
      note.appendChild(node("strong", null, group.path));
      note.append(" and ");
      note.appendChild(node("strong", null, partner.path));
      note.append(" hit the same payloads. Likely one upstream cause.");
      elements.correlated.appendChild(note);
    });
  });
}

function renderReport(report) {
  lastReport = report;
  elements.empty.classList.add("hidden");
  elements.report.hidden = false;
  renderSummary(report);
  renderNext(report);
  renderGroups(report);
  renderCorrelated(report);
  const clean = report.payloads_clean;
  const total = report.payloads_scanned;
  const findings = report.findings_total;
  setStatus(
    "Checked " + total + (total === 1 ? " payload" : " payloads") +
      " in " + report.duration_ms + " ms. " +
      clean + " clean, " + findings + (findings === 1 ? " finding" : " findings") + ".",
    ""
  );
}

async function run() {
  let payloads;
  let schema;
  try {
    payloads = parsePayloads(elements.payloads.value);
    schema = JSON.parse(elements.schema.value);
  } catch (error) {
    setStatus(error.message, "error");
    return;
  }
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
    setStatus("Schema must be a JSON object.", "error");
    return;
  }

  const body = {
    payloads,
    schema,
    redact: elements.redact.checked
  };
  const rulesText = elements.rules.value.trim();
  if (rulesText) body.rules_yaml = rulesText;

  elements.run.disabled = true;
  elements.run.setAttribute("aria-busy", "true");
  setStatus("Checking " + (payloads.length === 1 ? "1 payload" : payloads.length + " payloads") + "...", "busy");

  try {
    const response = await fetch("/assay/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    if (!response.ok) {
      let detail = "The server returned HTTP " + response.status + ".";
      try {
        const problem = await response.json();
        if (problem.detail) {
          detail = typeof problem.detail === "string"
            ? problem.detail
            : JSON.stringify(problem.detail);
        }
      } catch (ignored) {}
      throw new Error(detail);
    }
    const report = await response.json();
    renderReport(report);
    elements.payloadNote.textContent =
      payloads.length + (payloads.length === 1 ? " payload" : " payloads") +
      " in this run. JSON or NDJSON, one value per line for a batch.";
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    elements.run.disabled = false;
    elements.run.removeAttribute("aria-busy");
  }
}

function loadFile(input, target, note) {
  const file = input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    target.value = String(reader.result);
    if (note) note.textContent = file.name + " (" + Math.ceil(file.size / 1024) + " KB)";
  };
  reader.onerror = () => setStatus("Could not read " + file.name + ".", "error");
  reader.readAsText(file);
  input.value = "";
}

function wireFile(buttonId, inputId, target, note) {
  const button = document.getElementById(buttonId);
  const input = document.getElementById(inputId);
  button.addEventListener("click", () => input.click());
  input.addEventListener("change", () => loadFile(input, target, note));
}

function download() {
  if (!lastReport) return;
  const blob = new Blob([JSON.stringify(lastReport, null, 2) + "\n"], {
    type: "application/json"
  });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "assay-report.json";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

elements.sample.addEventListener("click", () => {
  elements.payloads.value = JSON.stringify(SAMPLE_PAYLOAD, null, 2);
  elements.schema.value = JSON.stringify(SAMPLE_SCHEMA, null, 2);
  elements.rules.value = "";
  elements.payloads.scrollTop = 0;
  elements.payloadNote.textContent = "Sample payload with a bad date, a number ZIP, an enum case and hidden characters.";
  setStatus("Sample loaded. Run the check to see grouped findings.", "");
});

elements.run.addEventListener("click", run);
elements.download.addEventListener("click", download);

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    run();
  }
});

wireFile("upload-payloads", "payload-file", elements.payloads, elements.payloadNote);
wireFile("upload-schema", "schema-file", elements.schema, null);
wireFile("upload-rules", "rules-file", elements.rules, null);

if (new URLSearchParams(window.location.search).has("sample")) {
  elements.sample.click();
  run();
}

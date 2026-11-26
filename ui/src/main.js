import { basicSetup } from "codemirror";
import { json } from "@codemirror/lang-json";
import { forceLinting, forEachDiagnostic, lintGutter, linter } from "@codemirror/lint";
import { EditorState, StateEffect, StateField } from "@codemirror/state";
import { Decoration, EditorView, placeholder } from "@codemirror/view";
import { parseTree } from "jsonc-parser";

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
  editor: document.getElementById("editor"),
  editorShell: document.getElementById("editor-shell"),
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
  downloadHtml: document.getElementById("download-html"),
  payloadNote: document.getElementById("payload-note"),
  layerFilter: document.getElementById("layer-filter"),
  severityFilter: document.getElementById("severity-filter"),
  visibleCount: document.getElementById("visible-count"),
  themeToggle: document.getElementById("theme-toggle"),
  themeColor: document.getElementById("theme-color")
};

const state = {
  report: null,
  segments: [],
  request: null,
  schema: null,
  active: null,
  ranges: new Map(),
  layer: "all",
  severity: "all",
  suppressEdit: false
};

let currentDiagnostics = [];

const setMarks = StateEffect.define();

const marksField = StateField.define({
  create() {
    return Decoration.none;
  },
  update(marks, transaction) {
    marks = marks.map(transaction.changes);
    for (const effect of transaction.effects) {
      if (effect.is(setMarks)) marks = effect.value;
    }
    return marks;
  },
  provide: (field) => EditorView.decorations.from(field)
});

const assayTheme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "transparent",
    color: "var(--ink)"
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    fontFamily: "var(--mono)",
    fontSize: "12.5px",
    lineHeight: "1.55",
    overflow: "auto"
  },
  ".cm-content": {
    padding: "0.65rem 0",
    caretColor: "var(--accent-2)"
  },
  ".cm-gutters": {
    backgroundColor: "transparent",
    border: "none",
    color: "var(--ink-3)",
    fontFamily: "var(--mono)",
    fontSize: "11px"
  },
  ".cm-lineNumbers .cm-gutterElement": { padding: "0 0.5rem 0 0.65rem" },
  ".cm-activeLine": { backgroundColor: "rgba(198, 155, 107, 0.05)" },
  ".cm-activeLineGutter": {
    backgroundColor: "transparent",
    color: "var(--accent-2)"
  },
  ".cm-selectionBackground, ::selection": {
    backgroundColor: "rgba(198, 155, 107, 0.24) !important"
  },
  ".cm-cursor": { borderLeftColor: "var(--accent-2)" },
  ".cm-placeholder": { color: "var(--ink-3)" },
  ".cm-tooltip": {
    backgroundColor: "var(--bg-2)",
    border: "1px solid var(--line-2)",
    color: "var(--ink)",
    fontFamily: "var(--sans)",
    fontSize: "12px",
    borderRadius: "6px"
  },
  ".cm-tooltip-lint": { padding: "0.35rem 0.45rem" },
  ".cm-lintRange-error, .cm-lintRange-warning, .cm-lintRange-info": {
    backgroundImage: "none",
    borderBottom: "1px dashed currentColor",
    cursor: "pointer"
  },
  ".cm-lintRange-error": { color: "var(--ember)" },
  ".cm-lintRange-warning": { color: "var(--data)" },
  ".cm-lintRange-info": { color: "var(--green)" },
  ".cm-lint-marker-error": { content: "var(--ember)" },
  ".cm-panels": {
    backgroundColor: "var(--bg-1)",
    color: "var(--ink)"
  }
});

const editor = new EditorView({
  parent: elements.editor,
  state: EditorState.create({
    doc: "",
    extensions: [
      basicSetup,
      json(),
      lintGutter(),
      linter(() => currentDiagnostics, { delay: 120 }),
      marksField,
      assayTheme,
      placeholder("Paste one JSON value, or one JSON value per line for a batch."),
      EditorView.updateListener.of((update) => {
        if (!update.docChanged || state.suppressEdit) return;
        if (state.report) {
          window.setTimeout(() => {
            clearReport();
            setStatus("Payload changed. Run the check to refresh the report.", "");
          }, 0);
        }
      }),
      EditorView.domEventHandlers({
        mousedown(event, view) {
          const mark = event.target.closest?.("[data-fingerprint]");
          if (mark) {
            event.preventDefault();
            selectGroup(mark.getAttribute("data-fingerprint"));
            return true;
          }
          const position = view.posAtCoords({ x: event.clientX, y: event.clientY });
          if (position === null) return false;
          let hit = null;
          forEachDiagnostic(view.state, (diagnostic, from, to) => {
            if (!hit && position >= from && position <= to) hit = diagnostic;
          });
          if (hit && hit.group) {
            event.preventDefault();
            selectGroup(hit.group);
            return true;
          }
          return false;
        }
      })
    ]
  })
});

function setStatus(message, kind) {
  elements.status.textContent = message || "";
  elements.status.className = "status" + (kind ? " " + kind : "");
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function editorText() {
  return editor.state.doc.toString();
}

function setEditorText(text) {
  editor.dispatch({
    changes: { from: 0, to: editor.state.doc.length, insert: text }
  });
}

function splitPayloads(text) {
  const trimmed = text.trim();
  if (!trimmed) throw new Error("Paste a payload or load the sample.");
  try {
    const value = JSON.parse(trimmed);
    const start = Math.max(0, text.indexOf(trimmed));
    return [{ id: "payload-1", from: start, to: start + trimmed.length, text: trimmed, value }];
  } catch (wholeError) {
    const segments = [];
    let offset = 0;
    let index = 0;
    for (const raw of text.split("\n")) {
      const line = raw.replace(/\r$/, "");
      const start = offset;
      const end = offset + line.length;
      offset = offset + raw.length + 1;
      if (!line.trim()) continue;
      index += 1;
      let value;
      try {
        value = JSON.parse(line);
      } catch (error) {
        throw new Error("Line " + index + " is not valid JSON: " + error.message);
      }
      segments.push({ id: "payload-" + index, from: start, to: end, text: line, value });
    }
    if (segments.length <= 1) {
      throw new Error("Payload is not valid JSON: " + wholeError.message);
    }
    return segments;
  }
}

function segmentsForMapping() {
  try {
    return splitPayloads(editorText());
  } catch (error) {
    return [];
  }
}

function parsePath(path) {
  const clean = String(path || "").replace(/^\$/, "");
  const parts = [];
  let index = 0;
  while (index < clean.length) {
    if (clean[index] === ".") {
      index += 1;
      continue;
    }
    if (clean[index] === "[") {
      const end = clean.indexOf("]", index);
      if (end === -1) break;
      const token = clean.slice(index + 1, end);
      parts.push(token === "*" ? { wildcard: true } : { index: Number(token) });
      index = end + 1;
      continue;
    }
    let end = index;
    while (end < clean.length && clean[end] !== "." && clean[end] !== "[") end += 1;
    parts.push({ key: clean.slice(index, end) });
    index = end;
  }
  return parts;
}

function resolveNodes(root, parts) {
  let nodes = [root];
  for (const part of parts) {
    const next = [];
    for (const parent of nodes) {
      if (!parent) continue;
      if (part.key !== undefined) {
        if (parent.type === "object" && parent.children) {
          for (const property of parent.children) {
            if (property.type === "property" && property.children?.[0]?.value === part.key) {
              next.push(property.children[1]);
            }
          }
        }
      } else if (part.wildcard) {
        if (parent.type === "array" && parent.children) next.push(...parent.children);
      } else if (parent.type === "array" && parent.children?.[part.index]) {
        next.push(parent.children[part.index]);
      }
    }
    nodes = next.filter(Boolean);
    if (!nodes.length) break;
  }
  return nodes;
}

function offsetAtLineCol(text, line, col) {
  const lines = text.split("\n");
  let offset = 0;
  for (let index = 0; index < Math.min(line - 1, lines.length); index += 1) {
    offset += lines[index].length + 1;
  }
  return offset + Math.max(0, (col || 1) - 1);
}

function resolveOccurrence(occurrence) {
  if (!state.segments.length) return null;
  let segment = state.segments.find((item) => item.id === occurrence.payload_id);
  if (!segment) segment = state.segments[0];
  const base = segment.from;
  const text = segment.text;

  if (occurrence.line) {
    const start = base + Math.min(offsetAtLineCol(text, occurrence.line, occurrence.col), text.length);
    return { from: start, to: Math.min(start + 1, base + text.length) };
  }

  const root = parseTree(text);
  if (!root) return null;
  let parts = parsePath(occurrence.path);
  let nodes = resolveNodes(root, parts);
  if (!nodes.length && parts.length) {
    parts = parts.slice(0, -1);
    nodes = resolveNodes(root, parts);
  }
  if (!nodes.length) return null;
  const offsets = nodes.map((item) => ({
    from: base + item.offset,
    to: base + item.offset + Math.max(1, item.length)
  }));
  return offsets[0];
}

function occurrenceRanges(group) {
  const occurrences = group.occurrences || [{ payload_id: "payload-1", path: group.path }];
  const ranges = [];
  for (const occurrence of occurrences) {
    const range = resolveOccurrence(occurrence);
    if (range) {
      ranges.push({
        from: Math.max(0, range.from),
        to: Math.max(range.from + 1, range.to),
        severity: group.severity,
        fingerprint: group.fingerprint
      });
    }
  }
  return ranges;
}

function collectRanges() {
  state.ranges = new Map();
  for (const group of state.report?.groups || []) {
    state.ranges.set(group.fingerprint, occurrenceRanges(group));
  }
}

function buildMarks(active) {
  const decorations = [];
  for (const [fingerprint, ranges] of state.ranges) {
    for (const range of ranges) {
      const classes =
        "cm-assay-" +
        range.severity +
        (fingerprint === active ? " cm-assay-active" : "");
      decorations.push(
        Decoration.mark({
          class: classes,
          attributes: { "data-fingerprint": fingerprint }
        }).range(range.from, range.to)
      );
    }
  }
  decorations.sort((left, right) => left.from - right.from || left.to - right.to);
  return Decoration.set(decorations, true);
}

function buildDiagnostics() {
  const diagnostics = [];
  const seen = new Set();
  for (const group of state.report?.groups || []) {
    for (const range of state.ranges.get(group.fingerprint) || []) {
      const key = range.from + ":" + range.to + ":" + group.fingerprint;
      if (seen.has(key)) continue;
      seen.add(key);
      diagnostics.push({
        from: range.from,
        to: range.to,
        severity: group.severity,
        source: "assay",
        message:
          group.message +
          (group.hint ? "\n" + group.hint : "") +
          "\n" +
          group.path,
        group: group.fingerprint,
        actions: [
          {
            name: "Show group",
            apply: () => selectGroup(group.fingerprint)
          }
        ]
      });
    }
  }
  return diagnostics;
}

function refreshMarks() {
  editor.dispatch({ effects: setMarks.of(buildMarks(state.active)) });
  currentDiagnostics = buildDiagnostics();
  forceLinting(editor);
}

function countFor(group) {
  return group.occurrences_total || group.count;
}

function plural(count, singular) {
  return count + " " + singular + (count === 1 ? "" : "s");
}

function renderSummary(report) {
  const elapsed = report.duration_ms < 1 ? "<1 ms" : report.duration_ms + " ms";
  const stat = (label, value, modifier) => {
    const box = node("div", "stat" + (modifier ? " " + modifier : ""));
    box.appendChild(node("dt", null, label));
    box.appendChild(node("dd", null, value));
    return box;
  };
  elements.summary.replaceChildren(
    stat("Payloads", report.payloads_scanned),
    stat("Clean", report.payloads_clean, "clean"),
    stat("Findings", report.findings_total, report.errors ? "error" : ""),
    stat("Groups", report.groups.length),
    stat("Time", elapsed)
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
      " clears " +
        top.count +
        " of " +
        report.findings_total +
        " findings (" +
        share +
        "%)."
    );
  }
  elements.next.appendChild(box);
  elements.next.classList.remove("hidden");
}

function occurrenceLabel(occurrence, group) {
  if (occurrence.line) {
    return occurrence.path + " · line " + occurrence.line + ":" + (occurrence.col || 0);
  }
  return occurrence.path || group.path;
}

function renderOccurrences(group) {
  const list = node("div", "occurrences");
  list.hidden = true;
  const occurrences = group.occurrences || [];
  if (!occurrences.length) {
    list.appendChild(
      node("p", "note", "This group has no record-level occurrence in the payload.")
    );
    return list;
  }
  occurrences.forEach((occurrence) => {
    const row = node("button", "occurrence");
    row.type = "button";
    row.appendChild(node("span", "occ-payload", occurrence.payload_id));
    row.appendChild(node("span", "occ-path", occurrenceLabel(occurrence, group)));
    if (occurrence.value_preview) {
      row.appendChild(node("span", "occ-value", occurrence.value_preview));
    }
    row.addEventListener("click", () => {
      const range = resolveOccurrence(occurrence);
      selectGroup(group.fingerprint, range);
    });
    list.appendChild(row);
  });
  return list;
}

function groupCard(group, index, largestImpact) {
  const card = node("article", "group");
  card.dataset.fingerprint = group.fingerprint;
  card.dataset.layer = group.layer;
  card.dataset.severity = group.severity;

  const head = node("div", "group-head");
  head.appendChild(node("span", "rank", "#" + index));
  const path = node("button", "path path-button", group.path);
  path.type = "button";
  path.title = "Highlight this path in the payload";
  path.addEventListener("click", () => selectGroup(group.fingerprint));
  head.appendChild(path);
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

  const actions = node("div", "group-actions");
  const expand = node("button", "link expand", "Show " + plural(countFor(group), "record"));
  expand.type = "button";
  expand.setAttribute("aria-expanded", "false");
  const occurrences = renderOccurrences(group);
  expand.addEventListener("click", () => {
    occurrences.hidden = !occurrences.hidden;
    expand.setAttribute("aria-expanded", String(!occurrences.hidden));
    expand.textContent = occurrences.hidden
      ? "Show " + plural(countFor(group), "record")
      : "Hide records";
    card.classList.toggle("expanded", !occurrences.hidden);
  });
  actions.appendChild(expand);

  const markdown = node("button", "link", "Copy MD");
  markdown.type = "button";
  markdown.title = "Copy a Markdown ticket, redacted";
  markdown.addEventListener("click", () => copyTicket(group, "markdown"));
  actions.appendChild(markdown);

  const jira = node("button", "link", "Copy Jira");
  jira.type = "button";
  jira.title = "Copy Jira wiki markup, redacted";
  jira.addEventListener("click", () => copyTicket(group, "jira"));
  actions.appendChild(jira);

  const adf = node("button", "link", "Copy ADF");
  adf.type = "button";
  adf.title = "Copy an Atlassian Document Format payload for the Jira API";
  adf.addEventListener("click", () => copyTicket(group, "adf"));
  actions.appendChild(adf);
  card.appendChild(actions);
  card.appendChild(occurrences);

  const impact = node("div", "impact " + group.severity);
  const bar = node("span");
  bar.style.width = Math.max(4, Math.round((group.impact / largestImpact) * 100)) + "%";
  impact.appendChild(bar);
  card.appendChild(impact);

  return card;
}

function applyFilters() {
  if (!state.report) return;
  const cards = Array.from(elements.groups.querySelectorAll(".group"));
  let visible = 0;
  cards.forEach((card) => {
    const layerMatch = state.layer === "all" || card.dataset.layer === state.layer;
    const severityMatch =
      state.severity === "all" || card.dataset.severity === state.severity;
    const show = layerMatch && severityMatch;
    card.hidden = !show;
    if (show) visible += 1;
  });
  const total = state.report.groups.length;
  elements.visibleCount.textContent =
    visible === total ? total + " groups" : "Showing " + visible + " of " + total + " groups";
  if (!visible) {
    if (!elements.groups.querySelector(".empty-filter")) {
      elements.groups.appendChild(
        node("p", "note empty-filter", "No groups match these filters.")
      );
    }
  } else {
    elements.groups.querySelector(".empty-filter")?.remove();
  }
}

function renderGroups(report) {
  elements.groups.replaceChildren();
  if (!report.groups.length) {
    elements.groups.appendChild(
      node("p", "next", "No findings. The payloads pass every check in this configuration.")
    );
    elements.visibleCount.textContent = "";
    return;
  }
  const largest = Math.max(...report.groups.map((group) => group.impact || 0), 1);
  report.groups.forEach((group, index) => {
    elements.groups.appendChild(groupCard(group, index + 1, largest));
  });

  const layers = Array.from(new Set(report.groups.map((group) => group.layer))).sort();
  elements.layerFilter.replaceChildren(node("option", null, "All layers"));
  elements.layerFilter.firstChild.value = "all";
  layers.forEach((layer) => {
    const option = node("option", null, layer);
    option.value = layer;
    elements.layerFilter.appendChild(option);
  });
  elements.layerFilter.value = state.layer;
  elements.severityFilter.value = state.severity;
  applyFilters();
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

function renderReport(report, segments, request) {
  state.report = report;
  state.segments = segments;
  state.request = request;
  state.active = null;
  collectRanges();

  elements.empty.classList.add("hidden");
  elements.report.hidden = false;
  renderSummary(report);
  renderNext(report);
  renderGroups(report);
  renderCorrelated(report);
  refreshMarks();

  const total = report.payloads_scanned;
  const clean = report.payloads_clean;
  const findings = report.findings_total;
  const elapsed = report.duration_ms < 1 ? "<1 ms" : report.duration_ms + " ms";
  setStatus(
    "Checked " +
      total +
      (total === 1 ? " payload" : " payloads") +
      " in " +
      elapsed +
      ". " +
      clean +
      " clean, " +
      findings +
      (findings === 1 ? " finding" : " findings") +
      ".",
    ""
  );
}

function clearReport() {
  state.report = null;
  state.request = null;
  state.active = null;
  state.ranges = new Map();
  currentDiagnostics = [];
  editor.dispatch({ effects: setMarks.of(Decoration.none) });
  forceLinting(editor);
  elements.report.hidden = true;
  elements.empty.classList.remove("hidden");
  elements.groups.replaceChildren();
  elements.summary.replaceChildren();
  elements.next.replaceChildren();
  elements.correlated.replaceChildren();
  elements.visibleCount.textContent = "";
}

function selectGroup(fingerprint, pushedRange) {
  if (!state.report) return;
  const group = state.report.groups.find((item) => item.fingerprint === fingerprint);
  if (!group) return;
  state.active = fingerprint;
  refreshMarks();

  elements.groups.querySelectorAll(".group").forEach((card) => {
    card.classList.toggle("active", card.dataset.fingerprint === fingerprint);
  });

  const ranges = state.ranges.get(fingerprint) || [];
  const range = pushedRange || ranges[0];
  if (range) {
    editor.dispatch({
      selection: { anchor: range.from, head: range.to },
      effects: [EditorView.scrollIntoView(range.from, { y: "center" })]
    });
    editor.focus();
  }
}

async function run() {
  let segments;
  let schema;
  try {
    segments = splitPayloads(editorText());
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
    payloads: segments.map((segment) => segment.value),
    schema,
    redact: elements.redact.checked
  };
  state.schema = schema;
  const rulesText = elements.rules.value.trim();
  if (rulesText) body.rules_yaml = rulesText;

  elements.run.disabled = true;
  elements.run.setAttribute("aria-busy", "true");
  setStatus(
    "Checking " +
      (segments.length === 1 ? "1 payload" : segments.length + " payloads") +
      "...",
    "busy"
  );

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
          detail =
            typeof problem.detail === "string" ? problem.detail : JSON.stringify(problem.detail);
        }
      } catch (ignored) {}
      throw new Error(detail);
    }
    const report = await response.json();
    renderReport(report, segments, body);
    elements.payloadNote.textContent =
      segments.length +
      (segments.length === 1 ? " payload" : " payloads") +
      " in this run. Click a group to jump to its spot in the payload.";
    return report;
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    elements.run.disabled = false;
    elements.run.removeAttribute("aria-busy");
  }
}

function downloadBlob(content, type, filename) {
  const blob = new Blob([content], { type });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

function downloadJson() {
  if (!state.report) return;
  downloadBlob(
    JSON.stringify(state.report, null, 2) + "\n",
    "application/json",
    "assay-report.json"
  );
}

async function downloadHtml() {
  if (!state.request) return;
  elements.downloadHtml.disabled = true;
  try {
    const response = await fetch("/assay/export/html", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.request)
    });
    if (!response.ok) throw new Error("HTTP " + response.status);
    downloadBlob(await response.text(), "text/html", "assay-report.html");
  } catch (error) {
    setStatus("Could not build the HTML report: " + error.message, "error");
  } finally {
    elements.downloadHtml.disabled = false;
  }
}

async function copyText(text, message) {
  try {
    await navigator.clipboard.writeText(text);
    setStatus(message, "");
    return;
  } catch (error) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "true");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    if (copied) {
      setStatus(message, "");
    } else {
      setStatus("Could not copy. Select the ticket text manually.", "error");
    }
  }
}

function shape(value) {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number") return "####";
  if (typeof value === "boolean") return "true/false";
  if (Array.isArray(value)) return "[" + value.length + " items]";
  if (typeof value === "object") return "{...}";
  const text = String(value);
  if (!text) return '""';
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) return "####-##-##";
  return text[0] + "*".repeat(Math.max(3, text.length - 1));
}

function buildMinimal(value, parts) {
  if (!parts.length) return shape(value);
  const [part, ...rest] = parts;
  if (part.key !== undefined) {
    return { [part.key]: buildMinimal(value?.[part.key], rest) };
  }
  if (part.wildcard) {
    return [buildMinimal(Array.isArray(value) ? value[0] : undefined, rest)];
  }
  return [buildMinimal(Array.isArray(value) ? value[part.index] : undefined, rest)];
}

function firstRecordFor(group) {
  if (!state.segments.length) return { payload: null };
  const occurrence = (group.occurrences || [])[0];
  let segment = state.segments[0];
  if (occurrence) {
    segment = state.segments.find((item) => item.id === occurrence.payload_id) || segment;
  }
  return { segment, occurrence };
}

function minimalRepro(group) {
  const { segment, occurrence } = firstRecordFor(group);
  if (!segment || !segment.value) return "{}";
  const path = occurrence?.path || group.path;
  const parts = parsePath(path);
  if (!parts.length) return JSON.stringify(shape(segment.value));
  return JSON.stringify(buildMinimal(segment.value, parts), null, 2);
}

function schemaAt(schema, parts) {
  let node = schema;
  for (const part of parts) {
    if (!node || typeof node !== "object") return null;
    if (part.key !== undefined) {
      node = node.properties?.[part.key] || null;
    } else {
      node = node.items || null;
    }
  }
  return node;
}

function expectedText(group) {
  const schemaNode = schemaAt(state.schema || {}, parsePath(group.path));
  if (!schemaNode) return null;
  if (schemaNode.enum) return "one of " + schemaNode.enum.join(", ");
  if (schemaNode.const !== undefined) return "exactly " + JSON.stringify(schemaNode.const);
  const parts = [];
  if (schemaNode.type) parts.push(String(schemaNode.type));
  if (schemaNode.format) parts.push("format " + schemaNode.format);
  if (schemaNode.pattern) parts.push("matching " + schemaNode.pattern);
  if (schemaNode.minLength !== undefined) parts.push("at least " + schemaNode.minLength + " chars");
  return parts.length ? parts.join(", ") : null;
}

function ticketLabel(group) {
  const raw = "assay-" + group.fingerprint;
  return raw
    .replace(/[^A-Za-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 200);
}

function ticketADF(group) {
  const label = ticketLabel(group);
  const { occurrence } = firstRecordFor(group);
  const actual = shape(occurrence?.value_preview || "");
  const expected = expectedText(group) || "see the message above";
  const rows = [
    ["Path", occurrence?.path || group.path],
    ["Layer", group.layer],
    ["Severity", group.severity],
    ["Affected", plural(group.payloads_affected, "payload") + ", " + plural(countFor(group), "finding")],
    ["Expected", expected],
    ["Actual", actual + " (redacted)"],
    ["Fix", group.hint || "See the path above."],
    ["Fingerprint", group.fingerprint],
    ["Label", label]
  ];
  const text = (value) => ({ type: "text", text: String(value) });
  return {
    type: "doc",
    version: 1,
    content: [
      { type: "heading", attrs: { level: 3 }, content: [text(group.message)] },
      {
        type: "bulletList",
        content: rows.map(([term, value]) => ({
          type: "listItem",
          content: [
            {
              type: "paragraph",
              content: [
                { type: "text", text: term + ": ", marks: [{ type: "strong" }] },
                text(value)
              ]
            }
          ]
        }))
      },
      { type: "heading", attrs: { level: 4 }, content: [text("Minimal repro")] },
      {
        type: "codeBlock",
        attrs: { language: "json" },
        content: [text(minimalRepro(group))]
      }
    ]
  };
}

function ticketText(group, format) {
  const label = ticketLabel(group);
  const { segment, occurrence } = firstRecordFor(group);
  const path = occurrence?.path || group.path;
  const actual = shape(occurrence?.value_preview || "");
  const expected = expectedText(group) || "see the message above";
  const repro = minimalRepro(group);
  const lines = [
    ["Path", path],
    ["Layer", group.layer],
    ["Severity", group.severity],
    ["Affected", plural(group.payloads_affected, "payload") + ", " + plural(countFor(group), "finding")],
    ["Expected", expected],
    ["Actual", actual + " (redacted)"],
    ["Fix", group.hint || "See the path above."],
    ["Fingerprint", group.fingerprint],
    ["Label", label]
  ];
  if (!segment) return "";

  if (format === "adf") {
    return JSON.stringify(ticketADF(group), null, 2);
  }

  if (format === "jira") {
    const body = lines.map(([term, value]) => "*" + term + ":* " + value).join("\n");
    return [
      "h3. " + group.message,
      body,
      "",
      "{panel:title=Minimal repro}",
      "{code:json}",
      repro,
      "{code}",
      "{panel}",
      "",
      "Label this issue {{" + label + "}} so the next run can find it again."
    ].join("\n");
  }

  return [
    "### " + group.message,
    "",
    ...lines.map(([term, value]) => "- " + term + ": " + value),
    "",
    "**Minimal repro**",
    "```json",
    repro,
    "```",
    "",
    "_Values are redacted by default. Label this issue `" + label + "`._"
  ].join("\n");
}

async function copyTicket(group, format) {
  const text = ticketText(group, format);
  if (!text) {
    setStatus("Run the check first.", "error");
    return;
  }
  await copyText(
    text,
    "Ticket copied. Label " + ticketLabel(group) + "."
  );
}

function wireFile(buttonId, inputId, apply) {
  const button = document.getElementById(buttonId);
  const input = document.getElementById(inputId);
  button.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (file) loadFile(file, apply);
    input.value = "";
  });
}

function loadFile(file, apply) {
  const reader = new FileReader();
  reader.onload = () => apply(String(reader.result), file);
  reader.onerror = () => setStatus("Could not read " + file.name + ".", "error");
  reader.readAsText(file);
}

function wireDrop(zone, apply) {
  ["dragenter", "dragover"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("dragover");
    });
  });
  ["dragleave", "drop"].forEach((name) => {
    zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.remove("dragover");
    });
  });
  zone.addEventListener("drop", (event) => {
    const file = event.dataTransfer?.files?.[0];
    if (file) loadFile(file, apply);
  });
}

function applyPayloadText(text, file) {
  setEditorText(text);
  elements.payloadNote.textContent = file
    ? file.name + " (" + Math.ceil(file.size / 1024) + " KB)"
    : "Loaded into the editor.";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem("assay.theme", theme);
  } catch (error) {}
  const day = theme === "day";
  if (elements.themeColor) elements.themeColor.content = day ? "#D8CDB7" : "#030705";
  if (elements.themeToggle) {
    elements.themeToggle.setAttribute("aria-pressed", day ? "true" : "false");
    elements.themeToggle.setAttribute(
      "aria-label",
      day ? "Switch to night theme" : "Switch to day theme"
    );
  }
}

function wireTheme() {
  if (!elements.themeToggle) return;
  elements.themeToggle.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "day" ? "night" : "day");
  });
  applyTheme(document.documentElement.dataset.theme === "day" ? "day" : "night");
}

elements.sample.addEventListener("click", () => {
  setEditorText(JSON.stringify(SAMPLE_PAYLOAD, null, 2));
  elements.schema.value = JSON.stringify(SAMPLE_SCHEMA, null, 2);
  elements.rules.value = "";
  elements.payloadNote.textContent =
    "Sample payload with a bad date, a number ZIP, an enum case and hidden characters.";
  setStatus("Sample loaded. Run the check to see grouped findings.", "");
});

elements.run.addEventListener("click", run);
elements.download.addEventListener("click", downloadJson);
elements.downloadHtml.addEventListener("click", downloadHtml);
elements.layerFilter.addEventListener("change", () => {
  state.layer = elements.layerFilter.value;
  applyFilters();
});
elements.severityFilter.addEventListener("change", () => {
  state.severity = elements.severityFilter.value;
  applyFilters();
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
    event.preventDefault();
    run();
  }
});

wireTheme();
wireFile("upload-payloads", "payload-file", applyPayloadText);
wireFile("upload-schema", "schema-file", (text, file) => {
  elements.schema.value = text;
  setStatus(file.name + " loaded as the schema.", "");
});
wireFile("upload-rules", "rules-file", (text, file) => {
  elements.rules.value = text;
  setStatus(file.name + " loaded as the rules.", "");
});
wireDrop(elements.editorShell, applyPayloadText);
wireDrop(elements.schema, (text) => {
  elements.schema.value = text;
});
wireDrop(elements.rules, (text) => {
  elements.rules.value = text;
});

if (new URLSearchParams(window.location.search).has("sample")) {
  const params = new URLSearchParams(window.location.search);
  elements.sample.click();
  run().then((report) => {
    const requested = Number(params.get("group") || 0);
    if (report && requested > 0 && report.groups[requested - 1]) {
      openFirstGroup(report.groups[requested - 1].fingerprint);
    }
  });
}

function openFirstGroup(fingerprint) {
  state.layer = "all";
  state.severity = "all";
  elements.layerFilter.value = "all";
  elements.severityFilter.value = "all";
  applyFilters();
  selectGroup(fingerprint);
}

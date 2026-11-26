import { mkdir } from "node:fs/promises";
import puppeteer from "puppeteer-core";

const BASE = process.env.ASSAY_URL || "http://127.0.0.1:8000";
const CHROME =
  process.env.CHROME_PATH || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const OUT = process.env.ASSAY_SHOT_DIR || "smoke-shots";
await mkdir(OUT, { recursive: true });

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  args: ["--no-sandbox", "--disable-gpu"]
});
const context = browser.defaultBrowserContext();
await context.overridePermissions(BASE, ["clipboard-read", "clipboard-write"]);
const page = await browser.newPage();
await page.setViewport({ width: 1500, height: 1250, deviceScaleFactor: 1 });
const errors = [];
page.on("pageerror", (error) => errors.push("pageerror: " + error.message));
page.on("console", (message) => {
  if (message.type() === "error") errors.push("console: " + message.text());
});

await page.goto(`${BASE}/?sample=1&theme=night&still=1`, {
  waitUntil: "networkidle0"
});
await page.waitForSelector(".group");

const initial = await page.evaluate(() => ({
  groups: document.querySelectorAll(".group").length,
  marks: document.querySelectorAll(".cm-assay-error, .cm-assay-warning").length,
  diagnostics: document.querySelectorAll(".cm-lintRange-error, .cm-lintRange-warning").length,
  editorLines: document.querySelectorAll(".cm-line").length,
  layerOptions: document.querySelectorAll("#layer-filter option").length,
  groupsLabel: document.querySelector("#visible-count").textContent
}));

await page.click(".group .path-button");
await new Promise((resolve) => setTimeout(resolve, 250));
const afterGroupClick = await page.evaluate(() => ({
  activeCards: document.querySelectorAll(".group.active").length,
  activeMarks: document.querySelectorAll(".cm-assay-active").length,
  selection: String(window.getSelection()).slice(0, 30)
}));

await page.click(".cm-lintRange-warning");
await new Promise((resolve) => setTimeout(resolve, 200));
const afterSquiggle = await page.evaluate(() => ({
  active: document.querySelector(".group.active")?.dataset.fingerprint,
  activeMarks: document.querySelectorAll(".cm-assay-active").length
}));

await page.click(".group .expand");
const expanded = await page.evaluate(() => ({
  visibleRows: Array.from(document.querySelectorAll(".group .occurrence")).filter(
    (row) => row.offsetParent !== null
  ).length,
  label: document.querySelector(".group .expand").textContent
}));

await page.click('.group [title^="Copy a Markdown"]');
await new Promise((resolve) => setTimeout(resolve, 250));
const ticket = await page.evaluate(() => navigator.clipboard.readText());
const copyStatus = await page.evaluate(() => document.querySelector("#status").textContent);

await page.select("#severity-filter", "warning");
const filtered = await page.evaluate(() => ({
  visibleCards: Array.from(document.querySelectorAll(".group")).filter(
    (card) => card.offsetParent !== null
  ).length,
  count: document.querySelector("#visible-count").textContent
}));

await page.screenshot({ path: `${OUT}/assay-editor-actions.png` });

const page2 = await browser.newPage();
await page2.setViewport({ width: 1500, height: 1250, deviceScaleFactor: 1 });
await page2.goto(`${BASE}/?sample=1&group=4&theme=night&still=1`, {
  waitUntil: "networkidle0"
});
await page2.waitForSelector(".group.active");
await page2.screenshot({ path: `${OUT}/assay-editor-night.png` });

console.log(
  JSON.stringify(
    {
      initial,
      afterGroupClick,
      afterSquiggle,
      expanded,
      filtered,
      ticket: ticket.slice(0, 320),
      ticketLength: ticket.length,
      copyStatus,
      errors
    },
    null,
    2
  )
);
await browser.close();

// Usage: node dashboard_axe.js GOLD_DIR DASHBOARD_HTML
// Serves each audience folder of a gold build with the dashboard copied in, runs axe-core on every tab in the
// light and dark schemes, and fails (exit 1) on any critical or serious violation or any CSP violation.
const fs = require("fs"), http = require("http"), path = require("path"), os = require("os");
const puppeteer = require("puppeteer");
const axeSource = require("axe-core").source;
const [gold, dashboard] = process.argv.slice(2);
const root = fs.mkdtempSync(path.join(os.tmpdir(), "pages-"));
const pick = (dir) => path.join(gold, dir, fs.readdirSync(path.join(gold, dir)).sort()[0]);
const pages = { central: path.join(gold, "central"), exec: path.join(gold, "exec"), manager: pick("manager"),
  person: pick("person"), empty: null };
for (const [name, src] of Object.entries(pages)) {
  const dst = path.join(root, name);
  if (src) fs.cpSync(src, dst, { recursive: true }); else fs.mkdirSync(dst);
  fs.copyFileSync(dashboard, path.join(dst, "index.html"));
}
const server = http.createServer((req, res) => {
  const file = path.join(root, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { "Content-Type": file.endsWith(".html") ? "text/html" : "text/csv" });
  fs.createReadStream(file).pipe(res);
});
server.listen(0, "127.0.0.1", async () => {
  const base = "http://127.0.0.1:" + server.address().port;
  const browser = await puppeteer.launch({ headless: "shell", args: ["--no-sandbox"] });
  const bad = [];
  let states = 0;
  for (const scheme of ["light", "dark"]) {
    for (const name of Object.keys(pages)) {
      const page = await browser.newPage();
      await page.emulateMediaFeatures([{ name: "prefers-color-scheme", value: scheme }]);
      await page.evaluateOnNewDocument(() => {
        window.__csp = [];
        document.addEventListener("securitypolicyviolation", (e) => window.__csp.push(e.violatedDirective));
      });
      await page.goto(base + "/" + name + "/index.html", { waitUntil: "networkidle0" });
      await page.evaluate(axeSource);
      const chart = await page.$eval("svg", (e) => e.getAttribute("aria-label")).catch(() => "");
      if (/^schema_version\b/.test(chart)) bad.push(`${name} ${scheme}: the first chart is of schema_version`);
      const count = (await page.$$("#tabs button")).length;
      for (let i = 0; i < Math.max(count, 1); i += 1) {
        if (count) { await (await page.$$("#tabs button"))[i].click(); }
        const res = await page.evaluate(() => axe.run(document, { resultTypes: ["violations"] }));
        states += 1;
        for (const v of res.violations) {
          if (v.impact === "critical" || v.impact === "serious") bad.push(`${name} ${scheme} tab ${i}: ${v.id} (${v.impact})`);
        }
      }
      for (const d of await page.evaluate(() => window.__csp)) bad.push(`${name} ${scheme}: CSP ${d}`);
      await page.close();
    }
  }
  await browser.close();
  server.close();
  console.log(`axe-core ${require("axe-core/package.json").version}: ${states} page states, ${bad.length} problems`);
  bad.forEach((b) => console.log("  " + b));
  process.exit(bad.length ? 1 : 0);
});

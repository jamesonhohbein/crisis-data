// T9 render check: load the site headless, wait for the map, confirm the coverage fill layer and
// the "lost since 2024" layer both draw features, save a screenshot.
// Usage: NODE_PATH=<dir containing playwright> node tests/t9_render.cjs <url> <screenshot.png>
const { chromium } = require("playwright");

(async () => {
  const [url, shot] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1100, height: 1400 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.covmapReady === true && window.covmap.loaded(), null, { timeout: 30000 });
  await page.waitForTimeout(1000);
  const result = await page.evaluate(() => {
    const m = window.covmap;
    const n = (id) => (m.getLayer(id) ? m.queryRenderedFeatures({ layers: [id] }).length : -1);
    const fill = n("cov-wfp");
    m.setLayoutProperty("cov-wfp", "visibility", "none");
    m.setLayoutProperty("cov-fewsnet", "visibility", "visible");
    return { fill_wfp: fill, lost: n("lost"), layers: m.getStyle().layers.map((l) => l.id) };
  });
  await page.evaluate(() => {
    window.covmap.setLayoutProperty("cov-fewsnet", "visibility", "none");
    window.covmap.setLayoutProperty("cov-wfp", "visibility", "visible");
  });
  await page.waitForTimeout(500);
  await page.screenshot({ path: shot, fullPage: false });
  result.errors = errors;
  console.log(JSON.stringify(result));
  await browser.close();
  process.exit(result.fill_wfp > 0 && result.lost > 0 && errors.length === 0 ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });

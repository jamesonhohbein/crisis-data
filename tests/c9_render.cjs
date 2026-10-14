// C9 render check (spec 002): load /cholera/ headless, wait for the map, confirm the choropleth draws
// features for Sudan and Nigeria and the charts draw lines, save a screenshot.
// Usage: NODE_PATH=<dir containing playwright> node tests/c9_render.cjs <cholera url> <screenshot.png>
const { chromium } = require("playwright");

(async () => {
  const [url, shot] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1100, height: 1500 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForFunction(() => window.cholmapReady === true && window.cholmap.loaded(), null, { timeout: 30000 });
  await page.waitForTimeout(1000);
  const sdn = await page.evaluate(() => window.cholmap.queryRenderedFeatures({ layers: ["chol-fill"] }).length);
  const sdnChart = await page.evaluate(() => document.querySelectorAll("#chart path.series").length);
  await page.check('input[name=c][value=NGA]');
  await page.waitForTimeout(1200);
  const nga = await page.evaluate(() => window.cholmap.queryRenderedFeatures({ layers: ["chol-fill"] }).length);
  const ngaChart = await page.evaluate(() => document.querySelectorAll("#chart path.series").length);
  const boundary = await page.evaluate(() => document.querySelectorAll("#boundary path.series").length);
  await page.check('input[name=c][value=SDN]');
  await page.waitForTimeout(1200);
  await page.screenshot({ path: shot, fullPage: false });
  const result = { sdn_features: sdn, nga_features: nga, sdn_chart_lines: sdnChart, nga_chart_lines: ngaChart,
    boundary_lines: boundary, errors };
  console.log(JSON.stringify(result));
  await browser.close();
  const ok = sdn > 0 && nga > 0 && sdnChart >= 2 && ngaChart >= 3 && boundary >= 3 && errors.length === 0;
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });

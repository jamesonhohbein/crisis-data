// /cholera/: admin1 choropleth with a week slider, national weekly chart with WHO overlaid, and
// the JHU boundary chart. Plain MapLibre GL JS and hand-drawn SVG, no base tiles, no API keys.
(async function () {
  const [S, geo] = await Promise.all([
    fetch("series.json").then((r) => r.json()),
    fetch("data/admin1.geojson").then((r) => r.json()),
  ]);
  let country = "SDN";
  let measure = "weekly";
  let wi = 0;

  const steps = [[0, "#f0f0f0"], [1, "#fee8c8"], [10, "#fdbb84"], [50, "#e34a33"], [200, "#7f0000"]];
  const fill = ["case", ["==", ["get", "v"], -1], "#d9d9d9",
    ["step", ["get", "v"], steps[0][1], ...steps.slice(1).flat()]];

  function weekData() {
    const C = S.countries[country];
    const m = C.measures[measure] || C.measures.weekly;
    for (const f of geo.features) {
      const p = f.properties;
      if (p.country !== country) { p.v = -2; continue; }
      const row = m[p.adm1_pcode];
      const v = row ? row[wi] : null;
      p.v = v === null || v === undefined ? -1 : v;
    }
    return geo;
  }

  const map = new maplibregl.Map({
    container: "map",
    style: { version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#f4f4f0" } }] },
    bounds: S.countries[country].bounds, fitBoundsOptions: { padding: 10 }, attributionControl: false,
  });
  map.addControl(new maplibregl.AttributionControl({ compact: false, customAttribution: "Boundaries: OCHA COD-AB via HDX (CC BY-IGO)" }));
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
  window.cholmap = map;

  map.on("load", () => {
    map.addSource("admin1", { type: "geojson", data: weekData() });
    map.addLayer({ id: "chol-fill", type: "fill", source: "admin1", filter: [">=", ["get", "v"], -1],
      paint: { "fill-color": fill, "fill-opacity": 0.9 } });
    map.addLayer({ id: "admin1-line", type: "line", source: "admin1", filter: [">=", ["get", "v"], -1],
      paint: { "line-color": "#666", "line-width": 0.6 } });
    window.cholmapReady = true;
  });

  const slider = document.getElementById("week");
  const label = document.getElementById("weeklabel");
  function setCountry(c) {
    country = c;
    const C = S.countries[c];
    if (!C.measures[measure]) { measure = "weekly"; document.querySelector('input[name=m][value=weekly]').checked = true; }
    // open on the peak week of the weekly state sum
    wi = C.ours.reduce((b, v, i) => (v !== null && v > (C.ours[b] ?? -1) ? i : b), 0);
    slider.max = C.weeks.length - 1;
    slider.value = wi;
    refresh();
    map.fitBounds(C.bounds, { padding: 10, duration: 0 });
    chart();
  }
  function refresh() {
    const C = S.countries[country];
    label.textContent = `${C.weeks[wi]} (from ${C.TL[wi]})`;
    const src = map.getSource("admin1");
    if (src) src.setData(weekData());
  }
  slider.addEventListener("input", () => { wi = +slider.value; refresh(); });
  document.getElementById("country").addEventListener("change", (e) => setCountry(e.target.value));
  document.getElementById("measure").addEventListener("change", (e) => {
    measure = e.target.value;
    if (measure === "cumulative" && country !== "NGA") { document.querySelector('input[name=c][value=NGA]').checked = true; setCountry("NGA"); }
    else refresh();
  });

  const labels = ["0", "1 to 9", "10 to 49", "50 to 199", "200 or more"];
  document.getElementById("legend").innerHTML = steps.map(([, c], i) => `<span><i style="background:${c}"></i>${labels[i]}</span>`).join("")
    + '<span><i style="background:#d9d9d9"></i>not listed</span>';

  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
  map.on("mousemove", (e) => {
    const f = map.queryRenderedFeatures(e.point, { layers: ["chol-fill"] })[0];
    if (!f) { popup.remove(); return; }
    const p = f.properties;
    const v = p.v < 0 ? "not listed" : `${p.v} suspected cases`;
    popup.setLngLat(e.lngLat).setHTML(`<b>${esc(p.adm1_name)}</b> (${esc(p.adm1_pcode)})<br>${esc(S.countries[country].weeks[wi])}: ${v}`).addTo(map);
  });

  // ------------------------------------------------------------ SVG line chart
  const day = (s) => Date.parse(s + "T00:00:00Z") / 864e5;
  function svgChart(el, series, opts) {
    const W = 940, H = 260, L = 50, R = 10, T = 10, B = 28;
    const pts = series.flatMap((s) => s.data.filter((d) => d[1] !== null));
    if (!pts.length) { el.innerHTML = ""; return 0; }
    const x0 = Math.min(...pts.map((d) => day(d[0]))), x1 = Math.max(...pts.map((d) => day(d[0])));
    const y1 = Math.max(1, ...pts.map((d) => d[1]));
    const X = (t) => L + (W - L - R) * (day(t) - x0) / Math.max(1, x1 - x0);
    const Y = (v) => H - B - (H - T - B) * v / y1;
    let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.title)}">`;
    for (let i = 0; i <= 4; i++) {
      const v = y1 * i / 4;
      s += `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="#eee"/><text x="${L - 4}" y="${Y(v) + 4}" font-size="10" text-anchor="end">${Math.round(v).toLocaleString()}</text>`;
    }
    const years = [];
    for (let y = new Date(x0 * 864e5).getUTCFullYear(); y <= new Date(x1 * 864e5).getUTCFullYear() + 1; y++) years.push(y);
    for (const y of years) {
      const t = `${y}-01-01`;
      if (day(t) < x0 || day(t) > x1) continue;
      s += `<line x1="${X(t)}" x2="${X(t)}" y1="${T}" y2="${H - B}" stroke="#ccc" stroke-dasharray="2,3"/><text x="${X(t)}" y="${H - 10}" font-size="10" text-anchor="middle">${y}</text>`;
    }
    let paths = 0;
    for (const se of series) {
      const d = se.data.filter((p) => p[1] !== null).sort((a, b) => day(a[0]) - day(b[0]));
      if (!d.length) continue;
      // break the line where consecutive points are more than gapDays apart
      let path = "", prev = null;
      for (const [t, v] of d) {
        path += (prev !== null && day(t) - prev <= (se.gapDays || 8) ? "L" : "M") + X(t).toFixed(1) + "," + Y(v).toFixed(1);
        prev = day(t);
      }
      s += `<path class="series" d="${path}" fill="none" stroke="${se.color}" stroke-width="1.6"/>`;
      if (se.dots) for (const [t, v] of d) s += `<circle cx="${X(t).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="2.4" fill="${se.color}"/>`;
      paths++;
    }
    el.innerHTML = s + "</svg>";
    return paths;
  }

  function chart() {
    const C = S.countries[country];
    const ours = C.TL.map((t, i) => [t, C.ours[i]]);
    const series = [{ data: C.who, color: "#999" }, { data: ours, color: "#2171b5", dots: true }];
    if (country === "NGA") series.push({ data: C.TL.map((t, i) => [t, C.cum_delta[i]]), color: "#e6550d", dots: true });
    window.cholChartPaths = svgChart(document.getElementById("chart"), series, { title: C.name + " weekly national totals" });
  }
  const b = S.boundary;
  window.cholBoundaryPaths = svgChart(document.getElementById("boundary"), [
    { data: b.jhu, color: "#756bb1" }, { data: b.who, color: "#999" }, { data: b.ours, color: "#2171b5", dots: true },
  ], { title: "Nigeria, JHU release end and this dataset" });
  setCountry(country);
})();

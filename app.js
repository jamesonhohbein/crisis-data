// Coverage map: one fill layer per source, a "lost since 2024" outline layer, a month slider.
// Plain MapLibre GL JS, no base tiles, no API keys.
(async function () {
  const [cov, geo] = await Promise.all([
    fetch("coverage_map.json").then((r) => r.json()),
    fetch("data/admin1.geojson").then((r) => r.json()),
  ]);
  const months = cov.months;
  const sources = cov.sources;
  let current = "wfp";
  let mi = months.length - 1;

  const steps = [[0, "#e3e3e3"], [1, "#c6dbef"], [10, "#6baed6"], [50, "#2171b5"], [200, "#08306b"]];
  const fillColor = (prop) => {
    const e = ["step", ["coalesce", ["get", prop], 0], steps[0][1]];
    for (const [v, c] of steps.slice(1)) e.push(v, c);
    return e;
  };

  function dataFor(i) {
    for (const f of geo.features) {
      const p = f.properties;
      for (const s of sources) {
        const row = cov.counts[s][p.adm1_pcode];
        p["n_" + s] = row ? row[i] : 0;
        p["lost_" + s] = cov.lost[s].includes(p.adm1_pcode) ? 1 : 0;
        p["last_" + s] = cov.last[s][p.adm1_pcode] || "never";
      }
    }
    return geo;
  }

  const map = new maplibregl.Map({
    container: "map",
    style: {
      version: 8,
      sources: {},
      layers: [{ id: "bg", type: "background", paint: { "background-color": "#f4f4f0" } }],
    },
    bounds: [[21.5, 8.5], [54.0, 23.5]],
    fitBoundsOptions: { padding: 10 },
    attributionControl: false,
  });
  map.addControl(new maplibregl.AttributionControl({
    compact: false,
    customAttribution: "Boundaries: OCHA COD-AB via HDX (CC BY-IGO)",
  }));
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }));
  window.covmap = map;

  map.on("load", () => {
    map.addSource("admin1", { type: "geojson", data: dataFor(mi) });
    for (const s of sources) {
      map.addLayer({
        id: "cov-" + s, type: "fill", source: "admin1",
        layout: { visibility: s === current ? "visible" : "none" },
        paint: { "fill-color": fillColor("n_" + s), "fill-opacity": 0.9 },
      });
    }
    map.addLayer({ id: "admin1-line", type: "line", source: "admin1", paint: { "line-color": "#666", "line-width": 0.6 } });
    map.addLayer({
      id: "lost", type: "line", source: "admin1",
      filter: ["==", ["get", "lost_" + current], 1],
      paint: { "line-color": "#b2182b", "line-width": 3 },
    });
    window.covmapReady = true;
  });

  const box = document.getElementById("sources");
  for (const s of sources) {
    const l = document.createElement("label");
    l.innerHTML = `<input type="radio" name="src" value="${s}" ${s === current ? "checked" : ""}> ${cov.labels[s]}`;
    box.appendChild(l);
  }
  box.addEventListener("change", (e) => {
    current = e.target.value;
    for (const s of sources) map.setLayoutProperty("cov-" + s, "visibility", s === current ? "visible" : "none");
    map.setFilter("lost", ["==", ["get", "lost_" + current], 1]);
  });
  document.getElementById("lost").addEventListener("change", (e) => {
    map.setLayoutProperty("lost", "visibility", e.target.checked ? "visible" : "none");
  });
  const slider = document.getElementById("month");
  const label = document.getElementById("monthlabel");
  slider.max = months.length - 1;
  slider.value = mi;
  label.textContent = months[mi];
  slider.addEventListener("input", () => {
    mi = +slider.value;
    label.textContent = months[mi];
    const src = map.getSource("admin1");
    if (src) src.setData(dataFor(mi));
  });

  const legend = document.getElementById("legend");
  const labels = ["none", "1 to 9", "10 to 49", "50 to 199", "200 or more"];
  legend.innerHTML = steps.map(([, c], i) => `<span><i style="background:${c}"></i>${labels[i]}</span>`).join("")
    + '<span><i style="background:#fff;border:3px solid #b2182b"></i>lost since 2024</span>';

  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
  map.on("mousemove", (e) => {
    const f = map.queryRenderedFeatures(e.point, { layers: ["cov-" + current] })[0];
    if (!f) { popup.remove(); return; }
    const p = f.properties;
    popup.setLngLat(e.lngLat).setHTML(
      `<b>${esc(p.adm1_name)}</b><br>${cov.labels[current]}, ${months[mi]}: ${p["n_" + current]} pairs` +
      `<br>last seen ${p["last_" + current]}${p["lost_" + current] == 1 ? " (lost since 2024)" : ""}`
    ).addTo(map);
  });
})();

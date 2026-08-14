/* ====================================================================
 * DORM station — shared front-end helpers
 *
 * Every page needs the same handful of things: fetch JSON with a clear
 * error, format a UTC timestamp, format the same moment in local time,
 * and build a Chart.js line chart that looks like all the others. Before
 * this file each page carried its own copy, which meant a fix had to be
 * made five times.
 *
 * Loaded as a plain script (no modules) so it works from file:// too.
 * ==================================================================== */

const DORM = (function () {

  /* The station. Used for labels; the server computes sun times from the
   * same coordinates. */
  const STATION = {
    name: "Višnjan Observatory",
    callsign: "DORM",
    latitude: 45.2775,
    longitude: 13.7261
  };

  /* Categorical palette, validated for colour-blind separation and for
   * contrast against the panel surface. Assigned in fixed order -- a
   * series keeps its colour even when other series come and go. */
  const SERIES_COLORS = {
    blue: "#3987e5",
    orange: "#d95926",
    green: "#199e70",
    yellow: "#c98500",
    teal: "#4FD8C4",
    red: "#E85D5D"
  };

  /* Reference lines are annotations, not data: achromatic and dashed, so
   * they can never be mistaken for a series. */
  const REFERENCE_LINE = "#8FA0B3";

  /* Instrument pages on the left; About and Contact pushed to the right.
   * Those two are about the project rather than the data, so grouping them
   * with the live tabs would suggest they are more readouts. */
  const NAV = [
    { href: "./index.html", label: "Overview" },
    { href: "./weather.html", label: "Weather" },
    { href: "./power.html", label: "Power" },
    { href: "./status.html", label: "Status" },
    { href: "./sun.html", label: "Sun" },
    { href: "./fits.html", label: "Spectrograms" },
    { href: "./about.html", label: "About", side: "right" },
    { href: "./contact.html", label: "Contact", side: "right" }
  ];


  // ---------------------------------------------------------------
  // DOM
  // ---------------------------------------------------------------

  function el(id) {
    return document.getElementById(id);
  }

  function setText(id, value) {
    const node = el(id);
    if (node) node.textContent = value;
  }


  /* ---------------------------------------------------------------
   * Reload returns to the Overview.
   *
   * Requested behaviour: refreshing should put you back on the station
   * overview rather than leaving you where you were. Note this is the
   * opposite of what a browser normally does -- a reload usually means
   * "give me this page again" -- so it is deliberately narrow: only a
   * genuine reload triggers it. Following a link, using back/forward, or
   * opening a page fresh all behave normally, otherwise the other seven
   * pages would be unreachable.
   * --------------------------------------------------------------- */
  function overviewOnReload(currentFile) {
    if (currentFile === "index.html") return;

    let navType = "";
    try {
      const entry = performance.getEntriesByType("navigation")[0];
      navType = entry ? entry.type : "";
    }
    catch (e) {
      return;   // API unavailable: leave the page where it is
    }

    if (navType === "reload") {
      location.replace("./index.html");
    }
  }

  function renderNav(currentFile) {
    const nav = document.querySelector(".site-nav");
    if (!nav) return;

    const link = item => {
      const isCurrent = item.href.endsWith(currentFile);
      const classes = [];
      if (isCurrent) classes.push("is-current");
      if (item.side === "right") classes.push("is-secondary");
      return '<a href="' + item.href + '"' +
        (classes.length ? ' class="' + classes.join(" ") + '"' : "") +
        (isCurrent ? ' aria-current="page"' : "") +
        ">" + item.label + "</a>";
    };

    overviewOnReload(currentFile);

    nav.innerHTML =
      '<span class="nav-main">' +
        NAV.filter(i => i.side !== "right").map(link).join("") +
      "</span>" +
      '<span class="nav-aside">' +
        NAV.filter(i => i.side === "right").map(link).join("") +
      "</span>";
  }


  // ---------------------------------------------------------------
  // Fetching
  // ---------------------------------------------------------------

  async function getJSON(url) {
    let response;
    try {
      response = await fetch(url + "?_=" + Date.now(), { cache: "no-store" });
    }
    catch (e) {
      throw new Error(
        "Could not fetch:\n" + url + "\n\n" + e.message +
        "\n\nIs the station server running?   python station/server.py"
      );
    }
    if (!response.ok) {
      throw new Error("Not found: " + url + "  (HTTP " + response.status + ")");
    }
    return response.json();
  }

  function showError(message) {
    const panel = el("error-panel");
    if (panel) {
      panel.textContent = message;
      panel.style.display = "block";
    }
    const dot = el("status-dot");
    if (dot) dot.style.background = SERIES_COLORS.red;
  }

  function clearError() {
    const panel = el("error-panel");
    if (panel) {
      panel.textContent = "";
      panel.style.display = "none";
    }
    const dot = el("status-dot");
    if (dot) dot.style.background = "var(--signal)";
  }


  // ---------------------------------------------------------------
  // Time
  //
  // Everything the station records is UTC. People reading the page are
  // in Croatia. Both are shown everywhere rather than picking one and
  // making the reader do the conversion in their head.
  // ---------------------------------------------------------------

  function parseDate(value) {
    if (!value) throw new Error("Missing timestamp");
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) throw new Error("Invalid timestamp: " + value);
    return date;
  }

  function utcStamp(value) {
    return parseDate(value).toISOString().replace("T", " ").slice(0, 16) + " UTC";
  }

  function utcTime(value) {
    return parseDate(value).toISOString().slice(11, 16) + " UTC";
  }

  function utcClock(value) {
    return parseDate(value).toISOString().slice(11, 16);
  }

  function utcDate(value) {
    return parseDate(value).toISOString().slice(0, 10);
  }

  function zoneName(date) {
    try {
      const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: "short" })
        .formatToParts(date);
      const zone = parts.find(p => p.type === "timeZoneName");
      if (zone && zone.value) return zone.value;
    }
    catch (e) { /* fall through to a numeric offset */ }

    const offset = -date.getTimezoneOffset();
    const sign = offset >= 0 ? "+" : "-";
    const abs = Math.abs(offset);
    return "UTC" + sign + String(Math.floor(abs / 60)).padStart(2, "0") +
      ":" + String(abs % 60).padStart(2, "0");
  }

  function localStamp(value) {
    const d = parseDate(value);
    const stamp = d.getFullYear() + "-" +
      String(d.getMonth() + 1).padStart(2, "0") + "-" +
      String(d.getDate()).padStart(2, "0") + " " +
      String(d.getHours()).padStart(2, "0") + ":" +
      String(d.getMinutes()).padStart(2, "0");
    return stamp + " " + zoneName(d);
  }

  function localClock(value) {
    const d = parseDate(value);
    return String(d.getHours()).padStart(2, "0") + ":" +
      String(d.getMinutes()).padStart(2, "0");
  }

  function ago(from) {
    const s = Math.max(0, Math.round((Date.now() - from.getTime()) / 1000));
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }

  function duration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
    return Math.floor(s / 86400) + "d " + Math.floor((s % 86400) / 3600) + "h";
  }


  // ---------------------------------------------------------------
  // Numbers
  // ---------------------------------------------------------------

  function num(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function fmt(value, decimals) {
    const n = num(value);
    return n === null ? "—" : n.toFixed(decimals === undefined ? 1 : decimals);
  }

  function median(values) {
    const clean = values.filter(v => v !== null && v !== undefined).sort((a, b) => a - b);
    if (!clean.length) return null;
    const mid = Math.floor(clean.length / 2);
    return clean.length % 2 ? clean[mid] : (clean[mid - 1] + clean[mid]) / 2;
  }

  function stats(values) {
    const clean = values.filter(v => v !== null && v !== undefined);
    if (!clean.length) return { min: null, max: null, avg: null };
    return {
      min: Math.min(...clean),
      max: Math.max(...clean),
      avg: clean.reduce((s, v) => s + v, 0) / clean.length
    };
  }

  function rgba(hex, alpha) {
    const v = hex.replace("#", "");
    return "rgba(" + parseInt(v.slice(0, 2), 16) + ", " +
      parseInt(v.slice(2, 4), 16) + ", " +
      parseInt(v.slice(4, 6), 16) + ", " + alpha + ")";
  }


  // ---------------------------------------------------------------
  // Charts
  // ---------------------------------------------------------------

  function chartOptions(unit, decimals, opts) {
    opts = opts || {};
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          display: opts.legend !== false,
          position: "bottom",
          align: "start",
          labels: {
            boxWidth: 18, boxHeight: 1, padding: 12,
            color: "#6B7A8B",
            font: { family: "IBM Plex Mono", size: 10 },
            usePointStyle: false
          }
        },
        tooltip: {
          backgroundColor: "#151D27",
          borderColor: "#263140",
          borderWidth: 1,
          titleColor: "#6B7A8B",
          bodyColor: "#DCE4EC",
          padding: 10,
          displayColors: false,
          titleFont: { family: "IBM Plex Mono", size: 10 },
          bodyFont: { family: "IBM Plex Mono", size: 12 },
          callbacks: {
            label: item => {
              if (item.parsed.y === null) return "no data";
              // A reference line's label already carries its value
              // (e.g. "Median 12.3 h"); appending the value again would
              // print it twice. Reference lines are the dashed datasets.
              if (item.dataset.borderDash) return item.dataset.label;
              const label = item.dataset.label ? item.dataset.label + "  " : "";
              return label + item.parsed.y.toFixed(decimals) + " " + unit;
            }
          }
        }
      },
      scales: {
        x: {
          grid: { color: "#1A2229", drawTicks: false },
          ticks: {
            color: "#45525F", maxTicksLimit: opts.xTicks || 7,
            maxRotation: 0, autoSkip: true,
            font: { family: "IBM Plex Mono", size: 10 }
          },
          border: { color: "#263140" }
        },
        y: {
          grid: { color: "#1A2229", drawTicks: false },
          suggestedMin: opts.min,
          suggestedMax: opts.max,
          ticks: {
            color: "#45525F",
            font: { family: "IBM Plex Mono", size: 10 },
            callback: value => Number(value).toFixed(decimals)
          },
          border: { display: false }
        }
      }
    };
  }

  /* One line series, optionally with a dashed median reference line.
   * Returns the Chart instance so the caller can update it in place --
   * rebuilding a chart on every refresh leaks canvases and flickers. */
  function lineChart(canvas, labels, values, spec) {
    const ctx = canvas.getContext("2d");
    const color = spec.color || SERIES_COLORS.blue;
    const decimals = spec.decimals === undefined ? 1 : spec.decimals;

    const gradient = ctx.createLinearGradient(0, 0, 0, spec.fillHeight || 260);
    gradient.addColorStop(0, rgba(color, 0.10));
    gradient.addColorStop(1, rgba(color, 0));

    const datasets = [{
      label: spec.label || "",
      data: values,
      borderColor: color,
      backgroundColor: spec.fill === false ? "transparent" : gradient,
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      pointHoverBackgroundColor: color,
      pointHoverBorderColor: "#0A0E13",
      pointHoverBorderWidth: 2,
      fill: spec.fill !== false,
      tension: spec.stepped ? 0 : 0.25,
      stepped: !!spec.stepped,
      spanGaps: false,
      order: 1
    }];

    if (spec.showMedian) {
      const mid = median(values);
      datasets.push({
        label: "Median " + (mid === null ? "—" : mid.toFixed(decimals) + " " + (spec.unit || "")),
        data: values.map(() => mid),
        borderColor: REFERENCE_LINE,
        borderWidth: 1,
        borderDash: [5, 4],
        pointRadius: 0,
        pointHoverRadius: 0,
        fill: false,
        tension: 0,
        order: 2
      });
    }

    return new Chart(ctx, {
      type: "line",
      data: { labels, datasets },
      options: chartOptions(spec.unit || "", decimals, spec)
    });
  }

  /* Update an existing chart without recreating it. */
  function updateChart(chart, labels, values, spec) {
    chart.data.labels = labels;
    chart.data.datasets[0].data = values;

    if (chart.data.datasets[1] && spec && spec.showMedian) {
      const decimals = spec.decimals === undefined ? 1 : spec.decimals;
      const mid = median(values);
      chart.data.datasets[1].data = values.map(() => mid);
      chart.data.datasets[1].label =
        "Median " + (mid === null ? "—" : mid.toFixed(decimals) + " " + (spec.unit || ""));
    }
    chart.update("none");
  }


  // ---------------------------------------------------------------
  // Range selector
  //
  // The API now writes one file per range, so switching range fetches a
  // different (small) file instead of filtering a huge one in the page.
  // ---------------------------------------------------------------

  function buildRangeBar(container, ranges, active, onSelect) {
    container.innerHTML = ranges.map(r =>
      '<button type="button" class="range-btn' +
      (r.slug === active ? " is-active" : "") +
      '" data-range="' + r.slug + '">' + r.slug.toUpperCase() + "</button>"
    ).join("");

    container.querySelectorAll(".range-btn").forEach(button => {
      button.addEventListener("click", () => {
        container.querySelectorAll(".range-btn").forEach(b => b.classList.remove("is-active"));
        button.classList.add("is-active");
        onSelect(button.dataset.range);
      });
    });
  }

  function rangeLabel(slug) {
    if (slug === "all") return "All";
    return slug.toUpperCase();
  }


  return {
    STATION, SERIES_COLORS, REFERENCE_LINE, NAV,
    el, setText, renderNav,
    getJSON, showError, clearError,
    parseDate, utcStamp, utcTime, utcClock, utcDate,
    localStamp, localClock, zoneName, ago, duration,
    num, fmt, median, stats, rgba,
    chartOptions, lineChart, updateChart,
    buildRangeBar, rangeLabel
  };

})();

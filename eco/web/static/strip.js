// Live strip chart on a <canvas>, no library. Same look and rules as the
// grids' strip chart (eco.widgets.item_menu.draw_strip): a step line held up
// to "now" (a monitored value stays where it is until it changes), the last
// `window` seconds, enum values by their index with the labels as ticks.
// Optional trailing running average over the last N samples, drawn on top.

export const WINDOWS = [["30 s", 30], ["1 min", 60], ["5 min", 300], ["15 min", 900], ["1 h", 3600]];
export const AVERAGES = [0, 5, 20, 100]; // samples; 0 = off
export const MAX_POINTS = 20000;
export const KEEP_SECONDS = 3700; // a little more than the longest window

/** Samples of one item: [[t, y], ...], oldest first. */
export class Series {
  constructor() {
    this.points = [];
  }

  add(t, y) {
    const p = this.points;
    if (p.length && t < p[p.length - 1][0]) return; // late duplicate
    p.push([t, y]);
    if (p.length > MAX_POINTS || (p.length > 2 && p[0][0] < t - KEEP_SECONDS)) {
      const cut = p.findIndex(([pt]) => pt >= t - KEEP_SECONDS);
      p.splice(0, Math.max(cut, p.length - MAX_POINTS, 0));
    }
  }

  /** The samples in [now - window, now], plus the one before (the step line starts at the left edge). */
  visible(now, window) {
    const p = this.points;
    let i = p.length - 1;
    while (i > 0 && p[i][0] > now - window) i--;
    return p.slice(Math.max(i, 0));
  }

  /** Trailing mean of the last n samples at each sample (fewer at the start). */
  static average(points, n) {
    const out = [];
    let sum = 0;
    for (let i = 0; i < points.length; i++) {
      sum += points[i][1];
      if (i >= n) sum -= points[i - n][1];
      out.push([points[i][0], sum / Math.min(i + 1, n)]);
    }
    return out;
  }
}

function css(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function fmt(v) {
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(2);
  return String(+v.toPrecision(4));
}

/** Draws `series` onto `canvas`; returns the latest average (or null). */
export function draw(canvas, series, { now, window, avg = 0, labels = null }) {
  const dpr = window_dpr();
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return null;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const pts = series.visible(now, window);
  const mean = avg > 1 && pts.length ? Series.average(series.points.slice(-Math.max(pts.length + avg, avg)), avg)
    .filter(([t]) => t >= (pts[0]?.[0] ?? now)) : [];
  const left = 46, right = 6, top = 6, bottom = 16;
  const pw = w - left - right, ph = h - top - bottom;
  const muted = css("--muted", "#667085"), line = css("--line", "#e2e5ea");
  ctx.font = "11px system-ui, sans-serif";
  ctx.fillStyle = muted;

  if (!pts.length) {
    ctx.fillText("waiting for values…", left, top + ph / 2);
    return null;
  }
  let lo = Infinity, hi = -Infinity;
  for (const [, y] of pts) { lo = Math.min(lo, y); hi = Math.max(hi, y); }
  for (const [, y] of mean) { lo = Math.min(lo, y); hi = Math.max(hi, y); }
  if (labels) { lo = Math.min(lo, 0); hi = Math.max(hi, labels.length - 1); }
  if (hi - lo < 1e-12 * Math.max(1, Math.abs(hi))) { const d = Math.abs(hi) * 0.01 || 1; lo -= d; hi += d; }
  const pad = (hi - lo) * 0.08;
  lo -= pad; hi += pad;
  const X = (t) => left + ((t - (now - window)) / window) * pw;
  const Y = (y) => top + (1 - (y - lo) / (hi - lo)) * ph;

  // grid and y ticks
  ctx.strokeStyle = line;
  ctx.lineWidth = 1;
  ctx.textAlign = "right";
  ctx.textBaseline = "middle";
  const ticks = labels ? labels.map((l, i) => [i, l]) : [0, 0.5, 1].map((f) => { const v = lo + pad + f * (hi - lo - 2 * pad); return [v, fmt(v)]; });
  for (const [v, text] of ticks) {
    const y = Y(v);
    ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(w - right, y); ctx.stroke();
    ctx.fillText(String(text).slice(0, 7), left - 4, y);
  }
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(`-${window >= 60 ? window / 60 + " min" : window + " s"}`, left, h - 3);
  ctx.textAlign = "right";
  ctx.fillText("now", w - right, h - 3);

  // the value: step line held up to now
  const stepLine = (points, color, width) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    points.forEach(([t, y], i) => {
      const x = Math.max(left, X(t));
      if (i === 0) ctx.moveTo(x, Y(y));
      else { ctx.lineTo(x, Y(points[i - 1][1])); ctx.lineTo(x, Y(y)); }
    });
    ctx.lineTo(X(now), Y(points[points.length - 1][1]));
    ctx.stroke();
  };
  ctx.save();
  ctx.beginPath(); ctx.rect(left, top, pw, ph); ctx.clip();
  stepLine(pts, mean.length ? css("--chart-raw", "#9bbbe6") : css("--chart", "#1f77b4"), 1.4);
  if (mean.length) {
    ctx.strokeStyle = css("--chart", "#1f77b4");
    ctx.lineWidth = 2;
    ctx.beginPath();
    mean.forEach(([t, y], i) => (i ? ctx.lineTo(X(t), Y(y)) : ctx.moveTo(Math.max(left, X(t)), Y(y))));
    ctx.lineTo(X(now), Y(mean[mean.length - 1][1]));
    ctx.stroke();
  }
  ctx.restore();
  return mean.length ? mean[mean.length - 1][1] : null;
}

function window_dpr() {
  return Math.min(window.devicePixelRatio || 1, 3);
}

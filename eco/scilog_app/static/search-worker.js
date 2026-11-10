// eco SciLog viewer: the search, in a worker of its own, so that a large logbook never
// freezes the page (typing, scrolling, a phone's Back) while it is searched.
//   {posts: [{id, t, text}]}  the posts to search (sent again when they change)
//   {seq, q}                  a query; answered with {seq, results: [{id, score, words}]}
"use strict";

const fold = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
let posts = [];

function wordsOf(p) {
  if (!p.words) p.words = [...new Set(p.text.match(/[\p{L}\p{N}_.-]{2,}/gu) || [])];
  return p.words;
}

// Levenshtein distance, giving up above `max`
function within(a, b, max) {
  if (Math.abs(a.length - b.length) > max) return false;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let best = i;
    for (let j = 1; j <= b.length; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      best = Math.min(best, cur[j]);
    }
    if (best > max) return false;
    prev = cur;
  }
  return prev[b.length] <= max;
}

function parseQuery(q) {
  const terms = [];
  q = fold(q).replace(/"([^"]+)"/g, (_, phrase) => { terms.push({ text: phrase.trim(), exact: true }); return " "; });
  for (const w of q.split(/\s+/)) if (w.length >= 2) terms.push({ text: w, exact: false });
  return terms;
}

// every term must match: as a substring, or (4+ letters) a word within 1 typo (8+: 2)
function search(q) {
  const terms = parseQuery(q);
  if (!terms.length) return [];
  const newest = posts.length ? posts[posts.length - 1].t : 0;
  const out = [];
  for (const p of posts) {
    let score = 0;
    const matched = [];
    let ok = true;
    for (const term of terms) {
      const count = p.text.split(term.text).length - 1;
      if (count) {
        score += 3 + Math.log2(1 + count);
        matched.push(term.text);
        continue;
      }
      if (term.exact || term.text.length < 4) { ok = false; break; }
      const max = term.text.length >= 8 ? 2 : 1;
      const near = wordsOf(p).filter((w) => within(term.text, w.slice(0, term.text.length + max), max));
      if (!near.length) { ok = false; break; }
      score += 1.5;
      matched.push(...near.slice(0, 5));
    }
    if (ok) out.push({ id: p.id, score: score + 0.5 * (p.t / (newest || 1)), words: matched });
  }
  return out.sort((a, b) => b.score - a.score).slice(0, 60);
}

onmessage = (e) => {
  const m = e.data;
  if (m.posts) {
    posts = m.posts.map((p) => ({ id: p.id, t: p.t, text: fold(p.text) }));
    // the word lists, which a search with a typo needs, are made now rather than on the first such search
    for (const p of posts) wordsOf(p);
  } else if (m.q !== undefined) {
    postMessage({ seq: m.seq, results: search(m.q) });
  }
};

/**
 * Offline classifier used by the mock API.
 *
 * The real backend scores posts with a scikit-learn model over a TF-IDF
 * vocabulary plus a lexicon overlay. Neither can run in the browser, so this
 * reproduces the lexicon half of the pipeline: term matching, category
 * posteriors, the spans/markup that drive <HighlightedText/>, and identifier
 * extraction. The response shape mirrors app/schemas.py:ClassifyResponse so
 * the UI cannot tell the difference.
 */

import lexicon from './mockdata/lexicon.json';

// model_label differs from the lexicon key for some categories (mule_account
// is scored as "mule_fin"), exactly as the Python engine does it.
const LABELS = {};
for (const [key, cfg] of Object.entries(lexicon.categories || {})) {
  LABELS[key] = cfg.model_label || key;
}

// The engine reports every lexicon hit under one of these display kinds.
const KIND_BY_FIELD = {
  slang: 'slang',
  terms: 'slang',
  devanagari: 'slang',
  emojis: 'emoji',
  intent: 'intent',
};
const WEIGHT_BY_FIELD = { slang: 1, terms: 1, devanagari: 1, emojis: 0.9, intent: 0.8 };
const FIELDS = ['slang', 'terms', 'devanagari', 'emojis', 'intent'];

// Posterior denominator bias observed in the engine's lexicon-only scorer.
const BENIGN_BIAS = 0.9;

const isAlnum = (c) => !!c && /[a-z0-9]/i.test(c);

function buildDictionary(overlay = []) {
  const out = [];
  for (const [key, cfg] of Object.entries(lexicon.categories || {})) {
    for (const field of FIELDS) {
      for (const term of cfg[field] || []) {
        if (typeof term !== 'string' || !term.trim()) continue;
        out.push({
          term,
          lower: term.toLowerCase(),
          category: LABELS[key],
          kind: KIND_BY_FIELD[field],
          weight: WEIGHT_BY_FIELD[field],
        });
      }
    }
  }
  for (const row of overlay || []) {
    if (!row || !row.term) continue;
    out.push({
      term: String(row.term),
      lower: String(row.term).toLowerCase(),
      category: LABELS[row.category] || row.category || 'other_illegal',
      kind: row.kind === 'intent' ? 'intent' : 'slang',
      weight: Number(row.weight) || 1,
    });
  }
  // Longest first so "country made pistol" wins over "pistol".
  return out.sort((a, b) => b.lower.length - a.lower.length);
}

const IDENTIFIER_PATTERNS = [
  { type: 'upi', re: /\b[\w.\-]{2,}@[a-z]{2,}\b/gi, confidence: 0.92 },
  { type: 'phone', re: /(?:\+?91[\s-]?)?\b\d{5}[\s-]?\d{5}\b/g, confidence: 0.9 },
  { type: 'username', re: /@[a-z0-9_]{4,}/gi, confidence: 0.85 },
  { type: 'wallet', re: /\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b/g, confidence: 0.7 },
  { type: 'link', re: /\bhttps?:\/\/[^\s<>"']+/g, confidence: 0.95 },
];

function normalisePhone(raw) {
  const digits = String(raw).replace(/\D/g, '');
  if (digits.length === 10) return `+91${digits}`;
  if (digits.length === 12 && digits.startsWith('91')) return `+${digits}`;
  return `+${digits}`;
}

function extractIdentifiers(text) {
  const found = [];
  for (const { type, re, confidence } of IDENTIFIER_PATTERNS) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(text)) !== null) {
      const raw = m[0];
      const start = m.index;
      const end = start + raw.length;
      // "@handle" only counts when it is not the domain half of an email/UPI.
      if (type === 'username' && isAlnum(text[start - 1])) continue;
      const value =
        type === 'phone' ? normalisePhone(raw)
          : type === 'username' ? raw
            : raw;
      const extra = {};
      if (type === 'upi') {
        extra.handle_known = true;
        extra.payee = raw.split('@')[0];
      }
      found.push({ type, value, raw, start, end, confidence, source: 'text', extra });
      if (re.lastIndex === m.index) re.lastIndex += 1;
    }
  }
  return found.sort((a, b) => a.start - b.start);
}

function buildMarkup(text, spans) {
  const markup = [];
  let cursor = 0;
  for (const s of [...spans].sort((a, b) => a.start - b.start)) {
    if (s.start < cursor) continue;
    if (s.start > cursor) markup.push({ text: text.slice(cursor, s.start), kind: 'plain' });
    markup.push({ text: text.slice(s.start, s.end), kind: s.kind, category: s.category });
    cursor = s.end;
  }
  if (cursor < text.length) markup.push({ text: text.slice(cursor), kind: 'plain' });
  return markup;
}

const UNAVAILABLE_LLM = {
  flag: false,
  category: 'benign',
  severity: 'LOW',
  confidence: 0,
  language: 'other',
  emoji_signals: [],
  evidence: [],
  explanation: '',
  identifiers: [],
  available: false,
  model: '',
  latency_ms: 0,
  error: 'not configured',
};

/**
 * Classify a string and return a ClassifyResponse-shaped object.
 * @param {string} raw
 * @param {Array<{category?: string, term: string, kind?: string, weight?: number}>} overlay
 */
export function classifyPayload(raw, overlay = []) {
  const text = String(raw ?? '');
  const lower = text.toLowerCase();
  const dictionary = buildDictionary(overlay);

  const spans = [];
  const occupied = [];
  const features = [];
  const categoryWeights = {};
  const matchedTerms = [];
  const intentPhrases = [];

  for (const entry of dictionary) {
    if (!entry.lower) continue;
    let from = 0;
    let hit = false;
    for (;;) {
      const i = lower.indexOf(entry.lower, from);
      if (i < 0) break;
      const j = i + entry.term.length;
      const fits =
        (isAlnum(entry.term[0]) ? !isAlnum(lower[i - 1]) : true) &&
        (isAlnum(entry.term[entry.term.length - 1]) ? !isAlnum(lower[j]) : true);
      const clash = occupied.some(([a, b]) => i < b && j > a);
      if (fits && !clash) {
        occupied.push([i, j]);
        spans.push({
          start: i,
          end: j,
          text: text.slice(i, j),
          kind: entry.kind,
          category: entry.category,
          weight: entry.weight,
        });
        categoryWeights[entry.category] = (categoryWeights[entry.category] || 0) + entry.weight;
        features.push({ feature: `${entry.kind}:${entry.term}`, category: entry.category, weight: entry.weight });
        if (!matchedTerms.includes(entry.term)) matchedTerms.push(entry.term);
        if (entry.kind === 'intent' && !intentPhrases.includes(entry.term)) intentPhrases.push(entry.term);
        hit = true;
        from = j;
      } else {
        from = i + 1;
      }
    }
    if (!hit) continue;
  }

  const totalWeight = Object.values(categoryWeights).reduce((a, b) => a + b, 0);
  const scores = {};
  for (const label of Object.values(LABELS)) scores[label] = 0;
  scores.benign = 1;
  if (totalWeight > 0) {
    for (const [label, w] of Object.entries(categoryWeights)) scores[label] = w / (totalWeight + BENIGN_BIAS);
    scores.benign = BENIGN_BIAS / (totalWeight + BENIGN_BIAS);
  }
  // Descending, the order the UI renders the score bars in.
  const orderedScores = Object.fromEntries(
    Object.entries(scores).sort((a, b) => b[1] - a[1]),
  );

  const top = Object.keys(orderedScores)[0] || 'benign';
  const isIllicit = top !== 'benign' && totalWeight > 0;
  const confidence = Number((orderedScores[top] || 0).toFixed(4));
  const risk = !isIllicit ? 'LOW' : confidence >= 0.72 ? 'HIGH' : confidence >= 0.5 ? 'MEDIUM' : 'LOW';

  const reasons = [];
  if (matchedTerms.length) {
    reasons.push(
      `Lexicon hits: ${matchedTerms.slice(0, 6).map((t) => {
        const hit = spans.find((s) => s.text.toLowerCase() === t.toLowerCase());
        return `${t} (${hit ? hit.category : top})`;
      }).join(', ')}`,
    );
  }
  if (intentPhrases.length) reasons.push(`Intent phrases: ${intentPhrases.join(', ')}`);
  if (totalWeight > 0) reasons.push(`Lexicon posterior: ${top} = ${confidence.toFixed(2)}`);
  if (!reasons.length) reasons.push('No lexicon hits - scored benign.');

  const identifiers = extractIdentifiers(text);

  return {
    text,
    label: top,
    category: top,
    subcategory: null,
    confidence,
    risk,
    is_illicit: isIllicit,
    scores: orderedScores,
    explanation: {
      spans,
      top_features: features.slice(0, 6),
      matched_terms: matchedTerms,
      intent_phrases: intentPhrases,
      hard_negatives: [],
      category_weights: categoryWeights,
      markup: buildMarkup(text, spans),
      reasons,
      financial: {
        is_financial: false,
        type: null,
        types: [],
        intent: null,
        risk: 'LOW',
        commission: null,
        has_contact: identifiers.length > 0,
        evidence: [],
        notes: [],
      },
      lexicon_only: { category_weights: categoryWeights, _probs: orderedScores },
      model_used: false,
      model_version: 'spectra-hybrid-v1',
      emoji_signals: [],
      emoji_summary: [],
      llm: { ...UNAVAILABLE_LLM },
      fusion: {
        source: 'engine',
        note: 'LLM scanner unavailable in the offline demo build',
        engine: { flag: isIllicit, category: top, confidence, risk },
        llm: { ...UNAVAILABLE_LLM },
        emoji_boost: {},
      },
    },
    identifiers,
  };
}

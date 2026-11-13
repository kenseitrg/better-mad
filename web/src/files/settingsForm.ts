/**
 * Pure settings ⇄ JSON ⇄ form helpers for the files pane (handoff M3.5 §6.4).
 * FilesPane stays a thin component over these — no fetching, no React here.
 *
 * Settings round-trip: a dataset's `settings` (from GET /api/files) seeds the
 * form; the edited form posts back to /api/files/import or /{name}/reparse
 * with the SAME JSON shape the hub's _settings_from_object validates.
 */

import type { DatasetSummary, ParserSettingsJson, SettingsPayload } from "../api";

/** The editable form state for one file card's settings block. */
export interface SettingsForm {
  delimiter: ParserSettingsJson["delimiter"];
  decimal: "." | ",";
  /** Free text: comma/space-separated numeric or string tokens ("" = none). */
  sentinels: string;
  /** One-character comment marker. */
  comment: string;
  /** Free text list of sanitized float64 column names; blank = auto-detect. */
  float64Columns: string;
}

/** Seed the form from a dataset's current settings (the block shows current values). */
export function settingsToForm(json: ParserSettingsJson): SettingsForm {
  return {
    delimiter: json.delimiter,
    decimal: json.decimal === "," ? "," : ".",
    sentinels: json.sentinels.map((s) => String(s)).join(", "),
    comment: json.comment,
    float64Columns: json.float64_columns === null ? "" : json.float64_columns.join(", "),
  };
}

/** Defaults for failed-import cards (best-guess import defaults, UX §3). */
export function defaultSettingsForm(): SettingsForm {
  return settingsToForm({
    delimiter: "auto",
    decimal: ".",
    sentinels: [-999.25, -999, -9999, -99999, 1e30, "NULL"],
    comment: "#",
    float64_columns: null,
  });
}

/**
 * Parse the sentinels free-text field: tokens split on commas and/or spaces;
 * numeric tokens become numbers, everything else stays a string. "" → []
 * (explicitly removing ALL sentinels is allowed — handoff §6.4).
 */
export function parseSentinels(raw: string): (number | string)[] {
  const tokens = raw
    .split(/[\s,]+/)
    .map((t) => t.trim())
    .filter((t) => t.length > 0);
  const out: (number | string)[] = [];
  for (const token of tokens) {
    const n = Number(token);
    out.push(token !== "" && Number.isFinite(n) ? n : token);
  }
  return out;
}

/** Parse the float64-columns free-text field: blank (auto) → null. */
function parseFloat64Columns(raw: string): string[] | null {
  const names = raw
    .split(/[\s,]+/)
    .map((t) => t.trim())
    .filter((t) => t.length > 0);
  return names.length === 0 ? null : names;
}

/** Form → the JSON payload for POST /api/files/import and /{name}/reparse. */
export function formToSettings(form: SettingsForm): SettingsPayload {
  return {
    delimiter: form.delimiter,
    decimal: form.decimal,
    sentinels: parseSentinels(form.sentinels),
    comment: form.comment === "" ? "#" : form.comment,
    float64_columns: parseFloat64Columns(form.float64Columns),
  };
}

/**
 * True iff `plot.py` references the dataset (handoff M3.5 §6.4, trap T2):
 * an EXACT quoted `bm.data("name")` / `bm.data('name')` match — never a bare
 * substring (`foo` must not match `foo_2`).
 */
export function referencedInPlot(name: string, content: string): boolean {
  return content.includes(`bm.data("${name}")`) || content.includes(`bm.data('${name}')`);
}

/** One-line source display: middle-truncated so the tail (filename) survives. */
export function truncateMiddle(text: string, max: number): string {
  if (text.length <= max) return text;
  const keep = Math.max(1, Math.floor((max - 1) / 2));
  return `${text.slice(0, keep)}…${text.slice(text.length - keep)}`;
}

/** Column stat cell: ≤6 significant digits, null min/max as "—" (handoff §6.4). */
export function formatStat(v: number | null): string {
  if (v === null) return "—";
  return Number(v.toPrecision(6)).toString();
}

/** Type-narrowing helper for API error bodies carrying a `detail` message. */
export function errorDetail(err: unknown): string {
  const body = (err as { body?: { detail?: string } } | null)?.body;
  return body?.detail ?? String(err);
}

/** Extract just the dataset-relevant bits when a mutation returns a summary. */
export function summarize(ds: DatasetSummary): string {
  return `${ds.name} (${ds.rows.toLocaleString()} × ${ds.columns.length})`;
}

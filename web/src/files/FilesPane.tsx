/**
 * Files pane (M3.5 rebuild — handoff §6.4): one card per registered dataset
 * (name, rows×cols, source; ⚙ settings / ⟳ reload / ✕ close actions; expanded
 * column table with min/max/NaN%), session-scoped failed-import cards, and a
 * busy Add-files button (trap T9).
 *
 * Discipline carried over from M3.4: the pane refetches after its OWN
 * mutations only (no dataset-changed SSE event exists — trap T10, by design);
 * no modal system — the close confirm is an inline strip, CodePane-precedent
 * (trap T11). The dataset list itself is lifted to App (the quick-start card
 * needs it too — one fetch loop, no duplication).
 *
 * Click-to-copy: the column script-name cell copies the sanitized name scripts
 * must use; the tooltip shows `original → script`. Clipboard failure degrades
 * to select-to-copy (trap T1), never an error banner.
 */

import { useEffect, useRef, useState } from "react";
import type { DatasetSummary, FilesInfo } from "../api";
import { api } from "../api";
import {
  defaultSettingsForm,
  errorDetail,
  formatStat,
  formToSettings,
  referencedInPlot,
  settingsToForm,
  truncateMiddle,
  type SettingsForm,
} from "./settingsForm";

/** Session-scoped failed import (dropped by F5 — decided, no persistence). */
interface FailedImport {
  id: number;
  file: File;
  error: string;
}

const PREVIEW_BYTES = 8192;
let failedIdSeq = 0;

/** Blob.text with a FileReader fallback (old engines / some embeds lack it). */
function readBlobText(blob: Blob): Promise<string> {
  if (typeof blob.text === "function") return blob.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsText(blob.slice(0, PREVIEW_BYTES));
  });
}

export function FilesPane(props: { files: FilesInfo | null; refresh: () => void }) {
  const { files, refresh } = props;
  const inputRef = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [failed, setFailed] = useState<FailedImport[]>([]);
  const [paneError, setPaneError] = useState<string | null>(null);

  // Per-card UI state, keyed by dataset name (or `failed:<id>` for error cards).
  const [settingsOpen, setSettingsOpen] = useState<Record<string, boolean>>({});
  const [forms, setForms] = useState<Record<string, SettingsForm>>({});
  const [previews, setPreviews] = useState<Record<string, string | null>>({});
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({});
  const [confirmClose, setConfirmClose] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);

  const seedForm = (key: string, ds: DatasetSummary) => {
    setForms((prev) =>
      prev[key] !== undefined ? prev : { ...prev, [key]: settingsToForm(ds.settings) },
    );
  };
  const setForm = (key: string, patch: Partial<SettingsForm>) => {
    setForms((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  };

  /** Load the first-20-rows preview once per card (registered: server; failed: client). */
  const loadPreview = (key: string, ds?: DatasetSummary, file?: File) => {
    if (previews[key] !== undefined) return;
    setPreviews((prev) => ({ ...prev, [key]: null })); // placeholder while loading
    const load = ds
      ? api.datasetHead(ds.name, 20).then((h) => h.text)
      : readBlobText(file as File);
    load.then(
      (text) => setPreviews((prev) => ({ ...prev, [key]: text })),
      (err: unknown) => setPreviews((prev) => ({ ...prev, [key]: `⚠ preview unavailable: ${errorDetail(err)}` })),
    );
  };

  const toggleSettings = (key: string, ds?: DatasetSummary, file?: File) => {
    const open = !settingsOpen[key];
    setSettingsOpen((prev) => ({ ...prev, [key]: open }));
    if (open) {
      if (ds !== undefined) seedForm(key, ds);
      loadPreview(key, ds, file);
    }
  };

  const importFiles = async (list: FileList) => {
    setPaneError(null);
    setImporting(true); // T9: no fetch progress exists — the button says so
    const failures: FailedImport[] = [];
    for (const file of Array.from(list)) {
      try {
        await api.importFile(file); // best-guess defaults first (UX §3)
      } catch (err) {
        failures.push({ id: ++failedIdSeq, file, error: errorDetail(err) });
      }
    }
    setImporting(false);
    if (failures.length > 0) setFailed((prev) => [...prev, ...failures]);
    refresh(); // T10: refetch after own mutations — and only then
  };

  /** Re-parse a registered card with the form's settings (nothing silent — UX §3). */
  const reparse = async (key: string, ds: DatasetSummary) => {
    setCardErrors((prev) => ({ ...prev, [key]: "" }));
    try {
      await api.reparse(ds.name, formToSettings(forms[key] ?? settingsToForm(ds.settings)));
      setSettingsOpen((prev) => ({ ...prev, [key]: false })); // fresh parse → collapse
      setPreviews((prev) => {
        const next = { ...prev };
        delete next[key]; // re-read on next open
        return next;
      });
      refresh();
    } catch (err) {
      setCardErrors((prev) => ({ ...prev, [key]: errorDetail(err) }));
    }
  };

  /** ⟳ Reload-from-disk: re-parse with the dataset's CURRENT settings. */
  const reload = async (ds: DatasetSummary) => {
    setCardErrors((prev) => ({ ...prev, [ds.name]: "" }));
    try {
      await api.reparse(ds.name, ds.settings);
      refresh();
    } catch (err) {
      setCardErrors((prev) => ({ ...prev, [ds.name]: errorDetail(err) }));
    }
  };

  /** ✕ Close: confirm inline iff plot.py references the dataset (UX §7, T2). */
  const requestClose = async (name: string) => {
    setCardErrors((prev) => ({ ...prev, [name]: "" }));
    let referenced = false;
    try {
      const plot = await api.getPlot(); // 404 → no script → no references
      referenced = referencedInPlot(name, plot.content);
    } catch {
      referenced = false;
    }
    if (referenced) {
      setConfirmClose(name);
      return;
    }
    await closeNow(name);
  };

  const closeNow = async (name: string) => {
    setConfirmClose(null);
    setClosing(true);
    try {
      await api.closeFile(name);
      // UX §7 needs no extra failure-path code: the next run surfaces the
      // bm.data() KeyError in the staleness banner.
      refresh();
    } catch (err) {
      setPaneError(`${name}: ${errorDetail(err)}`);
    } finally {
      setClosing(false);
    }
  };

  /** Failed-import card's Re-parse = full re-POST of the import with settings. */
  const retryImport = async (entry: FailedImport) => {
    const key = `failed:${entry.id}`;
    setCardErrors((prev) => ({ ...prev, [key]: "" }));
    try {
      await api.importFile(entry.file, formToSettings(forms[key] ?? defaultSettingsForm()));
      setFailed((prev) => prev.filter((f) => f.id !== entry.id)); // success: card goes
      refresh();
    } catch (err) {
      setFailed((prev) =>
        prev.map((f) => (f.id === entry.id ? { ...f, error: errorDetail(err) } : f)),
      );
    }
  };

  const dismissFailed = (id: number) => {
    setFailed((prev) => prev.filter((f) => f.id !== id));
  };

  const empty = (files === null || files.datasets.length === 0) && failed.length === 0;

  return (
    <div className="pane" id="files-pane">
      <div className="pane-head">Files</div>
      {paneError !== null && <div className="banner error">{paneError}</div>}
      <div className="files-scroll">
        {empty && (
          <div className="files-empty">
            No files yet — <strong>＋ Add files</strong> below, then tell the agent what to
            plot.
          </div>
        )}
        {(files?.datasets ?? []).map((ds) => (
          <DatasetCard
            key={ds.name}
            ds={ds}
            settingsOpen={settingsOpen[ds.name] === true}
            form={forms[ds.name] ?? settingsToForm(ds.settings)}
            preview={previews[ds.name]}
            error={cardErrors[ds.name] || ""}
            confirmPending={confirmClose === ds.name}
            closing={closing}
            onToggleSettings={() => toggleSettings(ds.name, ds)}
            onFormChange={(patch) => setForm(ds.name, patch)}
            onReparse={() => reparse(ds.name, ds)}
            onReload={() => reload(ds)}
            onClose={() => requestClose(ds.name)}
            onCloseConfirm={() => closeNow(ds.name)}
            onCloseCancel={() => setConfirmClose(null)}
          />
        ))}
        {failed.map((entry) => (
          <FailedCard
            key={entry.id}
            entry={entry}
            settingsOpen={settingsOpen[`failed:${entry.id}`] === true}
            form={forms[`failed:${entry.id}`] ?? defaultSettingsForm()}
            preview={previews[`failed:${entry.id}`]}
            error={cardErrors[`failed:${entry.id}`] || ""}
            onToggleSettings={() => toggleSettings(`failed:${entry.id}`, undefined, entry.file)}
            onFormChange={(patch) => setForm(`failed:${entry.id}`, patch)}
            onReparse={() => retryImport(entry)}
            onDismiss={() => dismissFailed(entry.id)}
          />
        ))}
      </div>
      <div className="files-foot">
        <button style={{ width: "100%" }} disabled={importing} onClick={() => inputRef.current?.click()}>
          {importing ? "importing…" : "＋ Add files"}
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          style={{ display: "none" }}
          onChange={(e) => {
            if (e.target.files !== null && e.target.files.length > 0) {
              void importFiles(e.target.files);
            }
            e.target.value = ""; // allow re-picking the same file
          }}
        />
      </div>
    </div>
  );
}

/** The shared settings form fields (registered + failed cards use the same shape). */
function SettingsFields(props: {
  form: SettingsForm;
  ambiguous: boolean;
  onChange: (patch: Partial<SettingsForm>) => void;
}) {
  const { form, ambiguous, onChange } = props;
  return (
    <div className="settings-form">
      <label>
        delimiter
        <select
          value={form.delimiter}
          onChange={(e) => onChange({ delimiter: e.target.value as SettingsForm["delimiter"] })}
        >
          <option value="auto">auto</option>
          <option value="whitespace">whitespace</option>
          <option value="comma">comma</option>
          <option value="tab">tab</option>
        </select>
      </label>
      <label>
        decimal
        <select value={form.decimal} onChange={(e) => onChange({ decimal: e.target.value as "." | "," })}>
          <option value=".">.</option>
          <option value=",">,</option>
        </select>
      </label>
      <label>
        sentinels
        <input
          type="text"
          value={form.sentinels}
          placeholder="-999.25, -9999, NULL (comma/space separated; empty = none)"
          onChange={(e) => onChange({ sentinels: e.target.value })}
        />
      </label>
      <label>
        comment
        <input
          type="text"
          maxLength={1}
          value={form.comment}
          onChange={(e) => onChange({ comment: e.target.value })}
        />
      </label>
      <label>
        float64 columns
        <input
          type="text"
          value={form.float64Columns}
          placeholder="blank = auto (coordinate-like names)"
          onChange={(e) => onChange({ float64Columns: e.target.value })}
        />
      </label>
      {ambiguous && (
        <div className="card-error">decimal ',' is ambiguous with comma delimiter</div>
      )}
    </div>
  );
}

function DatasetCard(props: {
  ds: DatasetSummary;
  settingsOpen: boolean;
  form: SettingsForm;
  preview: string | null | undefined; // undefined = not loaded yet
  error: string;
  confirmPending: boolean;
  closing: boolean;
  onToggleSettings: () => void;
  onFormChange: (patch: Partial<SettingsForm>) => void;
  onReparse: () => void;
  onReload: () => void;
  onClose: () => void;
  onCloseConfirm: () => void;
  onCloseCancel: () => void;
}) {
  const { ds } = props;
  const ambiguous = props.form.decimal === "," && props.form.delimiter === "comma"; // T14
  return (
    <div className="file-card">
      <div className="file-card-head">
        <strong>{ds.name}</strong>
        <span className="sub">
          {ds.rows.toLocaleString()} × {ds.columns.length} cols
        </span>
        <span className="source" title={ds.source}>
          {truncateMiddle(ds.source, 34)}
        </span>
        <span className="actions">
          <button
            className={props.settingsOpen ? "on" : ""}
            title="Import settings (delimiter, decimal, sentinels…)"
            onClick={props.onToggleSettings}
          >
            ⚙
          </button>
          <button
            title={
              ds.source_exists
                ? "Reload from disk with the current settings"
                : "Source file is missing on disk — reload unavailable"
            }
            disabled={!ds.source_exists}
            onClick={props.onReload}
          >
            ⟳
          </button>
          <button title="Close this dataset" onClick={props.onClose}>
            ✕
          </button>
        </span>
      </div>
      {props.confirmPending && (
        <div className="confirm-strip">
          plot.py references this dataset — the next run will fail with a KeyError.
          <span className="actions">
            <button className="danger" disabled={props.closing} onClick={props.onCloseConfirm}>
              {props.closing ? "closing…" : "Close anyway"}
            </button>
            <button onClick={props.onCloseCancel}>Cancel</button>
          </span>
        </div>
      )}
      {props.error !== "" && <div className="card-error">{props.error}</div>}
      <div className="columns">
        <table>
          <thead>
            <tr>
              <th>column</th>
              <th>script name</th>
              <th>dtype</th>
              <th>min</th>
              <th>max</th>
              <th>NaN%</th>
            </tr>
          </thead>
          <tbody>
            {ds.columns.map((col) => (
              <tr key={col.script}>
                <td>{col.original}</td>
                <td>
                  <CopyName name={col.script} original={col.original} />
                </td>
                <td className="dim">{col.dtype}</td>
                <td className="num">{formatStat(col.min)}</td>
                <td className="num">{formatStat(col.max)}</td>
                <td className="num">{col.nan_pct.toFixed(1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details open={props.settingsOpen} className="settings-block">
        <summary onClick={(e) => e.preventDefault()} aria-hidden="true" />
        {props.settingsOpen && (
          <div className="settings-body">
            <SettingsFields form={props.form} ambiguous={ambiguous} onChange={props.onFormChange} />
            <pre className="preview">
              {props.preview === undefined || props.preview === null
                ? "loading preview…"
                : props.preview}
            </pre>
            <div className="actions">
              {/* Explicit only (UX §3): nothing re-parses silently. */}
              <button disabled={ambiguous} onClick={props.onReparse}>
                Re-parse
              </button>
            </div>
          </div>
        )}
      </details>
    </div>
  );
}

function FailedCard(props: {
  entry: FailedImport;
  settingsOpen: boolean;
  form: SettingsForm;
  preview: string | null | undefined;
  error: string;
  onToggleSettings: () => void;
  onFormChange: (patch: Partial<SettingsForm>) => void;
  onReparse: () => void;
  onDismiss: () => void;
}) {
  const { entry } = props;
  const ambiguous = props.form.decimal === "," && props.form.delimiter === "comma"; // T14
  return (
    <div className="file-card failed">
      <div className="file-card-head">
        <strong>{entry.file.name}</strong>
        <span className="sub">✗ parse failed</span>
        <span className="actions">
          <button
            className={props.settingsOpen ? "on" : ""}
            title="Import settings"
            onClick={props.onToggleSettings}
          >
            ⚙
          </button>
          <button title="Dismiss (the file stays in this tab only — F5 drops it)" onClick={props.onDismiss}>
            Dismiss
          </button>
        </span>
      </div>
      <div className="card-error">{props.error !== "" ? props.error : entry.error}</div>
      <details open={props.settingsOpen} className="settings-block">
        <summary onClick={(e) => e.preventDefault()} aria-hidden="true" />
        {props.settingsOpen && (
          <div className="settings-body">
            <SettingsFields form={props.form} ambiguous={ambiguous} onChange={props.onFormChange} />
            <pre className="preview">
              {props.preview === undefined || props.preview === null
                ? "loading preview…"
                : props.preview}
            </pre>
            <div className="actions">
              <button disabled={ambiguous} onClick={props.onReparse}>
                Re-parse
              </button>
            </div>
          </div>
        )}
      </details>
    </div>
  );
}

/** Click-to-copy script-name cell (UX §7): copies the sanitized name; the
 *  tooltip shows the mapping; clipboard failure degrades to select (T1). */
export function CopyName({ name, original }: { name: string; original: string }) {
  const [state, setState] = useState<"idle" | "copied" | "selected">("idle");
  const btnRef = useRef<HTMLButtonElement>(null);
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  const flash = (next: "copied" | "selected") => {
    setState(next);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 1000);
  };
  const selectFallback = () => {
    const btn = btnRef.current;
    if (btn !== null && typeof Range !== "undefined") {
      const range = document.createRange();
      range.selectNodeContents(btn);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    }
  };
  const copy = () => {
    const clip = navigator.clipboard;
    if (clip !== undefined && typeof clip.writeText === "function") {
      clip.writeText(name).then(
        () => flash("copied"),
        () => {
          selectFallback(); // T1: degrade to select-to-copy, never an error banner
          flash("selected");
        },
      );
    } else {
      selectFallback();
      flash("selected");
    }
  };
  return (
    <button
      ref={btnRef}
      className={`copy-name${state === "idle" ? "" : ` ${state}`}`}
      title={`${original} → ${name} (click to copy)`}
      onClick={copy}
    >
      {state === "copied" ? "copied ✓" : state === "selected" ? "selected ✓" : name}
    </button>
  );
}

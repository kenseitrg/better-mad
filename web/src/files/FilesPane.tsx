/**
 * Files pane — BASIC only for M3.4 (handoff §6.5): cards from GET /api/files
 * (name, rows×cols) + expand → column list (display + script names); import
 * button → browser picker → POST import → refetch. No dataset-changed hub
 * event exists BY DESIGN — only the user imports, so refetching after own
 * imports is enough (trap T12). M3.5 enriches: settings/re-parse, click-to-
 * copy script names, close confirm, first-launch card.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { FilesInfo } from "../api";
import { api } from "../api";

export function FilesPane() {
  const [files, setFiles] = useState<FilesInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    api
      .files()
      .then((f) => {
        setFiles(f);
        setError(null);
      })
      .catch((err: unknown) => setError(String(err)));
  }, []);

  useEffect(() => refresh(), [refresh]);

  const importFiles = async (list: FileList) => {
    setError(null);
    for (const file of Array.from(list)) {
      try {
        await api.importFile(file);
      } catch (err) {
        const body = (err as { body?: { detail?: string } }).body;
        setError(`${file.name}: ${body?.detail ?? String(err)}`);
        break; // one bad file stops the batch; its error stays visible
      }
    }
    refresh();
  };

  return (
    <div className="pane" id="files-pane">
      <div className="pane-head">Files</div>
      {error !== null && <div className="banner error">{error}</div>}
      <div className="files-scroll">
        {files === null ? (
          <div className="files-empty">loading…</div>
        ) : files.datasets.length === 0 ? (
          <div className="files-empty">
            No files yet — add one, then tell the agent what to plot.
          </div>
        ) : (
          files.datasets.map((ds) => (
            <details key={ds.name} className="file-card">
              <summary>
                <strong>{ds.name}</strong>
                <span className="sub">
                  {ds.rows.toLocaleString()} × {ds.columns.length} cols
                </span>
              </summary>
              <div className="columns">
                <table>
                  <thead>
                    <tr>
                      <th>column</th>
                      <th>script name</th>
                      <th>dtype</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ds.columns.map((col) => (
                      <tr key={col.script}>
                        <td>{col.original}</td>
                        <td>{col.script}</td>
                        <td style={{ color: "var(--fg-dim)" }}>{col.dtype}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          ))
        )}
      </div>
      <div className="files-foot">
        <button style={{ width: "100%" }} onClick={() => inputRef.current?.click()}>
          ＋ Add files
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

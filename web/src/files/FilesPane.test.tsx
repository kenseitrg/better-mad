/**
 * FilesPane render/interaction tests (handoff M3.5 §9): min/max/NaN% columns,
 * click-to-copy, close-confirm on referenced datasets, settings block seeded
 * from the files payload. The api module is mocked at the seam (jsdom has no
 * fetch/clipboard); the dataset list itself arrives via the lifted props.
 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FilesPane } from "./FilesPane";
import type { DatasetSummary, FilesInfo } from "../api";

vi.mock("../api", () => ({
  api: {
    getPlot: vi.fn(),
    closeFile: vi.fn(),
    reparse: vi.fn(),
    datasetHead: vi.fn(),
    importFile: vi.fn(),
  },
}));
import { api } from "../api";

const mocked = vi.mocked(api, true);

const ds: DatasetSummary = {
  name: "sample_ws",
  source: "/tmp/ws/.data/sources/sample_ws.txt",
  rows: 708650,
  settings: {
    delimiter: "whitespace",
    decimal: ".",
    sentinels: [-999.25, -9999, "NULL"],
    comment: "#",
    float64_columns: null,
  },
  source_exists: true,
  columns: [
    {
      original: "TR.DOMFREQ",
      script: "TR_DOMFREQ",
      dtype: "float32",
      min: 0.0,
      max: 56.766998,
      nan_pct: 1.5,
    },
    {
      original: "ELEV_MIDPT",
      script: "ELEV_MIDPT",
      dtype: "float64",
      min: null,
      max: null,
      nan_pct: 100,
    },
  ],
};

const files: FilesInfo = { datasets: [ds] };

beforeEach(() => {
  mocked.getPlot.mockResolvedValue({ content: "# empty script" });
  mocked.closeFile.mockResolvedValue({ ok: true });
  mocked.reparse.mockResolvedValue(ds);
  mocked.datasetHead.mockResolvedValue({
    name: "sample_ws",
    text: "CMP XCORD_MIDPT\n876149.000 470098.450\n",
  });
  mocked.importFile.mockResolvedValue(ds);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  // jsdom has no clipboard — remove any per-test mock.
  Reflect.deleteProperty(navigator, "clipboard");
});

const btnByTitle = (title: string | RegExp) => screen.getByTitle(title) as HTMLButtonElement;

describe("FilesPane cards", () => {
  it("renders columns with min / max / NaN% (null min/max as em-dash)", () => {
    render(<FilesPane files={files} refresh={() => {}} />);
    expect(screen.getByText(/708,650/)).toBeTruthy(); // rows (× cols)
    expect(screen.getByText("56.767")).toBeTruthy(); // max, ≤6 significant digits (spec)
    expect(screen.getByText("1.5")).toBeTruthy(); // NaN%
    expect(screen.getAllByText("—").length).toBe(2); // null min AND max
    expect(screen.getByText("100.0")).toBeTruthy(); // NaN% = 100
  });

  it("click-to-copy calls the clipboard with the script name (then shows copied ✓)", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<FilesPane files={files} refresh={() => {}} />);
    await act(async () => {
      fireEvent.click(screen.getByText("TR_DOMFREQ"));
    });
    expect(writeText).toHaveBeenCalledWith("TR_DOMFREQ");
    expect(screen.getByText("copied ✓")).toBeTruthy();
  });

  it("clipboard rejection degrades to select-to-copy (no error banner, T1)", async () => {
    const writeText = vi.fn<(text: string) => Promise<void>>().mockRejectedValue(new Error("denied"));
    Object.assign(navigator, { clipboard: { writeText } });
    render(<FilesPane files={files} refresh={() => {}} />);
    await act(async () => {
      fireEvent.click(screen.getByText("TR_DOMFREQ"));
    });
    expect(screen.getByText("selected ✓")).toBeTruthy();
    expect(screen.queryByText(/banner/i)).toBeNull();
  });

  it("✕ on a referenced dataset shows the inline confirm, not an immediate close", async () => {
    mocked.getPlot.mockResolvedValue({ content: 'df = bm.data("sample_ws")\nbm.show(df)' });
    const refresh = vi.fn();
    render(<FilesPane files={files} refresh={refresh} />);
    await act(async () => {
      fireEvent.click(btnByTitle("Close this dataset"));
    });
    expect(mocked.closeFile).not.toHaveBeenCalled();
    expect(screen.getByText(/plot.py references this dataset/)).toBeTruthy();
    // confirm closes it
    await act(async () => {
      fireEvent.click(screen.getByText("Close anyway"));
    });
    expect(mocked.closeFile).toHaveBeenCalledWith("sample_ws");
    expect(refresh).toHaveBeenCalled();
    // cancel path leaves everything alone
  });

  it("Cancel on the confirm strip closes nothing", async () => {
    mocked.getPlot.mockResolvedValue({ content: 'bm.data("sample_ws")' });
    render(<FilesPane files={files} refresh={() => {}} />);
    await act(async () => {
      fireEvent.click(btnByTitle("Close this dataset"));
    });
    await act(async () => {
      fireEvent.click(screen.getByText("Cancel"));
    });
    expect(mocked.closeFile).not.toHaveBeenCalled();
    expect(screen.queryByText(/plot.py references this dataset/)).toBeNull();
  });

  it("✕ on an unreferenced dataset closes immediately (no confirm strip)", async () => {
    mocked.getPlot.mockResolvedValue({ content: 'df = bm.data("other")' });
    render(<FilesPane files={files} refresh={() => {}} />);
    await act(async () => {
      fireEvent.click(btnByTitle("Close this dataset"));
    });
    expect(mocked.closeFile).toHaveBeenCalledWith("sample_ws");
    expect(screen.queryByText(/plot.py references this dataset/)).toBeNull();
  });

  it("missing plot.py (404) counts as unreferenced", async () => {
    mocked.getPlot.mockRejectedValue(Object.assign(new Error("HTTP 404"), { status: 404 }));
    render(<FilesPane files={files} refresh={() => {}} />);
    await act(async () => {
      fireEvent.click(btnByTitle("Close this dataset"));
    });
    expect(mocked.closeFile).toHaveBeenCalledWith("sample_ws");
  });

  it("⟳ reload is disabled with a tooltip when the source is missing", () => {
    const gone = { ...ds, source_exists: false };
    render(<FilesPane files={{ datasets: [gone] }} refresh={() => {}} />);
    const reload = btnByTitle("Source file is missing on disk — reload unavailable");
    expect(reload.disabled).toBe(true);
  });
});

describe("FilesPane settings block", () => {
  it("shows the CURRENT settings from the files payload + server preview", async () => {
    render(<FilesPane files={files} refresh={() => {}} />);
    await act(async () => {
      fireEvent.click(btnByTitle(/Import settings/));
    });
    const delimiter = screen.getByDisplayValue("whitespace") as HTMLSelectElement;
    expect(delimiter.value).toBe("whitespace");
    expect(screen.getByDisplayValue("-999.25, -9999, NULL")).toBeTruthy(); // sentinels
    expect(screen.getByDisplayValue("#")).toBeTruthy(); // comment
    // server-side first-rows preview
    expect(await screen.findByText(/CMP XCORD_MIDPT/)).toBeTruthy();
    expect(mocked.datasetHead).toHaveBeenCalledWith("sample_ws", 20);
  });

  it("Re-parse posts the edited settings and refetches", async () => {
    const refresh = vi.fn();
    render(<FilesPane files={files} refresh={refresh} />);
    await act(async () => {
      fireEvent.click(btnByTitle(/Import settings/));
    });
    const sentinels = screen.getByDisplayValue("-999.25, -9999, NULL") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(sentinels, { target: { value: "" } }); // remove ALL sentinels
    });
    await act(async () => {
      fireEvent.click(screen.getByText("Re-parse"));
    });
    expect(mocked.reparse).toHaveBeenCalledWith("sample_ws", {
      delimiter: "whitespace",
      decimal: ".",
      sentinels: [],
      comment: "#",
      float64_columns: null,
    });
    expect(refresh).toHaveBeenCalled();
  });

  it("a LoaderError from re-parse surfaces as a card error", async () => {
    mocked.reparse.mockRejectedValue({
      body: { detail: "decimal ',' is ambiguous with comma delimiter" },
    });
    render(<FilesPane files={files} refresh={() => {}} />);
    await act(async () => {
      fireEvent.click(btnByTitle(/Import settings/));
    });
    await act(async () => {
      fireEvent.click(screen.getByText("Re-parse"));
    });
    expect(screen.getByText(/ambiguous with comma delimiter/)).toBeTruthy();
  });

  it("decimal ',' + comma delimiter disables Re-parse proactively (T14)", async () => {
    render(<FilesPane files={files} refresh={() => {}} />);
    await act(async () => {
      fireEvent.click(btnByTitle(/Import settings/));
    });
    await act(async () => {
      fireEvent.change(screen.getByDisplayValue("whitespace"), {
        target: { value: "comma" },
      });
      fireEvent.change(screen.getByDisplayValue("."), { target: { value: "," } });
    });
    expect((screen.getByText("Re-parse") as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("FilesPane failed-import cards", () => {
  const makeFile = (name: string, content: string): File =>
    new File([content], name, { type: "text/plain" });

  it("a failed import shows a session card with the error (not a top banner)", async () => {
    mocked.importFile.mockRejectedValueOnce({ body: { detail: "no data rows found" } });
    render(<FilesPane files={{ datasets: [] }} refresh={() => {}} />);
    const fileInput = document.querySelector('#files-pane input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [makeFile("bad.txt", "junk")] } });
    });
    expect(screen.getByText("bad.txt")).toBeTruthy();
    expect(screen.getByText("no data rows found")).toBeTruthy();
    // the failure lives in the card, not a pane-level banner
    expect(document.querySelector(".banner.error")).toBeNull();
  });

  it("retry succeeds → card dropped, dataset refetch", async () => {
    const refresh = vi.fn();
    mocked.importFile
      .mockRejectedValueOnce({ body: { detail: "no data rows found" } })
      .mockResolvedValueOnce({ ...ds, name: "bad" });
    render(<FilesPane files={{ datasets: [] }} refresh={refresh} />);
    const fileInput = document.querySelector('#files-pane input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [makeFile("bad.txt", "junk")] } });
    });
    expect(screen.getByText("bad.txt")).toBeTruthy();
    expect(screen.getByText("no data rows found")).toBeTruthy();

    // open settings (client-side preview from the retained File), retry
    await act(async () => {
      fireEvent.click(btnByTitle("Import settings"));
    });
    expect(await screen.findByText("junk")).toBeTruthy(); // file.slice preview
    await act(async () => {
      fireEvent.click(screen.getByText("Re-parse"));
    });
    expect(mocked.importFile).toHaveBeenCalledTimes(2);
    expect(mocked.importFile).toHaveBeenLastCalledWith(expect.any(File), {
      delimiter: "auto",
      decimal: ".",
      sentinels: [-999.25, -999, -9999, -99999, 1e30, "NULL"],
      comment: "#",
      float64_columns: null,
    });
    expect(refresh).toHaveBeenCalled();
    expect(screen.queryByText("bad.txt")).toBeNull(); // card dropped on success
  });

  it("Dismiss drops the failed card (session-scoped, no persistence)", async () => {
    mocked.importFile.mockRejectedValueOnce({ body: { detail: "boom" } });
    render(<FilesPane files={{ datasets: [] }} refresh={() => {}} />);
    const fileInput = document.querySelector('#files-pane input[type="file"]') as HTMLInputElement;
    await act(async () => {
      fireEvent.change(fileInput, { target: { files: [makeFile("bad.txt", "junk")] } });
    });
    expect(screen.getByText("bad.txt")).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByTitle(/Dismiss/));
    });
    expect(screen.queryByText("bad.txt")).toBeNull();
    expect(mocked.closeFile).not.toHaveBeenCalled();
  });

  it("import shows a busy Add-files button while the POST is in flight (T9)", async () => {
    let resolveImport: ((v: DatasetSummary) => void) | undefined;
    mocked.importFile.mockImplementation(
      () => new Promise<DatasetSummary>((res) => (resolveImport = res)),
    );
    render(<FilesPane files={{ datasets: [] }} refresh={() => {}} />);
    const fileInput = document.querySelector('#files-pane input[type="file"]') as HTMLInputElement;
    act(() => {
      fireEvent.change(fileInput, { target: { files: [makeFile("big.txt", "x")] } });
    });
    const add = screen.getByRole("button", { name: /importing…/ }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    await act(async () => {
      resolveImport?.(ds);
    });
    expect(screen.getByRole("button", { name: /Add files/ })).toBeTruthy();
  });
});

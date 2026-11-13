/** Pure-helper tests for the files-pane settings block (handoff M3.5 §9). */
import { describe, expect, it } from "vitest";
import {
  defaultSettingsForm,
  formToSettings,
  parseSentinels,
  referencedInPlot,
  settingsToForm,
  truncateMiddle,
  formatStat,
} from "./settingsForm";

describe("parseSentinels", () => {
  it("splits on commas and spaces, numeric tokens become numbers", () => {
    expect(parseSentinels("-999.25, -9999 NULL 1e30")).toEqual([
      -999.25, -9999, "NULL", 1e30,
    ]);
  });

  it("empty string → no sentinels (removing all is allowed)", () => {
    expect(parseSentinels("")).toEqual([]);
    expect(parseSentinels("   ")).toEqual([]);
  });

  it("non-numeric tokens stay strings", () => {
    expect(parseSentinels("NULL, NA")).toEqual(["NULL", "NA"]);
    expect(parseSentinels("12abc")).toEqual(["12abc"]);
  });

  it("mix of numeric and string tokens", () => {
    expect(parseSentinels("-9999.0 NaN_STR")).toEqual([-9999, "NaN_STR"]);
  });
});

describe("settings ⇄ form round-trip", () => {
  it("seeds the form from dataset settings and posts back the same values", () => {
    const json = {
      delimiter: "whitespace" as const,
      decimal: ",",
      sentinels: [-9999, "NULL"],
      comment: "#",
      float64_columns: ["XCORD_MIDPT", "YCORD_MIDPT"],
    };
    const form = settingsToForm(json);
    expect(form).toEqual({
      delimiter: "whitespace",
      decimal: ",",
      sentinels: "-9999, NULL",
      comment: "#",
      float64Columns: "XCORD_MIDPT, YCORD_MIDPT",
    });
    expect(formToSettings(form)).toEqual(json);
  });

  it("null float64_columns → blank form → null payload (auto-detect)", () => {
    const form = settingsToForm({
      delimiter: "auto",
      decimal: ".",
      sentinels: [1e30],
      comment: "#",
      float64_columns: null,
    });
    expect(form.float64Columns).toBe("");
    expect(formToSettings(form).float64_columns).toBeNull();
  });

  it("empty sentinels field posts an explicit empty list", () => {
    const form = { ...defaultSettingsForm(), sentinels: "" };
    expect(formToSettings(form).sentinels).toEqual([]);
  });

  it("defaults match the loader's import defaults", () => {
    expect(formToSettings(defaultSettingsForm())).toEqual({
      delimiter: "auto",
      decimal: ".",
      sentinels: [-999.25, -999, -9999, -99999, 1e30, "NULL"],
      comment: "#",
      float64_columns: null,
    });
  });

  it("blank comment falls back to '#'", () => {
    const form = { ...defaultSettingsForm(), comment: "" };
    expect(formToSettings(form).comment).toBe("#");
  });
});

describe("referencedInPlot (trap T2: exact quoted match only)", () => {
  const script = [
    "import better_mad.sdk as bm",
    'df = bm.data("foo")',
    "other = bm.data('foo_2')",
    "bm.show(hv.Points(df))",
  ].join("\n");

  it("matches double-quoted references exactly", () => {
    expect(referencedInPlot("foo", script)).toBe(true);
  });

  it("matches single-quoted references exactly", () => {
    expect(referencedInPlot("foo_2", script)).toBe(true);
  });

  it("never matches a bare substring (foo vs foo_2)", () => {
    expect(referencedInPlot("foo_2", 'df = bm.data("foo")')).toBe(false);
    expect(referencedInPlot("foo", "df = bm.data('foo_2')")).toBe(false);
  });

  it("no bm.data call → false", () => {
    expect(referencedInPlot("foo", "df = load('foo')  # not the SDK")).toBe(false);
    expect(referencedInPlot("foo", "")).toBe(false);
  });

  it("matches even inside a string literal (pure substring check — documented)", () => {
    // The policy is intentionally naive (handoff §6.4): an exact quoted
    // substring. A string literal mentioning bm.data("foo") matches —
    // acceptable for the confirm banner (worst case: one extra confirmation).
    expect(referencedInPlot("foo", "print('see bm.data(\"foo\") docs')")).toBe(true);
  });

  it("name-ending-inside-a-longer-token does not match", () => {
    expect(referencedInPlot("sample", 'bm.data("sample_ws")')).toBe(false);
  });
});

describe("display helpers", () => {
  it("truncateMiddle keeps head and tail", () => {
    expect(truncateMiddle("short/path", 20)).toBe("short/path");
    expect(truncateMiddle("/very/long/path/to/file.txt", 12)).toBe("/very…e.txt");
  });

  it("formatStat: ≤6 significant digits, null as em-dash", () => {
    expect(formatStat(null)).toBe("—");
    expect(formatStat(0)).toBe("0");
    expect(formatStat(56.766998291015625)).toBe("56.767");
    expect(formatStat(6943885.914)).toBe("6943890"); // 6 significant digits
  });
});

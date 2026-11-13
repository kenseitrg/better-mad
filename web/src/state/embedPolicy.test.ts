import { describe, expect, it } from "vitest";
import type { RunResult } from "../api";
import { needsReembed, shouldBumpForRun } from "./embedPolicy";

const run = (status: RunResult["status"], has_figure?: boolean): RunResult => ({
  status,
  run_seconds: 1,
  stdout_tail: "",
  stderr_tail: "",
  epoch: 1,
  ...(has_figure === undefined ? {} : { has_figure }),
});

describe("needsReembed", () => {
  it("embeds when nothing is embedded yet", () => {
    expect(needsReembed(null, "1#0", true)).toBe(true);
  });

  it("re-embeds when the token moved (new ok run / new epoch)", () => {
    expect(needsReembed("1#3", "1#4", true)).toBe(true); // new ok run
    expect(needsReembed("1#3", "2#0", true)).toBe(true); // worker restart
  });

  it("keeps a healthy current embed", () => {
    expect(needsReembed("1#3", "1#3", true)).toBe(false);
  });

  it("re-embeds a same-token embed whose bokeh WS died", () => {
    expect(needsReembed("1#3", "1#3", false)).toBe(true);
  });
});

describe("shouldBumpForRun (M3.5 §6.5: figure-less ok run keeps the token)", () => {
  it("ok with a figure → bump (fresh document)", () => {
    expect(shouldBumpForRun(run("ok", true))).toBe(true);
  });

  it("ok WITHOUT a figure → no bump (doc holds last good roots)", () => {
    expect(shouldBumpForRun(run("ok", false))).toBe(false);
  });

  it("old payload without has_figure → bump (missing → true)", () => {
    expect(shouldBumpForRun(run("ok"))).toBe(true);
  });

  it("error/timeout → never bump", () => {
    expect(shouldBumpForRun(run("error", false))).toBe(false);
    expect(shouldBumpForRun(run("timeout", true))).toBe(false);
  });
});

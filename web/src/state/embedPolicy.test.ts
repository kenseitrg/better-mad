import { describe, expect, it } from "vitest";
import { needsReembed } from "./embedPolicy";

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

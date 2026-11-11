/** External-change policy tests (handoff §10, trap T4). */
import { describe, expect, it } from "vitest";
import { externalChangePolicy } from "./savePolicy";

describe("externalChangePolicy", () => {
  it("identical content → no-op (own-write echo: PUT → watcher → GET)", () => {
    expect(externalChangePolicy("same", "same", true)).toBe("noop");
    expect(externalChangePolicy("same", "same", false)).toBe("noop");
  });

  it("clean buffer + disk differs → silent reload", () => {
    expect(externalChangePolicy("old", "new", false)).toBe("reload");
  });

  it("dirty buffer + disk differs → banner (never clobber either side)", () => {
    expect(externalChangePolicy("mine", "theirs", true)).toBe("banner");
  });
});

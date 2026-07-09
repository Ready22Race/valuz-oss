import { describe, expect, it } from "vitest";

import {
  buildFileRef,
  buildLocalFileUrl,
  isFileRef,
  parseFileRef,
  parseLocalFileUrl,
} from "./file-uri";

// The cross-layer contract: these exact paths are mirrored in the backend's
// tests/modules/files/test_file_resolve.py::TestUri so the TS and Python codecs
// stay in lockstep. Add a nasty path here → add it there too.
const CONTRACT_PATHS = [
  "/data/valuz_data/workspace/u/proj/a.md",
  "/Users/u/My Proj/r.pdf", // space
  "/tmp/name+with&chars.txt", // + and &
  "/tmp/a#b.txt", // # (fragment delimiter if not encoded)
  "/tmp/a%b.txt", // literal percent
  "/Users/river/Valuz/示例项目/晶合集成_688249_财务预测模型.xlsx", // CJK — the real 404 case
];

describe("file-uri codec", () => {
  describe("valuz-file:// round-trip", () => {
    it.each(CONTRACT_PATHS)("build→parse is identity for %s", (path) => {
      const ref = buildFileRef(path);
      expect(isFileRef(ref)).toBe(true);
      expect(ref.startsWith("valuz-file:///")).toBe(true); // canonical three-slash
      expect(parseFileRef(ref)).toBe(path);
    });
  });

  describe("valuz-local:// round-trip", () => {
    it.each(CONTRACT_PATHS)("build→parse is identity for %s", (path) => {
      const url = buildLocalFileUrl(path);
      expect(url.startsWith("valuz-local:///")).toBe(true); // canonical three-slash
      expect(parseLocalFileUrl(url)).toBe(path);
    });
  });

  describe("valuz-file:// is TOLERANT (models may drop a slash)", () => {
    it("folds a two-slash host back so //abs === ///abs", () => {
      expect(parseFileRef("valuz-file://Users/u/a.md")).toBe("/Users/u/a.md");
      expect(parseFileRef("valuz-file:///Users/u/a.md")).toBe("/Users/u/a.md");
    });
  });

  describe("valuz-local:// is STRICT (we build it — surface builder bugs)", () => {
    it("does not silently repair a two-slash url", () => {
      // A malformed two-slash valuz-local url must NOT resolve to the intended
      // absolute path — the handler should 404 so a builder regression is loud.
      expect(parseLocalFileUrl("valuz-local://Users/u/a.md")).not.toBe(
        "/Users/u/a.md",
      );
    });
  });

  describe("windows drive", () => {
    it("round-trips C:/…", () => {
      expect(parseFileRef(buildFileRef("C:/Users/u/x.txt"))).toBe(
        "C:/Users/u/x.txt",
      );
    });
  });

  describe("rejects foreign schemes", () => {
    it("returns null", () => {
      expect(isFileRef("https://x/y")).toBe(false);
      expect(parseFileRef("https://x/y")).toBeNull();
      expect(parseLocalFileUrl("https://x/y")).toBeNull();
    });
  });
});

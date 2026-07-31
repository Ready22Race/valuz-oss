import { describe, expect, it } from "vitest";
import {
  canUseNormalizedPdfRects,
  locatePdfTextItemIndexes,
  mapNormalizedPdfRects,
} from "./PdfDocumentRenderer";

describe("PDF citation geometry", () => {
  it("maps normalized multi-line rects into the current viewport", () => {
    expect(
      mapNormalizedPdfRects(
        [
          { x: 0.1, y: 0.2, width: 0.4, height: 0.03 },
          { x: 0.1, y: 0.24, width: 0.2, height: 0.03 },
        ],
        1000,
        800,
      ),
    ).toEqual([
      { left: 100, top: 160, width: 400, height: 24 },
      { left: 100, top: 192, width: 200, height: 24 },
    ]);
  });

  it("rejects invalid or out-of-bounds rects", () => {
    expect(
      mapNormalizedPdfRects(
        [
          { x: -0.1, y: 0.2, width: 0.4, height: 0.03 },
          { x: 0.8, y: 0.2, width: 0.4, height: 0.03 },
          { x: 0.1, y: 0.2, width: 0, height: 0.03 },
        ],
        1000,
        800,
      ),
    ).toEqual([]);
  });

  it("falls back to text when document or viewer rotation differs", () => {
    const rects = [{ x: 0.1, y: 0.2, width: 0.4, height: 0.03 }];
    expect(
      canUseNormalizedPdfRects({
        rects,
        locatorPageRotation: 90,
        documentPageRotation: 0,
        viewerRotation: 0,
      }),
    ).toBe(false);
    expect(
      canUseNormalizedPdfRects({
        rects,
        locatorPageRotation: 0,
        documentPageRotation: 0,
        viewerRotation: 90,
      }),
    ).toBe(false);
    expect(
      canUseNormalizedPdfRects({
        rects,
        locatorPageRotation: undefined,
        documentPageRotation: 90,
        viewerRotation: 0,
      }),
    ).toBe(false);
    expect(
      canUseNormalizedPdfRects({
        rects,
        locatorPageRotation: 0,
        documentPageRotation: 0,
        viewerRotation: 0,
      }),
    ).toBe(true);
  });

  it("finds quote text across PDF text items with prefix disambiguation", () => {
    const items = [
      { str: "Old guidance:", hasEOL: false },
      { str: "unchanged", hasEOL: true },
      { str: "Current guidance:", hasEOL: false },
      { str: "unchanged", hasEOL: false },
    ] as unknown as Parameters<typeof locatePdfTextItemIndexes>[0];

    expect(
      locatePdfTextItemIndexes(items, {
        exact: "unchanged",
        prefix: "Current guidance: ",
      }),
    ).toEqual([3]);
  });
});

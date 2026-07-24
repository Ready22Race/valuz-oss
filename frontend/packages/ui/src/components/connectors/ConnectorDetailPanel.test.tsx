/** @vitest-environment jsdom */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConnectorDetailPanel } from "./ConnectorDetailPanel";

describe("ConnectorDetailPanel", () => {
  it.each([false, true])(
    "renders edition-provided header actions when connected=%s",
    (connected) => {
      render(
        <ConnectorDetailPanel
          name="Shared Connector"
          connected={connected}
          tools={connected ? [] : undefined}
          headerActions={<button type="button">Publish</button>}
        />,
      );

      expect(screen.getByRole("button", { name: "Publish" })).toBeTruthy();
    },
  );
});

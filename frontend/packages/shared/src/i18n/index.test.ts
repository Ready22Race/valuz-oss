import { describe, expect, it } from "vitest";

import { initI18n, t } from "./index";

describe("i18n project messages", () => {
  it("keeps project creation and project import messages separate", () => {
    initI18n({ locale: "en-US", fallbackLocale: "en-US" });

    expect(t("project.created", { name: "Demo" })).toBe(
      'Project "Demo" created',
    );
    const importMessage = t("project.importCreated", {
      members: 2,
      automations: 1,
      agents: 3,
    });
    expect(importMessage).toContain("Imported");
    expect(importMessage).toContain("2 member(s)");
  });
});

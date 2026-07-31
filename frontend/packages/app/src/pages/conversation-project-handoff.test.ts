import { describe, expect, it } from "vitest";
import { canSendProjectHandoff } from "./conversation-project-handoff";

describe("canSendProjectHandoff", () => {
  it("holds the send until bootstrap has bound the project", () => {
    // The regression: firing here mints a quick chat on the default backend
    // instead of a project conversation on the project's own origin.
    expect(
      canSendProjectHandoff({ projectParam: "A", selectedProjectId: null }),
    ).toBe(false);
  });

  it("sends once the binding matches the URL", () => {
    expect(
      canSendProjectHandoff({ projectParam: "A", selectedProjectId: "A" }),
    ).toBe(true);
  });

  it("keeps holding while a previous project is still bound", () => {
    expect(
      canSendProjectHandoff({ projectParam: "A", selectedProjectId: "B" }),
    ).toBe(false);
  });

  it("does not wait when the entry carries no project", () => {
    expect(
      canSendProjectHandoff({ projectParam: null, selectedProjectId: null }),
    ).toBe(true);
  });
});

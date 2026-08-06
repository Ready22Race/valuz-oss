import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useRegistryStore } from "@valuz/core";
import {
  ResourceCopyMenuItemSlot,
  ResourceDetailActionSlot,
} from "./ResourceActionSlot";

describe("ResourceDetailActionSlot", () => {
  afterEach(() => {
    act(() => {
      useRegistryStore
        .getState()
        .unregisterSlot("resource.agent.detail.actions", "test-detail-action");
      useRegistryStore
        .getState()
        .unregisterSlot(
          "resource.agent.copy.menu-items",
          "test-copy-menu-item",
        );
    });
  });

  it("renders detail-only registrations with the resource context", () => {
    act(() => {
      useRegistryStore
        .getState()
        .registerSlot("resource.agent.detail.actions", {
          id: "test-detail-action",
          component: (props) => (
            <span>
              {String(props.resourceType)}:
              {String(
                (props.resource as Record<string, unknown> | undefined)?.slug,
              )}
            </span>
          ),
        });
    });

    render(
      <ResourceDetailActionSlot
        resourceType="agent"
        resource={{ slug: "course-builder" }}
      />,
    );

    expect(screen.getByText("agent:course-builder")).not.toBeNull();
  });

  it("renders copy-menu registrations with the resource context", () => {
    act(() => {
      useRegistryStore
        .getState()
        .registerSlot("resource.agent.copy.menu-items", {
          id: "test-copy-menu-item",
          component: (props) => (
            <span>
              Copy to {String(
                (props.resource as Record<string, unknown> | undefined)?.slug,
              )}
            </span>
          ),
        });
    });

    render(
      <ResourceCopyMenuItemSlot
        resourceType="agent"
        resource={{ slug: "course-builder" }}
      />,
    );

    expect(screen.getByText("Copy to course-builder")).not.toBeNull();
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  channelsApi,
  setChannelsApiBase,
  type FeishuBinding,
  type WeComAIBotBinding,
} from "./channels-api";

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const binding: WeComAIBotBinding = {
  enabled: true,
  channel_instance_id: "wecom-aibot-main",
  owner_user_id: "u1",
  agent_slug: "developer",
  bot_id: "bot-1",
  has_secret: true,
  connected: false,
  connection_status: "stopped",
  connection_error: null,
};

const feishuBinding: FeishuBinding = {
  enabled: true,
  channel_instance_id: "feishu-main",
  owner_user_id: "u1",
  agent_slug: "developer",
  app_id: "cli_app_1",
  has_verification_token: true,
  has_encrypt_key: true,
};

describe("channelsApi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setChannelsApiBase("http://api.test");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the WeCom AIBot local binding", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(binding));

    await expect(channelsApi.getWeComAIBotBinding("developer")).resolves.toEqual(binding);

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/v1/channels/wecom-aibot/bindings/developer",
    );
  });

  it("does not send an empty secret when saving a binding", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(binding));

    await channelsApi.updateWeComAIBotBinding({
      enabled: true,
      agent_slug: "developer",
      bot_id: "bot-1",
      secret: "",
    });

    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/v1/channels/wecom-aibot/bindings/developer",
    );
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      enabled: true,
      agent_slug: "developer",
      bot_id: "bot-1",
    });
  });

  it("loads the Feishu binding", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(feishuBinding));

    await expect(channelsApi.getFeishuBinding("developer")).resolves.toEqual(
      feishuBinding,
    );

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/v1/channels/feishu/bindings/developer",
    );
  });

  it("does not send empty Feishu secrets when saving a binding", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse(feishuBinding));

    await channelsApi.updateFeishuBinding({
      enabled: true,
      agent_slug: "developer",
      app_id: "cli_app_1",
      verification_token: "",
      encrypt_key: "",
    });

    const [, init] = fetchMock.mock.calls[0] ?? [];
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://api.test/v1/channels/feishu/bindings/developer",
    );
    const body = JSON.parse(String(init?.body));
    expect(body).toEqual({
      enabled: true,
      agent_slug: "developer",
      app_id: "cli_app_1",
    });
  });
});

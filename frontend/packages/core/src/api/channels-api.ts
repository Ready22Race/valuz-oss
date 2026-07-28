import { createFetchJson } from "./fetch-json";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>)
    .env?.VITE_API_BASE_URL || "http://localhost:8000";

export const setChannelsApiBase = (url: string): void => {
  _apiBase = url;
};

export interface WeComAIBotBinding {
  enabled: boolean;
  channel_instance_id: string;
  owner_user_id: string;
  agent_slug: string;
  bot_id: string;
  has_secret: boolean;
  connected: boolean;
  connection_status: string;
  connection_error?: string | null;
}

export interface UpdateWeComAIBotBindingPayload {
  enabled: boolean;
  channel_instance_id?: string;
  agent_slug: string;
  bot_id: string;
  secret?: string;
}

export interface FeishuBinding {
  enabled: boolean;
  channel_instance_id: string;
  owner_user_id: string;
  agent_slug: string;
  app_id: string;
  has_verification_token: boolean;
  has_encrypt_key: boolean;
}

export interface UpdateFeishuBindingPayload {
  enabled: boolean;
  channel_instance_id?: string;
  agent_slug: string;
  app_id: string;
  verification_token?: string;
  encrypt_key?: string;
}

const fetchJson = createFetchJson(() => _apiBase);

export const channelsApi = {
  getWeComAIBotBinding(agentSlug: string): Promise<WeComAIBotBinding> {
    return fetchJson(
      `/v1/channels/wecom-aibot/bindings/${encodeURIComponent(agentSlug)}`,
    );
  },

  updateWeComAIBotBinding(
    payload: UpdateWeComAIBotBindingPayload,
  ): Promise<WeComAIBotBinding> {
    const secret = payload.secret?.trim();
    const body: UpdateWeComAIBotBindingPayload = {
      enabled: payload.enabled,
      agent_slug: payload.agent_slug,
      bot_id: payload.bot_id,
    };
    if (payload.channel_instance_id?.trim()) {
      body.channel_instance_id = payload.channel_instance_id.trim();
    }
    if (secret) {
      body.secret = secret;
    }
    return fetchJson(
      `/v1/channels/wecom-aibot/bindings/${encodeURIComponent(
        payload.agent_slug,
      )}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  },

  getFeishuBinding(agentSlug: string): Promise<FeishuBinding> {
    return fetchJson(
      `/v1/channels/feishu/bindings/${encodeURIComponent(agentSlug)}`,
    );
  },

  updateFeishuBinding(payload: UpdateFeishuBindingPayload): Promise<FeishuBinding> {
    const verificationToken = payload.verification_token?.trim();
    const encryptKey = payload.encrypt_key?.trim();
    const body: UpdateFeishuBindingPayload = {
      enabled: payload.enabled,
      agent_slug: payload.agent_slug,
      app_id: payload.app_id,
    };
    if (payload.channel_instance_id?.trim()) {
      body.channel_instance_id = payload.channel_instance_id.trim();
    }
    if (verificationToken) {
      body.verification_token = verificationToken;
    }
    if (encryptKey) {
      body.encrypt_key = encryptKey;
    }
    return fetchJson(
      `/v1/channels/feishu/bindings/${encodeURIComponent(payload.agent_slug)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  },
};

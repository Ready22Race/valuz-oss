/**
 * Hidden "Data Service" settings section — unlock + registration.
 *
 * The section is an advanced/dev surface (drives the in-process kernel's
 * durable store tier), so it's hidden until the user taps the About card 9×
 * (Android-style developer-mode reveal). Once unlocked the flag persists in
 * localStorage and the section is registered into the live settings registry.
 */
import { useRegistryStore } from "@valuz/core";
import type { SettingsSectionModule } from "@valuz/core";

const UNLOCK_KEY = "valuz-data-service-unlocked";

/** Taps on the About card needed to reveal the hidden section. */
export const UNLOCK_TAP_COUNT = 9;

export const DATA_SERVICE_SECTION: SettingsSectionModule = {
  id: "data-service",
  label: "settings.tab.dataService.label",
  description: "settings.tab.dataService.desc",
  icon: "database",
  edition: "personal",
};

export function isDataServiceUnlocked(): boolean {
  try {
    return localStorage.getItem(UNLOCK_KEY) === "1";
  } catch {
    return false;
  }
}

/** Register the section into the live registry (idempotent — upsert by id). */
export function registerDataServiceSection(): void {
  useRegistryStore.getState().registerSettingsSection(DATA_SERVICE_SECTION);
}

/** Persist the unlock and reveal the section immediately. */
export function unlockDataService(): void {
  try {
    localStorage.setItem(UNLOCK_KEY, "1");
  } catch {
    // ignore — still reveal for this session
  }
  registerDataServiceSection();
}

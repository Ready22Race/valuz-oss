import type { SettingsSectionModule } from "../profile";

export const personalSettingsSections: SettingsSectionModule[] = [
  {
    id: "general",
    label: "settings.tab.general.label",
    description: "settings.tab.general.desc",
    icon: "palette",
    edition: "personal",
  },
  {
    id: "model",
    label: "settings.tab.model.label",
    description: "settings.tab.model.desc",
    icon: "cpu",
    edition: "personal",
  },
  {
    id: "personalization",
    label: "settings.tab.personalization.label",
    description: "settings.tab.personalization.desc",
    icon: "brain",
    edition: "personal",
  },
  {
    id: "browser",
    label: "settings.tab.browser.label",
    description: "settings.tab.browser.desc",
    icon: "globe",
    edition: "personal",
  },
  {
    id: "parsing",
    label: "settings.tab.parsing.label",
    description: "settings.tab.parsing.desc",
    icon: "file-text",
    edition: "personal",
  },
  {
    id: "backup",
    label: "settings.tab.backup.label",
    description: "settings.tab.backup.desc",
    icon: "hard-drive",
    edition: "personal",
  },
  {
    id: "system-logs",
    label: "settings.tab.systemLogs.label",
    description: "settings.tab.systemLogs.desc",
    icon: "activity",
    edition: "personal",
  },
  {
    id: "network",
    label: "settings.tab.network.label",
    description: "settings.tab.network.desc",
    icon: "network",
    edition: "personal",
  },
  {
    id: "about",
    label: "settings.tab.about.label",
    description: "settings.tab.about.desc",
    icon: "info",
    edition: "personal",
  },
];

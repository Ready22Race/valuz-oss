import React from "react";
import ReactDOM from "react-dom/client";
import { initI18n } from "@valuz/shared/i18n";
import type { LocaleCode } from "@valuz/shared/i18n";
import "@valuz/ui";
import { UpdateWindowApp } from "./components/UpdateWindowApp";

const storedLocale = localStorage.getItem("valuz-locale") as
  | LocaleCode
  | null;
initI18n({ locale: storedLocale ?? "zh-CN", fallbackLocale: "zh-CN" });

ReactDOM.createRoot(
  document.getElementById("root") as HTMLElement,
).render(
  <React.StrictMode>
    <UpdateWindowApp />
  </React.StrictMode>,
);

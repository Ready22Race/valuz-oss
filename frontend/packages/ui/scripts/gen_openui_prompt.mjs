// Regenerates the vendored OpenUI genui-lib system prompt. Run after bumping
// @openuidev/react-ui. The output is loaded by the generate_ui tool at runtime
// (backend/valuz_agent/modules/genui/prompts.py). Dev-only — not imported by the app.
//
//   pnpm --filter @valuz/ui gen:openui-prompt
import { openuiLibrary, openuiPromptOptions } from "@openuidev/react-ui/genui-lib";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(
  here,
  "../../../../backend/valuz_agent/modules/genui/openui_genui_lib_prompt.txt",
);
writeFileSync(out, openuiLibrary.prompt(openuiPromptOptions));
console.log(`wrote ${out} (${openuiLibrary.prompt(openuiPromptOptions).length} chars)`);

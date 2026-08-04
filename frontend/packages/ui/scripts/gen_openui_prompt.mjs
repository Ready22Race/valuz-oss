// Regenerates the vendored generative-UI system prompt. Run after bumping
// @openuidev/react-ui or after adding/changing a block in @valuz/genui-blocks.
// The output is loaded by the generate_ui tool at runtime
// (backend/valuz_agent/modules/genui/prompts.py). Dev-only — not imported by the app.
//
//   pnpm --filter @valuz/ui gen:openui-prompt
//
// Generated from the MERGED library (OpenUI's components plus the Valuz
// blocks), because the renderer resolves against that same merged library at
// runtime. Generating from OpenUI's library alone is the silent failure mode
// here: a block would still render if the model emitted it, but nothing would
// ever have told the model it exists.
import { createValuzLibrary, valuzPromptOptions } from "@valuz/genui-blocks";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(
  here,
  "../../../../backend/valuz_agent/modules/genui/openui_genui_lib_prompt.txt",
);
const prompt = createValuzLibrary().prompt(valuzPromptOptions);
writeFileSync(out, prompt);
console.log(`wrote ${out} (${prompt.length} chars)`);

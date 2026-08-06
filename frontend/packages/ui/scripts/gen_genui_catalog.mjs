// Regenerates the vendored generative-UI block catalog. Run after adding or
// changing a block in @valuz/genui-blocks. The output is loaded by the
// generate_ui tool at runtime (backend/valuz_agent/modules/genui/) and spliced
// into the A2UI component catalog. Dev-only — not imported by the app.
//
//   pnpm --filter @valuz/ui gen:genui-catalog
//
// Generated rather than hand-written because that is the whole point: the
// catalog comes from the same block registry A2UIRenderer resolves names
// against, so the model is never told about a block that cannot render, nor
// left unaware of one that can. Editing the asset by hand re-opens exactly
// that drift — and both directions of it fail silently.
import { renderBlockCatalogText } from "@valuz/genui-blocks";
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const genuiDir = resolve(here, "../../../../backend/valuz_agent/modules/genui");

const path = resolve(genuiDir, "a2ui_block_catalog.txt");
const contents = `${renderBlockCatalogText()}\n`;
writeFileSync(path, contents);
console.log(`wrote ${path} (${contents.length} chars)`);

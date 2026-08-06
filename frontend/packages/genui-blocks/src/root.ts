import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

/**
 * The name of the component every document roots in.
 *
 * Read from OpenUI rather than hardcoded to `"Stack"` so an OpenUI upgrade that
 * renames the root does not leave this package reserving a name nothing uses.
 * `createLibrary` throws when the root is missing from its component list, so
 * this is the one name that survives every mode.
 *
 * Lives in its own module because both `library.ts` and `registry.ts` need it
 * and the former already imports the latter.
 */
export const ROOT_COMPONENT_NAME: string = openuiLibrary.root ?? "Stack";

import { readFile } from "node:fs/promises";
import { protocol } from "electron";
import { parseLocalFileUrl } from "@valuz/shared";

/**
 * The ``valuz-local://`` scheme serves a local file to the app's OWN renderer so
 * a ``kind==="local"`` file renders by URL (<img>/<iframe>/fetch) — uniform with
 * a remote presigned URL. Client-side only: no network, the backend never
 * proxies bytes. See docs/design/file-address-resolution.md.
 *
 * Trust model matches the existing ``read_file_content`` IPC — the renderer is
 * trusted, and the backend already validated file ownership before handing the
 * absolute path back in a resolve descriptor.
 */
export const LOCAL_FILE_SCHEME = "valuz-local";

const MIME: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  svg: "image/svg+xml",
  bmp: "image/bmp",
  ico: "image/x-icon",
  pdf: "application/pdf",
  mp4: "video/mp4",
  webm: "video/webm",
  mov: "video/quicktime",
  mp3: "audio/mpeg",
  wav: "audio/wav",
  ogg: "audio/ogg",
  m4a: "audio/mp4",
  txt: "text/plain; charset=utf-8",
  md: "text/markdown; charset=utf-8",
  json: "application/json; charset=utf-8",
  csv: "text/csv; charset=utf-8",
  html: "text/html; charset=utf-8",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

function mimeFor(p: string): string {
  const ext = p.includes(".") ? p.split(".").pop()!.toLowerCase() : "";
  return MIME[ext] ?? "application/octet-stream";
}

/**
 * Must be called AFTER ``app.whenReady()`` (register the scheme as privileged
 * separately, before ready). Registers the ``valuz-local://`` request handler.
 */
export function registerLocalFileProtocolHandler(): void {
  protocol.handle(LOCAL_FILE_SCHEME, async (request) => {
    // parseLocalFileUrl is the single, STRICT codec (shared with the renderer
    // and backend). It is strict on purpose: this URL is always built by
    // buildLocalFileUrl, so a non-canonical form is a builder bug we want to
    // see as a loud 404 + log, not silently "repair".
    const abs = parseLocalFileUrl(request.url);
    if (!abs) {
      console.error("valuz-local:// unparseable url", request.url);
      return new Response("bad request", { status: 400 });
    }
    try {
      const data = await readFile(abs);
      return new Response(new Uint8Array(data), {
        headers: { "content-type": mimeFor(abs) },
      });
    } catch (err) {
      // Surface why — a 404 here means the resolved path was wrong or the file
      // moved/was deleted after resolve.
      console.error(
        "valuz-local:// failed to serve",
        request.url,
        "->",
        err instanceof Error ? err.message : String(err),
      );
      return new Response("not found", { status: 404 });
    }
  });
}

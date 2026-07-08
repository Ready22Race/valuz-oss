import { readFile } from "node:fs/promises";
import { protocol } from "electron";

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

function urlToAbsPath(rawUrl: string): string {
  // valuz-local:///abs/path -> /abs/path (empty authority, three-slash form)
  let p = decodeURIComponent(new URL(rawUrl).pathname);
  if (/^\/[A-Za-z]:\//.test(p)) p = p.slice(1); // /C:/x -> C:/x (Windows)
  return p;
}

/**
 * Must be called AFTER ``app.whenReady()`` (register the scheme as privileged
 * separately, before ready). Registers the ``valuz-local://`` request handler.
 */
export function registerLocalFileProtocolHandler(): void {
  protocol.handle(LOCAL_FILE_SCHEME, async (request) => {
    try {
      const abs = urlToAbsPath(request.url);
      const data = await readFile(abs);
      return new Response(new Uint8Array(data), {
        headers: { "content-type": mimeFor(abs) },
      });
    } catch {
      return new Response("not found", { status: 404 });
    }
  });
}

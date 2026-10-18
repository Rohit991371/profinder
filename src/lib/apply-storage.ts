import { mkdir, readFile, writeFile } from "fs/promises";
import path from "path";

const ALLOWED_EXTENSIONS = new Set(["pdf", "png", "jpg", "jpeg", "webp", "doc", "docx"]);

export function safeDocumentName(name: string) {
  const base = path.basename(name).replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 80);
  const cleaned = base.replace(/^\.+/, "") || "document";
  const extension = cleaned.split(".").pop()?.toLowerCase() || "";
  if (!ALLOWED_EXTENSIONS.has(extension)) return null;
  if (cleaned.includes("..") || cleaned.includes("/") || cleaned.includes("\\")) return null;
  return cleaned;
}

export function contentTypeFor(name: string) {
  const extension = name.split(".").pop()?.toLowerCase();
  if (extension === "pdf") return "application/pdf";
  if (extension === "png") return "image/png";
  if (extension === "jpg" || extension === "jpeg") return "image/jpeg";
  if (extension === "webp") return "image/webp";
  if (extension === "doc") return "application/msword";
  if (extension === "docx") return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  return "application/octet-stream";
}

function isBlobKeyForRequest(requestId: string, storedName: string) {
  return storedName.startsWith(`apply/${requestId}/`) &&
    !storedName.includes("..") &&
    !storedName.includes("\\") &&
    !storedName.includes("?") &&
    !storedName.includes("://");
}

/**
 * Persist uploaded files privately on Vercel. The existing storedName database
 * column holds the private Blob pathname, so this change needs no schema or DB
 * migration. Local filesystem storage is only a development fallback.
 */
export async function storeApplyFile(requestId: string, filename: string, bytes: Buffer) {
  const safeName = safeDocumentName(filename);
  if (!safeName) throw new Error("UNSUPPORTED_FILE");

  if (process.env.BLOB_READ_WRITE_TOKEN) {
    const { put } = await import("@vercel/blob");
    const blob = await put(`apply/${requestId}/${Date.now()}-${safeName}`, bytes, {
      access: "private",
      token: process.env.BLOB_READ_WRITE_TOKEN,
      contentType: contentTypeFor(safeName),
      addRandomSuffix: true,
    });
    return { storedName: blob.pathname };
  }

  if (process.env.VERCEL) throw new Error("PRIVATE_STORAGE_UNCONFIGURED");
  const dir = path.join(process.cwd(), "data", "apply-uploads");
  await mkdir(dir, { recursive: true });
  const storedName = `${requestId}-${Date.now()}-${safeName}`;
  await writeFile(path.join(dir, storedName), bytes, { flag: "wx" });
  return { storedName };
}

export async function readApplyFile(requestId: string, storedName: string) {
  if (storedName.startsWith("apply/")) {
    if (!isBlobKeyForRequest(requestId, storedName) || !process.env.BLOB_READ_WRITE_TOKEN) return null;
    const { get } = await import("@vercel/blob");
    const result = await get(storedName, { access: "private", token: process.env.BLOB_READ_WRITE_TOKEN });
    if (!result || result.statusCode !== 200 || !result.stream) return null;
    return { stream: result.stream, contentType: result.blob.contentType || contentTypeFor(storedName) };
  }

  // Legacy files may be read only in local development. Vercel's local filesystem
  // is ephemeral, so do not pretend those old uploads are persistent there.
  if (process.env.VERCEL || !storedName || storedName !== path.basename(storedName) ||
      storedName.includes("..") || storedName.includes("\\")) return null;
  const root = path.join(process.cwd(), "data", "apply-uploads");
  const filePath = path.join(root, storedName);
  if (path.dirname(filePath) !== root) return null;
  try {
    const bytes = await readFile(filePath);
    return {
      stream: new ReadableStream<Uint8Array>({
        start(controller) { controller.enqueue(new Uint8Array(bytes)); controller.close(); },
      }),
      contentType: contentTypeFor(storedName),
    };
  } catch {
    return null;
  }
}

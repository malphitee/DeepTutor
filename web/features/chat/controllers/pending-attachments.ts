import type { AttachmentLimits } from "@/lib/attachment-limits";
import {
  classifyFile,
  isSvgFilename,
  DEFAULT_MAX_ATTACHMENT_BYTES,
  DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES,
} from "@/lib/doc-attachments";
import {
  extractBase64FromDataUrl,
  InvalidImageAttachmentError,
  ImageAttachmentTooLargeError,
  MAX_SOURCE_IMAGE_BYTES,
  prepareImageForModel,
  readFileAsDataUrl,
} from "@/lib/file-attachments";

export interface PendingAttachment {
  type: string;
  filename: string;
  base64?: string;
  previewUrl?: string;
  size?: number;
  mimeType?: string;
}

export type AttachmentRejectionReason = "unsupported" | "too_large" | "quota";

export interface AttachmentRejection {
  name: string;
  reason: AttachmentRejectionReason;
}

export function selectAttachmentFiles(
  files: File[],
  existingBytes: number,
  limits: AttachmentLimits
): { accepted: File[]; rejected: AttachmentRejection[] } {
  let runningTotal = existingBytes;
  const accepted: File[] = [];
  const rejected: AttachmentRejection[] = [];

  for (const file of files) {
    const kind = classifyFile(file);
    if (!kind) {
      rejected.push({ name: file.name, reason: "unsupported" });
      continue;
    }
    const image = kind === "image" && !isSvgFilename(file.name) && file.type !== "image/svg+xml";
    if (file.size > (image ? MAX_SOURCE_IMAGE_BYTES : limits.maxFileBytes)) {
      rejected.push({ name: file.name, reason: "too_large" });
      continue;
    }
    // Images get their quota check after compression; a 25 MB photo may become
    // a 500 KB upload. Documents still fail fast without reading their bytes.
    if (!image && runningTotal + file.size > limits.maxTotalBytes) {
      rejected.push({ name: file.name, reason: "quota" });
      break;
    }
    if (!image) runningTotal += file.size;
    accepted.push(file);
  }

  return { accepted, rejected };
}

export async function fileToPendingAttachment(
  file: File,
  maxImageBytes?: number
): Promise<PendingAttachment> {
  const svg = isSvgFilename(file.name) || file.type === "image/svg+xml";
  const isImage = !svg && classifyFile(file) === "image";
  if (isImage) {
    const prepared = await prepareImageForModel(file, maxImageBytes);
    const raw = await readFileAsDataUrl(prepared.blob);
    return {
      type: "image",
      filename: prepared.filename,
      base64: extractBase64FromDataUrl(raw),
      previewUrl: raw,
      size: prepared.blob.size,
      mimeType: prepared.mimeType,
    };
  }

  const raw = await readFileAsDataUrl(file);
  return {
    type: "file",
    filename: file.name,
    base64: extractBase64FromDataUrl(raw),
    previewUrl: svg ? raw : undefined,
    size: file.size,
    mimeType: file.type || undefined,
  };
}

export interface AttachmentPreparationFailure {
  name: string;
  reason: "invalid_image" | "read_failed" | "too_large" | "quota";
}

export async function preparePendingAttachments(
  files: File[],
  existingBytes = 0,
  limits: AttachmentLimits = {
    maxFileBytes: DEFAULT_MAX_ATTACHMENT_BYTES,
    maxTotalBytes: DEFAULT_MAX_TOTAL_ATTACHMENT_BYTES,
  }
): Promise<{
  attachments: PendingAttachment[];
  failures: AttachmentPreparationFailure[];
}> {
  const attachments: PendingAttachment[] = [];
  const failures: AttachmentPreparationFailure[] = [];
  let total = existingBytes;

  // Keep image decoding sequential. Mobile photos can be large, and the HEIC
  // fallback owns a single codec worker; parallel conversion creates large
  // memory spikes and can cross-wire worker callbacks.
  for (const file of files) {
    try {
      const attachment = await fileToPendingAttachment(file, limits.maxFileBytes);
      const size = attachment.size ?? 0;
      if (size > limits.maxFileBytes) {
        failures.push({ name: file.name, reason: "too_large" });
      } else if (total + size > limits.maxTotalBytes) {
        failures.push({ name: file.name, reason: "quota" });
      } else {
        total += size;
        attachments.push(attachment);
      }
    } catch (error) {
      failures.push({
        name: file.name || "image",
        reason:
          error instanceof ImageAttachmentTooLargeError
            ? "too_large"
            : error instanceof InvalidImageAttachmentError
              ? "invalid_image"
              : "read_failed",
      });
    }
  }

  return { attachments, failures };
}

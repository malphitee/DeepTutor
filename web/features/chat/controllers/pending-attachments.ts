import type { AttachmentLimits } from "@/lib/attachment-limits";
import { classifyFile, isSvgFilename } from "@/lib/doc-attachments";
import {
  extractBase64FromDataUrl,
  InvalidImageAttachmentError,
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

export type AttachmentRejectionReason =
  | "unsupported"
  | "too_large"
  | "quota";

export interface AttachmentRejection {
  name: string;
  reason: AttachmentRejectionReason;
}

export function selectAttachmentFiles(
  files: File[],
  existingBytes: number,
  limits: AttachmentLimits,
): { accepted: File[]; rejected: AttachmentRejection[] } {
  let runningTotal = existingBytes;
  const accepted: File[] = [];
  const rejected: AttachmentRejection[] = [];

  for (const file of files) {
    if (!classifyFile(file)) {
      rejected.push({ name: file.name, reason: "unsupported" });
      continue;
    }
    if (file.size > limits.maxFileBytes) {
      rejected.push({ name: file.name, reason: "too_large" });
      continue;
    }
    if (runningTotal + file.size > limits.maxTotalBytes) {
      rejected.push({ name: file.name, reason: "quota" });
      break;
    }
    runningTotal += file.size;
    accepted.push(file);
  }

  return { accepted, rejected };
}

export async function fileToPendingAttachment(
  file: File,
): Promise<PendingAttachment> {
  const svg = isSvgFilename(file.name) || file.type === "image/svg+xml";
  const isImage = !svg && classifyFile(file) === "image";
  if (isImage) {
    const prepared = await prepareImageForModel(file);
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
  reason: "invalid_image" | "read_failed";
}

export async function preparePendingAttachments(files: File[]): Promise<{
  attachments: PendingAttachment[];
  failures: AttachmentPreparationFailure[];
}> {
  const attachments: PendingAttachment[] = [];
  const failures: AttachmentPreparationFailure[] = [];

  // Keep image decoding sequential. Mobile photos can be large, and the HEIC
  // fallback owns a single codec worker; parallel conversion creates large
  // memory spikes and can cross-wire worker callbacks.
  for (const file of files) {
    try {
      attachments.push(await fileToPendingAttachment(file));
    } catch (error) {
      failures.push({
        name: file.name || "image",
        reason:
          error instanceof InvalidImageAttachmentError
            ? "invalid_image"
            : "read_failed",
      });
    }
  }

  return { attachments, failures };
}

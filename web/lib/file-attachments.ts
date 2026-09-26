"use client";

export const MODEL_IMAGE_MIME_TYPES = [
  "image/jpeg",
  "image/png",
  "image/gif",
  "image/webp",
] as const;

type ModelImageMime = (typeof MODEL_IMAGE_MIME_TYPES)[number];

const IMAGE_EXTENSION_BY_MIME: Record<ModelImageMime, string> = {
  "image/jpeg": ".jpg",
  "image/png": ".png",
  "image/gif": ".gif",
  "image/webp": ".webp",
};

/**
 * Model-facing images are deliberately smaller than the upload hard cap.
 * Keeping this policy here gives every composer one normalization seam before
 * base64 expansion, attachment-store persistence, history replay, or tools can
 * duplicate the payload.
 */
export const MODEL_IMAGE_TARGET_BYTES = 3 * 1024 * 1024;
export const MODEL_IMAGE_MAX_PIXELS = 6_000_000;
export const MODEL_IMAGE_MAX_EDGE = 2_560;
export const MAX_SOURCE_IMAGE_BYTES = 32 * 1024 * 1024;
const MAX_SOURCE_IMAGE_PIXELS = 64_000_000;

const MODEL_IMAGE_INITIAL_QUALITY = 0.88;
const MODEL_IMAGE_MIN_QUALITY = 0.68;
const MODEL_IMAGE_ENCODE_ATTEMPTS = 5;

export class InvalidImageAttachmentError extends Error {
  constructor() {
    super("The selected file does not contain a valid image.");
    this.name = "InvalidImageAttachmentError";
  }
}

export class ImageAttachmentTooLargeError extends Error {
  constructor() {
    super("The image exceeds the safe processing size.");
    this.name = "ImageAttachmentTooLargeError";
  }
}

export async function readFileAsDataUrl(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function readBlobBytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

/** Detect the four raster formats accepted by the model API from file bytes. */
export function sniffModelImageMime(bytes: Uint8Array): ModelImageMime | null {
  if (
    bytes.length >= 8 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47 &&
    bytes[4] === 0x0d &&
    bytes[5] === 0x0a &&
    bytes[6] === 0x1a &&
    bytes[7] === 0x0a
  ) {
    return "image/png";
  }
  if (bytes.length >= 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) {
    return "image/jpeg";
  }
  if (
    bytes.length >= 6 &&
    bytes[0] === 0x47 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x38 &&
    (bytes[4] === 0x37 || bytes[4] === 0x39) &&
    bytes[5] === 0x61
  ) {
    return "image/gif";
  }
  if (
    bytes.length >= 12 &&
    bytes[0] === 0x52 &&
    bytes[1] === 0x49 &&
    bytes[2] === 0x46 &&
    bytes[3] === 0x46 &&
    bytes[8] === 0x57 &&
    bytes[9] === 0x45 &&
    bytes[10] === 0x42 &&
    bytes[11] === 0x50
  ) {
    return "image/webp";
  }
  return null;
}

interface DecodedImage {
  source: CanvasImageSource;
  width: number;
  height: number;
  dispose: () => void;
}

async function decodeImage(file: File): Promise<DecodedImage> {
  try {
    if (typeof createImageBitmap === "function") {
      const bitmap = await createImageBitmap(file);
      return {
        source: bitmap,
        width: bitmap.width,
        height: bitmap.height,
        dispose: () => bitmap.close(),
      };
    }

    const objectUrl = URL.createObjectURL(file);
    try {
      const image = await new Promise<HTMLImageElement>((resolve, reject) => {
        const element = new Image();
        element.onload = () => resolve(element);
        element.onerror = () => reject(new InvalidImageAttachmentError());
        element.src = objectUrl;
      });
      return {
        source: image,
        width: image.naturalWidth,
        height: image.naturalHeight,
        dispose: () => URL.revokeObjectURL(objectUrl),
      };
    } catch (error) {
      URL.revokeObjectURL(objectUrl);
      throw error;
    }
  } catch {
    throw new InvalidImageAttachmentError();
  }
}

function convertedFilename(filename: string, mimeType: ModelImageMime): string {
  const extension = IMAGE_EXTENSION_BY_MIME[mimeType];
  const dot = filename.lastIndexOf(".");
  const basename = dot > 0 ? filename.slice(0, dot) : filename;
  return `${basename || "image"}${extension}`;
}

function isLikelyHeic(file: File, bytes: Uint8Array): boolean {
  const type = file.type.toLowerCase();
  const lowerName = file.name.toLowerCase();
  if (
    type === "image/heic" ||
    type === "image/heif" ||
    lowerName.endsWith(".heic") ||
    lowerName.endsWith(".heif")
  ) {
    return true;
  }
  if (bytes.length < 12) return false;
  const brand = String.fromCharCode(...bytes.slice(8, 12));
  return new Set(["heic", "heix", "hevc", "hevx", "heim", "heis"]).has(brand);
}

async function convertHeicToJpeg(file: File): Promise<Blob> {
  try {
    // Loaded only when the browser cannot decode HEIC natively, keeping the
    // ~2.7 MB codec out of the normal chat-composer path.
    const { default: heic2any } = await import("heic2any");
    const result = await heic2any({
      blob: file,
      toType: "image/jpeg",
      quality: 0.9,
    });
    const blob = Array.isArray(result) ? result[0] : result;
    if (!blob || blob.size === 0) throw new InvalidImageAttachmentError();
    return blob;
  } catch {
    throw new InvalidImageAttachmentError();
  }
}

function normalizedDimensions(decoded: DecodedImage): {
  width: number;
  height: number;
} {
  const scale = Math.min(
    1,
    MODEL_IMAGE_MAX_EDGE / Math.max(decoded.width, decoded.height),
    Math.sqrt(MODEL_IMAGE_MAX_PIXELS / (decoded.width * decoded.height))
  );
  return {
    width: Math.max(1, Math.round(decoded.width * scale)),
    height: Math.max(1, Math.round(decoded.height * scale)),
  };
}

function needsModelNormalization(file: Blob, decoded: DecodedImage, targetBytes: number): boolean {
  return (
    file.size > targetBytes ||
    Math.max(decoded.width, decoded.height) > MODEL_IMAGE_MAX_EDGE ||
    decoded.width * decoded.height > MODEL_IMAGE_MAX_PIXELS
  );
}

async function encodeRaster(
  decoded: DecodedImage,
  mimeType: "image/jpeg" | "image/png" | "image/webp",
  width: number,
  height: number,
  quality: number | undefined
): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new InvalidImageAttachmentError();
  if (mimeType === "image/jpeg") {
    // JPEG has no alpha channel. A white matte avoids browser-dependent black
    // backgrounds when converting transparent HEIC/AVIF-like inputs.
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
  }
  context.drawImage(decoded.source, 0, 0, width, height);
  try {
    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        blob => {
          if (blob?.size) resolve(blob);
          else reject(new InvalidImageAttachmentError());
        },
        mimeType,
        quality
      );
    });
  } finally {
    // Release the backing pixels between attempts, especially on mobile.
    canvas.width = 0;
    canvas.height = 0;
  }
}

async function rasterizeImage(
  decoded: DecodedImage,
  mimeType: "image/jpeg" | "image/png" | "image/webp",
  targetBytes: number
): Promise<Blob> {
  if (decoded.width <= 0 || decoded.height <= 0) {
    throw new InvalidImageAttachmentError();
  }
  let { width, height } = normalizedDimensions(decoded);
  let currentQuality = MODEL_IMAGE_INITIAL_QUALITY;
  let lastBlob: Blob | null = null;

  for (let attempt = 0; attempt < MODEL_IMAGE_ENCODE_ATTEMPTS; attempt += 1) {
    lastBlob = await encodeRaster(
      decoded,
      mimeType,
      width,
      height,
      mimeType === "image/png" ? undefined : currentQuality
    );
    if (lastBlob.size <= targetBytes) return lastBlob;
    if (attempt === MODEL_IMAGE_ENCODE_ATTEMPTS - 1) break;

    // Try preserving text/line art losslessly first. WebP keeps alpha when PNG
    // is too large; browsers without a WebP encoder return PNG, which is still
    // shrunk below the byte budget on subsequent attempts.
    if (mimeType === "image/png" && attempt === 0) {
      mimeType = "image/webp";
      continue;
    }

    // Encoded byte size is roughly proportional to pixel count. Shrink both
    // dimensions using its square root, with a small safety margin, then lower
    // lossy quality gradually. The clamp keeps each retry materially useful
    // without making a single unexpectedly large encoding unreadably small.
    const scale = Math.min(0.9, Math.max(0.5, Math.sqrt(targetBytes / lastBlob.size) * 0.95));
    width = Math.max(1, Math.round(width * scale));
    height = Math.max(1, Math.round(height * scale));
    currentQuality = Math.max(MODEL_IMAGE_MIN_QUALITY, currentQuality - 0.06);
  }

  throw new ImageAttachmentTooLargeError();
}

function encodedMime(blob: Blob, fallback: ModelImageMime): ModelImageMime {
  return MODEL_IMAGE_MIME_TYPES.includes(blob.type as ModelImageMime)
    ? (blob.type as ModelImageMime)
    : fallback;
}

export interface PreparedImageFile {
  blob: Blob;
  filename: string;
  mimeType: ModelImageMime;
}

/** Validate, orient, and bound images before upload and base64 expansion. */
export async function prepareImageForModel(
  file: File,
  maxBytes = MODEL_IMAGE_TARGET_BYTES
): Promise<PreparedImageFile> {
  if (file.size > MAX_SOURCE_IMAGE_BYTES || !Number.isFinite(maxBytes) || maxBytes <= 0) {
    throw new ImageAttachmentTooLargeError();
  }
  const targetBytes = Math.min(maxBytes, MODEL_IMAGE_TARGET_BYTES);
  const header = await readBlobBytes(file.slice(0, 32));
  let detectedMime = sniffModelImageMime(header);
  // A PNG advertises its dimensions before decoding: reject decompression
  // bombs without allocating their pixel buffer.
  if (detectedMime === "image/png" && header.length >= 24) {
    const view = new DataView(header.buffer, header.byteOffset, header.byteLength);
    if (view.getUint32(16) * view.getUint32(20) > MAX_SOURCE_IMAGE_PIXELS) {
      throw new ImageAttachmentTooLargeError();
    }
  }
  let decoded: DecodedImage;
  try {
    decoded = await decodeImage(file);
  } catch (error) {
    if (!isLikelyHeic(file, header)) throw error;
    const blob = await convertHeicToJpeg(file);
    // Codec output can still be a full-resolution 48 MP photo. Feed it through
    // exactly the same dimension/byte budget instead of returning it early.
    file = new File([blob], convertedFilename(file.name, "image/jpeg"), { type: "image/jpeg" });
    detectedMime = "image/jpeg";
    decoded = await decodeImage(file);
  }
  try {
    if (decoded.width * decoded.height > MAX_SOURCE_IMAGE_PIXELS) {
      throw new ImageAttachmentTooLargeError();
    }
    if (decoded.width <= 0 || decoded.height <= 0) throw new InvalidImageAttachmentError();
    if (detectedMime && !needsModelNormalization(file, decoded, targetBytes)) {
      return {
        blob: file,
        filename: convertedFilename(file.name, detectedMime),
        mimeType: detectedMime,
      };
    }

    const heic = isLikelyHeic(file, header);
    const requestedMime = heic
      ? "image/jpeg"
      : detectedMime === "image/jpeg"
        ? "image/jpeg"
        : detectedMime === "image/webp" || detectedMime === "image/gif"
          ? "image/webp"
          : "image/png";
    const blob = await rasterizeImage(decoded, requestedMime, targetBytes);
    const mimeType = encodedMime(blob, requestedMime);
    return {
      blob,
      filename: convertedFilename(file.name, mimeType),
      mimeType,
    };
  } finally {
    decoded.dispose();
  }
}

export function extractBase64FromDataUrl(dataUrl: string): string {
  return dataUrl.includes(",") ? dataUrl.split(",")[1] : dataUrl;
}

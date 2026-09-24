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

const MAX_CONVERTED_PIXELS = 16_000_000;
const MAX_CONVERTED_EDGE = 4_096;

export class InvalidImageAttachmentError extends Error {
  constructor() {
    super("The selected file does not contain a valid image.");
    this.name = "InvalidImageAttachmentError";
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
  if (
    bytes.length >= 3 &&
    bytes[0] === 0xff &&
    bytes[1] === 0xd8 &&
    bytes[2] === 0xff
  ) {
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
  return new Set(["heic", "heix", "hevc", "hevx", "heim", "heis"]).has(
    brand,
  );
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

async function rasterizeImage(
  decoded: DecodedImage,
  mimeType: "image/jpeg" | "image/png",
  quality?: number,
): Promise<Blob> {
  if (decoded.width <= 0 || decoded.height <= 0) {
    throw new InvalidImageAttachmentError();
  }
  const scale = Math.min(
    1,
    MAX_CONVERTED_EDGE / Math.max(decoded.width, decoded.height),
    Math.sqrt(MAX_CONVERTED_PIXELS / (decoded.width * decoded.height)),
  );
  const width = Math.max(1, Math.round(decoded.width * scale));
  const height = Math.max(1, Math.round(decoded.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new InvalidImageAttachmentError();
  context.drawImage(decoded.source, 0, 0, width, height);
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new InvalidImageAttachmentError());
    }, mimeType, quality);
  });
}

export interface PreparedImageFile {
  blob: Blob;
  filename: string;
  mimeType: ModelImageMime;
}

/** Validate an image and convert browser-decodable non-provider formats. */
export async function prepareImageForModel(file: File): Promise<PreparedImageFile> {
  const header = await readBlobBytes(file.slice(0, 32));
  const detectedMime = sniffModelImageMime(header);
  let decoded: DecodedImage;
  try {
    decoded = await decodeImage(file);
  } catch (error) {
    if (!isLikelyHeic(file, header)) throw error;
    const blob = await convertHeicToJpeg(file);
    return {
      blob,
      filename: convertedFilename(file.name, "image/jpeg"),
      mimeType: "image/jpeg",
    };
  }
  try {
    if (detectedMime) {
      return {
        blob: file,
        filename: convertedFilename(file.name, detectedMime),
        mimeType: detectedMime,
      };
    }

    const heic = isLikelyHeic(file, header);
    const mimeType = heic ? "image/jpeg" : "image/png";
    const blob = await rasterizeImage(
      decoded,
      mimeType,
      heic ? 0.9 : undefined,
    );
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

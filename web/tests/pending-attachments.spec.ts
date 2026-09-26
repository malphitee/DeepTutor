import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fileToPendingAttachment,
  preparePendingAttachments,
  selectAttachmentFiles,
} from "../features/chat/controllers/pending-attachments";
import { prepareImageForModel } from "../lib/file-attachments";

const heic2anyMock = vi.hoisted(() => vi.fn());
vi.mock("heic2any", () => ({ default: heic2anyMock }));

const originalCreateImageBitmap = globalThis.createImageBitmap;

afterEach(() => {
  vi.restoreAllMocks();
  heic2anyMock.mockReset();
  if (originalCreateImageBitmap) {
    globalThis.createImageBitmap = originalCreateImageBitmap;
  } else {
    Reflect.deleteProperty(globalThis, "createImageBitmap");
  }
});

describe("image attachment preparation", () => {
  it("normalizes an oversized provider-supported PNG before creating base64", async () => {
    const close = vi.fn();
    const drawImage = vi.fn();
    globalThis.createImageBitmap = vi.fn().mockResolvedValue({
      width: 5_712,
      height: 4_284,
      close,
    }) as typeof createImageBitmap;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage,
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(callback =>
      callback(
        new Blob([new Uint8Array(2 * 1024 * 1024)], {
          type: "image/webp",
        })
      )
    );

    const bytes = new Uint8Array(4 * 1024 * 1024);
    bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    const attachment = await fileToPendingAttachment(
      new File([bytes], "large-scan.png", { type: "image/png" })
    );

    expect(attachment.filename).toBe("large-scan.webp");
    expect(attachment.mimeType).toBe("image/webp");
    expect(attachment.size).toBe(2 * 1024 * 1024);
    expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 2_560, 1_920);
    expect(close).toHaveBeenCalledOnce();
  });

  it("converts a browser-decodable unsupported image to PNG", async () => {
    const close = vi.fn();
    globalThis.createImageBitmap = vi.fn().mockResolvedValue({
      width: 2,
      height: 3,
      close,
    }) as typeof createImageBitmap;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
      fillRect: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(callback =>
      callback(new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" }))
    );

    const attachment = await fileToPendingAttachment(
      new File([new Uint8Array([0, 1, 2, 3])], "photo.avif", {
        type: "image/avif",
      })
    );

    expect(attachment.type).toBe("image");
    expect(attachment.filename).toBe("photo.png");
    expect(attachment.mimeType).toBe("image/png");
    expect(close).toHaveBeenCalledOnce();
  });

  it("rejects a claimed image whose bytes cannot be decoded", async () => {
    globalThis.createImageBitmap = vi
      .fn()
      .mockRejectedValue(new DOMException("decode failed")) as typeof createImageBitmap;

    await expect(
      fileToPendingAttachment(new File(["not an image"], "broken.png", { type: "image/png" }))
    ).rejects.toThrow("valid image");
  });

  it("uses the HEIC codec when the browser cannot decode an iPhone photo", async () => {
    globalThis.createImageBitmap = vi
      .fn()
      .mockRejectedValueOnce(new DOMException("unsupported HEIC"))
      .mockResolvedValue({ width: 2, height: 3, close: vi.fn() }) as typeof createImageBitmap;
    heic2anyMock.mockResolvedValue(
      new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], {
        type: "image/jpeg",
      })
    );

    const attachment = await fileToPendingAttachment(
      new File([new Uint8Array([0, 1, 2, 3])], "IMG_0123.HEIC", {
        type: "image/heic",
      })
    );

    expect(heic2anyMock).toHaveBeenCalledOnce();
    expect(attachment.filename).toBe("IMG_0123.jpg");
    expect(attachment.mimeType).toBe("image/jpeg");
    expect(attachment.base64).toBe("/9j/2Q==");
  });

  it("converts natively decoded HEIC photos to JPEG instead of oversized PNG", async () => {
    globalThis.createImageBitmap = vi.fn().mockResolvedValue({
      width: 8_064,
      height: 6_048,
      close: vi.fn(),
    }) as typeof createImageBitmap;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
      fillRect: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    const toBlob = vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(callback => {
      callback(
        new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], {
          type: "image/jpeg",
        })
      );
    });

    const attachment = await fileToPendingAttachment(
      new File([new Uint8Array([0, 1, 2, 3])], "IMG_0048.heic", {
        type: "image/heic",
      })
    );

    expect(attachment.filename).toBe("IMG_0048.jpg");
    expect(attachment.mimeType).toBe("image/jpeg");
    expect(toBlob).toHaveBeenCalledWith(expect.any(Function), "image/jpeg", 0.88);
  });

  it("keeps small valid PNG bytes unchanged", async () => {
    globalThis.createImageBitmap = vi
      .fn()
      .mockResolvedValue({ width: 400, height: 300, close: vi.fn() });
    const file = new File(
      [new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])],
      "small.png",
      { type: "image/png" }
    );
    const result = await prepareImageForModel(file);
    expect(result.blob).toBe(file);
  });

  it("tries lossless PNG before WebP and reports the actual encoder MIME", async () => {
    globalThis.createImageBitmap = vi
      .fn()
      .mockResolvedValue({ width: 5712, height: 4284, close: vi.fn() });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    const encode = vi
      .spyOn(HTMLCanvasElement.prototype, "toBlob")
      .mockImplementationOnce(callback =>
        callback(new Blob([new Uint8Array(4 * 1024 * 1024)], { type: "image/png" }))
      )
      // Simulate a browser without a WebP encoder. It may return a PNG instead.
      .mockImplementation(callback =>
        callback(new Blob([new Uint8Array(1024)], { type: "image/png" }))
      );
    const file = new File(
      [new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])],
      "scan.png",
      { type: "image/png" }
    );
    const result = await prepareImageForModel(file);
    expect(encode.mock.calls.map(call => call[1])).toEqual(["image/png", "image/webp"]);
    expect(result.mimeType).toBe("image/png");
    expect(result.filename).toBe("scan.png");
  });

  it("admits a 25 MB image then enforces quotas using the normalized bytes", async () => {
    globalThis.createImageBitmap = vi
      .fn()
      .mockResolvedValue({ width: 5712, height: 4284, close: vi.fn() });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(callback =>
      callback(new Blob([new Uint8Array(1024)], { type: "image/png" }))
    );
    const bytes = new Uint8Array(25 * 1024 * 1024);
    bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    const file = new File([bytes], "photo.png", { type: "image/png" });
    const limits = { maxFileBytes: 2048, maxTotalBytes: 2048 };
    const selected = selectAttachmentFiles([file, file], 1024, limits);
    expect(selected.rejected).toEqual([]);
    const prepared = await preparePendingAttachments(selected.accepted, 1024, limits);
    expect(prepared.attachments.map(item => item.size)).toEqual([1024]);
    expect(prepared.failures).toEqual([{ name: "photo.png", reason: "quota" }]);
  });

  it("bounds codec output from HEIC fallback too", async () => {
    const drawImage = vi.fn();
    globalThis.createImageBitmap = vi
      .fn()
      .mockRejectedValueOnce(new DOMException("unsupported HEIC"))
      .mockResolvedValue({ width: 8064, height: 6048, close: vi.fn() });
    heic2anyMock.mockResolvedValue(
      new Blob([new Uint8Array(4 * 1024 * 1024)], { type: "image/jpeg" })
    );
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage,
      fillRect: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(callback =>
      callback(new Blob([new Uint8Array(1024)], { type: "image/jpeg" }))
    );
    const result = await fileToPendingAttachment(
      new File(["heic"], "photo.heic", { type: "image/heic" })
    );
    expect(result.size).toBe(1024);
    expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 2560, 1920);
  });

  it("rejects PNG decompression bombs before decoding", async () => {
    const decode = vi.fn();
    globalThis.createImageBitmap = decode;
    const bytes = new Uint8Array(32);
    bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    new DataView(bytes.buffer).setUint32(16, 100_000);
    new DataView(bytes.buffer).setUint32(20, 100_000);
    await expect(prepareImageForModel(new File([bytes], "bomb.png"))).rejects.toThrow(
      "safe processing size"
    );
    expect(decode).not.toHaveBeenCalled();
  });

  it("does not return an over-budget image after bounded encoding attempts", async () => {
    globalThis.createImageBitmap = vi
      .fn()
      .mockResolvedValue({ width: 5712, height: 4284, close: vi.fn() });
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    const encode = vi
      .spyOn(HTMLCanvasElement.prototype, "toBlob")
      .mockImplementation(callback =>
        callback(new Blob([new Uint8Array(2048)], { type: "image/png" }))
      );
    await expect(prepareImageForModel(new File(["image"], "scan.avif"), 1024)).rejects.toThrow(
      "safe processing size"
    );
    expect(encode).toHaveBeenCalledTimes(5);
  });
});

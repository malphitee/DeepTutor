import { afterEach, describe, expect, it, vi } from "vitest";

import { fileToPendingAttachment } from "../features/chat/controllers/pending-attachments";

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
  it("converts a browser-decodable unsupported image to PNG", async () => {
    const close = vi.fn();
    globalThis.createImageBitmap = vi.fn().mockResolvedValue({
      width: 2,
      height: 3,
      close,
    }) as typeof createImageBitmap;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(
      (callback) => callback(new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" })),
    );

    const attachment = await fileToPendingAttachment(
      new File([new Uint8Array([0, 1, 2, 3])], "photo.avif", {
        type: "image/avif",
      }),
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
      fileToPendingAttachment(
        new File(["not an image"], "broken.png", { type: "image/png" }),
      ),
    ).rejects.toThrow("valid image");
  });

  it("uses the HEIC codec when the browser cannot decode an iPhone photo", async () => {
    globalThis.createImageBitmap = vi
      .fn()
      .mockRejectedValue(new DOMException("unsupported HEIC")) as typeof createImageBitmap;
    heic2anyMock.mockResolvedValue(
      new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], {
        type: "image/jpeg",
      }),
    );

    const attachment = await fileToPendingAttachment(
      new File([new Uint8Array([0, 1, 2, 3])], "IMG_0123.HEIC", {
        type: "image/heic",
      }),
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
    } as unknown as CanvasRenderingContext2D);
    const toBlob = vi
      .spyOn(HTMLCanvasElement.prototype, "toBlob")
      .mockImplementation((callback) => {
        callback(new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], {
          type: "image/jpeg",
        }));
      });

    const attachment = await fileToPendingAttachment(
      new File([new Uint8Array([0, 1, 2, 3])], "IMG_0048.heic", {
        type: "image/heic",
      }),
    );

    expect(attachment.filename).toBe("IMG_0048.jpg");
    expect(attachment.mimeType).toBe("image/jpeg");
    expect(toBlob).toHaveBeenCalledWith(expect.any(Function), "image/jpeg", 0.9);
  });
});

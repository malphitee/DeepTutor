import { act, renderHook, render, screen, fireEvent } from "@testing-library/react";
import { createRef } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { usePendingAttachments } from "@/features/chat/controllers/usePendingAttachments";
import { ComposerInput } from "@/components/chat/home/ComposerInput";

const prepare = vi.hoisted(() => vi.fn());
vi.mock("@/features/chat/controllers/pending-attachments", () => ({
  preparePendingAttachments: prepare,
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/components/chat/space/ChatSpaceMenu", () => ({ default: () => null }));

const limits = { maxFileBytes: 10, maxTotalBytes: 10 };
const file = new File(["image"], "scan.png", { type: "image/png" });
const attachment = { type: "image", filename: "scan.png", size: 6 };
const prepared = { attachments: [attachment], failures: [] };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => prepare.mockReset());

it("serializes separate paste batches and checks their combined quota", async () => {
  const first = deferred<typeof prepared>();
  prepare.mockReturnValueOnce(first.promise).mockResolvedValue(prepared);
  const { result } = renderHook(() => usePendingAttachments(limits));
  let batches!: Promise<{ failures: { reason: string }[] }>[];
  let sendAttachments!: ReturnType<typeof result.current.waitForAttachments>;
  await act(async () => {
    batches = [
      result.current.prepareAndAppendAttachments([file]),
      result.current.prepareAndAppendAttachments([file]),
    ];
    sendAttachments = result.current.waitForAttachments();
    await Promise.resolve();
  });
  expect(prepare).toHaveBeenCalledTimes(1);
  expect(result.current.attachmentsPreparing).toBe(true);
  await act(async () => {
    first.resolve(prepared);
    const outcomes = await Promise.all(batches);
    expect(outcomes[1].failures).toEqual([{ name: "scan.png", reason: "quota" }]);
    expect(await sendAttachments).toEqual([attachment]);
  });
  expect(result.current.attachments).toEqual([attachment]);
  expect(result.current.attachmentsPreparing).toBe(false);
});

it("shares the HEIC preparation queue across mounted composers", async () => {
  const first = deferred<typeof prepared>();
  prepare.mockReturnValueOnce(first.promise).mockResolvedValue(prepared);
  const one = renderHook(() => usePendingAttachments(limits));
  const two = renderHook(() => usePendingAttachments(limits));
  let batches!: Promise<unknown>[];
  await act(async () => {
    batches = [
      one.result.current.prepareAndAppendAttachments([file]),
      two.result.current.prepareAndAppendAttachments([file]),
    ];
    await Promise.resolve();
  });
  expect(prepare).toHaveBeenCalledTimes(1);
  await act(async () => {
    first.resolve(prepared);
    await Promise.all(batches);
  });
  expect(one.result.current.attachments).toHaveLength(1);
  expect(two.result.current.attachments).toHaveLength(1);
});

it("uses current attachments after removal during decoding", async () => {
  const conversion = deferred<typeof prepared>();
  prepare.mockReturnValue(conversion.promise);
  const { result } = renderHook(() => usePendingAttachments(limits));
  let batch!: Promise<unknown>;
  await act(async () => {
    result.current.setAttachments([attachment]);
    batch = result.current.prepareAndAppendAttachments([file]);
    await Promise.resolve();
    result.current.setAttachments(items => items.slice(1));
    conversion.resolve(prepared);
    await batch;
  });
  expect(result.current.attachments).toEqual([attachment]);
});

it("does not restore cleared attachments or send a cancelled pending batch", async () => {
  const conversion = deferred<typeof prepared>();
  prepare.mockReturnValue(conversion.promise);
  const { result } = renderHook(() => usePendingAttachments(limits));
  await act(async () => {
    const batch = result.current.prepareAndAppendAttachments([file]);
    const sending = result.current.waitForAttachments();
    await Promise.resolve();
    result.current.setAttachments([]);
    conversion.resolve(prepared);
    await batch;
    expect(await sending).toBeNull();
  });
  expect(result.current.attachments).toEqual([]);
  expect(result.current.attachmentsPreparing).toBe(false);
});

it("abandons an unmounted composer's pending send without blocking another composer", async () => {
  const conversion = deferred<typeof prepared>();
  prepare.mockReturnValueOnce(conversion.promise).mockResolvedValue(prepared);
  const one = renderHook(() => usePendingAttachments(limits));
  const two = renderHook(() => usePendingAttachments(limits));
  let sending!: ReturnType<typeof one.result.current.waitForAttachments>;
  let batches!: Promise<unknown>[];
  await act(async () => {
    batches = [
      one.result.current.prepareAndAppendAttachments([file]),
      two.result.current.prepareAndAppendAttachments([file]),
    ];
    sending = one.result.current.waitForAttachments();
    await Promise.resolve();
  });
  one.unmount();
  await act(async () => {
    conversion.resolve(prepared);
    await Promise.all(batches);
    expect(await sending).toBeNull();
  });
  expect(two.result.current.attachments).toEqual([attachment]);
});

it("keeps the draft and blocks Enter until its images are ready", () => {
  const onSend = vi.fn();
  const props = {
    textareaRef: createRef<HTMLTextAreaElement>(),
    isVisualizeMode: false,
    canSendEmpty: false,
    onSend,
    onInputChange: vi.fn(),
    onPaste: vi.fn(),
    selectedCounts: {
      attachments: 0,
      knowledge: 0,
      chatHistory: 0,
      myAgents: 0,
      books: 0,
      reading: 0,
      notebooks: 0,
      questionBank: 0,
      persona: 0,
      memory: 0,
    },
    knowledgeAvailable: false,
    personaAvailable: false,
    onSelectAttach: vi.fn(),
    onSelectNotebookPicker: vi.fn(),
    onSelectBookPicker: vi.fn(),
    onSelectHistoryPicker: vi.fn(),
    onSelectQuestionBankPicker: vi.fn(),
    onSelectPersonaPicker: vi.fn(),
    onSelectMemoryPicker: vi.fn(),
  };
  const view = render(<ComposerInput {...props} attachmentsPreparing />);
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "Read my image" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onSend).not.toHaveBeenCalled();
  expect(input).toHaveValue("Read my image");
  view.rerender(<ComposerInput {...props} attachmentsPreparing={false} />);
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onSend).toHaveBeenCalledWith("Read my image");
});

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initI18n } from "@/i18n/init";
import ChannelRuntimeStatus from "@/components/partners/ChannelRuntimeStatus";

const mocks = vi.hoisted(() => ({
  getRuntime: vi.fn(),
  reload: vi.fn(),
  start: vi.fn(),
}));

vi.mock("@/lib/partners-api", () => ({
  getPartnerChannelRuntime: mocks.getRuntime,
  reloadPartnerChannels: mocks.reload,
  startPartner: mocks.start,
}));

initI18n("en");

function response(status: string) {
  return {
    partner_id: "ada",
    running: true,
    channels: {
      whatsapp: {
        enabled: true,
        running: status === "connected" || status === "running",
        setup: { status },
      },
    },
  };
}

describe("ChannelRuntimeStatus", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-09-24T00:00:00Z"));
    mocks.getRuntime.mockReset();
    mocks.reload.mockReset();
    mocks.reload.mockResolvedValue(undefined);
    mocks.start.mockReset();
    mocks.start.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("updates when a connecting channel reaches a terminal runtime state", async () => {
    mocks.getRuntime
      .mockResolvedValueOnce(response("connecting"))
      .mockResolvedValue(response("running"));

    render(<ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent("Connecting");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByRole("status")).toHaveTextContent("Listener running");
  });

  it("turns a stale connecting response into an actionable retry state", async () => {
    mocks.getRuntime.mockResolvedValue(response("connecting"));

    render(<ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />);
    await act(async () => {
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(screen.getByRole("status")).toHaveTextContent("Connection failed");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Channel startup timed out. Retry the channel.",
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    expect(mocks.reload).toHaveBeenCalledWith("ada", expect.any(AbortSignal));
  });

  it("surfaces a polling failure instead of hiding the status forever", async () => {
    mocks.getRuntime.mockRejectedValue(new Error("offline"));

    render(<ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(screen.getByRole("status")).toHaveTextContent(
      "Unable to read channel status. Refresh to try again.",
    );
    mocks.getRuntime.mockResolvedValue(response("connected"));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    });
    expect(screen.getByRole("status")).toHaveTextContent("Connected");
    expect(mocks.reload).not.toHaveBeenCalled();
    expect(mocks.start).not.toHaveBeenCalled();
  });

  it("aborts a hung status request and exposes a refresh action", async () => {
    mocks.getRuntime.mockImplementation(
      (_id, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    render(<ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(mocks.getRuntime.mock.calls[0][1].aborted).toBe(true);
    expect(
      screen.getByRole("button", { name: "Refresh status" }),
    ).toBeEnabled();
  });

  it("preserves the server startup age when the page is reopened", async () => {
    const value = response("connecting");
    mocks.getRuntime.mockResolvedValue({
      ...value,
      channels: {
        whatsapp: {
          ...value.channels.whatsapp,
          setup_updated_at: Date.now() / 1000 - 90,
        },
      },
    });
    const first = render(
      <ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Channel startup timed out",
    );
    first.unmount();
    render(<ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Channel startup timed out",
    );
    mocks.getRuntime.mockResolvedValue(response("connected"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByRole("status")).toHaveTextContent("Connected");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("restarts a failed partner that has no running listener to reload", async () => {
    mocks.getRuntime.mockResolvedValue({
      ...response("error"),
      running: false,
    });
    render(<ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />);
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    expect(mocks.start).toHaveBeenCalledWith("ada", expect.any(AbortSignal));
    expect(mocks.reload).not.toHaveBeenCalled();
  });

  it("accepts a new server startup time after another page restarts the channel", async () => {
    let startedAt = Date.now() / 1000 - 90;
    mocks.getRuntime.mockImplementation(async () => ({
      ...response("connecting"),
      channels: {
        whatsapp: {
          ...response("connecting").channels.whatsapp,
          setup_updated_at: startedAt,
        },
      },
    }));
    render(<ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Channel startup timed out",
    );

    startedAt = Date.now() / 1000;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByRole("status")).toHaveTextContent("Connecting");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps a rejected restart actionable", async () => {
    mocks.getRuntime.mockResolvedValue(response("error"));
    mocks.reload.mockRejectedValue(
      new Error("Partner leader did not process the command"),
    );
    render(<ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />);
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Partner leader did not process the command",
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("bounds a restart request so Retry cannot remain busy forever", async () => {
    mocks.getRuntime.mockResolvedValue(response("error"));
    mocks.reload.mockImplementation(
      (_id, signal: AbortSignal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    render(<ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />);
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    });
    expect(screen.getByRole("button", { name: "Retrying…" })).toBeDisabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Channel restart failed",
    );
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("discards an old request after switching channels", async () => {
    let resolveOld: (value: ReturnType<typeof response>) => void = () => {};
    mocks.getRuntime.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    );
    const view = render(
      <ChannelRuntimeStatus partnerId="ada" channel="whatsapp" enabled />,
    );
    mocks.getRuntime.mockResolvedValue({
      ...response("connected"),
      channels: {
        feishu: response("running").channels.whatsapp,
      },
    });
    view.rerender(
      <ChannelRuntimeStatus partnerId="ada" channel="feishu" enabled />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByRole("status")).toHaveTextContent("Listener running");
    await act(async () => {
      resolveOld(response("error"));
    });
    expect(screen.getByRole("status")).toHaveTextContent("Listener running");
    view.unmount();
    const calls = mocks.getRuntime.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(mocks.getRuntime).toHaveBeenCalledTimes(calls);
  });
});

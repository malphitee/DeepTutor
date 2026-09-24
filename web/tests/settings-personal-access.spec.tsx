import React, { useEffect } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { AppShellProvider, useAppShell } from "@/context/AppShellContext";
import {
  LANGUAGE_STORAGE_KEY,
  RESPONSE_LANGUAGE_STORAGE_KEY,
} from "@/context/app-shell-storage";
import {
  SettingsProvider,
  defaultCatalog,
  useSettings,
} from "@/features/settings/store/SettingsStore";
import { SettingsLoadStatusBanner } from "@/components/settings/SettingsLoadStatusBanner";

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), router: { push: vi.fn() } }));
vi.mock("@/lib/api", () => ({
  apiUrl: (url: string) => url,
  apiFetch: (...args: unknown[]) => mocks.fetch(...args),
}));
vi.mock("next/navigation", () => ({ useRouter: () => mocks.router }));
const t = (key: string) => key;
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));

let settings: ReturnType<typeof useSettings>;
function Capture() {
  const value = useSettings();
  useEffect(() => { settings = value; }, [value]);
  const shell = useAppShell();
  return (
    <>
      <output data-testid="shell-language">{shell.language}</output>
      <output data-testid="settings-language">{value.language}</output>
      <output data-testid="response-language">{value.responseLanguage}</output>
      <SettingsLoadStatusBanner />
    </>
  );
}
function mount() {
  return render(
    <AppShellProvider>
      <SettingsProvider><Capture /></SettingsProvider>
    </AppShellProvider>,
  );
}
const reply = (payload: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => payload,
});
const ui = { theme: "dark", language: "zh", response_language: "en" };

beforeEach(() => {
  mocks.fetch.mockReset();
  vi.spyOn(console, "error").mockImplementation(() => {});
});

it("keeps denied settings in sync when the public shell language resolves later", async () => {
  let finishBootstrap!: (value: ReturnType<typeof reply>) => void;
  mocks.fetch.mockImplementation(async (url: string) => {
    if (url === "/api/settings/ui") {
      return new Promise((resolve) => { finishBootstrap = resolve; });
    }
    return reply({ detail: "Learning surface denied" }, 403);
  });
  mount();
  await screen.findByText("These settings are managed by your administrator.");
  expect(settings.settingsErrorStatus).toBe(403);
  expect(screen.queryByText(/Verify the backend is running/)).not.toBeInTheDocument();

  await act(async () => { finishBootstrap(reply(ui)); });
  await waitFor(() => expect(screen.getByTestId("settings-language")).toHaveTextContent("zh"));
  expect(screen.getByTestId("shell-language")).toHaveTextContent("zh");
  expect(screen.getByTestId("response-language")).toHaveTextContent("en");
  expect(localStorage.getItem(LANGUAGE_STORAGE_KEY)).toBe("zh");
  expect(localStorage.getItem(RESPONSE_LANGUAGE_STORAGE_KEY)).toBe("en");
  expect(mocks.fetch.mock.calls.map(([url]) => url)).not.toContain("/api/system/status");
  expect(mocks.fetch.mock.calls.map(([url]) => url)).not.toContain("/api/settings/draft");
});

it("retains browser preferences after 403 and clears the permission state on retry", async () => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh");
  localStorage.setItem(RESPONSE_LANGUAGE_STORAGE_KEY, "zh");
  mocks.fetch.mockResolvedValue(reply({ detail: "Forbidden" }, 403));
  mount();
  await screen.findByRole("alert");
  expect(settings.language).toBe("zh");
  expect(settings.responseLanguage).toBe("zh");

  mocks.fetch.mockImplementation(async (url: string) =>
    reply(url === "/api/settings" ? { ui } : { draft: null }),
  );
  await act(async () => { await settings.reloadSettings(); });
  expect(settings.settingsErrorStatus).toBeNull();
  expect(settings.settingsError).toBeNull();
  expect(settings.language).toBe("zh");
  expect(settings.responseLanguage).toBe("en");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it.each([401, 500, 0])("distinguishes authentication and connection failures (%s)", async (status) => {
  localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh");
  localStorage.setItem(RESPONSE_LANGUAGE_STORAGE_KEY, "zh");
  mocks.fetch.mockImplementation(async () => {
    if (!status) throw new TypeError("Failed to fetch");
    return reply({ detail: "Request failed" }, status);
  });
  mount();
  const alert = await screen.findByRole("alert");
  if (status === 401) {
    expect(alert).toHaveTextContent("Sign in to load your personal settings.");
    expect(alert).not.toHaveTextContent("Verify the backend is running");
  } else {
    expect(alert).toHaveTextContent("Could not load settings from the backend.");
    expect(alert).toHaveTextContent("Verify the backend is running");
  }
  expect(settings.language).toBe("zh");
});

it("saves and applies personal preferences without requesting administrator diagnostics or catalogs", async () => {
  mocks.fetch.mockImplementation(async (url: string) => {
    if (url === "/api/settings/ui") return reply(ui);
    if (url === "/api/settings") return reply({ ui });
    return reply({ draft: null });
  });
  mount();
  await waitFor(() => expect(settings.settingsLoading).toBe(false));
  expect(settings.catalogEditable).toBe(false);
  await act(async () => { await settings.updateLanguage("en"); });
  await act(async () => { await settings.saveDraft(); });
  await act(async () => { await settings.applyCatalog(); });
  const draftWrites = mocks.fetch.mock.calls.filter(([url, init]) =>
    url === "/api/settings/draft" && init?.method === "PUT",
  );
  expect(draftWrites).toHaveLength(2);
  expect(JSON.parse(draftWrites[0][1].body)).toMatchObject({
    catalog: null,
    extensions: { ui: { language: "en", response_language: "en", theme: "dark" } },
  });
  expect(settings.draftState).toBe("clean");
  expect(settings.language).toBe("en");
  expect(screen.getByTestId("shell-language")).toHaveTextContent("en");
  const urls = mocks.fetch.mock.calls.map(([url]) => url);
  expect(urls).not.toContain("/api/system/status");
  expect(urls).not.toContain("/api/settings/apply");
});

it("continues to load runtime diagnostics for administrator catalog settings", async () => {
  mocks.fetch.mockImplementation(async (url: string) => {
    if (url === "/api/settings/ui") return reply(ui);
    if (url === "/api/settings") return reply({ ui, catalog: defaultCatalog() });
    if (url === "/api/system/status") return reply({ marker: "admin diagnostics" });
    return reply({ draft: null });
  });
  mount();
  await waitFor(() => expect(settings.status).toEqual({ marker: "admin diagnostics" }));
  expect(settings.catalogEditable).toBe(true);
  expect(settings.settingsError).toBeNull();
});

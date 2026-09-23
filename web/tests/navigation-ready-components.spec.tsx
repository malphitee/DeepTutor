import React from "react";
import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import KnowledgePage from "@/components/knowledge/KnowledgePage";
import { useLearningCreation } from "@/components/learning/LibraryWorkspace";
import { WatchingSurface } from "@/components/watching/WatchingWorkspace";

const navigation = vi.hoisted(() => ({
  pathname: null as string | null,
  params: null as Record<string, string> | null,
  search: null as URLSearchParams | null,
  push: vi.fn(),
  replace: vi.fn(),
}));
const data = vi.hoisted(() => ({ knowledge: vi.fn(), watching: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useParams: () => navigation.params,
  useSearchParams: () => navigation.search,
  useRouter: () => ({ push: navigation.push, replace: navigation.replace }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("next/dynamic", () => ({
  default: () => function Detail({ provider }: { provider?: { id: string } }) {
    return <div>{provider?.id}</div>;
  },
}));
vi.mock("@/hooks/useKnowledgeBases", () => ({ useKnowledgeBases: data.knowledge }));
vi.mock("@/components/knowledge/KnowledgeHome", () => ({
  default: () => <div>Knowledge overview</div>,
}));
vi.mock("@/hooks/useChatWorkspaces", () => ({
  useChatWorkspaces: () => ({ workspaces: [], error: "" }),
}));
vi.mock("@/context/WatchingContext", () => ({ useWatching: data.watching }));
vi.mock("@/components/watching/WatchingBrowser", () => ({
  WatchingBrowser: () => <div>Video browser</div>,
}));
vi.mock("@/components/watching/WatchingPane", () => ({
  WatchingPane: () => <div>Video pane</div>,
  WATCHING_ASK_EVENT: "dt:watching-ask",
}));

beforeEach(() => {
  navigation.pathname = null;
  navigation.params = null;
  navigation.search = null;
  data.knowledge.mockReturnValue({
    kbs: [], providers: [{ id: "engine-a" }], loading: false,
  });
  data.watching.mockReturnValue({ material: null });
});

it("waits for knowledge query and route params before loading or synchronizing a deep link", () => {
  const view = render(<KnowledgePage />);
  expect(data.knowledge).not.toHaveBeenCalled();
  expect(navigation.replace).not.toHaveBeenCalled();

  navigation.params = {};
  view.rerender(<KnowledgePage />);
  expect(data.knowledge).not.toHaveBeenCalled();
  expect(navigation.replace).not.toHaveBeenCalled();

  navigation.search = new URLSearchParams("engine=engine-a");
  view.rerender(<KnowledgePage />);
  expect(screen.getByText("engine-a")).toBeInTheDocument();
  expect(screen.queryByText("Knowledge overview")).not.toBeInTheDocument();
  expect(navigation.replace).not.toHaveBeenCalled();
});

it("retains a watching callback until query and route params are ready", () => {
  const replaceState = vi.spyOn(window.history, "replaceState");
  const view = render(<WatchingSurface />);
  expect(data.watching).not.toHaveBeenCalled();
  expect(replaceState).not.toHaveBeenCalled();

  navigation.search = new URLSearchParams("account=connected");
  view.rerender(<WatchingSurface />);
  expect(data.watching).not.toHaveBeenCalled();
  expect(replaceState).not.toHaveBeenCalled();

  navigation.params = {};
  view.rerender(<WatchingSurface />);
  expect(screen.getByText("Video browser")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("Invidious account connected.");
  expect(replaceState).toHaveBeenCalledOnce();
});

function Creation({ open }: { open: () => void }) {
  useLearningCreation(open);
  return null;
}

it("preserves a pending creation query until pathname is ready and then consumes it once", () => {
  const open = vi.fn();
  const view = render(<Creation open={open} />);
  expect(open).not.toHaveBeenCalled();
  expect(navigation.replace).not.toHaveBeenCalled();

  navigation.search = new URLSearchParams("create=1&dt_workspace=other");
  view.rerender(<Creation open={open} />);
  expect(open).not.toHaveBeenCalled();
  expect(navigation.replace).not.toHaveBeenCalled();

  navigation.pathname = "/learning/books";
  view.rerender(<Creation open={open} />);
  expect(open).toHaveBeenCalledOnce();
  expect(navigation.replace).toHaveBeenCalledWith(
    "/learning/books?dt_workspace=other", { scroll: false },
  );
  view.rerender(<Creation open={open} />);
  expect(open).toHaveBeenCalledOnce();
});

// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

import { DIAGNOSTICS_ERROR_MESSAGE, formatBytes } from "@/lib/diagnostics";

const nav = vi.hoisted(() => ({
  pathname: "/dashboard/system/diagnostics",
  search: "",
  replace: vi.fn(),
}));

const userState = vi.hoisted(() => ({
  is_superuser: true,
}));

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({
    replace: nav.replace,
    push: vi.fn(),
    prefetch: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(nav.search),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...props
  }: {
    href: string;
    children: ReactNode;
    [key: string]: unknown;
  }) => (
    <a href={typeof href === "string" ? href : "#"} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("@/components/providers/user-provider", () => ({
  useUser: () => ({
    user: { id: "user-1", is_superuser: userState.is_superuser },
    isLoading: false,
    refresh: () => undefined,
  }),
}));

vi.mock("@/components/dashboard-header", () => ({
  DashboardHeader: () => <div data-testid="dashboard-header" />,
}));

vi.mock("@/hooks/use-mobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("@/lib/api/client", () => ({
  default: {
    GET: mocks.get,
  },
}));

import DiagnosticsPage from "./page";

const SECRET = "super-secret-db-password";
const TABLE_TOTAL_BYTES = 1_048_576;

const storageSummary = {
  generated_at: "2026-01-15T12:00:00Z",
  integrity_check_enabled: false,
  integrity_check_interval_hours: 24,
  freshness_note: "Read-only library file condition.",
  counts: {
    imported: 0,
    healthy: 0,
    unknown: 0,
    corrupt: 0,
    orphaned: 0,
    pending: 0,
    missing: null,
  },
  libraries: [],
  unconfigured_library_names: [],
  volumes: [],
};

const storageFiles = {
  items: [],
  total: 0,
  offset: 0,
  limit: 50,
};

const schedulerSnap = {
  generated_at: "2026-01-15T12:00:00Z",
  tasks: [],
  queue_background: null,
  queue_interactive: null,
  schedules_loaded: false,
};

const emptyDatabase = {
  generated_at: "2026-01-15T12:00:00Z",
  host: "db.internal",
  port: 5433,
  name: "mira_prod",
  user: "mira",
  server_version: "17.4",
  size_bytes: 4096,
  max_connections: 100,
  started_at: null,
  connections: [],
  pools: [],
  largest_tables: [],
};

const populatedDatabase = {
  ...emptyDatabase,
  started_at: "2026-01-15T12:00:00Z",
  connections: [{ state: "active", count: 3 }],
  largest_tables: [
    {
      name: "episode_file",
      total_bytes: TABLE_TOTAL_BYTES,
      table_bytes: 786_432,
      index_bytes: 262_144,
      estimated_rows: 42,
    },
  ],
  password: SECRET,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function ok(data: unknown) {
  return Promise.resolve({ data, error: undefined });
}

function fail(error: unknown = { detail: "unavailable" }) {
  return Promise.resolve({ data: undefined, error });
}

function callsTo(path: string) {
  return mocks.get.mock.calls.filter(([called]) => called === path);
}

function stubApi(
  handlers: Record<string, () => Promise<{ data?: unknown; error?: unknown }>> = {},
) {
  mocks.get.mockImplementation((path: string) => {
    if (handlers[path]) return handlers[path]();
    if (path === "/api/v1/diagnostics/storage") return ok(storageSummary);
    if (path === "/api/v1/diagnostics/storage/files") return ok(storageFiles);
    if (path === "/api/v1/diagnostics/scheduler") return ok(schedulerSnap);
    if (path === "/api/v1/diagnostics/database") return ok(emptyDatabase);
    return ok({});
  });
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <DiagnosticsPage />
    </QueryClientProvider>,
  );
}

function identityLabel() {
  return /mira@db\.internal:5433\/mira_prod/;
}

function tab(name: string) {
  return screen.getByRole("tab", { name });
}

beforeEach(() => {
  userState.is_superuser = true;
  nav.search = "";
  nav.replace.mockReset();
  mocks.get.mockReset();
  stubApi();
});

afterEach(() => {
  cleanup();
});

describe("DiagnosticsPage", () => {
  it("has tabs without a page title or subtitle", () => {
    renderPage();
    expect(screen.queryByRole("heading", { name: "Diagnostics" })).toBeNull();
    expect(
      screen.queryByText("Read-only storage, database, and scheduler snapshot for operators."),
    ).toBeNull();
    expect(tab("Storage")).toBeTruthy();
    expect(tab("Database")).toBeTruthy();
    expect(tab("Scheduled Tasks")).toBeTruthy();
  });

  it("denies non-superusers without a tab list", () => {
    userState.is_superuser = false;
    renderPage();
    expect(screen.getByText("Admin access required.")).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByRole("tab", { name: "Storage" })).toBeNull();
    expect(callsTo("/api/v1/diagnostics/database")).toHaveLength(0);
    expect(callsTo("/api/v1/diagnostics/storage")).toHaveLength(0);
  });

  it("defaults superusers to the Storage tab when tab is omitted", async () => {
    renderPage();
    expect(tab("Storage").getAttribute("aria-selected")).toBe("true");
    expect(tab("Database").getAttribute("aria-selected")).toBe("false");
    await waitFor(() => {
      expect(callsTo("/api/v1/diagnostics/storage").length).toBeGreaterThan(0);
    });
    expect(await screen.findByText(/Integrity audit off/)).toBeTruthy();
  });

  it("opens Database from ?tab=database", async () => {
    nav.search = "tab=database";
    renderPage();
    expect(tab("Database").getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText(identityLabel())).toBeTruthy();
  });

  it("opens Scheduled Tasks from ?tab=scheduler", async () => {
    nav.search = "tab=scheduler";
    renderPage();
    expect(tab("Scheduled Tasks").getAttribute("aria-selected")).toBe("true");
    expect(await screen.findByText(/Cron-scheduled background work/)).toBeTruthy();
  });

  it("does not fetch database on Storage; storage still fetches without enabled", async () => {
    renderPage();
    await waitFor(() => {
      expect(callsTo("/api/v1/diagnostics/storage").length).toBeGreaterThan(0);
    });
    expect(callsTo("/api/v1/diagnostics/database")).toHaveLength(0);
  });

  it("shows database loading copy while the snapshot is in flight", async () => {
    nav.search = "tab=database";
    const pending = deferred<{ data?: unknown; error?: unknown }>();
    stubApi({
      "/api/v1/diagnostics/database": () => pending.promise,
    });
    renderPage();
    expect(await screen.findByText("Loading database…")).toBeTruthy();
    pending.resolve({ data: emptyDatabase, error: undefined });
    await waitFor(() => {
      expect(screen.queryByText("Loading database…")).toBeNull();
    });
  });

  it("shows DIAGNOSTICS_ERROR_MESSAGE and Retry refetches the database", async () => {
    nav.search = "tab=database";
    stubApi({
      "/api/v1/diagnostics/database": () => fail(),
    });
    renderPage();
    expect(await screen.findByText(DIAGNOSTICS_ERROR_MESSAGE)).toBeTruthy();
    expect(callsTo("/api/v1/diagnostics/database")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => {
      expect(callsTo("/api/v1/diagnostics/database")).toHaveLength(2);
    });
  });

  it("shows an empty tables state", async () => {
    nav.search = "tab=database";
    renderPage();
    expect(await screen.findByText("No table sizes")).toBeTruthy();
  });

  it("renders populated identity and table bytes without credentials", async () => {
    nav.search = "tab=database";
    stubApi({
      "/api/v1/diagnostics/database": () => ok(populatedDatabase),
    });
    renderPage();
    expect(await screen.findByText(identityLabel())).toBeTruthy();
    expect(screen.getByText("episode_file")).toBeTruthy();
    expect(screen.getByText(formatBytes(TABLE_TOTAL_BYTES))).toBeTruthy();
    expect(screen.queryByText(SECRET)).toBeNull();
    expect(screen.queryByText(/password/i)).toBeNull();
  });
});

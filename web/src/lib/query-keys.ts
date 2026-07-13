// Central query-key factories for @tanstack/react-query.
//
// Why a factory: ad-hoc string-array keys are easy to typo and impossible to
// refactor safely. Co-locating them here lets us write
// `qk.torrents.detail(id)` and get a typed `readonly ["torrents", string]`
// back, while `qk.torrents.all` continues to work as a broad-invalidation
// prefix (TanStack matches by prefix).
//
// Migration policy: existing call sites that already use hard-coded arrays
// are left untouched to keep the diff small. New code (and pages touched in
// the round-2 frontend refactor) should use this factory.

export const qk = {
  torrents: {
    all: ["torrents"] as const,
    list: (page?: number, pageSize?: number) =>
      page !== undefined
        ? (["torrents", "list", page, pageSize] as const)
        : (["torrents", "list"] as const),
    detail: (id: string) => ["torrents", id] as const,
  },
  shows: {
    all: ["shows"] as const,
    list: () => ["shows", "list"] as const,
    detail: (id: string) => ["shows", id] as const,
  },
  movies: {
    all: ["movies"] as const,
    list: () => ["movies", "list"] as const,
    detail: (id: string) => ["movies", id] as const,
  },
  imports: {
    all: ["imports"] as const,
    list: (tab?: string, page?: number, pageSize?: number) =>
      tab !== undefined
        ? (["imports", "list", tab, page, pageSize] as const)
        : (["imports", "list"] as const),
    counts: () => ["imports", "counts"] as const,
    scan: () => ["imports", "scan"] as const,
    // Integrity-audit mismatches. Namespaced under "imports" so `refreshAll`
    // (which invalidates `imports.all`) also refreshes corruption rows.
    integrity: () => ["imports", "integrity", "mismatches"] as const,
  },
} as const;

import { afterEach, describe, expect, it, vi } from "vitest";

import { loggingMiddleware } from "@/lib/api/middlewares";

type OnErrorArgs = Parameters<NonNullable<typeof loggingMiddleware.onError>>[0];

/**
 * `onError` only reads `request.url` and `request.signal`, so a plain object is
 * a faithful stand-in — no `fetch` environment needed.
 */
function callOnError(error: unknown, aborted: boolean) {
  const options = {
    request: { url: "https://example.test/api/v1/auth/metadata", signal: { aborted } },
    error,
  } as unknown as OnErrorArgs;
  return loggingMiddleware.onError!(options);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loggingMiddleware.onError", () => {
  it("passes an aborted request's error through untouched and stays quiet", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const abort = new DOMException("signal is aborted without reason", "AbortError");

    expect(await callOnError(abort, true)).toBe(abort);
    expect(spy).not.toHaveBeenCalled();
  });

  it("passes an AbortError through even if the signal already reset", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const abort = new DOMException("signal is aborted without reason", "AbortError");

    expect(await callOnError(abort, false)).toBe(abort);
    expect(spy).not.toHaveBeenCalled();
  });

  it("wraps and logs a real transport failure", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const cause = new TypeError("Failed to fetch");

    const result = await callOnError(cause, false);

    expect(result).toBeInstanceOf(Error);
    expect((result as Error).cause).toBe(cause);
    expect((result as Error).message).toContain("Failed to fetch");
    expect(spy).toHaveBeenCalledOnce();
  });
});

type OnResponseArgs = Parameters<NonNullable<typeof loggingMiddleware.onResponse>>[0];

function callOnResponse(status: number) {
  const options = {
    request: { url: "https://example.test/api/v1/torrents/download" },
    response: { ok: status >= 200 && status < 300, status },
  } as unknown as OnResponseArgs;
  return loggingMiddleware.onResponse!(options);
}

describe("loggingMiddleware.onResponse", () => {
  it("does not console.error a 409 conflict the UI is expected to handle", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});

    await callOnResponse(409);

    expect(errorSpy).not.toHaveBeenCalled();
    expect(logSpy).toHaveBeenCalled();
  });

  it("console.errors unexpected 5xx failures", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    await callOnResponse(500);

    expect(errorSpy).toHaveBeenCalledOnce();
  });
});

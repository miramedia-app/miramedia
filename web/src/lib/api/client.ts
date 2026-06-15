import createClient from "openapi-fetch";
import type { paths } from "./api";
import { autoLogoutMiddleware, loggingMiddleware } from "./middlewares";

// `openapi-fetch.use(...)` appends middleware each call and is NOT idempotent.
// Under Next.js HMR this module may be re-evaluated; cache both the client
// and a registration flag on `globalThis` so middleware stacks don't grow.
const CLIENT_KEY = Symbol.for("mm.api.client");
const REGISTERED_KEY = Symbol.for("mm.api.middlewareRegistered");
type ClientHolder = {
  [CLIENT_KEY]?: ReturnType<typeof createClient<paths>>;
  [REGISTERED_KEY]?: boolean;
};
const holder = globalThis as unknown as ClientHolder;

const apiClient =
  holder[CLIENT_KEY] ??
  createClient<paths>({
    baseUrl: process.env.NEXT_PUBLIC_API_URL || "",
    credentials: "include",
  });
holder[CLIENT_KEY] = apiClient;

if (!holder[REGISTERED_KEY]) {
  apiClient.use(loggingMiddleware, autoLogoutMiddleware);
  holder[REGISTERED_KEY] = true;
}

export default apiClient;

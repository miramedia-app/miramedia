"use client";

import * as React from "react";

export type AnyObj = Record<string, unknown>;

export type SetPath = (path: string[], value: unknown) => void;

// Wraps a row shape with a client-only synthetic id used for stable React
// keys on reorderable/deletable lists. Never sent to the API — stripped in
// the save/serialize path (see page.tsx saveAllSettings / stableStringify).
export type Keyed<T> = T & { _key: string };

export type OverrideCtxValue = {
  isOverridden: (section: string, ...path: string[]) => boolean;
  defaults: AnyObj;
  resetField: (path: string[]) => void | Promise<void>;
};

export const OverrideCtx = React.createContext<OverrideCtxValue | null>(null);

export function formatDefault(value: unknown): string {
  if (value === undefined) return "(unset)";
  if (value === null) return "(none)";
  if (typeof value === "string") return value === "" ? "(empty)" : value;
  if (Array.isArray(value)) return value.length === 0 ? "(empty list)" : JSON.stringify(value);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function getAt(obj: AnyObj | undefined, path: string[]): unknown {
  let cur: unknown = obj;
  for (const k of path) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as AnyObj)[k];
  }
  return cur;
}

export function csvToArray(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

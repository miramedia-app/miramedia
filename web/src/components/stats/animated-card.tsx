"use client";

import * as React from "react";
import { StatCard } from "./card";

const DURATION_MS = 2000;

function easeInOutSine(t: number): number {
  return -(Math.cos(Math.PI * t) - 1) / 2;
}

function format(value: number): string {
  return Math.floor(value).toString().padStart(3, "0");
}

export function AnimatedCard({
  title,
  footer,
  number,
}: {
  title: string;
  footer: string;
  number: number;
}) {
  const ref = React.useRef<HTMLSpanElement>(null);

  React.useEffect(() => {
    if (!ref.current) return;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / DURATION_MS);
      const eased = easeInOutSine(t);
      if (ref.current) ref.current.textContent = format(eased * number);
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [number]);

  return (
    <StatCard title={title} footer={footer}>
      <span ref={ref}>{format(number ?? 0)}</span>
    </StatCard>
  );
}

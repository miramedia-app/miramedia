"use client";

import * as React from "react";
import { Progress } from "@/components/ui/progress";

// Capped tick: easing curve that approaches but never reaches 100, paced at
// 30ms so the bar advances smoothly without re-rendering Progress at 1kHz.
export function LoadingBar() {
  const [value, setValue] = React.useState(8);
  React.useEffect(() => {
    const id = setInterval(() => {
      setValue((v) => (v >= 95 ? v : v + Math.max(0.5, (95 - v) * 0.04)));
    }, 30);
    return () => clearInterval(id);
  }, []);
  return <Progress value={value} />;
}

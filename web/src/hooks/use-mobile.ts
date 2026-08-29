import * as React from "react";

/**
 * "Mobile" layout mode: viewport narrower than Tailwind `lg` (1024px) OR a
 * coarse pointer (touch) device at any width, so a phone in landscape still
 * gets the mobile layout. Tailwind counterpart: `max-lg:` / `coarse:`.
 */
const MOBILE_BREAKPOINT = 1024;

export function useIsMobile() {
  const [isMobile, setIsMobile] = React.useState<boolean | undefined>(undefined);

  React.useEffect(() => {
    const widthMql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
    const coarseMql = window.matchMedia("(pointer: coarse)");
    const onChange = () => {
      setIsMobile(window.innerWidth < MOBILE_BREAKPOINT || coarseMql.matches);
    };
    widthMql.addEventListener("change", onChange);
    coarseMql.addEventListener("change", onChange);
    onChange();
    return () => {
      widthMql.removeEventListener("change", onChange);
      coarseMql.removeEventListener("change", onChange);
    };
  }, []);

  return !!isMobile;
}

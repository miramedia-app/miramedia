"use client";

import * as React from "react";

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Drawer,
  DrawerClose,
  DrawerContent,
  DrawerDescription,
  DrawerFooter,
  DrawerHeader,
  DrawerTitle,
  DrawerTrigger,
} from "@/components/ui/drawer";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";

/**
 * Dialog on desktop, bottom-sheet Drawer (vaul, swipe-to-dismiss) on mobile.
 * Same API surface as `ui/dialog`; the mode is decided by `useIsMobile()`
 * (width < lg OR coarse pointer). Props specific to one primitive are not
 * forwarded — keep usage to the shared subset (`open`, `onOpenChange`,
 * `className`, `children`).
 */

type RootProps = {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children?: React.ReactNode;
};

type DivProps = React.ComponentProps<"div">;

interface ChildProps {
  className?: string;
  children?: React.ReactNode;
}

const ResponsiveDialogContext = React.createContext(false);

function useResponsiveDialogMobile() {
  return React.useContext(ResponsiveDialogContext);
}

function ResponsiveDialog({ children, ...props }: RootProps) {
  const isMobile = useIsMobile();
  const Root = isMobile ? Drawer : Dialog;
  return (
    <ResponsiveDialogContext.Provider value={isMobile}>
      <Root {...props}>{children}</Root>
    </ResponsiveDialogContext.Provider>
  );
}

type TriggerLikeProps = {
  className?: string;
  children?: React.ReactNode;
  /** Base UI style render element (e.g. `<Button />`); mapped to `asChild` on mobile. */
  render?: React.ReactElement;
  disabled?: boolean;
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
};

function asChildElement(render: React.ReactElement, children: React.ReactNode) {
  return React.cloneElement(
    render as React.ReactElement<{ children?: React.ReactNode }>,
    {},
    children,
  );
}

function ResponsiveDialogTrigger({ render, children, ...props }: TriggerLikeProps) {
  const isMobile = useResponsiveDialogMobile();
  if (isMobile) {
    return render ? (
      <DrawerTrigger asChild {...props}>
        {asChildElement(render, children)}
      </DrawerTrigger>
    ) : (
      <DrawerTrigger {...props}>{children}</DrawerTrigger>
    );
  }
  return (
    <DialogTrigger render={render} {...props}>
      {children}
    </DialogTrigger>
  );
}

function ResponsiveDialogClose({ render, children, ...props }: TriggerLikeProps) {
  const isMobile = useResponsiveDialogMobile();
  if (isMobile) {
    return render ? (
      <DrawerClose asChild {...props}>
        {asChildElement(render, children)}
      </DrawerClose>
    ) : (
      <DrawerClose {...props}>{children}</DrawerClose>
    );
  }
  return (
    <DialogClose render={render} {...props}>
      {children}
    </DialogClose>
  );
}

function ResponsiveDialogContent({
  className,
  children,
  showCloseButton,
}: ChildProps & { showCloseButton?: boolean }) {
  const isMobile = useResponsiveDialogMobile();
  if (isMobile) {
    return (
      <DrawerContent
        className={cn("pb-safe-b data-[vaul-drawer-direction=bottom]:max-h-[92dvh]", className)}
      >
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">{children}</div>
      </DrawerContent>
    );
  }
  return (
    <DialogContent className={className} showCloseButton={showCloseButton}>
      {children}
    </DialogContent>
  );
}

function ResponsiveDialogHeader(props: DivProps) {
  const isMobile = useResponsiveDialogMobile();
  if (isMobile) {
    return <DrawerHeader {...props} className={cn("p-0 text-left", props.className)} />;
  }
  return <DialogHeader {...props} />;
}

function ResponsiveDialogFooter(props: DivProps & { showCloseButton?: boolean }) {
  const isMobile = useResponsiveDialogMobile();
  if (isMobile) {
    const { showCloseButton: _s, ...rest } = props;
    return <DrawerFooter {...rest} className={cn("p-0", rest.className)} />;
  }
  return <DialogFooter {...props} />;
}

function ResponsiveDialogTitle(props: ChildProps) {
  const isMobile = useResponsiveDialogMobile();
  return isMobile ? <DrawerTitle {...props} /> : <DialogTitle {...props} />;
}

function ResponsiveDialogDescription(props: ChildProps) {
  const isMobile = useResponsiveDialogMobile();
  return isMobile ? <DrawerDescription {...props} /> : <DialogDescription {...props} />;
}

export {
  ResponsiveDialog,
  ResponsiveDialogClose,
  ResponsiveDialogContent,
  ResponsiveDialogDescription,
  ResponsiveDialogFooter,
  ResponsiveDialogHeader,
  ResponsiveDialogTitle,
  ResponsiveDialogTrigger,
};

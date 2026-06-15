"use client";

import * as React from "react";
import { X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Props = {
  value: string | null | undefined;
  onValueChange?: (value: string | null) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
  type?: "text" | "url" | "email";
};

export function NullableInput({
  value,
  onValueChange,
  placeholder = "Not set",
  className,
  disabled = false,
  type = "text",
}: Props) {
  const hasValue = value !== null && value !== undefined && value !== "";
  return (
    <div className={cn("relative", className)}>
      <Input
        type={type}
        value={value ?? ""}
        onChange={(e) => onValueChange?.(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className={hasValue ? "pr-9" : undefined}
      />
      {hasValue && !disabled && (
        <button
          type="button"
          className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          aria-label="Clear (revert to default)"
          title="Clear (revert to default)"
          onClick={() => onValueChange?.(null)}
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

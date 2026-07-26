"use client";

import * as React from "react";
import { Eye, EyeOff } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type Props = {
  id?: string;
  value: string | null | undefined;
  onValueChange?: (value: string) => void;
  placeholder?: string;
  className?: string;
  disabled?: boolean;
};

export function SecretInput({
  id,
  value,
  onValueChange,
  placeholder = "Not set",
  className,
  disabled = false,
}: Props) {
  const [revealed, setRevealed] = React.useState(false);
  return (
    <div className={cn("relative", className)}>
      <Input
        id={id}
        type={revealed ? "text" : "password"}
        value={value ?? ""}
        onChange={(e) => onValueChange?.(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="pr-9"
      />
      <button
        type="button"
        className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        aria-label={revealed ? "Hide value" : "Reveal value"}
        title={revealed ? "Hide" : "Reveal"}
        onClick={() => setRevealed((v) => !v)}
      >
        {revealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

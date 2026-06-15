"use client";

import * as React from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Combobox,
  ComboboxChip,
  ComboboxChips,
  ComboboxChipsInput,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxItem,
  ComboboxList,
  ComboboxValue,
} from "@/components/ui/combobox";
import { Label } from "@/components/ui/label";

export type PreferenceMode = "default" | "any" | "specific";

type Props = {
  label: string;
  mode: PreferenceMode;
  selected: string[];
  options: string[];
  onChange: (mode: PreferenceMode, selected: string[]) => void;
  description?: string;
};

const MODE_LABELS: Record<PreferenceMode, string> = {
  default: "Use global default",
  any: "Any",
  specific: "Specific",
};

export function PreferenceMultiSelect({
  label,
  mode,
  selected,
  options,
  onChange,
  description,
}: Props) {
  const handleModeChange = React.useCallback(
    (next: string) => {
      const m = next as PreferenceMode;
      if (m === "specific") {
        // Entering specific mode with no selection yet → keep selection empty;
        // user must pick at least one. We still persist [] here would mean Any,
        // so only persist once they actually pick.
        onChange(m, selected);
      } else {
        onChange(m, []);
      }
    },
    [onChange, selected],
  );

  return (
    <div className="flex flex-col gap-2">
      <Label>{label}</Label>
      <Select value={mode} onValueChange={handleModeChange}>
        <SelectTrigger className="w-full">
          <SelectValue>{(v) => MODE_LABELS[(v as PreferenceMode) ?? "default"]}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="default">{MODE_LABELS.default}</SelectItem>
          <SelectItem value="any">{MODE_LABELS.any}</SelectItem>
          <SelectItem value="specific">{MODE_LABELS.specific}</SelectItem>
        </SelectContent>
      </Select>

      {mode === "specific" && (
        <Combobox
          items={options}
          multiple
          value={selected}
          onValueChange={(next) => onChange("specific", next as string[])}
          itemToStringLabel={(name: string) => name}
        >
          <ComboboxChips>
            <ComboboxValue>
              {(sel: string[]) => (
                <>
                  {sel.map((name) => (
                    <ComboboxChip key={name}>{name}</ComboboxChip>
                  ))}
                  <ComboboxChipsInput placeholder="Add an option" />
                </>
              )}
            </ComboboxValue>
          </ComboboxChips>
          <ComboboxContent>
            <ComboboxEmpty>No options found.</ComboboxEmpty>
            <ComboboxList>
              {(name: string) => (
                <ComboboxItem key={name} value={name}>
                  {name}
                </ComboboxItem>
              )}
            </ComboboxList>
          </ComboboxContent>
        </Combobox>
      )}

      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  );
}

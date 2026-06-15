"use client";

import * as React from "react";
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
import { LANGUAGES, getLanguage, getLanguageLabel } from "@/lib/languages";

type Props = {
  value: string[];
  onChange: (codes: string[]) => void;
  placeholder?: string;
  id?: string;
  disabled?: boolean;
};

const ALL_CODES: string[] = LANGUAGES.map((l) => l.code);

export function LanguageMultiCombobox({
  value,
  onChange,
  placeholder = "Add a language",
  id,
  disabled,
}: Props) {
  const normalized = React.useMemo(() => value.map((c) => c.toLowerCase()), [value]);

  return (
    <Combobox
      items={ALL_CODES}
      multiple
      value={normalized}
      onValueChange={(next) => onChange(next as string[])}
      itemToStringLabel={(code: string) => {
        const lang = getLanguage(code);
        if (!lang) return code;
        return lang.native ? `${lang.name} (${lang.native}) — ${code}` : `${lang.name} — ${code}`;
      }}
      disabled={disabled}
    >
      <ComboboxChips>
        <ComboboxValue>
          {(selected: string[]) => (
            <>
              {selected.map((code) => (
                <ComboboxChip key={code}>
                  {getLanguage(code)?.name ?? code}
                  <span className="ml-1 text-muted-foreground uppercase">{code}</span>
                </ComboboxChip>
              ))}
              <ComboboxChipsInput id={id} placeholder={placeholder} />
            </>
          )}
        </ComboboxValue>
      </ComboboxChips>
      <ComboboxContent>
        <ComboboxEmpty>No languages found.</ComboboxEmpty>
        <ComboboxList>
          {(code: string) => (
            <ComboboxItem key={code} value={code}>
              <span className="flex-1">{getLanguageLabel(code)}</span>
              <span className="text-xs text-muted-foreground uppercase">{code}</span>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}

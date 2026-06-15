export type Language = {
  code: string;
  name: string;
  native?: string;
};

export const LANGUAGES: ReadonlyArray<Language> = [
  { code: "ar", name: "Arabic", native: "العربية" },
  { code: "bg", name: "Bulgarian", native: "Български" },
  { code: "bn", name: "Bengali", native: "বাংলা" },
  { code: "ca", name: "Catalan", native: "Català" },
  { code: "cs", name: "Czech", native: "Čeština" },
  { code: "da", name: "Danish", native: "Dansk" },
  { code: "de", name: "German", native: "Deutsch" },
  { code: "el", name: "Greek", native: "Ελληνικά" },
  { code: "en", name: "English" },
  { code: "es", name: "Spanish", native: "Español" },
  { code: "et", name: "Estonian", native: "Eesti" },
  { code: "fa", name: "Persian", native: "فارسی" },
  { code: "fi", name: "Finnish", native: "Suomi" },
  { code: "fr", name: "French", native: "Français" },
  { code: "he", name: "Hebrew", native: "עברית" },
  { code: "hi", name: "Hindi", native: "हिन्दी" },
  { code: "hr", name: "Croatian", native: "Hrvatski" },
  { code: "hu", name: "Hungarian", native: "Magyar" },
  { code: "id", name: "Indonesian", native: "Bahasa Indonesia" },
  { code: "is", name: "Icelandic", native: "Íslenska" },
  { code: "it", name: "Italian", native: "Italiano" },
  { code: "ja", name: "Japanese", native: "日本語" },
  { code: "ko", name: "Korean", native: "한국어" },
  { code: "lt", name: "Lithuanian", native: "Lietuvių" },
  { code: "lv", name: "Latvian", native: "Latviešu" },
  { code: "ms", name: "Malay", native: "Bahasa Melayu" },
  { code: "nb", name: "Norwegian Bokmål", native: "Norsk Bokmål" },
  { code: "nl", name: "Dutch", native: "Nederlands" },
  { code: "no", name: "Norwegian", native: "Norsk" },
  { code: "pl", name: "Polish", native: "Polski" },
  { code: "pt", name: "Portuguese", native: "Português" },
  { code: "ro", name: "Romanian", native: "Română" },
  { code: "ru", name: "Russian", native: "Русский" },
  { code: "sk", name: "Slovak", native: "Slovenčina" },
  { code: "sl", name: "Slovenian", native: "Slovenščina" },
  { code: "sr", name: "Serbian", native: "Српски" },
  { code: "sv", name: "Swedish", native: "Svenska" },
  { code: "ta", name: "Tamil", native: "தமிழ்" },
  { code: "te", name: "Telugu", native: "తెలుగు" },
  { code: "th", name: "Thai", native: "ไทย" },
  { code: "tl", name: "Tagalog" },
  { code: "tr", name: "Turkish", native: "Türkçe" },
  { code: "uk", name: "Ukrainian", native: "Українська" },
  { code: "vi", name: "Vietnamese", native: "Tiếng Việt" },
  { code: "zh", name: "Chinese", native: "中文" },
];

const LANGUAGE_MAP: Map<string, Language> = new Map(LANGUAGES.map((l) => [l.code, l]));
const LANGUAGE_NAME_MAP: Map<string, Language> = new Map(
  LANGUAGES.map((l) => [l.name.toLowerCase(), l]),
);

export function getLanguage(code: string): Language | undefined {
  const v = code.toLowerCase();
  // Accept ISO 639-1 codes ("en") or English names ("english").
  return LANGUAGE_MAP.get(v) ?? LANGUAGE_NAME_MAP.get(v);
}

/** Resolve a code or name to its display name; falls back to the input. */
export function languageName(value: string): string {
  return getLanguage(value)?.name ?? value;
}

export function getLanguageLabel(code: string): string {
  const lang = getLanguage(code);
  if (!lang) return code;
  return lang.native ? `${lang.name} (${lang.native})` : lang.name;
}

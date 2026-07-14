"use client";

import { Plus, Trash2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { ScoringPreview } from "@/components/system/scoring-preview";
import { csvToArray, newRowKey, type AnyObj, type Keyed, type SetPath } from "../_shared";

type Opt = {
  name?: string;
  keywords?: string[];
  score_modifier?: number;
  enabled?: boolean;
};

type Rule = {
  name?: string;
  keywords?: string[];
  flags?: string[];
  score_modifier?: number;
  enabled?: boolean;
};

type Ruleset = {
  name?: string;
  libraries?: string[];
  rule_names?: string[];
};

const OPTION_SECTIONS = [
  {
    key: "quality_options",
    title: "Quality Rules",
    desc: "Allowed qualities. List order sets dropdown display order and tie-breaks when a title matches multiple options. The score adds to a matched result's rank. Disabling an option excludes its torrents from matching AND removes it from per-show/movie dropdowns. At least one must be enabled.",
    placeholder: "2160p, 4k, uhd, ...",
    addLabel: "Add Quality Option",
  },
  {
    key: "codec_options",
    title: "Codec Rules",
    desc: "Allowed codecs. Same semantics as quality options. At least one must be enabled.",
    placeholder: "h265, hevc, x265, ...",
    addLabel: "Add Codec Option",
  },
] as const;

const RULE_SECTIONS = [
  {
    key: "title_scoring_rules",
    title: "Title Rules",
    desc: "Match keywords in torrent titles to adjust scores. Negative scores reject results.",
    keywordsField: "keywords" as const,
    placeholder: "keyword1, keyword2, ...",
    addLabel: "Add Title Rule",
  },
  {
    key: "indexer_flag_scoring_rules",
    title: "Flag Rules",
    desc: "Match indexer flags (freeleech, nuked, etc.) to adjust scores.",
    keywordsField: "flags" as const,
    placeholder: "freeleech, nuked, ...",
    addLabel: "Add Flag Rule",
  },
] as const;

export function ScoresTab({
  indexers,
  setIndexersPath,
}: {
  indexers: AnyObj;
  setIndexersPath: SetPath;
}) {
  const ind = indexers;
  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Score Settings</CardTitle>
              <CardDescription>
                Global filters and bonuses applied to all torrent results.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Minimum Seeders</Label>
                  <Input
                    type="number"
                    value={Number(ind.minimum_seeders ?? "") || ""}
                    onChange={(e) =>
                      setIndexersPath(["minimum_seeders"], Number(e.target.value) || 0)
                    }
                    placeholder="0"
                  />
                  <p className="text-xs text-muted-foreground">
                    Reject torrents with fewer seeders (0 = no minimum)
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>Maximum Seeders</Label>
                  <Input
                    type="number"
                    value={Number(ind.maximum_seeders ?? "") || ""}
                    onChange={(e) =>
                      setIndexersPath(["maximum_seeders"], Number(e.target.value) || 0)
                    }
                    placeholder="0"
                  />
                  <p className="text-xs text-muted-foreground">
                    Reject torrents with more seeders (0 = no maximum)
                  </p>
                </div>
              </div>
              <Separator />
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Preferred Languages</Label>
                  <Input
                    value={
                      Array.isArray(ind.preferred_languages)
                        ? (ind.preferred_languages as string[]).join(", ")
                        : ""
                    }
                    onChange={(e) =>
                      setIndexersPath(["preferred_languages"], csvToArray(e.target.value))
                    }
                    placeholder="english, eng, ..."
                  />
                  <p className="text-xs text-muted-foreground">
                    Boost score (+100) for titles matching these keywords
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>Rejected Languages</Label>
                  <Input
                    value={
                      Array.isArray(ind.rejected_languages)
                        ? (ind.rejected_languages as string[]).join(", ")
                        : ""
                    }
                    onChange={(e) =>
                      setIndexersPath(["rejected_languages"], csvToArray(e.target.value))
                    }
                    placeholder="french, german, ..."
                  />
                  <p className="text-xs text-muted-foreground">
                    Heavily penalize (-10000) titles matching these keywords
                  </p>
                </div>
              </div>
              <Separator />
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Min Size (MB)</Label>
                  <Input
                    type="number"
                    value={Number(ind.min_size_mb ?? "") || ""}
                    onChange={(e) => setIndexersPath(["min_size_mb"], Number(e.target.value) || 0)}
                    placeholder="0"
                  />
                  <p className="text-xs text-muted-foreground">
                    Reject results smaller than this. 0 = no minimum.
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>Max Size (MB)</Label>
                  <Input
                    type="number"
                    value={Number(ind.max_size_mb ?? "") || ""}
                    onChange={(e) => setIndexersPath(["max_size_mb"], Number(e.target.value) || 0)}
                    placeholder="0"
                  />
                  <p className="text-xs text-muted-foreground">
                    Reject results larger than this. 0 = no maximum.
                  </p>
                </div>
              </div>
              <Separator />
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Recency Bonus</Label>
                  <Input
                    type="number"
                    value={Number(ind.recency_bonus ?? "") || ""}
                    onChange={(e) =>
                      setIndexersPath(["recency_bonus"], Number(e.target.value) || 0)
                    }
                    placeholder="0"
                  />
                </div>
                <div className="space-y-2">
                  <Label>Recency Decay (days)</Label>
                  <Input
                    type="number"
                    value={Number(ind.recency_decay_days ?? "") || ""}
                    onChange={(e) =>
                      setIndexersPath(["recency_decay_days"], Number(e.target.value) || 0)
                    }
                    placeholder="30"
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          {OPTION_SECTIONS.map(({ key, title, desc, placeholder, addLabel }) => {
            const options = (ind[key] as Keyed<Opt>[]) ?? [];
            const enabledCount = options.filter((o) => o.enabled !== false).length;
            const move = (from: number, to: number) => {
              if (to < 0 || to >= options.length) return;
              const next = [...options];
              const [moved] = next.splice(from, 1);
              next.splice(to, 0, moved!);
              setIndexersPath([key], next);
            };
            return (
              <Card key={key}>
                <CardHeader>
                  <CardTitle>{title}</CardTitle>
                  <CardDescription>{desc}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {options.map((opt, i) => {
                    const isLastEnabled = opt.enabled !== false && enabledCount === 1;
                    return (
                      <div key={opt._key} className="flex flex-wrap items-center gap-2">
                        <div className="flex flex-col gap-0.5">
                          <Button
                            variant="ghost"
                            size="icon"
                            disabled={i === 0}
                            onClick={() => move(i, i - 1)}
                            title="Move up"
                            className="h-5 w-5"
                          >
                            ▲
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            disabled={i === options.length - 1}
                            onClick={() => move(i, i + 1)}
                            title="Move down"
                            className="h-5 w-5"
                          >
                            ▼
                          </Button>
                        </div>
                        <Input
                          value={opt.name ?? ""}
                          onChange={(e) => {
                            const next = [...options];
                            next[i] = { ...next[i], name: e.target.value };
                            setIndexersPath([key], next);
                          }}
                          placeholder="Option name"
                          className="max-w-[180px]"
                        />
                        <Input
                          value={Array.isArray(opt.keywords) ? opt.keywords.join(", ") : ""}
                          onChange={(e) => {
                            const next = [...options];
                            next[i] = { ...next[i], keywords: csvToArray(e.target.value) };
                            setIndexersPath([key], next);
                          }}
                          placeholder={placeholder}
                          className="min-w-[200px] flex-1"
                        />
                        <Input
                          type="number"
                          value={Number(opt.score_modifier ?? 0)}
                          onChange={(e) => {
                            const next = [...options];
                            next[i] = {
                              ...next[i],
                              score_modifier: Number(e.target.value) || 0,
                            };
                            setIndexersPath([key], next);
                          }}
                          placeholder="Score"
                          title="Score added to a matched result. Higher = preferred."
                          className="max-w-[100px]"
                        />
                        <div className="flex items-center gap-1">
                          <Switch
                            checked={opt.enabled !== false}
                            disabled={isLastEnabled}
                            onCheckedChange={(v) => {
                              const next = [...options];
                              next[i] = { ...next[i], enabled: v };
                              setIndexersPath([key], next);
                            }}
                          />
                          <Label className="text-xs">Enabled</Label>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() =>
                            setIndexersPath(
                              [key],
                              options.filter((_, j) => j !== i),
                            )
                          }
                          title="Delete"
                        >
                          <Trash2 className="h-4 w-4 text-muted-foreground" />
                        </Button>
                      </div>
                    );
                  })}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      setIndexersPath(
                        [key],
                        [
                          ...options,
                          {
                            _key: newRowKey(),
                            name: "",
                            keywords: [],
                            score_modifier: 0,
                            enabled: true,
                          },
                        ],
                      )
                    }
                  >
                    <Plus className="mr-1 h-4 w-4" />
                    {addLabel}
                  </Button>
                </CardContent>
              </Card>
            );
          })}

          {RULE_SECTIONS.map(({ key, title, desc, keywordsField, placeholder, addLabel }) => {
            const rules = (ind[key] as Keyed<Rule>[]) ?? [];
            return (
              <Card key={key}>
                <CardHeader>
                  <CardTitle>{title}</CardTitle>
                  <CardDescription>{desc}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {rules.map((rule, i) => (
                    <div key={rule._key} className="flex flex-wrap items-center gap-2">
                      <Input
                        value={rule.name ?? ""}
                        onChange={(e) => {
                          const next = [...rules];
                          next[i] = { ...next[i], name: e.target.value };
                          setIndexersPath([key], next);
                        }}
                        placeholder="Rule name"
                        className="max-w-[150px]"
                      />
                      <Input
                        value={
                          Array.isArray((rule as AnyObj)[keywordsField])
                            ? ((rule as AnyObj)[keywordsField] as string[]).join(", ")
                            : ""
                        }
                        onChange={(e) => {
                          const next = [...rules];
                          next[i] = {
                            ...next[i],
                            [keywordsField]: csvToArray(e.target.value),
                          };
                          setIndexersPath([key], next);
                        }}
                        placeholder={placeholder}
                        className="min-w-[200px] flex-1"
                      />
                      <Input
                        type="number"
                        value={Number(rule.score_modifier ?? 0)}
                        onChange={(e) => {
                          const next = [...rules];
                          next[i] = {
                            ...next[i],
                            score_modifier: Number(e.target.value) || 0,
                          };
                          setIndexersPath([key], next);
                        }}
                        className="max-w-[100px]"
                        placeholder="Score"
                      />
                      <div className="flex items-center gap-1">
                        <Switch
                          checked={rule.enabled !== false}
                          onCheckedChange={(v) => {
                            const next = [...rules];
                            next[i] = { ...next[i], enabled: v };
                            setIndexersPath([key], next);
                          }}
                        />
                        <Label className="text-xs">Enabled</Label>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() =>
                          setIndexersPath(
                            [key],
                            rules.filter((_, j) => j !== i),
                          )
                        }
                      >
                        <Trash2 className="h-4 w-4 text-muted-foreground" />
                      </Button>
                    </div>
                  ))}
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      setIndexersPath(
                        [key],
                        [
                          ...rules,
                          {
                            _key: newRowKey(),
                            name: "",
                            [keywordsField]: [],
                            score_modifier: 0,
                            enabled: true,
                          },
                        ],
                      )
                    }
                  >
                    <Plus className="mr-1 h-4 w-4" />
                    {addLabel}
                  </Button>
                </CardContent>
              </Card>
            );
          })}

          <Card>
            <CardHeader>
              <CardTitle>Score Rulesets</CardTitle>
              <CardDescription>
                Group rules and assign them to libraries. Use ALL_TV or ALL_MOVIES for broad
                targeting.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {((ind.scoring_rule_sets as Ruleset[]) ?? []).map((rs, i, all) => (
                <div key={i} className="space-y-2 rounded-md border p-3">
                  <div className="flex items-center gap-2">
                    <Input
                      value={rs.name ?? ""}
                      onChange={(e) => {
                        const next = [...all];
                        next[i] = { ...next[i], name: e.target.value };
                        setIndexersPath(["scoring_rule_sets"], next);
                      }}
                      placeholder="Ruleset name"
                      className="max-w-[200px]"
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() =>
                        setIndexersPath(
                          ["scoring_rule_sets"],
                          all.filter((_, j) => j !== i),
                        )
                      }
                    >
                      <Trash2 className="h-4 w-4 text-muted-foreground" />
                    </Button>
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">
                      Libraries (comma-separated: ALL_TV, ALL_MOVIES, or library names)
                    </Label>
                    <Input
                      value={Array.isArray(rs.libraries) ? rs.libraries.join(", ") : ""}
                      onChange={(e) => {
                        const next = [...all];
                        next[i] = {
                          ...next[i],
                          libraries: csvToArray(e.target.value),
                        };
                        setIndexersPath(["scoring_rule_sets"], next);
                      }}
                      placeholder="ALL_TV, ALL_MOVIES"
                    />
                  </div>
                  <div className="space-y-1">
                    <Label className="text-xs">
                      Rule Names (comma-separated, references rules defined above)
                    </Label>
                    <Input
                      value={Array.isArray(rs.rule_names) ? rs.rule_names.join(", ") : ""}
                      onChange={(e) => {
                        const next = [...all];
                        next[i] = {
                          ...next[i],
                          rule_names: csvToArray(e.target.value),
                        };
                        setIndexersPath(["scoring_rule_sets"], next);
                      }}
                      placeholder="prefer_h265, avoid_cam, reject_nuked"
                    />
                  </div>
                </div>
              ))}
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setIndexersPath(
                    ["scoring_rule_sets"],
                    [
                      ...((ind.scoring_rule_sets as Ruleset[]) ?? []),
                      { name: "", libraries: [], rule_names: [] },
                    ],
                  )
                }
              >
                <Plus className="mr-1 h-4 w-4" />
                Add Ruleset
              </Button>
            </CardContent>
          </Card>
          <ScoringPreview />
        </div>
      </section>
    </div>
  );
}

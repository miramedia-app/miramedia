"use client";

import * as React from "react";
import { LoaderCircle, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MediaGridSkeleton } from "@/components/media-grid-skeleton";
import { DashboardHeader } from "@/components/dashboard-header";
import { AddMediaSearch } from "@/components/add-media-search";
import { AddMediaCard } from "@/components/add-media-card";
import type { components } from "@/lib/api/api";

type SearchResult = components["schemas"]["MetaDataProviderSearchResult"];

export default function ShowsAddPage() {
  const [results, setResults] = React.useState<SearchResult[] | null>(null);
  const [isLoading, setIsLoading] = React.useState(false);
  const [hasMore, setHasMore] = React.useState(true);
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);

  return (
    <>
      <DashboardHeader
        crumbs={[
          { label: "Dashboard", href: "/dashboard" },
          { label: "Shows", href: "/dashboard/shows" },
          { label: "Add" },
        ]}
      />
      <main className="flex w-full flex-1 flex-col gap-4 p-4 pt-0">
        <AddMediaSearch
          mediaType="show"
          results={results}
          onResultsChange={setResults}
          isLoading={isLoading}
          onLoadingChange={setIsLoading}
          hasMore={hasMore}
          onHasMoreChange={setHasMore}
          errorMessage={errorMessage}
          onErrorMessageChange={setErrorMessage}
        />

        {errorMessage ? (
          <div className="relative w-full">
            <div className="grid w-full gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              <div className="pointer-events-none aspect-[2/3]" />
            </div>
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed text-center">
              <TriangleAlert className="h-8 w-8 text-muted-foreground" />
              <p className="max-w-xs text-sm text-muted-foreground">{errorMessage}</p>
              <Button variant="outline" onClick={() => location.reload()}>
                Retry
              </Button>
            </div>
          </div>
        ) : isLoading && !results ? (
          <MediaGridSkeleton />
        ) : results && results.length === 0 ? (
          <h3 className="mx-auto text-muted-foreground">No shows found.</h3>
        ) : results ? (
          <>
            <div className="grid w-full gap-4 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
              {results.map((dataItem) => (
                <AddMediaCard key={String(dataItem.external_id)} result={dataItem} isShow />
              ))}
            </div>
            {hasMore && isLoading && (
              <div className="flex w-full justify-center py-8">
                <LoaderCircle className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            )}
          </>
        ) : null}
      </main>
    </>
  );
}

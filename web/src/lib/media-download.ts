export type MediaStreamKind = "movie" | "show";

export function mediaStreamDownloadUrl({
  mediaType,
  mediaId,
  fileId,
  apiUrl = process.env.NEXT_PUBLIC_API_URL || "",
}: {
  mediaType: MediaStreamKind;
  mediaId: string;
  fileId: string;
  apiUrl?: string;
}): string {
  const endpoint = mediaType === "movie" ? "movies" : "episodes";
  return `${apiUrl}/api/v1/streams/${endpoint}/${mediaId}?file_id=${encodeURIComponent(fileId)}&download=true`;
}

export function importedFileRowActions({
  streaming,
  downloads,
  imported,
}: {
  streaming: boolean;
  downloads: boolean;
  imported: boolean;
}): { showPlayer: boolean; showDownload: boolean } {
  const showPlayer = imported && streaming;
  const showDownload = imported && downloads && !showPlayer;
  return { showPlayer, showDownload };
}

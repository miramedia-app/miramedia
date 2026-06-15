"use client";

import * as React from "react";
import { Download, LoaderCircle, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { MediaStreamSource, StreamingPlayer } from "@/lib/mediabunny";

type PlayerState = "idle" | "loading" | "playing" | "error";

export function VideoPlayerDialog({
  mediaType,
  mediaId,
  fileId,
  title,
  subtitleLanguages = [],
  buttonVariant = "outline",
  buttonSize = "sm",
}: {
  mediaType: "show" | "movie";
  mediaId: string;
  fileId: string;
  title: string;
  subtitleLanguages?: string[];
  buttonVariant?: "outline" | "ghost" | "default";
  buttonSize?: "sm" | "default" | "icon";
}) {
  const [open, setOpen] = React.useState(false);
  const [playerState, setPlayerState] = React.useState<PlayerState>("idle");
  const [errorMessage, setErrorMessage] = React.useState("");
  const [videoSrc, setVideoSrc] = React.useState<string | undefined>(undefined);
  const [subtitleSrcs, setSubtitleSrcs] = React.useState<{ lang: string; url: string }[]>([]);
  const videoRef = React.useRef<HTMLVideoElement | null>(null);
  const streamPlayerRef = React.useRef<StreamingPlayer | null>(null);
  const loadControllerRef = React.useRef<AbortController | null>(null);
  // True while the native <video> src is the HLS playlist (Safari warm-cache path).
  const usingHlsRef = React.useRef(false);

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
  const endpoint = mediaType === "movie" ? "movies" : "episodes";
  // Streaming endpoints identify the target file by its surrogate uuid.
  const streamUrl = `${apiUrl}/api/v1/streams/${endpoint}/${mediaId}?file_id=${encodeURIComponent(fileId)}`;
  const downloadUrl = `${streamUrl}&download=true`;

  function subtitleTrackUrl(language: string): string {
    return `${apiUrl}/api/v1/streams/subtitles/${endpoint}/${mediaId}/${language}?file_id=${encodeURIComponent(fileId)}`;
  }

  async function loadSubtitleBlobs(signal: AbortSignal) {
    const results: { lang: string; url: string }[] = [];
    for (const lang of subtitleLanguages) {
      try {
        const res = await fetch(subtitleTrackUrl(lang), {
          signal,
          credentials: "include",
        });
        if (res.ok) {
          const text = await res.text();
          const blob = new Blob([text], { type: "text/vtt" });
          results.push({ lang, url: URL.createObjectURL(blob) });
        }
      } catch {}
    }
    return results;
  }

  const cleanup = React.useCallback(() => {
    loadControllerRef.current?.abort();
    loadControllerRef.current = null;
    if (streamPlayerRef.current) {
      streamPlayerRef.current.dispose();
      streamPlayerRef.current = null;
    }
    usingHlsRef.current = false;
    setVideoSrc((prev) => {
      if (prev?.startsWith("blob:")) URL.revokeObjectURL(prev);
      return undefined;
    });
    setSubtitleSrcs((prev) => {
      for (const sub of prev) URL.revokeObjectURL(sub.url);
      return [];
    });
    setPlayerState("idle");
    setErrorMessage("");
  }, []);

  React.useEffect(() => () => cleanup(), [cleanup]);

  async function waitForVideoElement(signal: AbortSignal) {
    await new Promise<void>((resolve, reject) => {
      const check = () => {
        if (signal.aborted) {
          reject(new DOMException("Aborted", "AbortError"));
          return;
        }
        if (videoRef.current) {
          resolve();
          return;
        }
        requestAnimationFrame(check);
      };
      requestAnimationFrame(check);
    });
  }

  /** Mediabunny playback via UrlSource (range/HLS) or blob — no full-file download for URLs. */
  async function playWithMediabunny(source: MediaStreamSource, signal: AbortSignal) {
    const mb = await import("@/lib/mediabunny");
    if (!mb.hasWebCodecsSupport()) {
      setErrorMessage("This browser cannot play this file.");
      setPlayerState("error");
      return;
    }

    const probe = await mb.probeMedia(source);
    if (signal.aborted) return;

    const useNativeElement =
      source.type === "url" && !mb.isHlsPlaylistUrl(source.url) && !probe.needsConversion;

    if (useNativeElement) {
      setVideoSrc(source.url);
      setPlayerState("playing");
      return;
    }

    if (source.type === "blob" && !probe.needsConversion) {
      setVideoSrc(URL.createObjectURL(source.blob));
      setPlayerState("playing");
      return;
    }

    const streamPlayer = new mb.StreamingPlayer(source, probe.duration);
    streamPlayerRef.current = streamPlayer;
    setVideoSrc(undefined);
    setPlayerState("playing");
    await waitForVideoElement(signal);
    await streamPlayer.attach(videoRef.current!, 0);
  }

  function canPlayHlsNatively(): boolean {
    if (typeof navigator === "undefined") return false;
    const ua = navigator.userAgent;
    return /Safari/i.test(ua) && !/Chrome|Chromium|Android/i.test(ua);
  }

  async function loadAndPlay() {
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    const { signal } = controller;

    setPlayerState("loading");
    setSubtitleSrcs(await loadSubtitleBlobs(signal));
    if (signal.aborted) return;

    const qIndex = streamUrl.indexOf("?");
    const streamBase = qIndex >= 0 ? streamUrl.slice(0, qIndex) : streamUrl;
    const query = qIndex >= 0 ? streamUrl.slice(qIndex) : "";
    const probeUrl = `${streamBase}/probe${query}`;
    try {
      const probeRes = await fetch(probeUrl, { signal, credentials: "include" });
      if (probeRes.ok) {
        const probe = (await probeRes.json()) as {
          direct_play: boolean;
          hls_playlist_url?: string | null;
        };
        // Warm-cache HLS: the server already transcoded to H.264/AAC. Safari plays
        // the .m3u8 natively; other browsers remux it through Mediabunny (cheap — no
        // client-side decode/re-encode) instead of transcoding the raw file on the CPU.
        if (!probe.direct_play && probe.hls_playlist_url) {
          const hlsUrl = `${apiUrl}${probe.hls_playlist_url}`;
          if (canPlayHlsNatively()) {
            usingHlsRef.current = true;
            setVideoSrc(hlsUrl);
            setPlayerState("playing");
            return;
          }
          try {
            await playWithMediabunny({ type: "url", url: hlsUrl }, signal);
            return;
          } catch (err: unknown) {
            const e = err as { name?: string };
            if (e?.name === "AbortError") return;
            // HLS remux failed — fall through to raw direct stream + transcode.
          }
        }
      }
    } catch (err: unknown) {
      const e = err as { name?: string };
      if (e?.name === "AbortError") return;
      // Fall through to direct stream attempt.
    }

    usingHlsRef.current = false;
    setVideoSrc(streamUrl);
    setPlayerState("playing");
  }

  // Native <video> failed — transcode/remux via Mediabunny with HTTP range reads.
  async function fallbackToMediabunny() {
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    const { signal } = controller;

    usingHlsRef.current = false;
    try {
      setPlayerState("loading");
      await playWithMediabunny({ type: "url", url: streamUrl }, signal);
    } catch (err: unknown) {
      const e = err as { name?: string; message?: string };
      if (e?.name === "AbortError") return;
      console.error("Fallback playback error:", err);
      setErrorMessage(e?.message || "Playback failed");
      setPlayerState("error");
    }
  }

  function handleOpen(isOpen: boolean) {
    setOpen(isOpen);
    if (!isOpen) {
      cleanup();
      return;
    }
    requestAnimationFrame(() => requestAnimationFrame(() => loadAndPlay()));
  }

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogTrigger
        render={
          <Button
            variant={buttonVariant}
            size={buttonSize}
            className={buttonSize === "icon" ? "h-7 w-7 text-muted-foreground" : undefined}
          />
        }
      >
        <Play className="h-3.5 w-3.5" />
      </DialogTrigger>
      <DialogContent className="flex max-h-[90vh] w-[95vw] max-w-6xl flex-col sm:max-w-6xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="relative min-h-0 flex-1">
          {playerState === "loading" && (
            <div className="flex flex-col items-center justify-center gap-3 py-16">
              <LoaderCircle className="h-8 w-8 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">Loading media...</p>
            </div>
          )}
          {playerState === "error" && (
            <div className="flex flex-col items-center justify-center gap-4 py-12 text-center">
              <p className="text-muted-foreground">{errorMessage}</p>
              <a href={downloadUrl}>
                <Button variant="outline">
                  <Download className="mr-2 h-4 w-4" />
                  Download File
                </Button>
              </a>
            </div>
          )}
          {playerState === "playing" && (
            <video
              ref={videoRef}
              className="max-h-[70vh] w-full rounded-md bg-black"
              controls
              autoPlay
              crossOrigin="use-credentials"
              src={videoSrc}
              onError={() => {
                // Browser refused the native stream — likely MKV / AC3 / HEVC
                // without hardware support, or a failed HLS playlist load (Safari
                // warm-cache path). Fall back to mediabunny re-encode for both; if
                // that fails too, fallbackToMediabunny surfaces the error UI.
                if (videoSrc === streamUrl || usingHlsRef.current) {
                  void fallbackToMediabunny();
                }
              }}
            >
              <track kind="captions" />
              {subtitleSrcs.map((sub) => (
                <track
                  key={sub.lang}
                  kind="subtitles"
                  src={sub.url}
                  srcLang={sub.lang}
                  label={sub.lang.toUpperCase()}
                />
              ))}
            </video>
          )}
        </div>
        <DialogFooter>
          <a href={downloadUrl}>
            <Button variant="outline" size="sm">
              <Download className="mr-2 h-4 w-4" />
              Download
            </Button>
          </a>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

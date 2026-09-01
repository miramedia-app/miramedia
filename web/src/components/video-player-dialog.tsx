"use client";

import * as React from "react";
import { LoaderCircle, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DirectDownloadAction } from "@/components/direct-download-action";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useFeatures } from "@/components/providers/features-provider";
import { usePlaybackProgress } from "@/hooks/use-playback-progress";
import type { MediaStreamSource, StreamingPlayer } from "@/lib/mediabunny";
import { loadSubtitlesIfCurrent, runPlaybackLoad } from "@/lib/video-player-subtitles";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

type PlayerState = "idle" | "loading" | "playing" | "error";

function formatResumeClock(positionMs: number): string {
  const totalSec = Math.max(0, Math.floor(positionMs / 1000));
  const mm = Math.floor(totalSec / 60);
  const ss = totalSec % 60;
  return `${mm}:${ss.toString().padStart(2, "0")}`;
}

export function VideoPlayerDialog({
  mediaType,
  mediaId,
  fileId,
  title,
  subtitleLanguages = [],
  buttonVariant = "outline",
  buttonSize = "sm",
  buttonClassName,
  triggerLabel,
  resumeFromMs,
  trigger,
}: {
  mediaType: "show" | "movie";
  mediaId: string;
  fileId: string;
  title: string;
  subtitleLanguages?: string[];
  buttonVariant?: "outline" | "ghost" | "default";
  buttonSize?: "sm" | "default" | "icon";
  buttonClassName?: string;
  /** Visible label on the trigger button. Icon-only when omitted and size is icon. */
  triggerLabel?: string;
  /** When set (e.g. continue-watching), seek here and skip the resume prompt. */
  resumeFromMs?: number;
  /** Replaces the default play button. Use for poster/card triggers. */
  trigger?: React.ReactElement;
}) {
  const [open, setOpen] = React.useState(false);
  const [playerState, setPlayerState] = React.useState<PlayerState>("idle");
  const [errorMessage, setErrorMessage] = React.useState("");
  const [videoSrc, setVideoSrc] = React.useState<string | undefined>(undefined);
  const [subtitleSrcs, setSubtitleSrcs] = React.useState<{ lang: string; url: string }[]>([]);
  const [showResumePrompt, setShowResumePrompt] = React.useState(false);
  const videoRef = React.useRef<HTMLVideoElement | null>(null);
  const streamPlayerRef = React.useRef<StreamingPlayer | null>(null);
  const loadControllerRef = React.useRef<AbortController | null>(null);
  // True while the native <video> src is the HLS playlist (Safari warm-cache path).
  const usingHlsRef = React.useRef(false);
  const appliedResumeFromMsRef = React.useRef(false);

  const { downloads: downloadsEnabled } = useFeatures();
  const mediaKind = mediaType === "movie" ? "movie" : "episode";
  const { initialProgress, reporter } = usePlaybackProgress({
    fileId,
    mediaKind,
    enabled: open,
  });

  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "";
  const endpoint = mediaType === "movie" ? "movies" : "episodes";
  // Streaming endpoints identify the target file by its surrogate uuid.
  const streamUrl = `${apiUrl}/api/v1/streams/${endpoint}/${mediaId}?file_id=${encodeURIComponent(fileId)}`;

  const resumePromptPositionMs =
    resumeFromMs == null &&
    initialProgress &&
    !initialProgress.completed &&
    initialProgress.position_ms >= 5_000
      ? initialProgress.position_ms
      : null;

  React.useEffect(() => {
    if (playerState !== "playing") return;
    if (resumeFromMs != null) {
      setShowResumePrompt(false);
      return;
    }
    if (resumePromptPositionMs != null) {
      setShowResumePrompt(true);
    }
  }, [playerState, resumeFromMs, resumePromptPositionMs]);

  function subtitleTrackUrl(language: string): string {
    return `${apiUrl}/api/v1/streams/subtitles/${endpoint}/${mediaId}/${language}?file_id=${encodeURIComponent(fileId)}`;
  }

  /**
   * Kick off optional subtitle downloads concurrently, without blocking
   * playback. Results are installed only if `signal` is still the current load;
   * otherwise every created object URL is revoked so nothing leaks. Installed
   * URLs are owned by `subtitleSrcs` state and revoked in `cleanup`.
   */
  function startSubtitleLoad(signal: AbortSignal) {
    void loadSubtitlesIfCurrent({
      languages: subtitleLanguages,
      trackUrl: subtitleTrackUrl,
      signal,
      deps: {
        fetch: (input, init) => fetch(input, init),
        createObjectURL: (blob) => URL.createObjectURL(blob),
        revokeObjectURL: (url) => URL.revokeObjectURL(url),
      },
      isCurrent: () => loadControllerRef.current?.signal === signal,
      install: (tracks) =>
        setSubtitleSrcs((prev) => {
          for (const sub of prev) URL.revokeObjectURL(sub.url);
          return tracks;
        }),
    });
  }

  const cleanup = React.useCallback(() => {
    loadControllerRef.current?.abort();
    loadControllerRef.current = null;
    if (streamPlayerRef.current) {
      streamPlayerRef.current.dispose();
      streamPlayerRef.current = null;
    }
    usingHlsRef.current = false;
    appliedResumeFromMsRef.current = false;
    setVideoSrc((prev) => {
      if (prev?.startsWith("blob:")) URL.revokeObjectURL(prev);
      return undefined;
    });
    setSubtitleSrcs((prev) => {
      for (const sub of prev) URL.revokeObjectURL(sub.url);
      return [];
    });
    setShowResumePrompt(false);
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

  function applyResumeFromMsSeek(video: HTMLVideoElement) {
    if (resumeFromMs == null || appliedResumeFromMsRef.current) return;
    appliedResumeFromMsRef.current = true;
    video.currentTime = resumeFromMs / 1000;
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

    if (!mb.hasMediaSourceSupport()) {
      setErrorMessage("This browser cannot play this file.");
      setPlayerState("error");
      return;
    }

    const streamPlayer = new mb.StreamingPlayer(source, probe.duration);
    streamPlayerRef.current = streamPlayer;
    setVideoSrc(undefined);
    setPlayerState("playing");
    await waitForVideoElement(signal);
    const startSeconds = resumeFromMs != null ? resumeFromMs / 1000 : 0;
    await streamPlayer.attach(videoRef.current!, startSeconds);
    if (resumeFromMs != null) appliedResumeFromMsRef.current = true;
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

    const qIndex = streamUrl.indexOf("?");
    const streamBase = qIndex >= 0 ? streamUrl.slice(0, qIndex) : streamUrl;
    const query = qIndex >= 0 ? streamUrl.slice(qIndex) : "";
    const probeUrl = `${streamBase}/probe${query}`;

    // Warm-cache HLS: the server already transcoded to H.264/AAC. Safari plays
    // the .m3u8 natively; other browsers remux it through Mediabunny (cheap — no
    // client-side decode/re-encode) instead of transcoding the raw file on the CPU.
    // Subtitles are optional and load concurrently — playback never awaits them.
    await runPlaybackLoad({
      fetch: (input, init) => fetch(input, init),
      probeUrl,
      streamUrl,
      apiUrl,
      signal,
      startSubtitles: () => startSubtitleLoad(signal),
      canPlayHlsNatively,
      playWithMediabunny: (source, sig) => playWithMediabunny(source, sig),
      onNativeHls: (hlsUrl) => {
        usingHlsRef.current = true;
        setVideoSrc(hlsUrl);
        setPlayerState("playing");
      },
      onDirect: (url) => {
        usingHlsRef.current = false;
        setVideoSrc(url);
        setPlayerState("playing");
      },
    });
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
      reporter.flush("close");
      cleanup();
      return;
    }
    requestAnimationFrame(() => requestAnimationFrame(() => loadAndPlay()));
  }

  function sampleFromVideo(video: HTMLVideoElement) {
    return {
      positionMs: video.currentTime * 1000,
      durationMs: video.duration * 1000,
    };
  }

  function handleTimeUpdate(event: React.SyntheticEvent<HTMLVideoElement>) {
    const video = event.currentTarget;
    const { positionMs, durationMs } = sampleFromVideo(video);
    reporter.report(positionMs, durationMs);
  }

  function handlePause(event: React.SyntheticEvent<HTMLVideoElement>) {
    const video = event.currentTarget;
    const { positionMs, durationMs } = sampleFromVideo(video);
    reporter.report(positionMs, durationMs);
    reporter.flush("pause");
  }

  function handleSeeked(event: React.SyntheticEvent<HTMLVideoElement>) {
    if (showResumePrompt) setShowResumePrompt(false);
    const video = event.currentTarget;
    const { positionMs, durationMs } = sampleFromVideo(video);
    reporter.report(positionMs, durationMs);
    reporter.flush("seeked");
  }

  function handleEnded(event: React.SyntheticEvent<HTMLVideoElement>) {
    const video = event.currentTarget;
    const durationMs = video.duration * 1000;
    reporter.report(durationMs, durationMs);
    reporter.flush("ended");
  }

  function handleLoadedMetadata(event: React.SyntheticEvent<HTMLVideoElement>) {
    applyResumeFromMsSeek(event.currentTarget);
  }

  function resumeFromPrompt() {
    if (resumePromptPositionMs == null || !videoRef.current) return;
    setShowResumePrompt(false);
    videoRef.current.currentTime = resumePromptPositionMs / 1000;
  }

  async function startOverFromPrompt() {
    if (!videoRef.current) return;
    const video = videoRef.current;
    const ok = await reporter.clearProgress();
    if (!ok) {
      toast.error("Could not clear saved resume position");
    }
    // Keep playback usable either way; only successful DELETE claims a clear.
    setShowResumePrompt(false);
    video.currentTime = 0;
  }

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      {trigger ? (
        <DialogTrigger render={trigger} />
      ) : (
        <DialogTrigger
          render={
            <Button
              variant={buttonVariant}
              size={buttonSize}
              className={cn(
                buttonSize === "icon" ? "text-muted-foreground" : undefined,
                buttonSize === "icon" && !buttonClassName ? "h-7 w-7" : undefined,
                buttonClassName,
              )}
              aria-label={triggerLabel ?? "Play"}
            />
          }
        >
          <Play className={buttonSize === "icon" ? "size-4" : "h-3.5 w-3.5"} />
          {triggerLabel && buttonSize !== "icon" ? <span>{triggerLabel}</span> : null}
        </DialogTrigger>
      )}
      <DialogContent className="flex max-h-[90vh] w-[95vw] max-w-6xl flex-col max-lg:top-0 max-lg:left-0 max-lg:h-dvh max-lg:max-h-none max-lg:w-screen max-lg:max-w-none max-lg:translate-x-0 max-lg:translate-y-0 max-lg:rounded-none max-lg:pb-safe-b sm:max-w-6xl">
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
              {downloadsEnabled && (
                <DirectDownloadAction
                  mediaType={mediaType}
                  mediaId={mediaId}
                  fileId={fileId}
                  buttonVariant="outline"
                  buttonSize="default"
                  triggerLabel="Download File"
                />
              )}
            </div>
          )}
          {playerState === "playing" && (
            <video
              ref={videoRef}
              className="max-h-[70vh] w-full rounded-md bg-black max-lg:max-h-[calc(100dvh-8rem)]"
              controls
              autoPlay
              crossOrigin="use-credentials"
              src={videoSrc}
              onLoadedMetadata={handleLoadedMetadata}
              onTimeUpdate={handleTimeUpdate}
              onPause={handlePause}
              onSeeked={handleSeeked}
              onEnded={handleEnded}
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
          {playerState === "playing" && showResumePrompt && resumePromptPositionMs != null && (
            <div className="absolute inset-x-0 bottom-14 z-10 flex justify-center px-4">
              <div className="flex flex-wrap items-center justify-center gap-2 rounded-md border bg-background/95 px-3 py-2 shadow-sm">
                <Button size="sm" className="coarse:min-h-11" onClick={resumeFromPrompt}>
                  Resume from {formatResumeClock(resumePromptPositionMs)}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="coarse:min-h-11"
                  onClick={startOverFromPrompt}
                >
                  Start over
                </Button>
              </div>
            </div>
          )}
        </div>
        {downloadsEnabled && (
          <DialogFooter>
            <DirectDownloadAction
              mediaType={mediaType}
              mediaId={mediaId}
              fileId={fileId}
              buttonVariant="outline"
              buttonSize="sm"
            />
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

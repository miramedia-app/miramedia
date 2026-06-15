import {
  Input,
  Output,
  EncodedPacketSink,
  EncodedVideoPacketSource,
  AudioSampleSink,
  AudioSampleSource,
  BlobSource,
  UrlSource,
  StreamTarget,
  Mp4OutputFormat,
  Mp4InputFormat,
  MatroskaInputFormat,
  WebMInputFormat,
  MpegTsInputFormat,
  QuickTimeInputFormat,
  HLS_FORMATS,
  QUALITY_HIGH,
  type UrlSourceOptions,
  type StreamTargetChunk,
  type InputVideoTrack,
  type InputAudioTrack,
} from "mediabunny";
import { registerAc3Decoder } from "@mediabunny/ac3";

registerAc3Decoder();

const BROWSER_NATIVE_AUDIO = new Set(["aac", "opus", "mp3", "vorbis", "flac"]);

const FILE_INPUT_FORMATS = [
  new Mp4InputFormat(),
  new MatroskaInputFormat(),
  new WebMInputFormat(),
  new MpegTsInputFormat(),
  new QuickTimeInputFormat(),
];

/** Authenticated range reads against the MiraMedia API. */
const API_URL_SOURCE_OPTIONS: UrlSourceOptions = {
  requestInit: { credentials: "include" },
};

export type MediaStreamSource = { type: "blob"; blob: Blob } | { type: "url"; url: string };

export function isHlsPlaylistUrl(url: string): boolean {
  const path = url.split("?")[0]?.toLowerCase() ?? "";
  return path.endsWith(".m3u8");
}

function formatsForSource(source: MediaStreamSource) {
  if (source.type === "url" && isHlsPlaylistUrl(source.url)) {
    return HLS_FORMATS;
  }
  return FILE_INPUT_FORMATS;
}

function createInput(source: MediaStreamSource): Input {
  if (source.type === "blob") {
    return new Input({ source: new BlobSource(source.blob), formats: formatsForSource(source) });
  }
  return new Input({
    source: new UrlSource(source.url, API_URL_SOURCE_OPTIONS),
    formats: formatsForSource(source),
  });
}

export interface ProbeResult {
  needsConversion: boolean;
  audioCodec: string | null;
  videoCodec: string | null;
  duration: number;
}

export async function probeMedia(source: MediaStreamSource): Promise<ProbeResult> {
  const input = createInput(source);
  const format = await input.getFormat();
  const tracks = await input.getTracks();
  const duration = await input.computeDuration();

  let audioCodec: string | null = null;
  let videoCodec: string | null = null;
  let needsConversion = false;

  for (const track of tracks) {
    const codec = track.codec;
    if (track.type === "audio" && codec) {
      audioCodec = codec;
      if (!BROWSER_NATIVE_AUDIO.has(codec)) needsConversion = true;
    }
    if (track.type === "video" && codec) videoCodec = codec;
  }

  if (format instanceof MatroskaInputFormat) needsConversion = true;

  input[Symbol.dispose]();
  return { needsConversion, audioCodec, videoCodec, duration };
}

/** Remux/transcode into fragmented MP4 for MSE (files, byte streams, and HLS). */
export class StreamingPlayer {
  private source: MediaStreamSource;
  private mediaSource: MediaSource;
  private sourceBuffer: SourceBuffer | null = null;
  private videoElement: HTMLVideoElement | null = null;
  private durationHint: number | undefined;

  private input: Input | null = null;
  private videoTrack: InputVideoTrack | null = null;
  private audioTrack: InputAudioTrack | null = null;

  private currentOutput: Output | null = null;
  private generation = 0;

  private appendQueue: Uint8Array[] = [];
  private appending = false;

  private suppressSeekHandler = false;
  private seekDebounce: ReturnType<typeof setTimeout> | null = null;
  private playbackStarted = false;

  public url: string;

  constructor(source: MediaStreamSource, durationHint?: number) {
    this.source = source;
    this.durationHint = durationHint;
    this.mediaSource = new MediaSource();
    this.url = "";
  }

  /** Wire MSE and begin remux; returns once the pipeline is ready to append. */
  async attach(videoElement: HTMLVideoElement, startTime = 0): Promise<void> {
    this.videoElement = videoElement;
    this.playbackStarted = false;
    this.input = createInput(this.source);

    this.url = URL.createObjectURL(this.mediaSource);
    videoElement.src = this.url;

    const sourceOpenPromise = new Promise<void>((resolve, reject) => {
      this.mediaSource.addEventListener(
        "sourceopen",
        () => {
          void (async () => {
            try {
              const [videoTracks, audioTracks] = await Promise.all([
                this.input!.getVideoTracks(),
                this.input!.getAudioTracks(),
              ]);
              this.videoTrack = videoTracks[0] ?? null;
              this.audioTrack = audioTracks[0] ?? null;

              const codecParts: string[] = [];
              if (this.videoTrack) {
                const vCodec = await this.videoTrack.getCodecParameterString();
                if (vCodec) codecParts.push(vCodec);
              }
              codecParts.push("mp4a.40.2");
              const sbMime = `video/mp4; codecs="${codecParts.join(", ")}"`;

              this.sourceBuffer = this.mediaSource.addSourceBuffer(sbMime);
              this.sourceBuffer.mode = "segments";
              this.sourceBuffer.addEventListener("updateend", () => {
                this.appending = false;
                requestAnimationFrame(() => this.flushQueue());
              });

              const durationHint = this.durationHint;
              if (durationHint && isFinite(durationHint)) {
                this.mediaSource.duration = durationHint;
              } else {
                void this.input!.computeDuration().then((d) => {
                  if (this.mediaSource && isFinite(d)) {
                    this.mediaSource.duration = d;
                  }
                });
              }
              resolve();
            } catch (err) {
              reject(err);
            }
          })();
        },
        { once: true },
      );
    });

    await sourceOpenPromise;
    videoElement.addEventListener("seeking", this.onSeeking);
    void this.startFrom(startTime);
  }

  private onSeeking = () => {
    if (!this.videoElement || this.suppressSeekHandler) return;
    const time = this.videoElement.currentTime;
    if (this.seekDebounce) clearTimeout(this.seekDebounce);
    this.seekDebounce = setTimeout(() => this.handleSeek(time), 250);
  };

  private handleSeek(time: number): void {
    if (this.isTimeBuffered(time)) return;
    this.startFrom(time)
      .then(() => {
        if (this.videoElement) {
          this.suppressSeekHandler = true;
          this.videoElement.currentTime = time;
          requestAnimationFrame(() => {
            this.suppressSeekHandler = false;
          });
        }
      })
      .catch(() => {});
  }

  private isTimeBuffered(time: number): boolean {
    if (!this.sourceBuffer) return false;
    const buffered = this.sourceBuffer.buffered;
    for (let i = 0; i < buffered.length; i++) {
      const start = buffered.start(i);
      const end = buffered.end(i);
      if (time + 0.35 >= start && time <= end - 0.15) return true;
    }
    return false;
  }

  private tryStartPlayback() {
    if (this.playbackStarted || !this.videoElement) return;
    const buffered = this.sourceBuffer?.buffered;
    if (!buffered || buffered.length === 0) return;
    this.playbackStarted = true;
    void this.videoElement.play().catch(() => {});
  }

  private flushQueue() {
    if (this.appending || !this.sourceBuffer || this.sourceBuffer.updating) return;
    if (this.appendQueue.length === 0) return;
    this.appending = true;
    const chunk = this.appendQueue.shift()!;
    try {
      this.sourceBuffer.appendBuffer(chunk as BufferSource);
      this.tryStartPlayback();
    } catch (err) {
      this.appending = false;
      if (err instanceof DOMException && err.name === "QuotaExceededError") {
        this.appendQueue.unshift(chunk);
        this.evictOldData();
      } else {
        console.error("appendBuffer error:", err);
      }
    }
  }

  private evictOldData() {
    if (!this.sourceBuffer || !this.videoElement || this.sourceBuffer.updating) return;
    const buffered = this.sourceBuffer.buffered;
    if (buffered.length === 0) return;
    const currentTime = this.videoElement.currentTime;
    const bufferStart = buffered.start(0);
    const evictEnd = Math.max(bufferStart, currentTime - 30);
    if (evictEnd <= bufferStart) return;
    try {
      this.sourceBuffer.remove(bufferStart, evictEnd);
      this.sourceBuffer.addEventListener("updateend", () => this.flushQueue(), { once: true });
    } catch {
      if (this.appendQueue.length > 0) {
        this.appendQueue.shift();
        this.flushQueue();
      }
    }
  }

  async startFrom(startTime: number): Promise<void> {
    const gen = ++this.generation;
    if (this.currentOutput) {
      this.currentOutput.cancel().catch(() => {});
      this.currentOutput = null;
    }
    this.appendQueue = [];
    this.appending = false;
    if (!this.input || !this.videoTrack || gen !== this.generation) return;
    const videoSink = new EncodedPacketSink(this.videoTrack);
    const startPacket = await videoSink.getKeyPacket(startTime);
    if (!startPacket || gen !== this.generation) return;

    const writable = new WritableStream<StreamTargetChunk>({
      write: (chunk) => {
        if (gen !== this.generation) return;
        this.appendQueue.push(new Uint8Array(chunk.data));
        this.flushQueue();
      },
    });

    const output = new Output({
      format: new Mp4OutputFormat({ fastStart: "fragmented", minimumFragmentDuration: 0.25 }),
      target: new StreamTarget(writable),
    });

    const videoSource = new EncodedVideoPacketSource(this.videoTrack.codec!);
    output.addVideoTrack(videoSource);

    let audioSampleSink: AudioSampleSink | null = null;
    let audioSource: AudioSampleSource | null = null;
    if (this.audioTrack) {
      audioSampleSink = new AudioSampleSink(this.audioTrack);
      audioSource = new AudioSampleSource({ codec: "aac", bitrate: QUALITY_HIGH });
      output.addAudioTrack(audioSource);
    }

    await output.start();
    if (gen !== this.generation) {
      output.cancel().catch(() => {});
      return;
    }
    this.currentOutput = output;

    (async () => {
      try {
        const config = await this.videoTrack!.getDecoderConfig();
        const videoDone = (async () => {
          let first = true;
          for await (const packet of videoSink.packets(startPacket)) {
            if (gen !== this.generation) break;
            try {
              await videoSource.add(
                packet,
                first && config ? { decoderConfig: config } : undefined,
              );
              first = false;
            } catch {
              break;
            }
          }
        })();

        const audioDone = (async () => {
          if (!audioSampleSink || !audioSource) return;
          for await (const sample of audioSampleSink.samples(startTime)) {
            if (gen !== this.generation) {
              sample.close();
              break;
            }
            try {
              await audioSource.add(sample);
            } catch {
              sample.close();
              break;
            }
            sample.close();
          }
        })();

        await Promise.all([videoDone, audioDone]);
        if (gen === this.generation) {
          try {
            await output.finalize();
          } catch {}
        }
      } catch {}
    })();
  }

  dispose() {
    this.generation++;
    if (this.seekDebounce) clearTimeout(this.seekDebounce);
    if (this.videoElement) {
      this.videoElement.removeEventListener("seeking", this.onSeeking);
    }
    if (this.currentOutput) {
      this.currentOutput.cancel().catch(() => {});
    }
    if (this.input) {
      this.input[Symbol.dispose]();
    }
    if (this.url) {
      URL.revokeObjectURL(this.url);
    }
  }
}

export function hasWebCodecsSupport(): boolean {
  return (
    typeof globalThis.VideoDecoder !== "undefined" && typeof globalThis.AudioDecoder !== "undefined"
  );
}

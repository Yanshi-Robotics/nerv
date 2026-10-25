export type InstallJob = {
  id: string;
  module: string;
  state: string;
  phase: string;
  message?: string;
  error_code?: string | null;
  asset?: string | null;
  downloaded?: number | null;
  download_total?: number | null;
  installation_path?: string;
  cached_bytes?: number;
  speed_bytes_s?: number;
  eta_seconds?: number | null;
  concurrency?: number;
  bandwidth_bytes_s?: number;
  files?: {name: string; bytes: number; received: number; state: string; attempt?: number; next_retry_at?: number | null; reason?: string}[];
};

export const isInstalling = (job: InstallJob) => ["queued", "running"].includes(job.state);

// Exact artifact bytes across all dependencies; installation stages have no invented percentage.
export function downloadPercent(job: InstallJob): number | undefined {
  if (!["queued", "running", "paused", "waiting"].includes(job.state) || job.phase !== "download" ||
      !Number.isFinite(job.download_total) || !Number.isFinite(job.downloaded) ||
      !job.download_total || job.download_total < 0 || job.downloaded == null || job.downloaded < 0) return undefined;
  return Math.min(100, Math.floor(job.downloaded / job.download_total * 100));
}

// Schedule after completion to prevent overlapping requests; discard late results on unmount.
export function watchInstallLog<T>(read: (signal: AbortSignal) => Promise<T>,
  receive: (value: T) => void, failed: (error: unknown) => void,
  intervalMs: number, repeat = true) {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let reading = false;
  async function poll() {
    if (reading) return;
    reading = true;
    try {
      const value = await read(controller.signal);
      if (!controller.signal.aborted) receive(value);
    } catch (error) {
      if (!controller.signal.aborted) failed(error);
    } finally {
      reading = false;
      if (repeat && !controller.signal.aborted) timer = setTimeout(poll, typeof document !== "undefined" && document.hidden ? intervalMs * 5 : intervalMs);
    }
  }
  const visible = () => {if (!document.hidden) {clearTimeout(timer); void poll();}};
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", visible);
  void poll();
  return () => { if (typeof document !== "undefined") document.removeEventListener("visibilitychange", visible); controller.abort(); clearTimeout(timer); };
}

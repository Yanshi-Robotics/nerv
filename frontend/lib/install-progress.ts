export type InstallJob = {
  id: string;
  module: string;
  state: string;
  phase: string;
  message?: string;
  asset?: string | null;
  downloaded?: number | null;
  download_total?: number | null;
  installation_path?: string;
};

export const isInstalling = (job: InstallJob) => ["queued", "running"].includes(job.state);

// This is the current release file only, never a fabricated overall percentage.
export function downloadPercent(job: InstallJob): number | undefined {
  if (!isInstalling(job) || job.phase !== "download" ||
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
  async function poll() {
    try {
      const value = await read(controller.signal);
      if (!controller.signal.aborted) receive(value);
    } catch (error) {
      if (!controller.signal.aborted) failed(error);
    } finally {
      if (repeat && !controller.signal.aborted) timer = setTimeout(poll, intervalMs);
    }
  }
  void poll();
  return () => { controller.abort(); clearTimeout(timer); };
}

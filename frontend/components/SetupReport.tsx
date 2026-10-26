"use client";
import { useI18n } from "@/lib/i18n";

const labels: Record<string, string> = {
  permissions: "Installation permissions", system: "Computer compatibility", disk: "Available storage", download_tool: "Download tool",
  environment: "Runtime environment", calibration_file: "Calibration file", motors: "Motor configuration",
  device_calibration: "Device calibration",
};
const explanations: Record<string, string> = {
  calibration_missing: "No calibration file found. Select an existing file or complete calibration outside NERV.",
  calibration_invalid: "The calibration file does not match the standard device. Check the robot ID and file.",
  calibration_unreadable: "The calibration file could not be read. Check permissions.",
  calibration_mismatch: "The file and device calibration differ. Select the correct file or calibrate outside NERV.",
  motors_unreachable: "Some motors did not respond. Check power, cables and the selected port, then retry.",
  motor_model_mismatch: "The motors do not match the standard product. Complete motor setup outside NERV.",
  device_busy: "Another process owns this device. Close it before checking again.",
  device_unreadable: "Device check incomplete. Check permissions, port availability and the USB connection.",
  check_required: "Check this device before continuing.",
  device_changed: "The device or configuration changed. Check again.",
  environment_missing: "Install the required runtime to continue.",
  environment_repair: "The installed runtime needs repair.",
  system_requires_attention: "Review the system and installation settings.",
  check_incomplete: "Check incomplete. Review the details and retry.",
  worker_failed: "The check process stopped. Check again.",
  cancelled: "Check cancelled. No device changes were made.",
  page_closed: "Check stopped because the setup page was closed.",
  timeout: "Check timed out. Check the connection and retry.",
};
export function SetupExplanation({ code }: { code?: string }) {
  const { t } = useI18n();
  return code && explanations[code] ? <p className="text-sm text-neutral-400">{t(explanations[code])}</p> : null;
}
export default function SetupReport({ checks, checkedAt }: { checks: any[] | Record<string, any>; checkedAt?: number }) {
  const { t } = useI18n();
  const rows = Array.isArray(checks) ? checks : Object.entries(checks).map(([key, v]) => ({ key, ...v }));
  return <div className="space-y-3" aria-live="polite">
    {rows.map((row) => <div key={row.key} className="rounded-xl border border-neutral-700 p-4">
      <div className="flex items-center justify-between gap-3"><span>{t(labels[row.key] || row.key)}</span>
        <span className="text-sm">{row.state === 'passed' ? '✓' : row.state === 'unmet' ? '!' : '○'} {t(row.state === 'passed' ? 'Passed' : row.state === 'unmet' ? 'Needs attention' : 'Not checked')}</span></div>
      <SetupExplanation code={row.code} />
      {row.evidence != null && <details className="mt-2 text-xs text-neutral-400"><summary>{t("Technical details")}</summary><pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap break-all">{typeof row.evidence === 'string' ? row.evidence : JSON.stringify(row.evidence, null, 2)}</pre></details>}
    </div>)}
    {checkedAt && <p className="text-xs text-neutral-400">{t("Last checked")}: {new Date(checkedAt * 1000).toLocaleString()}</p>}
  </div>;
}

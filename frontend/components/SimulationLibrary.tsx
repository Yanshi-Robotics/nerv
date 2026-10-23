"use client";
import { useState } from "react";
import { userApi, type Registry, type SessionSummary } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
export default function SimulationLibrary({
  registry,
}: {
  registry: Registry | null;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function open(body: string, world: string) {
    setBusy(true);
    setError("");
    try {
      const session = await userApi<SessionSummary>(
        "operator/workspace",
        "POST",
        { body, world, mode: "sim" },
      );
      window.dispatchEvent(
        new CustomEvent("nerv-instance-created", { detail: session.id }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const worlds = registry?.worlds.filter((w) => w.kind === "sim") || [];
  return (
    <section className="settings-content space-y-5">
      <h1>{t("Installed simulation scenes")}</h1>
      <p className="muted">
        {t(
          "Open a scene without a chat model. Motion stays disabled until you authorize it.",
        )}
      </p>
      {worlds.map((w) => {
        const match = registry?.matrix.find((m) => m.world === w.name && m.ok);
        return (
          <article key={w.name} className="settings-row">
            <div>
              <span className="environment-badge">Sim</span>
              <h2 className="mt-3">{w.label || w.name}</h2>
            </div>
            <button
              disabled={busy || !match}
              className="nerv-control action"
              onClick={() => match && open(match.body, w.name)}
            >
              {t("Open workspace")}
            </button>
          </article>
        );
      })}
      {!worlds.length && <p>{t("Install the simulation component first.")}</p>}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
    </section>
  );
}

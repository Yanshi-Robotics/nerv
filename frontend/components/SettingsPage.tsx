"use client";
import { useEffect, useState } from "react";
import {
  setupDevice,
  checkRelease,
  type Registry,
  type SessionSummary,
  type NodeInfo,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import ControlSchemes from "./ControlSchemes";
import ModulesPanel from "./ModulesPanel";
import PolicyLibrary from "./PolicyLibrary";
import ThemeToggle from "./ThemeToggle";
import LangToggle from "./LangToggle";
import NervDashboard from "./NervDashboard";
import SessionLogsView from "./SessionLogsView";
import SystemInstallation from "./SystemInstallation";
import SimulationLibrary from "./SimulationLibrary";
import AssetLibrary from './AssetLibrary';
const sections = [
  ["general", "General"],
  ["agents", "Agent connections"],
  ["schemes", "Control schemes"],
  ["policies", "Policy library"],
  ["components", "Components and simulation"],
  ["assets", "My assets"],
  ["system", "System and installation"],
  ["diagnostics", "Diagnostics and logs"],
  ["updates", "Updates"],
  ["about", "About"],
] as const;
function SchemeSettings({
  registry,
  session,
  bodyNode,
  navigate,
  onChanged,
}: {
  registry: Registry | null;
  session: SessionSummary | null;
  bodyNode?: NodeInfo | null;
  navigate: (route: string) => void;
  onChanged: () => void;
}) {
  const { t } = useI18n();
  const [actions, setActions] = useState<any[]>([]),
    [error, setError] = useState("");
  const product = registry?.bodies.find((b) => b.buses.real?.provider);
  useEffect(() => {
    if (!session?.body && product)
      setupDevice("scheme_options")
        .then((v) => setActions(v.actions))
        .catch((e) => setError(e.message));
  }, [session?.body, product?.name]);
  return (
    <>
      <ControlSchemes
        session={session?.body ? session : null}
        bodyNode={bodyNode}
        brains={registry?.brains || []}
        offline={
          product ? { robot: product.name, mode: "real", actions } : undefined
        }
        onSetup={() => navigate("robots")}
        onModels={() => navigate("settings/models")}
        onChanged={onChanged}
      />
      {error && (
        <div className="p-6">
          <p role="alert">{error}</p>
          <button
            className="nerv-control action"
            onClick={() => navigate("settings/components")}
          >
            {t("Components and simulation")} ↗
          </button>
        </div>
      )}
    </>
  );
}
function RetiredInternalSettings() {
  const {t}=useI18n();
  return <div className="settings-content space-y-5">
    <h1>{t('Internal Agent settings retired')}</h1>
    <p>{t('External Agents manage their own models, credentials and context. NERV Brain Core provides observations, control and execution records.')}</p>
    <p className="muted">{t('Historical configuration is preserved. It does not authorize new tasks.')}</p>
  </div>;
}
export default function SettingsPage({
  section,
  registry,
  session,
  bodyNode,
  version,
  versionError,
  navigate,
  onChanged,
}: {
  section: string;
  registry: Registry | null;
  session: SessionSummary | null;
  bodyNode: NodeInfo | null;
  version: string;
  versionError: boolean;
  navigate: (r: string) => void;
  onChanged: () => void;
}) {
  const { t } = useI18n();
  const [release, setRelease] = useState(""),
    [logs, setLogs] = useState(false);
  return (
    <section className="settings-layout">
      <details className="settings-mobile">
        <summary>
          {t("Settings")} ·{" "}
          {t(sections.find(([id]) => id === section)?.[1] || (section === "explore" ? "Components and simulation" : "General"))}
        </summary>
        {sections.map(([id, label]) => (
          <button
            key={id}
            className="nerv-nav"
            onClick={(e) => {
              navigate(`settings/${id}`);
              e.currentTarget.closest("details")?.removeAttribute("open");
            }}
          >
            {t(label)}
          </button>
        ))}
      </details>
      <nav className="settings-nav" aria-label={t("Settings")}>
        <h1>{t("Settings")}</h1>
        {sections.map(([id, label]) => (
          <button
            key={id}
            className={`nerv-nav ${section === id ? "nerv-selected" : ""}`}
            aria-current={section === id ? "page" : undefined}
            onClick={() => navigate(`settings/${id}`)}
          >
            {t(label)}
          </button>
        ))}
      </nav>
      <div className="settings-detail">
        {section === "general" && (
          <div className="settings-content">
            <h1>{t("General")}</h1>
            <div className="settings-row">
              <div>
                <h2>{t("Appearance")}</h2>
                <p className="muted">
                  {t("Choose a comfortable theme for your workspace.")}
                </p>
              </div>
              <ThemeToggle />
            </div>
            <div className="settings-row">
              <h2>{t("Language")}</h2>
              <LangToggle />
            </div>
            <div className="settings-row">
              <h2>{t("Device configuration")}</h2>
              <button
                className="nerv-control action"
                onClick={() => navigate("robots")}
              >
                {t("My robot")}
              </button>
            </div>
          </div>
        )}
        {["models", "runtime"].includes(section) && <RetiredInternalSettings />}
        {section === "schemes" && (
          <SchemeSettings
            registry={registry}
            session={session}
            bodyNode={bodyNode}
            navigate={navigate}
            onChanged={onChanged}
          />
        )}
        {section === "policies" && (
          <PolicyLibrary onComponents={() => navigate("settings/components")} />
        )}
        {section === "components" && (
          <ModulesPanel
            onChanged={onChanged}
            onOpen={(id) =>
              navigate(id === "so101-sim" ? "settings/explore" : "robots")
            }
          />
        )}
        {section === "system" && <SystemInstallation />}
        {section === "assets" && <AssetLibrary />}
        {section === "diagnostics" && (
          <>
            <div className="p-4">
              <button
                className="nerv-control action"
                onClick={() => setLogs(!logs)}
              >
                {t(logs ? "NERV dashboard" : "Session Logs")}
              </button>
            </div>
            {logs ? (
              <SessionLogsView embedded sessionId={session?.id || ""} />
            ) : (
              <NervDashboard
                embedded
                onOpenLogs={() => setLogs(true)}
                onNodesChanged={onChanged}
              />
            )}
          </>
        )}
        {section === "explore" && <SimulationLibrary registry={registry} />}
        {section === "about" && (
          <div className="settings-content space-y-5">
            <h1>{t("About")}</h1>
            {version ? <p className="text-3xl">NERV {version}</p> : <p role="status">{t(versionError ? "Unable to read version." : "Loading version…")}</p>}
          </div>
        )}
        {section === "updates" && (
          <div className="settings-content space-y-5">
            <h1>{t("Updates")}</h1>
            <p className="muted">
              {t("Development release. Hardware acceptance remains separate.")}
            </p>
            <button
              className="nerv-control action"
              onClick={() =>
                checkRelease()
                  .then((v) =>
                    setRelease(
                      `${v.installed_version || version} → ${v.available_version}`,
                    ),
                  )
                  .catch((e) => setRelease(e.message))
              }
            >
              {t("Check for updates")}
            </button>
            <p role="status">{release}</p>
            <p>
              {t(
                "Stop tasks, disconnect and exit NERV before running nerv update",
              )}
            </p>
            <pre className="rounded-xl bg-neutral-900 p-4">
              {"nerv update --check\nnerv update\nnerv rollback"}
            </pre>
            <a
              className="block underline"
              href="https://github.com/Yanshi-Robotics/nerv/releases"
              target="_blank"
              rel="noreferrer"
            >
              {t("Release notes")}
            </a>
            <a className="block underline" href="/robots/so101/NOTICE.md">
              {t("Robot model credits")}
            </a>
            <a className="block underline" href="/robots/so101/LICENSE">
              Apache-2.0
            </a>
          </div>
        )}
      </div>
    </section>
  );
}

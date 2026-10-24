"use client";
import { useCallback, useEffect, useState } from "react";
import {
  getRegistry,
  getNodes,
  listSessions,
  userApi,
  POLL_NODES_MS,
  type Registry,
  type NodeInfo,
  type SessionSummary,
  type RobotDisplay,
} from "@/lib/api";
import { canonicalRoute } from "@/lib/navigation";
import AppSidebar from "@/components/AppSidebar";
import HomeScreen, { type SessionDraft } from "@/components/HomeScreen";
import MyRobot from "@/components/MyRobot";
import SettingsPage from "@/components/SettingsPage";
import GlobalTaskBar from "@/components/GlobalTaskBar";
import OperatorToolbox from "@/components/OperatorToolbox";
import ChatPanel from "@/components/ChatPanel";
import SensingArea from "@/components/SensingArea";
import ControlSchemes from "@/components/ControlSchemes";
import { useI18n } from "@/lib/i18n";
export default function Home() {
  const { t } = useI18n();
  const [route, setRoute] = useState("new"),
    [collapsed, setCollapsed] = useState(false),
    [registry, setRegistry] = useState<Registry | null>(null),
    [nodes, setNodes] = useState<NodeInfo[]>([]),
    [sessions, setSessions] = useState<SessionSummary[]>([]),
    [robot, setRobot] = useState<RobotDisplay | null>(null),
    [version, setVersion] = useState(""),
    [versionError, setVersionError] = useState(false),
    [focusRequest, setFocusRequest] = useState(0),
    [currentId, setCurrentId] = useState(""),
    [error, setError] = useState("");
  const [draft, setDraft] = useState<SessionDraft>({
    text: "",
    brain: "",
    body: "",
    mode: "sim",
    world: "",
    scheme: "",
  });
  const [initial, setInitial] = useState<
    { id: string; text: string } | undefined
  >();
  const navigate = useCallback((value: string) => {
    value = canonicalRoute(value);
    if (value === "new") setFocusRequest((n) => n + 1);
    window.location.hash = value;
    setRoute(value);
    if (value.startsWith("session/")) setCurrentId(value.slice(8));
  }, []);
  const refresh = useCallback(async () => {
    try {
      const [r, n, s, d] = await Promise.all([
        getRegistry(),
        getNodes(),
        listSessions(),
        userApi<RobotDisplay>("robot"),
      ]);
      setRegistry(r);
      setNodes(n);
      setSessions(s);
      setRobot(d);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    const restore = () => {
      const value = canonicalRoute(window.location.hash);
      if (window.location.hash !== `#${value}`) window.history.replaceState(null, "", `#${value}`);
      setRoute(value);
      if (value.startsWith("session/")) setCurrentId(value.slice(8));
    };
    restore();
    try {
      setCollapsed(window.matchMedia("(max-width: 800px)").matches || localStorage.getItem("nerv-sidebar") === "1");
    } catch {}
    const narrow = window.matchMedia("(max-width: 800px)");
    const adaptSidebar = () => { if (narrow.matches) setCollapsed(true); };
    narrow.addEventListener("change", adaptSidebar);
    window.addEventListener("hashchange", restore);
    refresh();
    userApi("status")
      .then((v) => { if (v.version) setVersion(v.version); else setVersionError(true); })
      .catch(() => setVersionError(true));
    const timer = setInterval(refresh, POLL_NODES_MS);
    return () => {
      narrow.removeEventListener("change", adaptSidebar);
      window.removeEventListener("hashchange", restore);
      clearInterval(timer);
    };
  }, [refresh]);
  useEffect(() => {
    const created = (event: Event) => {
      navigate(`session/${(event as CustomEvent<string>).detail}`);
      refresh();
    };
    window.addEventListener("nerv-instance-created", created);
    return () => window.removeEventListener("nerv-instance-created", created);
  }, [navigate, refresh]);
  const current = sessions.find((s) => s.id === currentId) || null;
  const active = sessions.find((s) => s.body && s.status === "active") || null;
  const schemeSession = current?.body ? current : active;
  const bodyNode = (s: SessionSummary | null) =>
    s?.body
      ? nodes.find(
          (n) =>
            n.key === `body:${s.body}` && (n.meta.world || null) === s.world,
        ) || null
      : null;
  const worldNode =
    current?.world && current.body
      ? nodes.find((n) => n.key === `world:${current.world}/${current.body}`) ||
        null
      : null;
  return (
    <main className={`app-shell ${collapsed ? "sidebar-collapsed" : ""}`}>
      <AppSidebar
        sessions={sessions}
        current={currentId}
        collapsed={collapsed}
        route={route}
        navigate={navigate}
        toggle={() =>
          setCollapsed((v) => {
            localStorage.setItem("nerv-sidebar", v ? "0" : "1");
            return !v;
          })
        }
        onDelete={refresh}
      />
      <div className="app-main">
        <GlobalTaskBar sessions={sessions} nodes={nodes} navigate={navigate} />
        {error && (
          <p role="alert" className="error-text px-5">
            {t("Unable to refresh local state. Check the NERV service.")}{" "}
            {error}
          </p>
        )}
        {route === "new" && (
          <HomeScreen
            registry={registry}
            robot={robot}
            sessions={sessions}
            draft={draft}
            setDraft={setDraft}
            navigate={navigate}
            focusRequest={focusRequest}
            onCreated={async (id, text) => {
              setInitial({ id, text });
              setDraft({ ...draft, text: "" });
              await refresh();
              navigate(`session/${id}`);
            }}
          />
        )}
        {route === "robots" && (
          <MyRobot
            robot={robot}
            registry={registry}
            sessions={sessions}
            onChanged={refresh}
            navigate={navigate}
            openWorkspace={(id) => {
              setCurrentId(id);
              refresh();
              navigate(`session/${id}`);
            }}
          />
        )}
        {route.startsWith("settings/") && (
          <SettingsPage
            section={route.slice(9)}
            registry={registry}
            session={schemeSession}
            bodyNode={bodyNode(schemeSession)}
            version={version}
            versionError={versionError}
            navigate={navigate}
            onChanged={refresh}
          />
        )}
        <div
          className="workspace-container"
          style={{ display: route.startsWith("session/") ? "flex" : "none" }}
        >
          <OperatorToolbox
            session={current}
            bodyNode={bodyNode(current)}
            nodes={nodes}
            onChanged={refresh}
            chat={
              current?.brain === "operator" ? (
                <div className="overflow-auto">
                  <div className="p-4">
                    <h2>{t("Device and fixed policy workspace")}</h2>
                    <p className="muted">
                      {t(
                        "No chat model is used here. Choose a fixed policy to run without a key.",
                      )}
                    </p>
                    <button
                      className="nerv-control action"
                      onClick={() => navigate("new")}
                    >
                      {t("Start an Agent session")}
                    </button>
                  </div>
                  <ControlSchemes
                    session={current}
                    bodyNode={bodyNode(current)}
                    brains={registry?.brains || []}
                    onChanged={refresh}
                    onSetup={() => navigate("settings/policies")}
                    onModels={() => navigate("settings/models")}
                  />
                </div>
              ) : (
                <ChatPanel
                  session={current}
                  brains={registry?.brains || []}
                  bodyNode={bodyNode(current)}
                  onSetup={() => navigate("robots")}
                  onModels={() => navigate("settings/models")}
                  onSessionsChanged={refresh}
                  initialMessage={initial}
                  onInitialConsumed={() => setInitial(undefined)}
                />
              )
            }
          >
            {current ? (
              <SensingArea
                key={`${current.id}:${current.status}`}
                session={current}
                bodyNode={bodyNode(current)}
                worldNode={worldNode}
                robotSensors={
                  registry?.bodies.find((b) => b.name === current.body)?.sensors
                }
                onStateChanged={refresh}
              />
            ) : (
              <div className="p-8">
                {t("Select a session or start a new one.")}
                <button
                  className="nerv-control action"
                  onClick={() => navigate("new")}
                >
                  {t("New Session")}
                </button>
              </div>
            )}
          </OperatorToolbox>
        </div>
      </div>
    </main>
  );
}

"use client";
import { useEffect, useState } from "react";
import {
  userApi,
  getRobotTask,
  getPerception,
  runtimeOperation,
  robotTaskOperation,
  estopSession,
  POLL_NODES_MS,
  type SessionSummary,
  type NodeInfo,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n";
export default function GlobalTaskBar({
  sessions,
  nodes,
  navigate,
}: {
  sessions: SessionSummary[];
  nodes: NodeInfo[];
  navigate: (r: string) => void;
}) {
  const { t } = useI18n();
  const [task, setTask] = useState<any>(null),
    [error, setError] = useState(""),
    [trust, setTrust] = useState(false);
  const robot = sessions.find((s) => s.body && s.status === "active");
  useEffect(() => {
    userApi("config")
      .then((v) => setTrust(v.trust_all))
      .catch(() => {});
  }, []);
  useEffect(() => {
    if (!robot) return;
    let gone = false,
      pending = false;
    const tick = async () => {
      if (pending) return;
      pending = true;
      try {
        const result = await getRobotTask(robot.id);
        if (!gone) setTask(result.task);
        const modelTask =
          result.task &&
          ["starting", "running", "updating", "stopping"].includes(
            result.task.status,
          ) &&
          result.task.scheme?.template !== "fixed_policy";
        const node = nodes.find((n) => n.key === `body:${robot.body}`);
        if (
          !gone &&
          robot.armed &&
          node?.online &&
          !modelTask &&
          document.visibilityState === "visible"
        ) {
          const obs = await getPerception(robot.id);
          if (!gone && document.visibilityState === "visible")
            await runtimeOperation(robot.id, "operator/supervise", {
              frame_seq: obs.state.frame_seq,
            });
        }
      } catch {
        if (!gone) setTask(null);
      } finally {
        pending = false;
      }
    };
    tick();
    const timer = setInterval(tick, POLL_NODES_MS);
    return () => {
      gone = true;
      clearInterval(timer);
    };
  }, [
    robot?.id,
    robot?.armed,
    nodes.some((n) => n.key === `body:${robot?.body}` && n.online),
  ]);
  async function stop(emergency = false) {
    if (!robot) return;
    try {
      const result = emergency
        ? await estopSession(robot.id)
        : await robotTaskOperation(robot.id, "cancel");
      setError(result.message || "");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (!robot && !trust) return null;
  return (
    <div className="global-task-bar">
      {trust && (
        <span className="error-text">
          {t("NERV_TRUST_ALL is on — development only")}
        </span>
      )}
      {robot && (
        <>
          <span className="min-w-0 flex-1 truncate">
            {robot.mode === "real" ? "Real" : "Sim"} ·{" "}
            {task ? t(task.status) : t("Robot workspace")} {task?.goal || ""}
          </span>
          <button
            className="nerv-control action"
            onClick={() => navigate(`session/${robot.id}`)}
          >
            {t("Return to task")}
          </button>
          <button
            className="nerv-control nerv-warning action"
            onClick={() => stop()}
          >
            {t("Stop task")}
          </button>
          <button
            className="nerv-control nerv-danger action"
            onClick={() => stop(true)}
          >
            {t("Emergency stop")}
          </button>
        </>
      )}
      {error && (
        <p className="w-full" role="status">
          {error}
        </p>
      )}
    </div>
  );
}

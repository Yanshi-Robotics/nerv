"use client";
import { useEffect, useRef } from "react";
import { useI18n } from "@/lib/i18n";
import { deleteSession, type SessionSummary } from "@/lib/api";
import NavigationIcon, { type NavigationIconName } from "./NavigationIcon";
export default function AppSidebar({ sessions, current, collapsed, route, navigate, toggle, onDelete }: {
  sessions: SessionSummary[]; current: string; collapsed: boolean; route: string;
  navigate: (route: string) => void; toggle: () => void; onDelete: () => void;
}) {
  const { t } = useI18n();
  const list = useRef<HTMLElement>(null);
  const focusList = useRef(false);
  useEffect(() => {
    if (!collapsed && focusList.current) {
      focusList.current = false;
      list.current?.focus();
      list.current?.querySelector('[data-current-session="true"]')?.scrollIntoView({ block: "nearest" });
    }
  }, [collapsed]);
  const link = (target: string, label: string, icon: NavigationIconName) => {
    const selected = target.startsWith("settings/") ? route.startsWith("settings/") : route === target;
    return <button className={`nerv-nav ${selected ? "nerv-selected" : ""}`} onClick={() => navigate(target)} aria-current={selected ? "page" : undefined} title={t(label)} aria-label={t(label)}>
      <NavigationIcon name={icon} />{!collapsed && <span>{t(label)}</span>}
    </button>;
  };
  return <aside className={`app-sidebar ${collapsed ? "is-collapsed" : ""}`}>
    <div className="sidebar-header">
      <button className="sidebar-brand" onClick={() => navigate("new")} aria-label={t("New Session")} title={t("New Session")}>
        <img src="/brand/nerv-mark-light.png" alt="" className="nerv-brand-mark h-8 w-6 object-contain" />
        {!collapsed && <b className="text-xl tracking-widest">NERV</b>}
      </button>
      <button className="nerv-control sidebar-toggle" onClick={toggle} aria-expanded={!collapsed} aria-controls="session-navigation" title={t(collapsed ? "Expand sidebar" : "Collapse sidebar")} aria-label={t(collapsed ? "Expand sidebar" : "Collapse sidebar")}>
        <NavigationIcon name="panel" expanded={!collapsed} />
      </button>
    </div>
    <div className="sidebar-primary">
      {link("new", "New Session", "new")}
      {link("robots", "Your Robots", "robot")}
    </div>
    <nav id="session-navigation" ref={list} tabIndex={-1} className="sidebar-sessions" aria-label={t("Sessions")}>
      {collapsed ? <button className={`nerv-nav ${route.startsWith("session/") ? "nerv-selected" : ""}`} title={t("Sessions")} aria-label={t("Sessions")} onClick={() => { focusList.current = true; toggle(); }}><NavigationIcon name="sessions" /></button> : <>
        <p className="sidebar-section-label">{t("Sessions")}</p>
        {sessions.filter((s) => s.brain !== "operator").map((s) => <div key={s.id} className="group flex items-center">
          <button className={`nerv-nav min-w-0 flex-1 ${route.startsWith("session/") && current === s.id ? "nerv-selected" : ""}`} onClick={() => navigate(`session/${s.id}`)} title={s.title} data-current-session={current === s.id} aria-current={route.startsWith("session/") && current === s.id ? "page" : undefined}>
            <span className="truncate">{s.title}</span>
          </button>
          <button className="nerv-control session-delete" aria-label={t("Delete session")} title={t("Delete session")} onClick={async () => {
            if (confirm(t("Delete this session? This cannot be undone."))) { await deleteSession(s.id); onDelete(); }
          }}><NavigationIcon name="close" /></button>
        </div>)}
      </>}
    </nav>
    <div className="sidebar-footer">{link("settings/general", "Settings", "settings")}</div>
  </aside>;
}

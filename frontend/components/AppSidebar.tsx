"use client";
import { useI18n } from "@/lib/i18n";
import { deleteSession, type SessionSummary } from "@/lib/api";
export default function AppSidebar({
  sessions,
  current,
  collapsed,
  route,
  version,
  navigate,
  toggle,
  onDelete,
}: {
  sessions: SessionSummary[];
  current: string;
  collapsed: boolean;
  route: string;
  version: string;
  navigate: (route: string) => void;
  toggle: () => void;
  onDelete: () => void;
}) {
  const { t } = useI18n();
  const link = (target: string, label: string, icon: string) => (
    <button
      className={`nerv-nav ${route === target ? "nerv-selected" : ""}`}
      onClick={() => navigate(target)}
      aria-current={route === target ? "page" : undefined}
      title={t(label)}
      aria-label={t(label)}
    >
      <span aria-hidden>{icon}</span>
      {!collapsed && <span>{t(label)}</span>}
    </button>
  );
  return (
    <aside className={`app-sidebar ${collapsed ? "is-collapsed" : ""}`}>
      <div className="flex items-center justify-between px-3 py-5">
        <button
          className="flex items-center gap-3"
          onClick={() => navigate("home")}
          aria-label={t("Home")}
        >
          <img
            src="/brand/nerv-mark-light.png"
            alt=""
            className="nerv-brand-mark h-8 w-6 object-contain"
          />
          {!collapsed && <b className="text-xl tracking-widest">NERV</b>}
        </button>
        <button
          className="nerv-control p-2"
          onClick={toggle}
          aria-label={t(collapsed ? "Expand sidebar" : "Collapse sidebar")}
        >
          {collapsed ? "›" : "‹"}
        </button>
      </div>
      <div className="space-y-1 px-2">
        {link("home/new", "New session", "+")}
        {link("home", "Home", "⌂")}
        {link("robots", "My robot", "◇")}
      </div>
      <nav
        className="min-h-0 flex-1 overflow-y-auto px-2 py-6"
        aria-label={t("Recent sessions")}
      >
        {!collapsed && (
          <p className="px-3 pb-3 text-xs text-neutral-500">
            {t("Recent sessions")}
          </p>
        )}
        {sessions
          .filter((s) => s.brain !== "operator")
          .map((s) => (
            <div key={s.id} className="group flex items-center">
              <button
                className={`nerv-nav min-w-0 flex-1 ${route.startsWith("session/") && current === s.id ? "nerv-selected" : ""}`}
                onClick={() => navigate(`session/${s.id}`)}
                title={s.title}
              >
                <span className="truncate">{collapsed ? "◦" : s.title}</span>
              </button>
              {!collapsed && (
                <button
                  className="nerv-control shrink-0 p-2 text-xs opacity-0 group-hover:opacity-100 focus:opacity-100"
                  aria-label={t("Delete session")}
                  onClick={async () => {
                    if (
                      confirm(t("Delete this session? This cannot be undone."))
                    ) {
                      await deleteSession(s.id);
                      onDelete();
                    }
                  }}
                >
                  ×
                </button>
              )}
            </div>
          ))}
      </nav>
      <div className="border-t border-neutral-800 px-2 py-3">
        {link("settings/general", "Settings", "⚙")}
        {!collapsed && (
          <p className="px-3 pt-2 text-xs text-neutral-500">NERV {version}</p>
        )}
      </div>
    </aside>
  );
}

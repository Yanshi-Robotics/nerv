import type { ReactNode } from "react";
export type NavigationIconName = "new" | "robot" | "sessions" | "settings" | "panel" | "close";
export default function NavigationIcon({ name, expanded = false }: { name: NavigationIconName; expanded?: boolean }) {
  const shapes: Record<NavigationIconName, ReactNode> = {
    new: <><path d="M12 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6" /><path d="m16 3 5 5-9 9-5 1 1-5Z M14 5l5 5" /></>,
    robot: <><rect x="4" y="7" width="16" height="13" rx="3" /><path d="M12 3v4 M2 12v4 M22 12v4 M8 12v2 M16 12v2 M9 17h6" /></>,
    sessions: <path d="M20 15a3 3 0 0 1-3 3H9l-5 3V7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3Z" />,
    settings: <><path d="m9 3-1 3-3 1-2 3 2 2-1 3 2 3 3-1 2 3h3l1-3 3-1 2-3-2-2 1-3-2-3-3 1-2-3Z" /><circle cx="11.5" cy="11.5" r="3" /></>,
    panel: <><rect x="3" y="4" width="18" height="16" rx="3" /><path d="M9 4v16" />{expanded && <path d="M5 7h2v10H5Z" fill="currentColor" stroke="none" />}</>,
    close: <path d="m6 6 12 12 M18 6 6 18" />,
  };
  return <svg className="navigation-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name]}</svg>;
}

/** Canonical routes preserve old bookmarks without duplicate history entries. */
export function canonicalRoute(hash: string): string {
  let route: string;
  try { route = decodeURIComponent(hash.replace(/^#/, "")); }
  catch { return "new"; }
  return !route || route === "home" || route === "home/new" ? "new" : route;
}

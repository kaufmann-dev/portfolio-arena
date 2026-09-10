export type Route =
  | { name: "home"; params: Record<string, never> }
  | { name: "portfolio"; params: { slug: string } }
  | { name: "prompt"; params: { slug: string } }
  | { name: "agent"; params: { slug: string } }
  | { name: "admin"; params: Record<string, never> }
  | { name: "about"; params: Record<string, never> };

function decodePart(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function parsePath(pathname: string): Route {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "p" && parts[1]) {
    return { name: "portfolio", params: { slug: decodePart(parts[1]) } };
  }
  if (parts[0] === "prompt" && parts[1]) {
    return { name: "prompt", params: { slug: decodePart(parts[1]) } };
  }
  if (parts[0] === "agent" && parts[1]) {
    return { name: "agent", params: { slug: decodePart(parts[1]) } };
  }
  if (parts[0] === "admin") {
    return { name: "admin", params: {} };
  }
  if (parts[0] === "about") {
    return { name: "about", params: {} };
  }
  return { name: "home", params: {} };
}

class RouterStore {
  version = $state(
    typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("version"),
  );
  navigation = $state(0);

  syncVersion(): void {
    if (typeof window !== "undefined")
      this.version = new URLSearchParams(window.location.search).get("version");
  }

  route = $state<Route>(parsePath(typeof window === "undefined" ? "/" : window.location.pathname));

  constructor() {
    if (typeof window === "undefined") return;
    window.addEventListener("popstate", () => {
      this.route = parsePath(window.location.pathname);
      this.syncVersion();
      this.navigation += 1;
    });
  }

  navigate(path: string): void {
    if (typeof window === "undefined") return;
    const destination = new URL(path, window.location.origin);
    const version = new URLSearchParams(window.location.search).get("version");
    if (version && !destination.searchParams.has("version")) destination.searchParams.set("version", version);
    path = destination.pathname + destination.search;
    if (window.location.pathname + window.location.search !== path) {
      window.history.pushState({}, "", path);
    }
    this.route = parsePath(window.location.pathname);
    this.syncVersion();
    this.navigation += 1;
    window.scrollTo(0, 0);
  }
}

export const router = new RouterStore();

/** Include the selected version in actual links, including links opened in a new tab. */
export function versionHref(path: string): string {
  const url = new URL(path, "http://localhost");
  if (router.version && !url.searchParams.has("version")) url.searchParams.set("version", router.version);
  return url.pathname + url.search;
}

/** Click handler for internal <a> links so the SPA router handles them. */
export function link(event: MouseEvent, path: string): void {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
  event.preventDefault();
  router.navigate(path);
}

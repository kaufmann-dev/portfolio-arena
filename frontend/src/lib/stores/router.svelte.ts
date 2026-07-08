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

function parsePath(pathname: string): Route {
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
  route = $state<Route>(parsePath(typeof window === "undefined" ? "/" : window.location.pathname));

  constructor() {
    if (typeof window === "undefined") return;
    window.addEventListener("popstate", () => {
      this.route = parsePath(window.location.pathname);
    });
  }

  navigate(path: string): void {
    if (typeof window === "undefined") return;
    if (window.location.pathname !== path) {
      window.history.pushState({}, "", path);
    }
    this.route = parsePath(window.location.pathname);
    window.scrollTo(0, 0);
  }
}

export const router = new RouterStore();

/** Click handler for internal <a> links so the SPA router handles them. */
export function link(event: MouseEvent, path: string): void {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
  event.preventDefault();
  router.navigate(path);
}

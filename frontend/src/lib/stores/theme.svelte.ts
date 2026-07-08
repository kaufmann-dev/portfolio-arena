const THEME_KEY = "arena_theme";

type Theme = "dark" | "light";

function initial(): Theme {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

class ThemeStore {
  theme = $state<Theme>(initial());

  constructor() {
    $effect.root(() => {
      $effect(() => {
        document.documentElement.dataset.theme = this.theme;
      });
    });
  }

  toggle(): void {
    this.theme = this.theme === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, this.theme);
  }
}

export const theme = new ThemeStore();

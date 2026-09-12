import type { ArenaTrack, Direction, HorizonObjective } from "./types";

export interface ArenaView {
  versionId: number;
  track: ArenaTrack;
  direction: Direction;
  objective: HorizonObjective;
}

export function arenaQueryUrl(view: ArenaView): string {
  const query = new URLSearchParams({ direction: view.direction, version_id: String(view.versionId) });
  if (view.track === "rebuilt") query.set("objective", view.objective);
  return `/api/arena/${view.track}?${query}`;
}

function otherViews(view: ArenaView): string[] {
  const otherTrack = view.track === "rebuilt" ? "managed" : "rebuilt";
  const otherDirection = view.direction === "long" ? "short" : "long";
  return [
    arenaQueryUrl({ ...view, track: otherTrack }),
    arenaQueryUrl({ ...view, direction: otherDirection }),
    arenaQueryUrl({ ...view, track: otherTrack, direction: otherDirection }),
  ];
}

/** One speculative request at a time; foreground navigation uses the shared cache directly. */
export class ArenaPrefetchQueue {
  private queued: string[] = [];
  private running = false;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly load: (url: string) => Promise<void>,
    private readonly canRun: () => boolean,
  ) {}

  schedule(view: ArenaView): void {
    this.cancel();
    this.queued = otherViews(view);
    // Yield so the selected view can render before speculative work starts.
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.run();
    }, 0);
  }

  cancel(): void {
    clearTimeout(this.timer);
    this.timer = undefined;
    this.queued = [];
    // An in-flight load may now serve the selected view; let the shared request finish.
  }

  private async run(): Promise<void> {
    if (this.running) return;
    this.running = true;
    try {
      while (this.queued.length && this.canRun()) {
        const url = this.queued.shift()!;
        await this.load(url).catch(() => undefined);
      }
    } finally {
      this.running = false;
    }
  }
}

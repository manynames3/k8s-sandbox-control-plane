import { createSseParser } from "./sse";

export type Status =
  | "queued" | "dispatching" | "running"
  | "succeeded" | "failed" | "timed_out" | "oom_killed" | "cancelled" | "infra_error";

export const TERMINAL: Status[] = ["succeeded", "failed", "timed_out", "oom_killed", "cancelled", "infra_error"];
export const isTerminal = (s: Status) => TERMINAL.includes(s);

export interface RunSummary {
  id: string;
  language: string;
  status: Status;
  timeout_seconds: number;
  exit_code: number | null;
  termination_reason: string | null;
  node: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Run extends RunSummary {
  stdout: string;
  stderr: string;
  truncated: boolean;
}

export interface Cluster {
  concurrency_cap: number;
  queued: number;
  dispatching: number;
  running: number;
  free_slots: number;
  nodes: { name: string; active_sandboxes: number }[];
  snapshot_age_seconds: number | null;
}

export type StreamEvent =
  | { kind: "state"; status: Status; ts: string }
  | { kind: "stdout" | "stderr"; text: string; ts: string }
  | { kind: "end" };

export interface Banner {
  /** Shown permanently at the top. Live mode has none. */
  text: string;
  links?: { label: string; href: string }[];
}

/** The UI only talks to this interface. Live mode implements it with the API; replay mode (public
 *  site) implements it from a recorded snapshot and cannot execute anything. */
export interface DataSource {
  mode: "live" | "replay";
  canExecute: boolean;
  banner?: Banner;
  submit(language: string, code: string, timeoutSeconds: number): Promise<string>;
  cancel(id: string): Promise<void>;
  get(id: string): Promise<Run>;
  list(): Promise<RunSummary[]>;
  cluster(): Promise<Cluster | null>;
  stream(id: string, onEvent: (e: StreamEvent) => void, signal: AbortSignal): Promise<void>;
}

export function liveSource(baseUrl: string, apiKey: string): DataSource {
  const headers = (extra: Record<string, string> = {}) => ({ Authorization: `Bearer ${apiKey}`, ...extra });
  const call = async <T>(path: string, init: RequestInit = {}): Promise<T> => {
    const res = await fetch(baseUrl + path, { ...init, headers: headers(init.headers as Record<string, string>) });
    if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);
    return (await res.json()) as T;
  };
  return {
    mode: "live",
    canExecute: true,
    async submit(language, code, timeoutSeconds) {
      const run = await call<Run>("/v1/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language, code, timeout_seconds: timeoutSeconds }),
      });
      return run.id;
    },
    async cancel(id) {
      await call(`/v1/runs/${id}`, { method: "DELETE" });
    },
    get: (id) => call<Run>(`/v1/runs/${id}`),
    list: async () => (await call<{ runs: RunSummary[] }>("/v1/runs?limit=15")).runs,
    cluster: () => call<Cluster>("/v1/cluster"),
    async stream(id, onEvent, signal) {
      // EventSource cannot send an Authorization header, so read the stream with fetch.
      const res = await fetch(`${baseUrl}/v1/runs/${id}/events`, { headers: headers(), signal });
      if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const parse = createSseParser();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) return;
        for (const ev of parse(decoder.decode(value, { stream: true }))) {
          const data = ev.data ? JSON.parse(ev.data) : {};
          if (ev.event === "state") onEvent({ kind: "state", status: data.status, ts: data.ts });
          else if (ev.event === "stdout" || ev.event === "stderr") onEvent({ kind: ev.event, text: data.text, ts: data.ts });
          else if (ev.event === "end") {
            onEvent({ kind: "end" });
            return;
          }
        }
      }
    },
  };
}

export function durationSeconds(r: Pick<RunSummary, "started_at" | "finished_at" | "created_at">): number | null {
  const start = r.started_at ?? r.created_at;
  if (!r.finished_at) return null;
  return (Date.parse(r.finished_at) - Date.parse(start)) / 1000;
}

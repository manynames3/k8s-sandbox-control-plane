import type { Banner, DataSource, Run, RunSummary, Status, StreamEvent } from "./api";

export interface SnapEvent {
  t: number; // seconds from the run's first event
  kind: "state" | "stdout" | "stderr";
  status?: Status;
  text?: string;
}

export interface SnapRun {
  id: string;
  label: string;
  kind: "state" | "attack";
  attack_id: string | null;
  language: string;
  code?: string;
  timeout_seconds: number;
  status: Status;
  exit_code: number | null;
  termination_reason: string | null;
  truncated: boolean;
  node: string | null;
  stdout: string;
  stderr: string;
  duration_seconds: number | null;
  events: SnapEvent[];
}

export interface BenchLevel {
  concurrency: number;
  sample_size: number;
  statuses: Record<string, number>;
  throughput_runs_per_second: number;
  queue_wait_seconds: Pct;
  schedule_seconds: Pct;
  e2e_seconds: Pct;
}
export interface Pct { p50: number | null; p95: number | null; p99: number | null }

export interface Benchmark {
  date: string;
  environment: string;
  git_sha: string;
  kubernetes_version: string;
  container_runtime: string;
  worker_nodes: number;
  concurrency_cap: number;
  workload: string;
  levels: BenchLevel[];
  results_file: string;
  vm?: { cpus?: number; memory_bytes?: number };
  cold_start?: { measured: boolean; reason?: string };
}

export interface Snapshot {
  version: number;
  recorded_on: string;
  environment: string;
  meta: Record<string, string | number>;
  links: { repo: string; ci: string; results: string };
  runs: SnapRun[];
  benchmark: Benchmark | null;
}

export interface ReplaySource extends DataSource {
  snapshot: Snapshot;
  setSpeed(multiplier: number): void;
  attackRun(attackId: string): string | undefined;
}

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    if (ms <= 0) return resolve();
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
  });

export function replaySource(snap: Snapshot): ReplaySource {
  const day = `${snap.recorded_on}T00:00:00Z`;
  const byId = new Map(snap.runs.map((r) => [r.id, r]));
  let speed = 1;
  const progress = new Map<string, Run>();

  const summary = (r: SnapRun): RunSummary => ({
    id: r.id, label: r.label, language: r.language, status: r.status, timeout_seconds: r.timeout_seconds,
    exit_code: r.exit_code, termination_reason: r.termination_reason, node: r.node, created_at: day,
    started_at: day, finished_at: r.duration_seconds === null ? null : new Date(Date.parse(day) + r.duration_seconds * 1000).toISOString(),
  });
  const full = (r: SnapRun): Run => ({ ...summary(r), code: r.code, stdout: r.stdout, stderr: r.stderr, truncated: r.truncated });
  const need = (id: string) => {
    const r = byId.get(id);
    if (!r) throw new Error(`no recorded run ${id}`);
    return r;
  };

  const banner: Banner = {
    text:
      `Recorded on ${snap.recorded_on} on ${snap.environment}. The cluster is offline by design (idle cost $0); ` +
      "this page cannot execute code. You are watching real recorded runs play back with their original timing.",
    links: [
      { label: "CI run", href: snap.links.ci },
      ...(snap.links.results ? [{ label: "results file", href: snap.links.results }] : []),
      { label: "source", href: snap.links.repo },
    ],
  };

  return {
    mode: "replay",
    canExecute: false,
    banner,
    snapshot: snap,
    setSpeed: (m) => { speed = m; },
    attackRun: (attackId) => snap.runs.find((r) => r.attack_id === attackId)?.id,
    submit: () => Promise.reject(new Error("replay mode cannot execute code")),
    cancel: () => Promise.resolve(),
    async get(id) {
      const r = need(id);
      return progress.get(id) ?? { ...full(r), status: "queued", exit_code: null, termination_reason: null, stdout: "", stderr: "", finished_at: null };
    },
    list: () => Promise.resolve(snap.runs.map(summary)),
    cluster: () => Promise.resolve(null),
    async stream(id, onEvent: (e: StreamEvent) => void, signal) {
      const r = need(id);
      let current: Run = { ...full(r), status: "queued", exit_code: null, termination_reason: null, stdout: "", stderr: "", finished_at: null };
      progress.set(id, current);
      const started = performance.now();
      for (const ev of r.events) {
        await sleep(started + (ev.t * 1000) / speed - performance.now(), signal);
        if (signal.aborted) return;
        if (ev.kind === "state" && ev.status) {
          current = { ...current, status: ev.status };
          progress.set(id, current);
          onEvent({ kind: "state", status: ev.status, ts: day });
        } else if (ev.text !== undefined && (ev.kind === "stdout" || ev.kind === "stderr")) {
          current = { ...current, [ev.kind]: current[ev.kind] + ev.text };
          progress.set(id, current);
          onEvent({ kind: ev.kind, text: ev.text, ts: day });
        }
      }
      progress.set(id, full(r)); // the recorded final record (exit code, reason, node, duration)
      onEvent({ kind: "end" });
    },
  };
}

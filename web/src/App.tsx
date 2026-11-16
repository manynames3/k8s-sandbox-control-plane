import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type Cluster, type DataSource, type Run, type RunSummary, type Status, type StreamEvent,
  durationSeconds, isTerminal,
} from "./api";
import { ATTACKS, HELLO, type Attack, type Language } from "./attacks";
import type { ReplaySource, Snapshot } from "./replay";

type Line = { kind: "stdout" | "stderr" | "state"; text: string };

const fmt = (n: number | null) => (n === null ? "–" : `${n.toFixed(1)}s`);
const num = (n: number | null) => (n === null ? "n/a" : n.toFixed(2));
const isReplay = (s: DataSource): s is ReplaySource => s.mode === "replay";

function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge s-${status}`}>{status}</span>;
}

function ClusterPanel({ cluster }: { cluster: Cluster | null }) {
  if (!cluster) return <section className="panel"><h2>Cluster</h2><p className="muted">no data</p></section>;
  const active = cluster.dispatching + cluster.running;
  return (
    <section className="panel">
      <h2>Cluster</h2>
      <dl className="kv">
        <dt>slots</dt>
        <dd>{active} / {cluster.concurrency_cap} used · {cluster.free_slots} free</dd>
        <dt>queue</dt>
        <dd>{cluster.queued} queued · {cluster.dispatching} dispatching · {cluster.running} running</dd>
      </dl>
      <div className="slots" aria-label="concurrency slots">
        {Array.from({ length: cluster.concurrency_cap }, (_, i) => (
          <span key={i} className={i < active ? "slot used" : "slot"} />
        ))}
      </div>
      <h3>Nodes</h3>
      {cluster.nodes.length === 0 && <p className="muted">no node snapshot yet</p>}
      <ul className="nodes">
        {cluster.nodes.map((n) => (
          <li key={n.name}>
            <span className="mono">{n.name}</span>
            <span className="muted"> {n.active_sandboxes} sandbox{n.active_sandboxes === 1 ? "" : "es"}</span>
          </li>
        ))}
      </ul>
      {cluster.snapshot_age_seconds !== null && cluster.snapshot_age_seconds > 10 && (
        <p className="warn">node view is {Math.round(cluster.snapshot_age_seconds)}s old: dispatcher not reporting</p>
      )}
    </section>
  );
}

/** Replay mode: the measured results, straight from the recorded results file. */
function ResultsPanel({ snap }: { snap: Snapshot }) {
  const b = snap.benchmark;
  if (!b) return <section className="panel"><h2>Measured results</h2><p className="muted">no benchmark recorded</p></section>;
  const mem = b.vm?.memory_bytes ? `${Math.round(b.vm.memory_bytes / 2 ** 30)} GiB` : "n/a";
  return (
    <section className="panel results">
      <h2>Measured results</h2>
      <p className="muted">
        {b.environment}, {b.date}, <span className="mono">{b.git_sha}</span>. Kubernetes {b.kubernetes_version},{" "}
        {b.container_runtime}, {b.worker_nodes} workers, cap {b.concurrency_cap}, VM {b.vm?.cpus ?? "n/a"} CPU / {mem}.
      </p>
      <table>
        <thead>
          <tr><th>in flight</th><th>runs</th><th>queue p50/p95</th><th>e2e p50/p95</th><th>runs/s</th></tr>
        </thead>
        <tbody>
          {b.levels.map((l) => (
            <tr key={l.concurrency}>
              <td>{l.concurrency}</td>
              <td>{l.statuses.succeeded ?? 0}/{l.sample_size}</td>
              <td>{num(l.queue_wait_seconds.p50)} / {num(l.queue_wait_seconds.p95)} s</td>
              <td>{num(l.e2e_seconds.p50)} / {num(l.e2e_seconds.p95)} s</td>
              <td>{l.throughput_runs_per_second}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">{b.workload}</p>
      <p className="muted">
        Results file:{" "}
        {snap.links.results ? <a href={snap.links.results} target="_blank" rel="noreferrer">{b.results_file}</a> : b.results_file}
      </p>
    </section>
  );
}

export default function App({ source, onKeyChange }: { source: DataSource; onKeyChange?: (k: string) => void }) {
  const replay = isReplay(source) ? source : null;
  const [language, setLanguage] = useState<Language>("python3.12");
  const [code, setCode] = useState(HELLO["python3.12"]);
  const [timeout, setTimeoutSeconds] = useState(30);
  const [expect, setExpect] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [cluster, setCluster] = useState<Cluster | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLPreElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [c, r] = await Promise.all([source.cluster(), source.list()]);
      setCluster(c);
      setRuns(r);
    } catch (e) {
      setError(String(e));
    }
  }, [source]);

  useEffect(() => {
    void refresh();
    if (replay) return; // recorded data does not change
    const t = setInterval(() => void refresh(), 2000);
    return () => clearInterval(t);
  }, [refresh, replay]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [lines]);

  const follow = useCallback(async (id: string) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setRunId(id);
    setLines([]);
    setRun(null);
    setError(null);
    setBusy(true);
    const onEvent = (e: StreamEvent) => {
      if (e.kind === "state") {
        setLines((l) => [...l, { kind: "state", text: `— ${e.status}` }]);
        setRun((r) => (r ? { ...r, status: e.status } : r));
        void source.get(id).then((r) => !ctl.signal.aborted && setRun(r)).catch(() => undefined);
      } else if (e.kind === "stdout" || e.kind === "stderr") {
        setLines((l) => [...l, { kind: e.kind, text: e.text }]);
      }
    };
    try {
      const first = await source.get(id);
      setRun(first);
      if (first.code) {
        setCode(first.code);
        setLanguage(first.language as Language);
      }
      await source.stream(id, onEvent, ctl.signal);
      if (!ctl.signal.aborted) setRun(await source.get(id));
    } catch (e) {
      if (!ctl.signal.aborted) setError(String(e));
    } finally {
      if (abort.current === ctl) setBusy(false);
      void refresh();
    }
  }, [source, refresh]);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      await follow(await source.submit(language, code, timeout));
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  };

  const loadAttack = (a: Attack) => {
    setLanguage(a.language);
    setCode(a.code);
    setTimeoutSeconds(a.timeout);
    setExpect(a.expect);
    if (replay) {
      const id = replay.attackRun(a.id);
      if (id) void follow(id);
    }
  };

  const status = run?.status;
  const running = status !== undefined && !isTerminal(status) && busy;
  const duration = useMemo(() => (run ? durationSeconds(run) : null), [run]);

  return (
    <div className="app">
      <header>
        <strong>ksc console</strong>
        <span className={`mode mode-${source.mode}`}>{source.mode}</span>
        {source.mode === "live" && onKeyChange && (
          <label className="key">
            API key
            <input
              type="password"
              placeholder="ksc_…"
              defaultValue={sessionStorage.getItem("ksc_key") ?? ""}
              onChange={(e) => onKeyChange(e.target.value)}
              autoComplete="off"
            />
          </label>
        )}
      </header>

      {source.banner && (
        <div className="banner" role="note">
          {source.banner.text}{" "}
          {source.banner.links?.map((l) => (
            <a key={l.href} href={l.href} target="_blank" rel="noreferrer">{l.label}</a>
          ))}
        </div>
      )}

      <main>
        <section className="panel editor">
          <h2>{replay ? "Recorded code" : "Code"}</h2>
          <div className="controls">
            {!replay && (
              <>
                <select
                  value={language}
                  onChange={(e) => {
                    const l = e.target.value as Language;
                    setLanguage(l);
                    setCode(HELLO[l]);
                    setExpect(null);
                  }}
                  aria-label="language"
                >
                  <option value="python3.12">python 3.12</option>
                  <option value="node20">node 20</option>
                </select>
                <label>
                  timeout
                  <input type="number" min={1} max={120} value={timeout}
                         onChange={(e) => setTimeoutSeconds(Number(e.target.value))} /> s
                </label>
                <button className="primary" disabled={busy || running} onClick={() => void submit()}>Run</button>
                <button disabled={!running || !runId} onClick={() => runId && void source.cancel(runId)}>Cancel</button>
              </>
            )}
            {replay && (
              <>
                <span className="muted">{language === "node20" ? "node 20" : "python 3.12"} · recorded, read-only</span>
                <label>
                  speed
                  <select defaultValue="1" onChange={(e) => replay.setSpeed(Number(e.target.value))} aria-label="speed">
                    <option value="1">1× (original timing)</option>
                    <option value="4">4×</option>
                    <option value="20">20×</option>
                  </select>
                </label>
                <button className="primary" disabled={!runId || busy} onClick={() => runId && void follow(runId)}>
                  Replay
                </button>
              </>
            )}
          </div>
          <textarea className="code mono" spellCheck={false} value={code} readOnly={!!replay}
                    onChange={(e) => setCode(e.target.value)} aria-label="code" />
          <div className="attacks">
            <span className="muted">{replay ? "Watch an attack:" : "Try an attack:"}</span>
            {ATTACKS.map((a) => (
              <button key={a.id} className="attack" onClick={() => loadAttack(a)} title={a.expect}>
                {a.label}
              </button>
            ))}
          </div>
          {expect && <p className="expect"><strong>Healthy outcome:</strong> {expect}</p>}
        </section>

        {replay ? <ResultsPanel snap={replay.snapshot} /> : <ClusterPanel cluster={cluster} />}
      </main>

      <section className="panel">
        <h2>
          Output {run && <StatusBadge status={run.status} />}
          {run?.truncated && <span className="warn"> truncated</span>}
        </h2>
        {run && (
          <dl className="kv inline">
            <dt>exit</dt><dd>{run.exit_code ?? "–"}</dd>
            <dt>reason</dt><dd>{run.termination_reason ?? "–"}</dd>
            <dt>duration</dt><dd>{fmt(duration)}</dd>
            <dt>node</dt><dd className="mono">{run.node ?? "–"}</dd>
            <dt>id</dt><dd className="mono">{run.id.slice(0, 8)}</dd>
          </dl>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <pre className="log mono" ref={logRef} aria-live="polite">
          {lines.length === 0 && <span className="muted">{replay ? "pick a recorded run below, or an attack above" : "no output yet"}</span>}
          {lines.map((l, i) => (
            <span key={i} className={`l-${l.kind}`}>{l.kind === "state" ? l.text + "\n" : l.text}</span>
          ))}
        </pre>
      </section>

      <section className="panel">
        <h2>{replay ? "Recorded runs" : "Recent runs"}</h2>
        <table>
          <thead>
            <tr>
              <th>{replay ? "run" : "id"}</th><th>lang</th><th>status</th><th>exit</th><th>time</th><th>node</th>
              {!replay && <th>created</th>}
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className={r.id === runId ? "sel" : ""} onClick={() => void follow(r.id)}>
                <td className={replay ? "" : "mono"}>{replay ? r.label : r.id.slice(0, 8)}</td>
                <td>{r.language}</td>
                <td><StatusBadge status={r.status} /></td>
                <td>{r.exit_code ?? "–"}</td>
                <td>{fmt(durationSeconds(r))}</td>
                <td className="mono">{r.node ?? "–"}</td>
                {!replay && <td>{new Date(r.created_at).toLocaleTimeString()}</td>}
              </tr>
            ))}
            {runs.length === 0 && <tr><td colSpan={7} className="muted">no runs yet</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  );
}

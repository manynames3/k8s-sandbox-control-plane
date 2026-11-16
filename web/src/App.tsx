import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  type Cluster, type DataSource, type Run, type RunSummary, type Status, type StreamEvent,
  durationSeconds, isTerminal,
} from "./api";
import { ATTACKS, HELLO, type Attack } from "./attacks";

type Line = { kind: "stdout" | "stderr" | "state"; text: string };
type Lang = "python3.12" | "node20";

const fmt = (n: number | null) => (n === null ? "–" : `${n.toFixed(1)}s`);

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

export default function App({ source, onKeyChange }: { source: DataSource; onKeyChange?: (k: string) => void }) {
  const [language, setLanguage] = useState<Lang>("python3.12");
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
    const t = setInterval(() => void refresh(), 2000);
    return () => clearInterval(t);
  }, [refresh]);

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
    const onEvent = (e: StreamEvent) => {
      if (e.kind === "state") {
        setLines((l) => [...l, { kind: "state", text: `— ${e.status}` }]);
        setRun((r) => (r ? { ...r, status: e.status } : r));
        void source.get(id).then(setRun).catch(() => undefined);
      } else if (e.kind === "stdout" || e.kind === "stderr") {
        setLines((l) => [...l, { kind: e.kind, text: e.text }]);
      }
    };
    try {
      setRun(await source.get(id));
      await source.stream(id, onEvent, ctl.signal);
      setRun(await source.get(id));
    } catch (e) {
      if (!ctl.signal.aborted) setError(String(e));
    } finally {
      setBusy(false);
      void refresh();
    }
  }, [source, refresh]);

  const submit = async (lang: Lang, src: string, secs: number) => {
    setError(null);
    setBusy(true);
    try {
      await follow(await source.submit(lang, src, secs));
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
  };

  const status = run?.status;
  const running = status !== undefined && !isTerminal(status);
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
          <h2>Code</h2>
          <div className="controls">
            <select
              value={language}
              onChange={(e) => {
                const l = e.target.value as Lang;
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
            <button className="primary" disabled={!source.canExecute || busy || running}
                    onClick={() => void submit(language, code, timeout)}>
              Run
            </button>
            <button disabled={!running || !runId} onClick={() => runId && void source.cancel(runId)}>
              Cancel
            </button>
            {!source.canExecute && <span className="muted">replay: this page cannot execute code</span>}
          </div>
          <textarea className="code mono" spellCheck={false} value={code}
                    onChange={(e) => setCode(e.target.value)} aria-label="code" />
          <div className="attacks">
            <span className="muted">Try an attack:</span>
            {ATTACKS.map((a) => (
              <button key={a.id} className="attack" onClick={() => loadAttack(a)} title={a.expect}>
                {a.label}
              </button>
            ))}
          </div>
          {expect && <p className="expect"><strong>Healthy outcome:</strong> {expect}</p>}
        </section>

        <ClusterPanel cluster={cluster} />
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
          {lines.length === 0 && <span className="muted">no output yet</span>}
          {lines.map((l, i) => (
            <span key={i} className={`l-${l.kind}`}>{l.kind === "state" ? l.text + "\n" : l.text}</span>
          ))}
        </pre>
      </section>

      <section className="panel">
        <h2>Recent runs</h2>
        <table>
          <thead>
            <tr><th>id</th><th>lang</th><th>status</th><th>exit</th><th>time</th><th>node</th><th>created</th></tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className={r.id === runId ? "sel" : ""} onClick={() => void follow(r.id)}>
                <td className="mono">{r.id.slice(0, 8)}</td>
                <td>{r.language}</td>
                <td><StatusBadge status={r.status} /></td>
                <td>{r.exit_code ?? "–"}</td>
                <td>{fmt(durationSeconds(r))}</td>
                <td className="mono">{r.node ?? "–"}</td>
                <td>{new Date(r.created_at).toLocaleTimeString()}</td>
              </tr>
            ))}
            {runs.length === 0 && <tr><td colSpan={7} className="muted">no runs yet</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  );
}

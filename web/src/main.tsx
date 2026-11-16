import { StrictMode, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { liveSource } from "./api";
import { replaySource, type Snapshot } from "./replay";
import "./styles.css";

// Build-time switch: the public site is built with VITE_MODE=replay and contains no API address.
const REPLAY = import.meta.env.VITE_MODE === "replay";
const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8080";

function Live() {
  const [key, setKey] = useState(() => sessionStorage.getItem("ksc_key") ?? "");
  const source = useMemo(() => liveSource(API_URL, key), [key]);
  return (
    <App
      key={key}
      source={source}
      onKeyChange={(k) => {
        sessionStorage.setItem("ksc_key", k);
        setKey(k);
      }}
    />
  );
}

function Replay() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetch(`${import.meta.env.BASE_URL}snapshot.json`)
      .then((r) => (r.ok ? (r.json() as Promise<Snapshot>) : Promise.reject(new Error(`snapshot: ${r.status}`))))
      .then(setSnap)
      .catch((e) => setErr(String(e)));
  }, []);
  const source = useMemo(() => (snap ? replaySource(snap) : null), [snap]);
  if (err) return <p className="error">{err}</p>;
  if (!source) return <p className="muted" style={{ padding: 16 }}>loading recorded runs…</p>;
  return <App source={source} />;
}

createRoot(document.getElementById("root")!).render(<StrictMode>{REPLAY ? <Replay /> : <Live />}</StrictMode>);

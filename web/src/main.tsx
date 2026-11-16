import { StrictMode, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { liveSource } from "./api";
import "./styles.css";

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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Live />
  </StrictMode>,
);

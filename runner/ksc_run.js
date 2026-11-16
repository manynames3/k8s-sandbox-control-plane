"use strict";
// Node twin of ksc_run.py: same framing, same caps, same exit codes (see ksc_run.py).
//   {"s":"out"|"err","b":"<base64>"}   user output
//   {"s":"meta","truncated":true,"timeout":true}
// Exit: child's exit code, or 124 on timeout.
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");

const MAX_STREAM_BYTES = 1024 * 1024;
const TIMEOUT_EXIT = 124;
const codeDir = process.env.KSC_CODE_DIR || "/code";
const timeoutMs = parseFloat(process.env.KSC_TIMEOUT_SECONDS || "30") * 1000;
const cmd = JSON.parse(process.env.KSC_COMMAND);

const emit = (obj) => fs.writeSync(1, JSON.stringify(obj) + "\n");
const stdinPath = path.join(codeDir, "stdin");
const stdinFd = fs.existsSync(stdinPath) ? fs.openSync(stdinPath, "r") : "ignore";

const child = spawn(cmd[0], cmd.slice(1), {
  stdio: [stdinFd, "pipe", "pipe"],
  detached: true, // own process group so the whole tree can be killed
  cwd: "/tmp",
});
if (stdinFd !== "ignore") fs.closeSync(stdinFd);

let truncated = false;
function pump(stream, tag) {
  let sent = 0;
  stream.on("data", (buf) => {
    const room = MAX_STREAM_BYTES - sent;
    if (room > 0) {
      const part = buf.subarray(0, room);
      emit({ s: tag, b: part.toString("base64") });
      sent += part.length;
    }
    if (buf.length > room && !truncated) {
      truncated = true;
      emit({ s: "meta", truncated: true });
    }
  });
}
pump(child.stdout, "out");
pump(child.stderr, "err");

let timedOut = false;
const timer = setTimeout(() => {
  timedOut = true;
  try { process.kill(-child.pid, "SIGKILL"); } catch (_) {}
}, timeoutMs);

child.on("close", (code, signal) => {
  clearTimeout(timer);
  try { process.kill(-child.pid, "SIGKILL"); } catch (_) {} // stragglers holding pipes
  if (timedOut) {
    emit({ s: "meta", timeout: true });
    process.exit(TIMEOUT_EXIT);
  }
  if (code === null) {
    const sigs = { SIGKILL: 9, SIGTERM: 15, SIGSEGV: 11, SIGABRT: 6 };
    process.exit(128 + (sigs[signal] || 1));
  }
  process.exit(code);
});

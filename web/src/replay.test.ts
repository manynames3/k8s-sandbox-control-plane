import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StreamEvent } from "./api";
import { replaySource, type Snapshot } from "./replay";

const snap: Snapshot = {
  version: 1, recorded_on: "2026-11-16", environment: "kind-on-mac", meta: {},
  links: { repo: "https://example.test/r", ci: "https://example.test/ci", results: "" },
  benchmark: null,
  runs: [
    {
      id: "aaaa1111", label: "Hello", kind: "state", attack_id: null, language: "python3.12", code: "print('hi')",
      timeout_seconds: 30, status: "succeeded", exit_code: 0, termination_reason: null, truncated: false,
      node: "worker-1", stdout: "hi\n", stderr: "", duration_seconds: 2,
      events: [
        { t: 0, kind: "state", status: "queued" },
        { t: 1, kind: "state", status: "running" },
        { t: 2, kind: "stdout", text: "hi\n" },
        { t: 3, kind: "state", status: "succeeded" },
      ],
    },
    {
      id: "bbbb2222", label: "Loop", kind: "attack", attack_id: "loop", language: "python3.12", timeout_seconds: 5,
      status: "timed_out", exit_code: 124, termination_reason: "timeout", truncated: false, node: "worker-2",
      stdout: "", stderr: "", duration_seconds: 5, events: [{ t: 0, kind: "state", status: "queued" }],
    },
  ],
};

describe("replaySource", () => {
  beforeEach(() => vi.useFakeTimers({ toFake: ["setTimeout", "performance"] }));
  afterEach(() => vi.useRealTimers());

  it("cannot execute anything", async () => {
    const src = replaySource(snap);
    expect(src.canExecute).toBe(false);
    await expect(src.submit("python3.12", "x", 5)).rejects.toThrow(/cannot execute/);
    expect(src.banner?.text).toMatch(/cannot execute code/);
    expect(src.banner?.text).toMatch(/2026-11-16/);
  });

  it("plays events back with the original timing", async () => {
    const src = replaySource(snap);
    const seen: { at: number; e: StreamEvent }[] = [];
    const t0 = performance.now();
    const done = src.stream("aaaa1111", (e) => seen.push({ at: Math.round(performance.now() - t0), e }), new AbortController().signal);
    await vi.advanceTimersByTimeAsync(3500);
    await done;
    expect(seen.map((s) => [s.at, s.e.kind])).toEqual([[0, "state"], [1000, "state"], [2000, "stdout"], [3000, "state"], [3000, "end"]]);
  });

  it("reflects progress through get() and ends on the recorded record", async () => {
    const src = replaySource(snap);
    const done = src.stream("aaaa1111", () => undefined, new AbortController().signal);
    await vi.advanceTimersByTimeAsync(1500);
    expect((await src.get("aaaa1111")).status).toBe("running");
    await vi.advanceTimersByTimeAsync(2000);
    await done;
    const final = await src.get("aaaa1111");
    expect(final).toMatchObject({ status: "succeeded", exit_code: 0, stdout: "hi\n", node: "worker-1" });
  });

  it("speed multiplier shortens playback and abort stops it", async () => {
    const src = replaySource(snap);
    src.setSpeed(10);
    const seen: StreamEvent[] = [];
    const done = src.stream("aaaa1111", (e) => seen.push(e), new AbortController().signal);
    await vi.advanceTimersByTimeAsync(400);
    await done;
    expect(seen.at(-1)).toEqual({ kind: "end" });

    src.setSpeed(1);
    const ctl = new AbortController();
    const got: StreamEvent[] = [];
    const p = src.stream("aaaa1111", (e) => got.push(e), ctl.signal);
    await vi.advanceTimersByTimeAsync(1500);
    ctl.abort();
    await vi.advanceTimersByTimeAsync(5000);
    await p;
    expect(got.some((e) => e.kind === "end")).toBe(false);
  });

  it("maps an attack id to its recorded run and lists summaries", async () => {
    const src = replaySource(snap);
    expect(src.attackRun("loop")).toBe("bbbb2222");
    expect(src.attackRun("nope")).toBeUndefined();
    expect((await src.list()).map((r) => r.label)).toEqual(["Hello", "Loop"]);
    expect(await src.cluster()).toBeNull();
  });
});

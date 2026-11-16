import { describe, expect, it } from "vitest";
import { createSseParser } from "./sse";

describe("createSseParser", () => {
  it("parses events split across chunks", () => {
    const parse = createSseParser();
    expect(parse("id: 1\nevent: stdout\nda")).toEqual([]);
    expect(parse('ta: {"text":"hi"}\n\n')).toEqual([{ id: "1", event: "stdout", data: '{"text":"hi"}' }]);
  });
  it("ignores keep-alive comments and handles CRLF", () => {
    const parse = createSseParser();
    expect(parse(": keep-alive\n\n")).toEqual([]);
    expect(parse("event: end\r\ndata: {}\r\n\r\n")).toEqual([{ id: undefined, event: "end", data: "{}" }]);
  });
  it("returns several events from one chunk", () => {
    const parse = createSseParser();
    const evs = parse("event: state\ndata: {}\n\nevent: stdout\ndata: {}\n\n");
    expect(evs.map((e) => e.event)).toEqual(["state", "stdout"]);
  });
});

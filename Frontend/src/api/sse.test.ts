import { describe, expect, it } from "vitest";

import { SseParser } from "./sse";

describe("SseParser", () => {
  it("parses complete events", () => {
    const parser = new SseParser();
    const events = parser.push('event: token\ndata: {"text":"Hi"}\n\nevent: done\ndata: {}\n\n');
    expect(events).toEqual([
      { event: "token", data: { text: "Hi" } },
      { event: "done", data: {} },
    ]);
  });

  it("buffers events split across chunks", () => {
    const parser = new SseParser();
    expect(parser.push('event: token\ndata: {"te')).toEqual([]);
    expect(parser.push('xt":"lo"}\n')).toEqual([]);
    expect(parser.push("\n")).toEqual([{ event: "token", data: { text: "lo" } }]);
  });

  it("handles CRLF line endings and skips malformed data", () => {
    const parser = new SseParser();
    const events = parser.push("event: token\r\ndata: not-json\r\n\r\nevent: done\r\ndata: {}\r\n\r\n");
    expect(events).toEqual([{ event: "done", data: {} }]);
  });
});

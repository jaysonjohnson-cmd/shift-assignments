import { describe, expect, it, vi } from "vitest";
import {
  CLOSE_MY_ASSIGNMENTS_URL,
  CLOSE_WINDOW_MS,
  closeFieldAgentAssignments,
} from "./reviewLink";

describe("closeFieldAgentAssignments", () => {
  it("opens FieldAgent's close page in a small window and closes it after the delay", () => {
    const win = { close: vi.fn(), opener: {} as unknown };
    const open = vi.fn(() => win as unknown as Window);
    const timers: Array<[() => void, number]> = [];
    const ok = closeFieldAgentAssignments(open, (fn, ms) => timers.push([fn, ms]));

    expect(ok).toBe(true);
    expect(open).toHaveBeenCalledWith(
      CLOSE_MY_ASSIGNMENTS_URL, "fa-close-assignments", expect.stringContaining("popup"));
    expect(win.opener).toBeNull();
    expect(win.close).not.toHaveBeenCalled();
    expect(timers[0][1]).toBe(CLOSE_WINDOW_MS);
    timers[0][0]();
    expect(win.close).toHaveBeenCalledOnce();
  });

  it("reports a blocked window so the caller can fall back to the link", () => {
    const later = vi.fn();
    expect(closeFieldAgentAssignments(() => null, later)).toBe(false);
    expect(later).not.toHaveBeenCalled();
  });
});

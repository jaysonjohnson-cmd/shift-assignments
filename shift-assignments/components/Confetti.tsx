"use client";

import { useEffect, useRef } from "react";

/**
 * A one-shot confetti burst rendered on a full-screen <canvas>. Dependency-free
 * (no npm package), self-cleaning, and skipped entirely when the user prefers
 * reduced motion. Mount it to fire; it animates for `durationMs` then clears.
 */

const COLORS = [
  "#7554c2", // purple
  "#9c5cff", // violet
  "#c386ff", // light violet
  "#3b82f6", // blue
  "#00b8a3", // teal
  "#16a34a", // green
  "#ffa450", // amber
  "#e0457b", // pink
];

type Piece = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  rot: number;
  vr: number;
  size: number;
  color: string;
  round: boolean;
};

export function Confetti({
  count = 170,
  durationMs = 3400,
}: {
  count?: number;
  durationMs?: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return;

    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = canvas.offsetWidth * dpr;
    canvas.height = canvas.offsetHeight * dpr;
    const W = canvas.width;
    const H = canvas.height;

    // Burst from two lower corners arcing inward, plus a center fountain —
    // reads as a "party popper" rather than a flat rain.
    const pieces: Piece[] = [];
    for (let i = 0; i < count; i++) {
      const fromLeft = i % 3 === 0;
      const fromRight = i % 3 === 1;
      const originX = fromLeft ? 0 : fromRight ? W : W * 0.5;
      const aim = fromLeft ? 1 : fromRight ? -1 : (Math.random() - 0.5) * 2;
      pieces.push({
        x: originX,
        y: H * (fromLeft || fromRight ? 0.85 : 0.5),
        vx: (aim * (Math.random() * 5 + 3) + (Math.random() - 0.5) * 3) * dpr,
        vy: -(Math.random() * 9 + 8) * dpr,
        rot: Math.random() * Math.PI * 2,
        vr: (Math.random() - 0.5) * 0.4,
        size: (Math.random() * 6 + 5) * dpr,
        color: COLORS[i % COLORS.length],
        round: Math.random() > 0.55,
      });
    }

    const gravity = 0.32 * dpr;
    const drag = 0.992;
    let raf = 0;
    let start = 0;

    const tick = (now: number) => {
      if (!start) start = now;
      const elapsed = now - start;
      ctx.clearRect(0, 0, W, H);
      // Fade the whole field out over the last 900ms.
      ctx.globalAlpha =
        elapsed > durationMs - 900
          ? Math.max(0, (durationMs - elapsed) / 900)
          : 1;

      for (const p of pieces) {
        p.vy += gravity;
        p.vx *= drag;
        p.x += p.vx;
        p.y += p.vy;
        p.rot += p.vr;

        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.rot);
        ctx.fillStyle = p.color;
        if (p.round) {
          ctx.beginPath();
          ctx.arc(0, 0, p.size / 2, 0, Math.PI * 2);
          ctx.fill();
        } else {
          ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        }
        ctx.restore();
      }

      if (elapsed < durationMs) {
        raf = requestAnimationFrame(tick);
      } else {
        ctx.clearRect(0, 0, W, H);
      }
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [count, durationMs]);

  return (
    <canvas
      ref={ref}
      aria-hidden
      className="pointer-events-none fixed inset-0 z-50 h-full w-full"
    />
  );
}

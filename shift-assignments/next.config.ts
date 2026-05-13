import type { NextConfig } from "next";

/**
 * Production build is a static export that the Flask container serves from
 * `/app/frontend/`. Every page in this app is `"use client"` — no server
 * components — so `output: "export"` is safe. `images.unoptimized` is required
 * because static export has no image optimizer.
 *
 * Dev-only `/api/*` and `/logout` rewrites proxy to the Flask app at
 * :8080 when running `next dev`. Rewrites are ignored by static export; in
 * production Next and Flask share the same origin (the Cloud Run service), so
 * relative `/api/*` fetches from the browser hit Flask directly.
 */
const API_ORIGIN = process.env.NEXT_PUBLIC_API_ORIGIN ?? "http://localhost:8080";

const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_ORIGIN}/api/:path*`,
      },
      {
        source: "/logout",
        destination: `${API_ORIGIN}/logout`,
      },
    ];
  },
};

export default nextConfig;

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  // NEXT_DIST_DIR lets a verification build run without touching the .next used by a running `next start`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

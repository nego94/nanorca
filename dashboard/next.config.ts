import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Allow API routes to access internal Docker services
  experimental: {
    serverComponentsExternalPackages: ["postgres"],
  },
};

export default nextConfig;

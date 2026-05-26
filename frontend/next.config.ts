import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["192.168.101.27"],

  async rewrites() {
    return [
      {
        source: "/api/embedder/:path*",
        destination: "http://localhost:8001/:path*",
      },
    ];
  },
};

export default nextConfig;

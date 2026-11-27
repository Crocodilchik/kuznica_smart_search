import path from "node:path";
import { existsSync } from "node:fs";
import { loadEnvFile } from "node:process";
import type { NextConfig } from "next";

const projectRoot = path.resolve(process.cwd(), "..");
const rootEnvFile = path.join(projectRoot, ".env");
if (existsSync(rootEnvFile)) loadEnvFile(rootEnvFile);

const nextConfig: NextConfig = {
  turbopack: {
    root: projectRoot,
  },
};

export default nextConfig;

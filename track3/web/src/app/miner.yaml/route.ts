import { readFile } from "node:fs/promises";
import { join } from "node:path";

export const dynamic = "force-dynamic";

export async function GET() {
  const manifest = await readFile(join(process.cwd(), "config", "miner.yaml"), "utf8");
  return new Response(manifest, {
    headers: {
      "Content-Type": "application/yaml; charset=utf-8",
      "Cache-Control": "public, max-age=300, must-revalidate",
    },
  });
}

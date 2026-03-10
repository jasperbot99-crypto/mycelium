import { MyceliumHttpClient } from "../src/index.js";

export async function rememberObservation(agentId: string, observation: string): Promise<void> {
  const client = new MyceliumHttpClient({
    baseUrl: process.env.MYCELIUM_URL ?? "http://127.0.0.1:8080",
    apiKey: process.env.MYCELIUM_API_KEY ?? "dev-key",
    timeoutMs: 10_000,
    retries: 1,
  });

  await client.connect({ agent_id: agentId, role: "openclaw" });
  await client.ingest(agentId, {
    content: {
      subject: "runtime-observation",
      predicate: "notes",
      object: observation,
    },
    source_type: "agent_extraction",
    tags: ["runtime", "openclaw"],
  });
}

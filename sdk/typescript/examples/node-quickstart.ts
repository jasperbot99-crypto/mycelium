import { MyceliumHttpClient } from "../src/index.js";

const client = new MyceliumHttpClient({
  baseUrl: process.env.MYCELIUM_URL ?? "http://127.0.0.1:8080",
  apiKey: process.env.MYCELIUM_API_KEY ?? "dev-key",
});

async function main(): Promise<void> {
  await client.connect({ agent_id: "example-node", role: "demo" });

  await client.ingest("example-node", {
    content: { subject: "api-orders", predicate: "has_status", object: "healthy" },
    source_type: "agent_extraction",
    tags: ["api.orders"],
  });

  const results = await client.query("example-node", "api orders health");
  console.log(results[0]?.fact.content);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

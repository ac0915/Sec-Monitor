import process from "node:process";
import { CopilotClient } from "@github/copilot-sdk";

async function main() {
  const client = new CopilotClient();
  await client.start();
  try {
    const auth = await client.getAuthStatus();
    const models = await client.listModels();
    process.stdout.write(
      JSON.stringify({
        auth,
        models: models.map((model) => ({
          id: model.id,
          name: model.name,
          capabilities: Object.entries(model.capabilities?.supports || {})
            .filter(([, value]) => Boolean(value))
            .map(([key]) => key),
          supportedReasoningEfforts: model.supportedReasoningEfforts || [],
        })),
      })
    );
  } finally {
    await client.stop();
  }
}

main().catch((error) => {
  process.stderr.write(String(error?.stack || error?.message || error));
  process.exit(1);
});

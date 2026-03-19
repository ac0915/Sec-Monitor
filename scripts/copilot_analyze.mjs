import process from "node:process";
import { CopilotClient } from "@github/copilot-sdk";

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

async function main() {
  const raw = await readStdin();
  const input = JSON.parse(raw || "{}");

  if (!input.prompt || !input.model) {
    throw new Error("Expected JSON stdin with 'prompt' and 'model'.");
  }

  const client = new CopilotClient();
  await client.start();
  try {
    const session = await client.createSession({ model: input.model });
    let latestUsage = null;
    session.on("assistant.usage", (event) => {
      latestUsage = event?.data || null;
    });

    const response = await session.sendAndWait({ prompt: input.prompt });
    const content = response?.data?.content ?? "";
    process.stdout.write(
      JSON.stringify({
        content,
        response: response?.data ?? null,
        usage: latestUsage,
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

import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        miniflare: {
          compatibilityDate: "2026-06-01",
          compatibilityFlags: ["nodejs_compat"],
          kvNamespaces: ["DECISIONS"],
          bindings: {
            WEBHOOK_SECRET: "test-webhook-secret",
            DECISIONS_API_SECRET: "test-api-secret",
            TELEGRAM_BOT_TOKEN: "test-bot-token",
          },
        },
      },
    },
  },
});

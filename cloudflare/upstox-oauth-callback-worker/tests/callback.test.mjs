import assert from "node:assert/strict";
import { mock, test } from "node:test";
import worker from "../src/index.ts";

for (const path of ["/oauth/callback", "/oauth/schwab/callback"]) {
  test(`${path} catches asynchronous callback failures`, async () => {
    const errorLog = mock.method(console, "error", () => {});
    try {
      const env = {
        RUNTIME_STATE: { get: async () => { throw new Error("KV unavailable"); } },
      };
      const response = await worker.fetch(
        new Request(`https://callback.example${path}?state=legacy-state&code=code`), env,
      );
      assert.equal(response.status, 500);
      assert.equal(await response.text(), "OAuth callback failed.");
      assert.equal(errorLog.mock.callCount(), 1);
    } finally {
      errorLog.mock.restore();
    }
  });
}

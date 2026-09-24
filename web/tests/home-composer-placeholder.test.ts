import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const chatWorkspacePath = path.join(
  process.cwd(),
  "features",
  "chat",
  "components",
  "ChatWorkspace.tsx",
);

const sessionApiPath = path.join(process.cwd(), "lib", "session-api.ts");

test("the home composer uses product copy instead of model-generated placeholder text", () => {
  const workspace = fs.readFileSync(chatWorkspacePath, "utf8");
  const sessionApi = fs.readFileSync(sessionApiPath, "utf8");

  assert.doesNotMatch(workspace, /fetchSessionAskHint|inputPlaceholderCompletion=\{askHint\}/);
  assert.doesNotMatch(sessionApi, /\/ask-hint/);
});

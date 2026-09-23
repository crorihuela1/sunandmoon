/**
 * One-time OAuth bootstrap. Run on your Mac/PC (needs a browser):
 *   npm run auth
 *
 * Opens a browser, you sign in with the Sun & Moon Workspace account,
 * and the refresh token is written to token.json. Because the OAuth
 * consent screen is set to "Internal", this token does not expire on
 * the 7-day testing timeline that plagues personal-Gmail setups.
 */
import { authenticate } from "@google-cloud/local-auth";
import * as fs from "node:fs";
import { CREDENTIALS_PATH, TOKEN_PATH, SCOPES } from "./gmail.js";

async function main(): Promise<void> {
  if (!fs.existsSync(CREDENTIALS_PATH)) {
    console.error(`credentials.json not found at ${CREDENTIALS_PATH}`);
    console.error(
      "Download it from Google Cloud Console → APIs & Services → Credentials → your Desktop-app OAuth client."
    );
    process.exit(1);
  }

  const client = await authenticate({ scopes: SCOPES, keyfilePath: CREDENTIALS_PATH });

  if (!client.credentials.refresh_token) {
    console.warn(
      "Warning: no refresh_token returned. Revoke the app at myaccount.google.com/permissions and run auth again."
    );
  }

  fs.writeFileSync(TOKEN_PATH, JSON.stringify(client.credentials, null, 2));
  console.log(`Token saved to ${TOKEN_PATH}. You're authorized — the MCP server is ready to run.`);
}

main().catch((err) => {
  console.error("Authorization failed:", err);
  process.exit(1);
});

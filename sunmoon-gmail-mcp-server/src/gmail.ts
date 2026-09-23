import { google, gmail_v1 } from "googleapis";
import { OAuth2Client } from "google-auth-library";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Paths are resolved relative to the project root (one level up from dist/)
export const PROJECT_ROOT = path.resolve(__dirname, "..");
export const CREDENTIALS_PATH =
  process.env.GMAIL_CREDENTIALS_PATH ?? path.join(PROJECT_ROOT, "credentials.json");
export const TOKEN_PATH =
  process.env.GMAIL_TOKEN_PATH ?? path.join(PROJECT_ROOT, "token.json");

// Both scopes together cover: search/read, labels, drafts, and send.
export const SCOPES = [
  "https://www.googleapis.com/auth/gmail.modify",
  "https://www.googleapis.com/auth/gmail.compose",
];

export function loadAuthorizedClient(): OAuth2Client {
  if (!fs.existsSync(CREDENTIALS_PATH)) {
    throw new Error(
      `Missing credentials.json at ${CREDENTIALS_PATH}. Download the OAuth Desktop-app client from Google Cloud Console and place it there (or set GMAIL_CREDENTIALS_PATH).`
    );
  }
  if (!fs.existsSync(TOKEN_PATH)) {
    throw new Error(
      `Missing token.json at ${TOKEN_PATH}. Run "npm run auth" once (in a browser-capable environment) to authorize the Sun & Moon account.`
    );
  }

  const creds = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, "utf-8"));
  const key = creds.installed ?? creds.web;
  const client = new google.auth.OAuth2(key.client_id, key.client_secret, key.redirect_uris?.[0]);
  client.setCredentials(JSON.parse(fs.readFileSync(TOKEN_PATH, "utf-8")));

  // Persist refreshed tokens so the refresh token is never lost.
  client.on("tokens", (tokens) => {
    try {
      const existing = JSON.parse(fs.readFileSync(TOKEN_PATH, "utf-8"));
      fs.writeFileSync(TOKEN_PATH, JSON.stringify({ ...existing, ...tokens }, null, 2));
    } catch {
      /* non-fatal */
    }
  });

  return client;
}

export function gmailClient(): gmail_v1.Gmail {
  return google.gmail({ version: "v1", auth: loadAuthorizedClient() });
}

// ---------- Message parsing ----------

export interface ParsedEmail {
  id: string;
  threadId: string;
  labelIds: string[];
  from: string;
  to: string;
  cc?: string;
  subject: string;
  date: string;
  snippet: string;
  body: string;
  messageIdHeader?: string;
  referencesHeader?: string;
}

function header(payload: gmail_v1.Schema$MessagePart | undefined, name: string): string {
  return (
    payload?.headers?.find((h) => h.name?.toLowerCase() === name.toLowerCase())?.value ?? ""
  );
}

function decodeBody(data?: string | null): string {
  if (!data) return "";
  return Buffer.from(data, "base64url").toString("utf-8");
}

/** Walk MIME parts, preferring text/plain, falling back to stripped text/html. */
function extractBody(payload?: gmail_v1.Schema$MessagePart): string {
  if (!payload) return "";
  if (payload.mimeType === "text/plain" && payload.body?.data) {
    return decodeBody(payload.body.data);
  }
  if (payload.parts?.length) {
    const plain = findPart(payload, "text/plain");
    if (plain?.body?.data) return decodeBody(plain.body.data);
    const html = findPart(payload, "text/html");
    if (html?.body?.data) return stripHtml(decodeBody(html.body.data));
  }
  if (payload.mimeType === "text/html" && payload.body?.data) {
    return stripHtml(decodeBody(payload.body.data));
  }
  return "";
}

function findPart(
  part: gmail_v1.Schema$MessagePart,
  mimeType: string
): gmail_v1.Schema$MessagePart | undefined {
  if (part.mimeType === mimeType) return part;
  for (const p of part.parts ?? []) {
    const found = findPart(p, mimeType);
    if (found) return found;
  }
  return undefined;
}

function stripHtml(html: string): string {
  return html
    .replace(/<style[\s\S]*?<\/style>/gi, "")
    .replace(/<script[\s\S]*?<\/script>/gi, "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/(p|div|tr|li|h[1-6])>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function parseMessage(msg: gmail_v1.Schema$Message): ParsedEmail {
  const p = msg.payload;
  return {
    id: msg.id ?? "",
    threadId: msg.threadId ?? "",
    labelIds: msg.labelIds ?? [],
    from: header(p, "From"),
    to: header(p, "To"),
    cc: header(p, "Cc") || undefined,
    subject: header(p, "Subject"),
    date: header(p, "Date"),
    snippet: msg.snippet ?? "",
    body: extractBody(p),
    messageIdHeader: header(p, "Message-ID") || undefined,
    referencesHeader: header(p, "References") || undefined,
  };
}

// ---------- RFC 2822 message building ----------

export interface OutgoingMessage {
  to: string;
  cc?: string;
  bcc?: string;
  subject: string;
  body: string;
  inReplyTo?: string; // Message-ID header of the message being replied to
  references?: string;
}

/** Encode a header value as RFC 2047 if it contains non-ASCII characters (e.g. Spanish accents). */
function encodeHeader(value: string): string {
  // eslint-disable-next-line no-control-regex
  if (/^[\x00-\x7F]*$/.test(value)) return value;
  return `=?UTF-8?B?${Buffer.from(value, "utf-8").toString("base64")}?=`;
}

export function buildRawMessage(m: OutgoingMessage): string {
  const lines = [
    `To: ${m.to}`,
    ...(m.cc ? [`Cc: ${m.cc}`] : []),
    ...(m.bcc ? [`Bcc: ${m.bcc}`] : []),
    `Subject: ${encodeHeader(m.subject)}`,
    ...(m.inReplyTo ? [`In-Reply-To: ${m.inReplyTo}`] : []),
    ...(m.references ? [`References: ${m.references}`] : []),
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="UTF-8"',
    "Content-Transfer-Encoding: base64",
    "",
    Buffer.from(m.body, "utf-8").toString("base64"),
  ];
  return Buffer.from(lines.join("\r\n")).toString("base64url");
}

/** Extract bare email addresses from a header like: 'Mindy C <mindy@vrn.com>, x@y.com' */
export function extractAddresses(headerValue: string): string[] {
  const matches = headerValue.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g);
  return (matches ?? []).map((a) => a.toLowerCase());
}

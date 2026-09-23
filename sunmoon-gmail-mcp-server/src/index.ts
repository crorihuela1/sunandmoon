#!/usr/bin/env node
/**
 * sunmoon-gmail-mcp-server
 *
 * Gives Claude (Desktop / Code) full visibility into the Sun & Moon 30A
 * Workspace Gmail account: search, read, label, draft, and guardrailed send.
 *
 * Send tiers ("overlap" model):
 *   - allowlist recipients        → auto-send OK
 *   - replies in existing threads → auto-send OK (configurable)
 *   - anything else / sensitive   → requires owner_approved=true, which the
 *     agent may only set after the human explicitly approves in conversation.
 * Every send is appended to a JSONL audit log and rate-capped per hour.
 */
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import {
  gmailClient,
  parseMessage,
  buildRawMessage,
  extractAddresses,
  type ParsedEmail,
} from "./gmail.js";
import {
  loadGuardrails,
  checkSend,
  logSend,
  recipientsFromFields,
} from "./guardrails.js";

const CHARACTER_LIMIT = 40_000;

const server = new McpServer({
  name: "sunmoon-gmail-mcp-server",
  version: "1.0.0",
});

function ok(payload: unknown) {
  let text = JSON.stringify(payload, null, 2);
  if (text.length > CHARACTER_LIMIT) {
    text =
      text.slice(0, CHARACTER_LIMIT) +
      `\n... [truncated at ${CHARACTER_LIMIT} chars — narrow the query or lower max_results]`;
  }
  return { content: [{ type: "text" as const, text }] };
}

function fail(message: string) {
  return { content: [{ type: "text" as const, text: `Error: ${message}` }], isError: true };
}

function errMsg(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

function emailSummary(m: ParsedEmail) {
  return {
    id: m.id,
    threadId: m.threadId,
    from: m.from,
    to: m.to,
    subject: m.subject,
    date: m.date,
    snippet: m.snippet,
    labelIds: m.labelIds,
  };
}

// ---------------------------------------------------------------- search
server.registerTool(
  "gmail_search_emails",
  {
    title: "Search Sun & Moon Emails",
    description: `Search the Sun & Moon inbox using standard Gmail query syntax.

Args:
  - query (string): Gmail search query, e.g. 'from:@vacayrentalnetwork.com newer_than:7d', 'is:unread', 'subject:"booking inquiry"', 'label:guests has:attachment'
  - max_results (number, 1-50, default 15)

Returns: JSON array of message summaries {id, threadId, from, to, subject, date, snippet, labelIds}.
Use gmail_read_email or gmail_read_thread with the returned ids for full bodies.`,
    inputSchema: {
      query: z.string().min(1).max(500).describe("Gmail search query syntax"),
      max_results: z.number().int().min(1).max(50).default(15),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  },
  async ({ query, max_results }) => {
    try {
      const gmail = gmailClient();
      const list = await gmail.users.messages.list({ userId: "me", q: query, maxResults: max_results });
      const ids = list.data.messages ?? [];
      if (ids.length === 0) return ok({ total: 0, results: [], note: `No messages matched '${query}'` });

      const results = await Promise.all(
        ids.map(async ({ id }) => {
          const msg = await gmail.users.messages.get({ userId: "me", id: id!, format: "metadata", metadataHeaders: ["From", "To", "Subject", "Date"] });
          return emailSummary(parseMessage(msg.data));
        })
      );
      return ok({ total: results.length, estimatedTotal: list.data.resultSizeEstimate, results });
    } catch (e) {
      return fail(errMsg(e));
    }
  }
);

// ---------------------------------------------------------------- read one
server.registerTool(
  "gmail_read_email",
  {
    title: "Read Email",
    description: `Fetch the full content of one email by message id (from gmail_search_emails).

Returns: {id, threadId, from, to, cc, subject, date, body, labelIds, messageIdHeader}. Body is plain text (HTML is stripped).`,
    inputSchema: {
      message_id: z.string().min(1).describe("Gmail message id"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  },
  async ({ message_id }) => {
    try {
      const gmail = gmailClient();
      const msg = await gmail.users.messages.get({ userId: "me", id: message_id, format: "full" });
      return ok(parseMessage(msg.data));
    } catch (e) {
      return fail(errMsg(e));
    }
  }
);

// ---------------------------------------------------------------- read thread
server.registerTool(
  "gmail_read_thread",
  {
    title: "Read Thread",
    description: `Fetch every message in a conversation thread in chronological order. Use before drafting a reply so the response has full context.

Returns: {threadId, messageCount, messages: [{id, from, to, subject, date, body}...]}.`,
    inputSchema: {
      thread_id: z.string().min(1).describe("Gmail thread id"),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  },
  async ({ thread_id }) => {
    try {
      const gmail = gmailClient();
      const thread = await gmail.users.threads.get({ userId: "me", id: thread_id, format: "full" });
      const messages = (thread.data.messages ?? []).map((m) => parseMessage(m));
      return ok({ threadId: thread_id, messageCount: messages.length, messages });
    } catch (e) {
      return fail(errMsg(e));
    }
  }
);

// ---------------------------------------------------------------- labels
server.registerTool(
  "gmail_list_labels",
  {
    title: "List Labels",
    description: "List all labels in the Sun & Moon account with their ids (needed for gmail_modify_labels and label: search filters).",
    inputSchema: {},
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  },
  async () => {
    try {
      const gmail = gmailClient();
      const res = await gmail.users.labels.list({ userId: "me" });
      return ok((res.data.labels ?? []).map((l) => ({ id: l.id, name: l.name, type: l.type })));
    } catch (e) {
      return fail(errMsg(e));
    }
  }
);

server.registerTool(
  "gmail_modify_labels",
  {
    title: "Modify Labels",
    description: `Add and/or remove labels on a message — used for triage (e.g. add 'Guests/Inquiry', remove 'UNREAD' to mark read, add 'TRASH' is NOT supported here).

Args:
  - message_id (string)
  - add_label_ids (string[]): label IDs to add (from gmail_list_labels)
  - remove_label_ids (string[]): label IDs to remove (e.g. 'UNREAD')`,
    inputSchema: {
      message_id: z.string().min(1),
      add_label_ids: z.array(z.string()).default([]),
      remove_label_ids: z.array(z.string()).default([]),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: true },
  },
  async ({ message_id, add_label_ids, remove_label_ids }) => {
    try {
      if (add_label_ids.includes("TRASH") || remove_label_ids.includes("INBOX")) {
        return fail("Trashing/archiving via this tool is disabled by policy. Ask the owner to do it in Gmail.");
      }
      const gmail = gmailClient();
      const res = await gmail.users.messages.modify({
        userId: "me",
        id: message_id,
        requestBody: { addLabelIds: add_label_ids, removeLabelIds: remove_label_ids },
      });
      return ok({ id: res.data.id, labelIds: res.data.labelIds });
    } catch (e) {
      return fail(errMsg(e));
    }
  }
);

// ---------------------------------------------------------------- drafts
server.registerTool(
  "gmail_create_draft",
  {
    title: "Create Draft",
    description: `Create a draft in the Sun & Moon account. If reply_to_message_id is provided, the draft is threaded as a reply (correct In-Reply-To/References headers and recipient are derived automatically; 'to' may be omitted).

This is the SAFE default for anything the guardrails would block — the owner reviews it in Gmail's Drafts folder or sends it later via gmail_send_draft.

Returns: {draftId, messageId, threadId, to, subject}.`,
    inputSchema: {
      to: z.string().optional().describe("Recipient(s), comma-separated. Optional when replying."),
      cc: z.string().optional(),
      subject: z.string().optional().describe("Optional when replying (Re: is derived)."),
      body: z.string().min(1).describe("Plain-text body"),
      reply_to_message_id: z.string().optional().describe("Message id being replied to, if this is a reply"),
    },
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: true },
  },
  async ({ to, cc, subject, body, reply_to_message_id }) => {
    try {
      const gmail = gmailClient();
      let threadId: string | undefined;
      let finalTo = to;
      let finalSubject = subject;
      let inReplyTo: string | undefined;
      let references: string | undefined;

      if (reply_to_message_id) {
        const orig = parseMessage(
          (await gmail.users.messages.get({ userId: "me", id: reply_to_message_id, format: "full" })).data
        );
        threadId = orig.threadId;
        finalTo = finalTo ?? extractAddresses(orig.from).join(", ");
        finalSubject = finalSubject ?? (orig.subject.startsWith("Re:") ? orig.subject : `Re: ${orig.subject}`);
        inReplyTo = orig.messageIdHeader;
        references = [orig.referencesHeader, orig.messageIdHeader].filter(Boolean).join(" ") || undefined;
      }

      if (!finalTo) return fail("'to' is required when not replying to an existing message.");
      if (!finalSubject) return fail("'subject' is required when not replying to an existing message.");

      const raw = buildRawMessage({ to: finalTo, cc, subject: finalSubject, body, inReplyTo, references });
      const res = await gmail.users.drafts.create({
        userId: "me",
        requestBody: { message: { raw, threadId } },
      });
      return ok({
        draftId: res.data.id,
        messageId: res.data.message?.id,
        threadId: res.data.message?.threadId,
        to: finalTo,
        subject: finalSubject,
        note: "Draft created. Owner can review in Gmail, or send with gmail_send_draft after approval.",
      });
    } catch (e) {
      return fail(errMsg(e));
    }
  }
);

// ---------------------------------------------------------------- send (guardrailed)
const sendGuardrailDoc = `Guardrails (enforced server-side):
  - Recipients on the allowlist (guardrails.json) → sends immediately.
  - Replies within an existing thread → sends immediately (if allowRepliesInThread).
  - Anything else, or bodies containing sensitive keywords → BLOCKED unless owner_approved=true.
  - owner_approved=true may ONLY be set after the human owner explicitly approves the exact message in conversation. Never set it unilaterally.
  - All sends are rate-capped per hour and appended to logs/send-audit.jsonl.`;

server.registerTool(
  "gmail_send_email",
  {
    title: "Send Email (guardrailed)",
    description: `Send an email from the Sun & Moon account, optionally as a threaded reply via reply_to_message_id (recipient/subject/threading derived automatically like gmail_create_draft).

${sendGuardrailDoc}

Returns: {sent: true, messageId, threadId, tier} or a blocked explanation with next steps.`,
    inputSchema: {
      to: z.string().optional(),
      cc: z.string().optional(),
      subject: z.string().optional(),
      body: z.string().min(1),
      reply_to_message_id: z.string().optional(),
      owner_approved: z
        .boolean()
        .default(false)
        .describe("Set true ONLY after the human explicitly approved this exact send in conversation."),
    },
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
  },
  async ({ to, cc, subject, body, reply_to_message_id, owner_approved }) => {
    try {
      const gmail = gmailClient();
      const cfg = loadGuardrails();

      let threadId: string | undefined;
      let finalTo = to;
      let finalSubject = subject;
      let inReplyTo: string | undefined;
      let references: string | undefined;
      let isReply = false;

      if (reply_to_message_id) {
        const orig = parseMessage(
          (await gmail.users.messages.get({ userId: "me", id: reply_to_message_id, format: "full" })).data
        );
        threadId = orig.threadId;
        finalTo = finalTo ?? extractAddresses(orig.from).join(", ");
        finalSubject = finalSubject ?? (orig.subject.startsWith("Re:") ? orig.subject : `Re: ${orig.subject}`);
        inReplyTo = orig.messageIdHeader;
        references = [orig.referencesHeader, orig.messageIdHeader].filter(Boolean).join(" ") || undefined;
        isReply = true;
      }

      if (!finalTo) return fail("'to' is required when not replying.");
      if (!finalSubject) return fail("'subject' is required when not replying.");

      const recipients = recipientsFromFields(finalTo, cc);
      const check = checkSend(cfg, {
        recipients,
        body,
        isReplyToExistingThread: isReply,
        ownerApproved: owner_approved,
      });
      if (!check.allowed) return fail(`SEND BLOCKED — ${check.reason}`);

      const raw = buildRawMessage({ to: finalTo, cc, subject: finalSubject, body, inReplyTo, references });
      const res = await gmail.users.messages.send({ userId: "me", requestBody: { raw, threadId } });

      logSend(cfg, {
        ts: new Date().toISOString(),
        tool: "gmail_send_email",
        to: recipients,
        subject: finalSubject,
        tier: check.tier,
        messageId: res.data.id ?? undefined,
        threadId: res.data.threadId ?? undefined,
      });

      return ok({ sent: true, messageId: res.data.id, threadId: res.data.threadId, tier: check.tier });
    } catch (e) {
      return fail(errMsg(e));
    }
  }
);

server.registerTool(
  "gmail_send_draft",
  {
    title: "Send Existing Draft (guardrailed)",
    description: `Send a previously created draft by draftId. The same guardrails as gmail_send_email are applied to the draft's recipients and body.

${sendGuardrailDoc}`,
    inputSchema: {
      draft_id: z.string().min(1),
      owner_approved: z.boolean().default(false),
    },
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
  },
  async ({ draft_id, owner_approved }) => {
    try {
      const gmail = gmailClient();
      const cfg = loadGuardrails();

      const draft = await gmail.users.drafts.get({ userId: "me", id: draft_id, format: "full" });
      const parsed = parseMessage(draft.data.message ?? {});
      const recipients = recipientsFromFields(parsed.to, parsed.cc);
      // A draft attached to a thread with >1 message is a reply.
      let isReply = false;
      if (parsed.threadId) {
        const t = await gmail.users.threads.get({ userId: "me", id: parsed.threadId, format: "minimal" });
        isReply = (t.data.messages?.length ?? 0) > 1;
      }

      const check = checkSend(cfg, {
        recipients,
        body: parsed.body,
        isReplyToExistingThread: isReply,
        ownerApproved: owner_approved,
      });
      if (!check.allowed) return fail(`SEND BLOCKED — ${check.reason}`);

      const res = await gmail.users.drafts.send({ userId: "me", requestBody: { id: draft_id } });
      logSend(cfg, {
        ts: new Date().toISOString(),
        tool: "gmail_send_draft",
        to: recipients,
        subject: parsed.subject,
        tier: check.tier,
        messageId: res.data.id ?? undefined,
        threadId: res.data.threadId ?? undefined,
      });
      return ok({ sent: true, messageId: res.data.id, threadId: res.data.threadId, tier: check.tier });
    } catch (e) {
      return fail(errMsg(e));
    }
  }
);

// ---------------------------------------------------------------- audit
server.registerTool(
  "gmail_get_send_audit",
  {
    title: "Get Send Audit Log",
    description:
      "Return the most recent automated/approved sends from the audit log (newest first). Use for the owner's oversight: 'what did you send this week?'",
    inputSchema: {
      limit: z.number().int().min(1).max(200).default(25),
    },
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  },
  async ({ limit }) => {
    try {
      const cfg = loadGuardrails();
      const fs = await import("node:fs");
      if (!fs.existsSync(cfg.auditLogPath)) return ok({ count: 0, entries: [] });
      const entries = fs
        .readFileSync(cfg.auditLogPath, "utf-8")
        .split("\n")
        .filter(Boolean)
        .map((l) => JSON.parse(l))
        .reverse()
        .slice(0, limit);
      return ok({ count: entries.length, entries });
    } catch (e) {
      return fail(errMsg(e));
    }
  }
);

// ---------------------------------------------------------------- boot
async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("sunmoon-gmail-mcp-server running on stdio");
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});

import * as fs from "node:fs";
import * as path from "node:path";
import { PROJECT_ROOT, extractAddresses } from "./gmail.js";

export interface GuardrailsConfig {
  /** Addresses or patterns that may be emailed automatically without owner approval.
   *  Supports: exact address ("mindy@vrn.com"), domain ("@vacayrentalnetwork.com"),
   *  or wildcard-local ("*@sunandmoon30a.com"). */
  allowlist: string[];
  /** If true, replies within an existing thread are auto-send eligible
   *  (the recipient already emailed the business — lowest-risk category). */
  allowRepliesInThread: boolean;
  /** Hard cap on total sends per rolling hour. */
  maxSendsPerHour: number;
  /** If any of these substrings appear in an outgoing body, auto-send is refused
   *  and owner approval is required regardless of recipient. */
  blockedKeywords: string[];
  /** JSONL audit log of every send. */
  auditLogPath: string;
}

const DEFAULTS: GuardrailsConfig = {
  allowlist: [],
  allowRepliesInThread: true,
  maxSendsPerHour: 10,
  blockedKeywords: [
    "wire transfer",
    "bank account",
    "routing number",
    "social security",
    "ssn",
    "password",
    "gift card",
    "crypto",
    "refund your",
  ],
  auditLogPath: path.join(PROJECT_ROOT, "logs", "send-audit.jsonl"),
};

const CONFIG_PATH =
  process.env.GUARDRAILS_PATH ?? path.join(PROJECT_ROOT, "guardrails.json");

export function loadGuardrails(): GuardrailsConfig {
  if (!fs.existsSync(CONFIG_PATH)) return { ...DEFAULTS };
  const user = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
  return { ...DEFAULTS, ...user };
}

function matchesAllowlist(address: string, patterns: string[]): boolean {
  const addr = address.toLowerCase().trim();
  return patterns.some((p) => {
    const pat = p.toLowerCase().trim();
    if (pat.startsWith("*@")) return addr.endsWith(pat.slice(1));
    if (pat.startsWith("@")) return addr.endsWith(pat);
    return addr === pat;
  });
}

export interface SendCheckInput {
  recipients: string[]; // all To/Cc/Bcc bare addresses
  body: string;
  isReplyToExistingThread: boolean;
  /** True only when the human owner explicitly approved this send in conversation. */
  ownerApproved: boolean;
}

export interface SendCheckResult {
  allowed: boolean;
  reason: string;
  tier: "allowlist" | "thread-reply" | "owner-approved" | "blocked";
}

export function checkSend(cfg: GuardrailsConfig, input: SendCheckInput): SendCheckResult {
  // 1. Rate cap — applies to everything, including owner-approved sends.
  const sendsLastHour = countRecentSends(cfg.auditLogPath, 60 * 60 * 1000);
  if (sendsLastHour >= cfg.maxSendsPerHour) {
    return {
      allowed: false,
      tier: "blocked",
      reason: `Rate cap reached: ${sendsLastHour} sends in the last hour (max ${cfg.maxSendsPerHour}). Wait or raise maxSendsPerHour in guardrails.json.`,
    };
  }

  // 2. Keyword tripwire — sensitive content always requires owner approval.
  const bodyLower = input.body.toLowerCase();
  const tripped = cfg.blockedKeywords.filter((k) => bodyLower.includes(k.toLowerCase()));
  if (tripped.length > 0 && !input.ownerApproved) {
    return {
      allowed: false,
      tier: "blocked",
      reason: `Body contains sensitive keyword(s): ${tripped.join(", ")}. Create a draft with gmail_create_draft instead, or ask the owner and resend with owner_approved=true.`,
    };
  }

  // 3. Owner approval overrides recipient checks.
  if (input.ownerApproved) {
    return { allowed: true, tier: "owner-approved", reason: "Owner explicitly approved this send." };
  }

  // 4. Every recipient must be covered by allowlist or thread-reply rule.
  const uncovered = input.recipients.filter((r) => !matchesAllowlist(r, cfg.allowlist));
  if (uncovered.length === 0) {
    return { allowed: true, tier: "allowlist", reason: "All recipients on allowlist." };
  }
  if (cfg.allowRepliesInThread && input.isReplyToExistingThread) {
    return {
      allowed: true,
      tier: "thread-reply",
      reason: "Reply within an existing thread (recipient previously emailed this account).",
    };
  }

  return {
    allowed: false,
    tier: "blocked",
    reason: `New outbound to non-allowlisted recipient(s): ${uncovered.join(", ")}. Options: (a) create a draft with gmail_create_draft for the owner to review, or (b) ask the owner for explicit approval in the conversation and retry with owner_approved=true.`,
  };
}

// ---------- Audit log ----------

interface AuditEntry {
  ts: string;
  tool: string;
  to: string[];
  subject: string;
  tier: string;
  messageId?: string;
  threadId?: string;
}

export function logSend(cfg: GuardrailsConfig, entry: AuditEntry): void {
  fs.mkdirSync(path.dirname(cfg.auditLogPath), { recursive: true });
  fs.appendFileSync(cfg.auditLogPath, JSON.stringify(entry) + "\n");
}

function countRecentSends(logPath: string, windowMs: number): number {
  if (!fs.existsSync(logPath)) return 0;
  const cutoff = Date.now() - windowMs;
  return fs
    .readFileSync(logPath, "utf-8")
    .split("\n")
    .filter(Boolean)
    .filter((line) => {
      try {
        return new Date((JSON.parse(line) as AuditEntry).ts).getTime() >= cutoff;
      } catch {
        return false;
      }
    }).length;
}

export function recipientsFromFields(to: string, cc?: string, bcc?: string): string[] {
  return [
    ...extractAddresses(to),
    ...(cc ? extractAddresses(cc) : []),
    ...(bcc ? extractAddresses(bcc) : []),
  ];
}

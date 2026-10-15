/** Translate Pi events into the admitted native run's capture and tool contract.
 * Child sessions inherit the same run and frozen scope; each observation retains
 * its child identity. Completion and continuation belong to the ledger runtime.
 */
import { spawn, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "typebox";

const SOURCE: Record<string, string> = {
  startup: "startup",
  resume: "resume",
  fork: "resume",
  reload: "startup",
  new: "clear",
};

let contextTokens: number | null = null;
let settledThisRun = false;
const inFlight = new Set<string>();

// Snapshot once when the extension loads. A later worker installation must not
// change the run identity of callbacks already owned by this native session.
const hookContractPath = join(dirname(fileURLToPath(import.meta.url)), "hook-contract.json");
function installedHook(): { command: string; args: string[] } {
  if (!existsSync(hookContractPath)) throw new Error("hx native hook contract is missing");
  if (statSync(hookContractPath).size > 8192) throw new Error("hx hook contract exceeds 8 KiB");
  const value = JSON.parse(readFileSync(hookContractPath, "utf8"));
  if (typeof value.command !== "string" || !Array.isArray(value.args) || value.args.length > 20 ||
      value.args.some((item: unknown) => typeof item !== "string")) throw new Error("invalid hx hook contract");
  return value;
}
const nativeHook = installedHook();
const nativeToolEnvironment = process.env.HX_CONTINUITY_CAPSULE && process.env.HX_CONTINUITY_RUN
  ? { ...process.env } : null;

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is unset`);
  return value;
}

function hookBin(): string {
  return nativeHook.command;
}

function callHook(event: string, payload: Record<string, unknown>): string {
  const result = spawnSync(hookBin(), [...nativeHook.args, event], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    env: process.env,
  });
  if (result.status !== 0 && result.stderr) {
    process.stderr.write(result.stderr);
  }
  if ((event === "request" || event === "tool-start") && result.status !== 0) {
    throw new Error("hx refused native admission; reconcile the assignment before continuing");
  }
  return result.stdout ?? "";
}

function forPi(line: string): string {
  return line.replace("Use the Read tool", "Use the read tool");
}

function sessionFile(ctx: { sessionManager?: { getSessionFile?: () => string | null } }): string {
  return ctx.sessionManager?.getSessionFile?.() ?? "";
}

function textOf(message: { content?: unknown } | undefined): string {
  const content = message?.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content.map((part) => (part && typeof part.text === "string" ? part.text : "")).join("\n");
}

function usageTokens(usage: { input?: number; cacheRead?: number; cacheWrite?: number } | undefined): number | null {
  if (!usage) return null;
  let total = 0;
  let seen = false;
  for (const value of [usage.input, usage.cacheRead, usage.cacheWrite]) {
    if (typeof value === "number") {
      total += value;
      seen = true;
    }
  }
  return seen ? total : null;
}

function rememberUsage(message: { role?: string; usage?: { input?: number; cacheRead?: number; cacheWrite?: number } }): void {
  if (message?.role !== "assistant") return;
  const tokens = usageTokens(message.usage);
  if (tokens !== null) contextTokens = tokens;
}

function publicMessage(message: any): Record<string, unknown> {
  // Project before serialization: private reasoning, signatures, image bytes,
  // and provider-specific fields never cross the hook pipe.
  const content = typeof message.content === "string" ? message.content :
    Array.isArray(message.content) ? message.content.flatMap((part: any) => {
      if (part?.type === "thinking" || part?.type === "redacted_thinking") return [];
      if (part?.type === "text") return [{ type: "text", text: part.text }];
      if (part?.type === "toolCall") return [{ type: "toolCall", id: part.id, name: part.name, arguments: part.arguments,
        namespace: typeof part.namespace === "string" ? part.namespace : undefined }];
      // Keep the tag so unknown public shapes become visible decoder gaps.
      return [{ type: part?.type ?? "unknown" }];
    }) : null;
  const usage: Record<string, number> = {};
  for (const key of ["input", "output", "cacheRead", "cacheWrite", "cacheWrite1h", "reasoning", "totalTokens"]) {
    const value = message.usage?.[key];
    if (Number.isSafeInteger(value) && value >= 0) usage[key] = value;
  }
  return { role: message.role, content, usage,
    stopReason: typeof message.stopReason === "string" ? message.stopReason : undefined,
    errorMessage: typeof message.errorMessage === "string" ? message.errorMessage : undefined };
}

function inject(pi: { sendMessage: (message: { customType: string; content: string; display: boolean }) => void }, stdout: string): void {
  const line = forPi(stdout.trim());
  if (!line || line.startsWith("{")) return;
  pi.sendMessage({ customType: "hx-context", content: line, display: true });
}

function contentText(content: Array<{ type?: string; text?: string }> | undefined): string {
  if (!content) return "";
  return content.map((part) => part.text ?? "").join("\n");
}

export default function (pi: {
  on: (event: string, handler: (event: any, ctx: any) => any) => void;
  sendMessage: (message: { customType: string; content: string; display: boolean }, options?: { triggerTurn?: boolean }) => void;
  sendUserMessage: (content: string, options?: { deliverAs?: string }) => void;
  registerTool: (tool: Record<string, unknown>) => void;
  getAllTools: () => { name: string }[];
  getActiveTools: () => string[];
  setActiveTools: (names: string[]) => void;
}): void {
  const subagent = process.env.PI_SUBAGENT === "1";
  const childIdentity = subagent ? { agent_id: required("HX_PI_CHILD_ID") } : null;
  const toolsetPath = join(dirname(fileURLToPath(import.meta.url)), "../../../toolset.json");
  let toolsetStamp = "";
  const syncTools = () => {
    if (!nativeToolEnvironment || subagent) return;
    const stat = statSync(toolsetPath);
    const stamp = `${stat.mtimeMs}:${stat.size}`;
    if (stamp === toolsetStamp) return;
    if (stat.size > 8192) throw new Error("hx tool selection exceeds 8 KiB");
    const value = JSON.parse(readFileSync(toolsetPath, "utf8"));
    const known = new Set(pi.getAllTools().map(tool => tool.name));
    if (value.adapter !== "pi" || !Array.isArray(value.selected) || value.selected.length > 16 ||
        value.selected.some((name: unknown) => typeof name !== "string" || !known.has(name as string))) {
      throw new Error("hx selected tools are unavailable in this Pi runtime");
    }
    pi.setActiveTools(value.selected);
    const actual = pi.getActiveTools();
    const result = spawnSync(join(nativeToolEnvironment.HX_CONTINUITY_CAPSULE!, "bin/hx"),
      ["tools", "acknowledge", ...actual], { encoding: "utf8", env: nativeToolEnvironment });
    if (result.status !== 0) throw new Error("hx could not acknowledge native tool visibility");
    toolsetStamp = stamp;
  };

  pi.on("message_end", (event, ctx) => {
    rememberUsage(event.message);
    if (!["user", "assistant"].includes(event.message?.role)) return;
    callHook("message", { type: "message_end", message: publicMessage(event.message),
      ...(childIdentity ?? { transcript_path: sessionFile(ctx) }) });
  });

  pi.on("input", (event, ctx) => {
    if (subagent) return;
    try {
      callHook("request", { prompt: event.text, transcript_path: sessionFile(ctx) });
    } catch {
      return { action: "handled" };
    }
  });

  pi.on("before_provider_request", (event) => {
    const cap = Number(process.env.HX_MAX_OUTPUT_TOKENS);
    if (!Number.isSafeInteger(cap) || cap < 1 || !event.payload || typeof event.payload !== "object") return;
    const payload = { ...(event.payload as Record<string, any>) };
    // Only change fields emitted by the selected provider serializer.
    for (const key of ["max_tokens", "max_completion_tokens", "max_output_tokens"]) {
      if (typeof payload[key] === "number") payload[key] = Math.min(payload[key], cap);
    }
    for (const key of ["generationConfig", "config"]) {
      if (typeof payload[key]?.maxOutputTokens === "number") {
        payload[key] = { ...payload[key], maxOutputTokens: Math.min(payload[key].maxOutputTokens, cap) };
      }
    }
    if (payload.thinking?.type === "enabled" && typeof payload.thinking.budget_tokens === "number") {
      if (cap <= 1024) throw new Error("hx output cap cannot fit the provider's minimum thinking budget");
      payload.thinking = { ...payload.thinking, budget_tokens: Math.min(payload.thinking.budget_tokens, cap - 1) };
    }
    return payload;
  });

  pi.on("session_start", (event, ctx) => {
    if (subagent) return;
    settledThisRun = false;
    const stdout = callHook("context", {
      source: SOURCE[event.reason] ?? "startup",
      session_id: event.previousSessionFile,
      transcript_path: sessionFile(ctx),
    });
    inject(pi, stdout);
    syncTools();
  });

  pi.on("session_compact", (event, ctx) => {
    if (subagent) return;
    callHook("postcompact", {
      trigger: event.reason,
      compact_summary: event.compactionEntry?.summary,
      transcript_path: sessionFile(ctx),
    });
    const stdout = callHook("context", {
      source: "compact",
      transcript_path: sessionFile(ctx),
    });
    inject(pi, stdout);
  });

  pi.on("session_before_compact", (event, ctx) => {
    if (subagent) return;
    const checkpoint = callHook("precompact", {
      trigger: event.reason,
      transcript_path: sessionFile(ctx),
    });
    if (checkpoint.trim()) {
      // Pi's public hook supports replacing the summary. Keep its native cut
      // boundary and recent messages; the checkpoint holds current task state.
      return { compaction: {
        summary: checkpoint.trim(),
        firstKeptEntryId: event.preparation.firstKeptEntryId,
        tokensBefore: event.preparation.tokensBefore,
      } };
    }
  });

  pi.on("tool_call", (event, ctx) => {
    try {
      callHook("tool-start", {
        tool_name: event.toolName,
        tool_use_id: event.toolCallId,
        tool_input: event.input,
        ...(childIdentity ?? { transcript_path: sessionFile(ctx) }),
      });
    } catch {
      return { block: true, reason: "hx closed native tool admission; reconcile the assignment before continuing" };
    }
  });

  pi.on("tool_result", (event, ctx) => {
    const stdout = callHook("log", {
      tool_name: event.toolName,
      tool_use_id: event.toolCallId,
      tool_input: event.input,
      tool_response: { text: contentText(event.content), isError: event.isError },
      context_tokens: contextTokens,
      ...(childIdentity ?? { transcript_path: sessionFile(ctx) }),
    });
    syncTools();
    const value = stdout.trim().startsWith('{') ? JSON.parse(stdout) : null;
    if (value?.hx_complete_read || ["read", "Read", "read_file"].includes(event.toolName)) {
      if (!value.hx_complete_read?.complete || typeof value.hx_complete_read.text !== "string") {
        throw new Error("hx complete-file broker did not deliver the whole file");
      }
      return { content: [{ type: "text", text: value.hx_complete_read.text }],
        details: { hxCompleteRead: value.hx_complete_read.receipt }, isError: false };
    }
  });

  const settle = async (event: { type?: string; entries?: unknown[] }, ctx: any) => {
    if (subagent || settledThisRun) return;
    settledThisRun = true;
    callHook("stop", {
      session_id: sessionFile(ctx),
      background_tasks: [...inFlight].map((agentId) => ({ agent_id: agentId })),
    });

  };

  pi.on("agent_before_settle", (event, ctx) => settle(event, ctx));
  pi.on("agent_start", () => {
    settledThisRun = false;
  });
  pi.on("agent_settled", (event, ctx) => settle(event, ctx));

  if (subagent) return;

  pi.registerTool({
    name: "subagent",
    label: "Subagent",
    description: "Delegate one bounded task to a child Pi session. Its observations attach to the same admitted run and frozen scope; the child result comes back directly.",
    parameters: Type.Object({
      prompt: Type.String({ description: "The whole task: deliverables, bounds, and what done means" }),
      agent_type: Type.Optional(Type.String({ description: "A short role name, such as scout" })),
    }),
    async execute(_toolCallId: string, params: { prompt: string; agent_type?: string }, signal?: AbortSignal) {
      const agentId = randomUUID();
      inFlight.add(agentId);
      try {
        callHook("subagent-start", {
          agent_id: agentId,
          agent_type: params.agent_type || "subagent",
          tool_input: { prompt: params.prompt },
        });
        const childText = await runChild(params.prompt, agentId, signal);
        callHook("subagent-stop", {
          agent_id: agentId,
          last_assistant_message: childText,
        });
        return { content: [{ type: "text", text: childText || "(no output)" }], details: { agentId } };
      } finally {
        inFlight.delete(agentId);
      }
    },
  });
}

function runChild(prompt: string, agentId: string, signal?: AbortSignal): Promise<string> {
  const root = required("HX_CONTINUITY_CAPSULE");
  const id = required("HARNESS_ID");
  const home = join(root, "run", id, "home");
  const bin = process.env.HX_PI_BIN || "pi";
  const harness = JSON.parse(readFileSync(join(root, "config", id, "harness.json"), "utf8"));
  const identity = "You are a child of this admitted assignment. Follow the parent's bounded request and frozen scope. " +
    "Return findings and evidence to the parent; do not approve bindings or request unit completion.\n\n" +
    readFileSync(join(root, "run", id, "persona.md"), "utf8");
  if (typeof harness.workdir !== "string" || !isAbsolute(harness.workdir)) throw new Error("native child requires its admitted workdir");
  const workdir = harness.workdir;
  const args = [
    "--mode", "json", "-p", "--no-session",
    "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
    "--extension", fileURLToPath(import.meta.url),
    "--no-context-files", "--no-approve", "--offline",
    "--model", harness.model,
    "--thinking", harness.effort,
    "--append-system-prompt", identity,
    prompt,
  ];
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, {
      cwd: workdir,
      env: {
        ...process.env,
        PI_SUBAGENT: "1",
        HX_PI_CHILD_ID: agentId,
        PI_CODING_AGENT_DIR: home,
        PI_OFFLINE: "1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let last = "";
    let buffer = "";
    const onAbort = () => child.kill("SIGTERM");
    signal?.addEventListener("abort", onAbort);
    child.stdout.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      buffer += chunk;
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const event = JSON.parse(line);
          if (event.type === "message_end" && event.message?.role === "assistant") {
            last = textOf(event.message);
          }

        } catch {
          // A non-JSON line is Pi's own diagnostic. The stream stays the JSON events.
        }
      }
    });
    child.on("error", (error) => {
      signal?.removeEventListener("abort", onAbort);
      reject(error);
    });
    child.on("close", () => {
      signal?.removeEventListener("abort", onAbort);
      resolve(last);
    });
  });
}

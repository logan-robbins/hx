/**
 * hx's Pi extension. It translates Pi's events into the same `hx-hook` calls
 * Claude's settings.json makes, and it is the `/goal` capability Pi does not ship.
 *
 * Pi 0.84 (the binary this was written against) emits `agent_settled` as the idle
 * boundary and does not emit `agent_before_settle`. Newer docs add that event and
 * let it return `continue`. Both are subscribed; the first one in a run does the work.
 */
import { spawn, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { isAbsolute, join } from "node:path";
import { Type } from "typebox";

const SOURCE: Record<string, string> = {
  startup: "startup",
  resume: "resume",
  fork: "resume",
  reload: "startup",
  new: "clear",
};

interface GoalState {
  text: string;
  active: boolean;
}

let goal: GoalState | null = null;
let contextTokens: number | null = null;
let settledThisRun = false;
const inFlight = new Set<string>();

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is unset`);
  return value;
}

function hookBin(): string {
  return join(required("HARNESS_ROOT"), "bin", "hx-hook");
}

function callHook(event: string, payload: Record<string, unknown>): string {
  const result = spawnSync(hookBin(), ["--id", required("HARNESS_ID"), event], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    env: process.env,
  });
  if (result.status !== 0 && result.stderr) {
    process.stderr.write(result.stderr);
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

function rebuildGoal(ctx: { sessionManager: { getBranch: () => Array<{ type?: string; customType?: string; data?: GoalState }> } }): void {
  goal = null;
  for (const entry of ctx.sessionManager.getBranch()) {
    if (entry.type === "custom" && entry.customType === "hx-goal" && entry.data) {
      goal = entry.data.active ? { text: entry.data.text, active: true } : null;
    }
  }
}

function assistantHasComplete(ctx: { sessionManager: { getBranch: () => Array<{ type?: string; message?: { role?: string; content?: unknown } }> } }, id: string): boolean {
  const needle = `HX-COMPLETE ${id}`;
  const branch = ctx.sessionManager.getBranch();
  for (let index = branch.length - 1; index >= 0; index -= 1) {
    const entry = branch[index];
    if (entry.type === "message" && entry.message?.role === "assistant") {
      return textOf(entry.message).includes(needle);
    }
  }
  return false;
}

function inject(pi: { sendMessage: (message: { customType: string; content: string; display: boolean }) => void }, stdout: string): void {
  const line = forPi(stdout.trim());
  if (!line || line.startsWith("{")) return;
  pi.sendMessage({ customType: "hx-context", content: line, display: true });
}

function additionalContext(stdout: string): string {
  try {
    const body = JSON.parse(stdout);
    const text = body?.hookSpecificOutput?.additionalContext;
    return typeof text === "string" ? forPi(text) : "";
  } catch {
    return "";
  }
}

function contentText(content: Array<{ type?: string; text?: string }> | undefined): string {
  if (!content) return "";
  return content.map((part) => part.text ?? "").join("\n");
}

export default function (pi: {
  on: (event: string, handler: (event: any, ctx: any) => any) => void;
  sendMessage: (message: { customType: string; content: string; display: boolean }, options?: { triggerTurn?: boolean }) => void;
  sendUserMessage: (content: string, options?: { deliverAs?: string }) => void;
  appendEntry: (customType: string, data?: GoalState) => void;
  registerCommand: (name: string, options: { description: string; handler: (args: string, ctx: any) => void }) => void;
  registerTool: (tool: Record<string, unknown>) => void;
}): void {
  const subagent = process.env.PI_SUBAGENT === "1";

  pi.on("message_end", (event) => {
    rememberUsage(event.message);
  });

  pi.on("session_start", (event, ctx) => {
    if (subagent) return;
    settledThisRun = false;
    rebuildGoal(ctx);
    const stdout = callHook("context", {
      source: SOURCE[event.reason] ?? "startup",
      session_id: event.previousSessionFile,
      transcript_path: sessionFile(ctx),
    });
    inject(pi, stdout);
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
    callHook("precompact", {
      trigger: event.reason,
      transcript_path: sessionFile(ctx),
    });
  });

  pi.on("tool_result", (event, ctx) => {
    if (subagent) return;
    callHook("log", {
      tool_name: event.toolName,
      tool_use_id: event.toolCallId,
      tool_input: event.input,
      tool_response: { text: contentText(event.content), isError: event.isError },
      context_tokens: contextTokens,
      transcript_path: sessionFile(ctx),
    });
  });

  const settle = async (event: { type?: string; entries?: unknown[] }, ctx: any) => {
    if (subagent || settledThisRun) return;
    settledThisRun = true;
    const id = required("HARNESS_ID");
    const marker = join(required("HARNESS_ROOT"), "run", id, "seam");
    const seamPending = existsSync(marker);
    callHook("stop", {
      session_id: sessionFile(ctx),
      background_tasks: [...inFlight].map((agentId) => ({ agent_id: agentId })),
    });
    if (seamPending || !goal?.active) return;
    if (assistantHasComplete(ctx, id)) {
      goal = { text: goal.text, active: false };
      pi.appendEntry("hx-goal", goal);
      return;
    }
    if (event.type === "agent_before_settle") {
      return {
        continue: true,
        entries: [
          ...(event.entries ?? []),
          { type: "custom_message", customType: "hx-goal", content: goal.text, display: false },
        ],
      };
    }
    pi.sendUserMessage(goal.text);
  };

  pi.on("agent_before_settle", (event, ctx) => settle(event, ctx));
  pi.on("agent_start", () => {
    settledThisRun = false;
  });
  pi.on("agent_settled", (event, ctx) => settle(event, ctx));

  if (subagent) return;

  pi.registerCommand("goal", {
    description: "Keep working until hx complete prints HX-COMPLETE",
    handler(args) {
      const text = (args || "").trim();
      goal = { text, active: true };
      pi.appendEntry("hx-goal", goal);
      if (text) pi.sendUserMessage(text);
    },
  });

  pi.registerTool({
    name: "subagent",
    label: "Subagent",
    description: "Delegate one bounded task to a child Pi session. It gets its own context file and stream; the digest path comes back in the result.",
    parameters: Type.Object({
      prompt: Type.String({ description: "The whole task: deliverables, bounds, and what done means" }),
      agent_type: Type.Optional(Type.String({ description: "A short role name, such as scout" })),
    }),
    async execute(_toolCallId: string, params: { prompt: string; agent_type?: string }, signal?: AbortSignal) {
      const agentId = randomUUID();
      inFlight.add(agentId);
      try {
        const started = callHook("subagent-start", {
          agent_id: agentId,
          agent_type: params.agent_type || "subagent",
          tool_input: { prompt: params.prompt },
        });
        const contextLine = additionalContext(started);
        const childText = await runChild(params.prompt, contextLine, agentId, signal);
        callHook("subagent-stop", {
          agent_id: agentId,
          last_assistant_message: childText,
        });
        const finished = callHook("subagent-result", {
          agent_id: agentId,
          tool_response: { status: "completed", agentId, prompt: params.prompt },
        });
        const digest = additionalContext(finished);
        const text = [childText, digest].filter(Boolean).join("\n\n");
        return { content: [{ type: "text", text: text || "(no output)" }], details: { agentId } };
      } finally {
        inFlight.delete(agentId);
      }
    },
  });
}

function runChild(prompt: string, contextLine: string, agentId: string, signal?: AbortSignal): Promise<string> {
  const root = required("HARNESS_ROOT");
  const id = required("HARNESS_ID");
  const home = join(root, "run", id, "home");
  const subagents = join(root, "config", id, "SUBAGENTS.md");
  const identity = existsSync(subagents) ? readFileSync(subagents, "utf8") : "";
  const bin = process.env.HX_PI_BIN || "pi";
  const harness = JSON.parse(readFileSync(join(root, "config", id, "harness.json"), "utf8"));
  const workdir = typeof harness.workdir === "string" && harness.workdir
    ? (isAbsolute(harness.workdir) ? harness.workdir : join(root, harness.workdir))
    : root;
  const args = [
    "--mode", "json", "-p", "--no-session",
    "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
    "--no-context-files", "--no-approve", "--offline",
    "--model", harness.model,
    "--thinking", harness.effort,
    "--append-system-prompt", identity,
    contextLine ? `${contextLine}\n\n${prompt}` : prompt,
  ];
  return new Promise((resolve, reject) => {
    const child = spawn(bin, args, {
      cwd: workdir,
      env: {
        ...process.env,
        PI_SUBAGENT: "1",
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
          if (event.type === "tool_execution_end") {
            const tokens = usageTokens(event.result?.usage);
            callHook("log", {
              agent_id: agentId,
              tool_name: event.toolName,
              tool_use_id: event.toolCallId,
              tool_input: event.args,
              tool_response: event.result,
              context_tokens: tokens,
              transcript_path: event.sessionFile,
            });
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

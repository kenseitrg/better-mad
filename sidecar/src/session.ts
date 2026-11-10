/**
 * Chat session factory (handoff M3.3 §6.1): one pi SDK AgentSession rooted in
 * the workspace. Adapted from pwb's server/agent/sessions.ts (createPersonaSession /
 * resolveModel / historyForClient) with the persona/skill/custom-tools machinery
 * dropped — the sidecar is a dumb pi-session host (design §9.1):
 *
 * - built-in file tools ON (they are the agent ↔ app protocol),
 * - context files ON (workspace AGENTS.md auto-loads; design §8),
 * - no extensions / prompt templates / themes,
 * - sessions persisted to <workspace>/.chat/ (resume most recent on boot;
 *   a fresh session file for "New chat" via the `fresh` variant).
 *
 * Model: `resolveModel("provider/model")` against the user's ~/.pi/agent
 * catalogs via ModelRuntime (pwb's resolveModel, verbatim); no spec → pi's
 * configured default, else first available.
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import {
	createAgentSession,
	DefaultResourceLoader,
	getAgentDir,
	ModelRuntime,
	SessionManager,
	type AgentSession,
} from "@earendil-works/pi-coding-agent";

/** One booted chat session plus the metadata the contract reports. */
export interface ChatSessionRuntime {
	session: AgentSession;
	/** The concrete model id in use, "provider/id". */
	modelId: string;
	/** pi's model-fallback warning, when the requested model was unavailable. */
	fallback?: string;
}

let modelRuntime: ModelRuntime | undefined;

/** Shared ModelRuntime using the default auth path (~/.pi/agent/auth.json). */
async function getModelRuntime(): Promise<ModelRuntime> {
	if (!modelRuntime) modelRuntime = await ModelRuntime.create();
	return modelRuntime;
}

/** Resolve a "provider/model" config string against the runtime's catalogs. */
export async function resolveModel(spec: string) {
	const slash = spec.indexOf("/");
	if (slash <= 0 || slash === spec.length - 1) {
		throw new Error(`model must look like "provider/model", got "${spec}"`);
	}
	const provider = spec.slice(0, slash);
	const id = spec.slice(slash + 1);
	const runtime = await getModelRuntime();
	const model = runtime.getModel(provider, id);
	if (!model) throw new Error(`model "${spec}" not found in provider catalogs (check ~/.pi/agent setup)`);
	return model;
}

/** Session directory under the workspace (handoff §6: workspace/.chat/). */
export function chatDir(workspace: string): string {
	return join(workspace, ".chat");
}

/** `provider/id` from the session's resolved model, for /health + READY. */
export function modelIdOf(session: AgentSession): string {
	const model = session.model;
	return model ? `${model.provider}/${model.id}` : "none";
}

/**
 * Create (or resume) the chat AgentSession for one workspace.
 *
 * The .chat dir is created up front — SessionManager scans it on boot.
 * By default continueRecent resumes the latest session file or starts one
 * when the dir is empty; `fresh: true` starts a NEW session file instead
 * (the /new route) — the previous file stays on disk (UX §8).
 *
 * Boot CAN fail (missing ~/.pi/agent, unknown model spec, provider auth):
 * callers surface the error as a one-line stderr message + exit 1 (§5).
 */
export async function createChatSession(
	workspace: string,
	modelSpec?: string,
	opts: { fresh?: boolean } = {},
): Promise<ChatSessionRuntime> {
	mkdirSync(chatDir(workspace), { recursive: true });

	const loader = new DefaultResourceLoader({
		cwd: workspace,
		agentDir: getAgentDir(),
		// Lean, purpose-built sessions: no user extensions/prompt templates/themes.
		// Context files stay ON — the workspace AGENTS.md is the skill channel.
		noExtensions: true,
		noPromptTemplates: true,
		noThemes: true,
	});
	await loader.reload();

	const model = modelSpec ? await resolveModel(modelSpec) : undefined;
	const dir = chatDir(workspace);
	const sessionManager = opts.fresh
		? SessionManager.create(workspace, dir)
		: SessionManager.continueRecent(workspace, dir);

	const { session, modelFallbackMessage } = await createAgentSession({
		cwd: workspace,
		resourceLoader: loader,
		sessionManager,
		...(model ? { model } : {}),
	});

	if (modelFallbackMessage) {
		console.warn(`[chat] model fallback: ${modelFallbackMessage}`);
	}
	return { session, modelId: modelIdOf(session), fallback: modelFallbackMessage };
}

/** Map session messages to a compact replay shape for the client (pwb's
 * historyForClient, verbatim). Skips messages with no text content. */
export function historyForClient(session: AgentSession): Array<{
	role: "user" | "assistant";
	text: string;
}> {
	const out: Array<{ role: "user" | "assistant"; text: string }> = [];
	for (const message of session.messages) {
		if (message.role !== "user" && message.role !== "assistant") continue;
		const content = message.content;
		const text = (
			Array.isArray(content)
				? content.filter((c): c is { type: "text"; text: string } => c.type === "text")
				: [{ type: "text" as const, text: content }]
		)
			.map((c) => c.text)
			.join("")
			.trim();
		if (text.length > 0) out.push({ role: message.role, text });
	}
	return out;
}

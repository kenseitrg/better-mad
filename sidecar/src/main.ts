/**
 * Sidecar entry point (handoff M3.3 §6.4). Run directly by the hub's
 * SidecarManager under Node ≥22.18 (native type stripping — erasable-only
 * TS discipline, traps T6/T7):
 *
 *   node sidecar/src/main.ts --workspace <path> [--port <n>] [--model provider/model]
 *
 * Boot order: create/resume the workspace session → listen → print ONE line
 * `SIDECAR_READY {json}` to stdout (the Python handshake reads exactly this,
 * mirroring the worker's WORKER_READY). Boot failure → single stderr line +
 * exit 1 (missing ~/.pi/agent, unknown --model, provider auth, …; trap T10).
 */
import { ChatManager } from "./manager.ts";
import { createChatServer } from "./server.ts";

interface Args {
	workspace: string;
	port: number;
	model?: string;
}

function parseArgs(argv: string[]): Args {
	const args: Args = { workspace: "", port: 0 };
	for (let i = 0; i < argv.length; i++) {
		const flag = argv[i];
		const value = argv[i + 1];
		switch (flag) {
			case "--workspace":
				if (!value) throw new Error("--workspace requires a path");
				args.workspace = value;
				i++;
				break;
			case "--port": {
				if (!value || !/^\d+$/.test(value)) throw new Error("--port requires a number (0 = ephemeral)");
				args.port = Number(value);
				i++;
				break;
			}
			case "--model":
				if (!value) throw new Error("--model requires provider/model");
				args.model = value;
				i++;
				break;
			default:
				throw new Error(`unknown argument: ${flag ?? "(none)"} (usage: main.ts --workspace <path> [--port <n>] [--model provider/model])`);
		}
	}
	if (!args.workspace) throw new Error("--workspace <path> is required");
	return args;
}

async function main(): Promise<void> {
	const args = parseArgs(process.argv.slice(2));

	// Boot failure path (§5): one clear stderr line, exit non-zero — the
	// manager surfaces it as the down-reason. Never a stack dump.
	const manager = await ChatManager.create(args.workspace, args.model).catch((err: unknown) => {
		const message = err instanceof Error ? err.message : String(err);
		console.error(`sidecar boot failed: ${message}`);
		process.exit(1);
	});

	const chat = await createChatServer(manager, args.port);
	process.stdout.write(
		`SIDECAR_READY ${JSON.stringify({
			port: chat.port,
			model: manager.modelId,
			session_file: manager.sessionFile,
		})}\n`,
	);

	let shuttingDown = false;
	const shutdown = (): void => {
		if (shuttingDown) return;
		shuttingDown = true;
		// dispose() aborts an in-flight turn and awaits it (trap T5), so a
		// wedged provider call cannot stall shutdown; closeAllConnections
		// reaps the SSE sockets that would hold server.close() open.
		void manager
			.dispose()
			.catch(() => {})
			.finally(() => {
				chat.close();
				process.exit(0);
			});
	};
	process.on("SIGTERM", shutdown);
	process.on("SIGINT", shutdown);
}

main().catch((err: unknown) => {
	const message = err instanceof Error ? err.message : String(err);
	console.error(`sidecar boot failed: ${message}`);
	process.exit(1);
});

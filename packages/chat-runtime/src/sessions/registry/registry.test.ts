import { describe, expect, test } from "bun:test";
import { randomUUID } from "node:crypto";
import type { AdapterEvent, HarnessAdapter } from "../../harness";
import { FakeHarness } from "../../harness/fake";
import { createTestRuntime } from "../../testing/testRuntime";
import { waitFor } from "../../testing/testUtils";
import type { HarnessRegistry } from "./registry";

const HARNESS = "registry-fake";

function idleAdapter(): HarnessAdapter {
	return new FakeHarness({ turns: [] });
}

function registryOf(factory: () => HarnessAdapter): HarnessRegistry {
	return new Map([[HARNESS, factory]]);
}

function onDispose(after: () => void): HarnessAdapter {
	const inner = new FakeHarness({ turns: [] });
	return {
		start: (options) => inner.start(options),
		prompt: (content) => inner.prompt(content),
		cancelTurn: () => inner.cancelTurn(),
		respondToApproval: (id, decision) => inner.respondToApproval(id, decision),
		setMode: (modeId) => inner.setMode(modeId),
		dispose: async () => {
			await inner.dispose();
			after();
		},
	};
}

function createSession(runtime: ReturnType<typeof createTestRuntime>): string {
	return runtime.commands.createSession({
		commandId: randomUUID(),
		scopeId: "workspace-1",
		harness: HARNESS,
		cwd: "/tmp/workspace",
	}).sessionId;
}

describe("LiveSessionRegistry", () => {
	test("a host which acknowledged idle refuses new sessions, prompts and resumed work until cancellation", async () => {
		let held = false;
		const runtime = createTestRuntime({
			harnesses: registryOf(
				() =>
					new FakeHarness({
						start: [{ kind: "session", session: { status: "idle" } }],
						turns: [[]],
					}),
			),
			assertChatAdmission: () => {
				if (held) throw new Error("update in progress");
			},
		});
		try {
			const id = createSession(runtime);
			const live = runtime.live.require(id);
			await waitFor(() => live.state.status === "idle");
			expect(runtime.live.hasActiveWork()).toBe(false);
			held = true;
			const prompt = () =>
				runtime.commands.prompt({
					commandId: randomUUID(),
					sessionId: id,
					clientId: randomUUID(),
					content: [{ type: "text", text: "run" }],
				});
			expect(() => createSession(runtime)).toThrow("update in progress");
			expect(prompt).toThrow("update in progress");
			expect(() =>
				live.promptOnceStarted([[{ type: "text", text: "resume" }]]),
			).toThrow("update in progress");
			expect(runtime.sessions.list()).toHaveLength(1);
			held = false;
			expect(prompt).not.toThrow();
		} finally {
			await runtime.dispose();
		}
	});

	test("an in-flight native discovery counts as work and a held host cannot spawn discovery", async () => {
		let held = false;
		const finish = Promise.withResolvers<{
			supported: boolean;
			sessions: [];
		}>();
		const runtime = createTestRuntime({
			assertChatAdmission: () => {
				if (held) throw new Error("desktop update");
			},
			harnesses: registryOf(() =>
				Object.assign(idleAdapter(), {
					discoverSessions: () => finish.promise,
				}),
			),
		});
		const input = { harness: HARNESS, scopeId: "scope", cwd: "/tmp" };
		try {
			const discovery = runtime.live.discoverSessions(input);
			expect(runtime.live.hasActiveWork()).toBe(true);
			finish.resolve({ supported: true, sessions: [] });
			await discovery;
			expect(runtime.live.hasActiveWork()).toBe(false);
			held = true;
			await expect(runtime.live.discoverSessions(input)).rejects.toThrow(
				"desktop update",
			);
		} finally {
			finish.resolve({ supported: true, sessions: [] });
			await runtime.dispose();
		}
	});

	test("an update sees a starting chat even before the harness assigns a session id", async () => {
		const runtime = createTestRuntime({ harnesses: registryOf(idleAdapter) });
		expect(runtime.live.hasActiveWork()).toBe(false);
		const sessionId = createSession(runtime);
		expect(runtime.live.hasActiveWork()).toBe(true);
		await runtime.live.dispose(sessionId);
		expect(runtime.live.hasActiveWork()).toBe(false);
		await runtime.dispose();
	});

	test("refuses to replace a session that is already running", async () => {
		const runtime = createTestRuntime({ harnesses: registryOf(idleAdapter) });
		const sessionId = createSession(runtime);

		expect(() =>
			runtime.live.create({
				sessionId,
				scopeId: "workspace-1",
				harness: HARNESS,
				cwd: "/tmp/workspace",
			}),
		).toThrow(/already running/);

		await runtime.dispose();
	});

	test("unregisters and disposes a session whose start throws", async () => {
		let disposed = false;
		const runtime = createTestRuntime({
			harnesses: registryOf(() => ({
				start: (): AsyncIterable<AdapterEvent> => {
					throw new Error("spawn failed");
				},
				prompt: () => undefined,
				cancelTurn: () => undefined,
				respondToApproval: () => undefined,
				setMode: () => Promise.resolve(true),
				dispose: async () => {
					disposed = true;
				},
			})),
		});

		expect(() => createSession(runtime)).toThrow("spawn failed");
		expect(runtime.sessions.list()).toEqual([]);
		expect(disposed).toBe(true);

		await runtime.dispose();
	});

	test("closing a scope stops only that scope's sessions", async () => {
		const runtime = createTestRuntime({ harnesses: registryOf(idleAdapter) });
		const kept = createSession(runtime);
		const removed = runtime.commands.createSession({
			commandId: randomUUID(),
			scopeId: "workspace-2",
			harness: HARNESS,
			cwd: "/tmp/workspace-2",
		}).sessionId;

		await runtime.commands.closeScope("workspace-2");

		expect(runtime.live.get(removed)).toBeNull();
		expect(runtime.live.get(kept)).not.toBeNull();
		await runtime.dispose();
	});

	test("settles every disposal and still closes the database when one fails", async () => {
		let secondDisposed = false;
		let created = 0;
		const runtime = createTestRuntime({
			harnesses: registryOf(() => {
				created += 1;
				return created === 1
					? onDispose(() => {
							throw new Error("adapter teardown failed");
						})
					: onDispose(() => {
							secondDisposed = true;
						});
			}),
		});

		createSession(runtime);
		createSession(runtime);

		await expect(runtime.dispose()).rejects.toThrow("adapter teardown failed");
		expect(secondDisposed).toBe(true);
		expect(() => runtime.sessions.list()).toThrow();
	});
});

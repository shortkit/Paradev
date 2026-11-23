import { describe, expect, it, mock } from "bun:test";
import type { ChatRuntime } from "@superset/chat-runtime";
import { promptChatSession } from "./promptChatSession";

function runtime() {
	const prompt = mock((_input: unknown) => ({ itemId: "i", queued: false }));
	return {
		prompt,
		runtime: {
			commands: { prompt },
			live: {
				get: () => ({ state: { status: "idle" } }),
				runNativeOperation: <T>(work: () => Promise<T>) => work(),
			},
		} as unknown as ChatRuntime,
	};
}

describe("promptChatSession", () => {
	it("a draining host rejects a native prompt before reserving delivery", async () => {
		const { runtime: chat, prompt } = runtime();
		chat.live.runNativeOperation = async () => {
			throw new Error("Desktop update in progress");
		};
		const acquireDelivery = mock(async () => ({ isValid: () => true }));
		await expect(
			promptChatSession({
				runtime: chat,
				chatSessionId: "chat-1",
				text: "deferred",
				acquireDelivery,
				signal: new AbortController().signal,
			}),
		).rejects.toThrow("Desktop update");
		expect(acquireDelivery).not.toHaveBeenCalled();
		expect(prompt).not.toHaveBeenCalled();
	});

	it("sends the text to the chat once delivery is acquired", async () => {
		const { prompt, runtime: chat } = runtime();
		await promptChatSession({
			runtime: chat,
			chatSessionId: "chat-1",
			text: "Fix the heading",
			acquireDelivery: async () => ({ isValid: () => true }),
			signal: new AbortController().signal,
		});
		expect(prompt.mock.calls[0]?.[0]).toMatchObject({
			sessionId: "chat-1",
			content: [{ type: "text", text: "Fix the heading" }],
		});
	});

	it("sends nothing when delivery ownership was lost", async () => {
		const { prompt, runtime: chat } = runtime();
		await expect(
			promptChatSession({
				runtime: chat,
				chatSessionId: "chat-1",
				text: "Fix the heading",
				acquireDelivery: async () => null,
				signal: new AbortController().signal,
			}),
		).rejects.toThrow();
		expect(prompt).not.toHaveBeenCalled();
	});

	it("sends nothing when the acquired delivery is no longer valid", async () => {
		const { prompt, runtime: chat } = runtime();
		await expect(
			promptChatSession({
				runtime: chat,
				chatSessionId: "chat-1",
				text: "Fix the heading",
				acquireDelivery: async () => ({ isValid: () => false }),
				signal: new AbortController().signal,
			}),
		).rejects.toThrow();
		expect(prompt).not.toHaveBeenCalled();
	});
});

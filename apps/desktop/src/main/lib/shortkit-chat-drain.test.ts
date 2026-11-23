import { expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { ShortkitChatDrain } from "./shortkit-chat-drain";

test("one idle host holds starts while a second answers, cancellation releases all holds", () => {
	const home = mkdtempSync(join(tmpdir(), "chat-drain-"));
	try {
		const desktop = new ShortkitChatDrain(home);
		const first = new ShortkitChatDrain(home);
		const second = new ShortkitChatDrain(home);
		const token = desktop.begin();
		expect(first.holdIfIdle(token, false)).toBe(token);
		expect(() => first.assertAdmission()).toThrow();
		expect(second.holdIfIdle(token, true)).toBe(null);
		expect(() => second.assertAdmission()).not.toThrow();
		desktop.cancel();
		expect(() => first.assertAdmission()).not.toThrow();
		const next = desktop.begin();
		expect(() => first.holdIfIdle(token, false)).toThrow();
		expect(first.holdIfIdle(next, false)).toBe(next);
	} finally {
		rmSync(home, { recursive: true, force: true });
	}
});

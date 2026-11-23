import { expect, test } from "bun:test";
import { EventEmitter } from "node:events";
import type { WebContents } from "electron";
import { trackShortkitRenderer } from "./shortkit-renderer-health";

test("requires the mounted route in this build and frame after each navigation", () => {
	const contents = Object.assign(new EventEmitter(), {
		mainFrame: { url: "file:///bundle/index.html#/sign-in" },
		ipc: new EventEmitter(),
		isCrashed: () => false,
		isDestroyed: () => false,
		isLoadingMainFrame: () => false,
	});
	const ready = trackShortkitRenderer(
		contents as unknown as WebContents,
		"build-b",
		"/bundle/index.html",
	);
	const ack = (commit: string, frame = contents.mainFrame, mounted = true) =>
		contents.ipc.emit(
			"shortkit:renderer-ready",
			{ senderFrame: frame },
			commit,
			mounted,
		);
	expect(ready()).toBe(false);
	contents.mainFrame.url = "file:///other/index.html#/sign-in";
	ack("build-b");
	expect(ready()).toBe(false);
	contents.mainFrame.url = "file:///bundle/index.html#/sign-in";
	ack("build-a");
	ack("build-b", { url: contents.mainFrame.url });
	expect(ready()).toBe(false);
	ack("build-b");
	expect(ready()).toBe(true);
	ack("build-b", contents.mainFrame, false);
	expect(ready()).toBe(false);
	for (const failure of [
		"preload-error",
		"render-process-gone",
		"did-fail-load",
	]) {
		contents.emit("did-start-navigation", {
			isMainFrame: true,
			isSameDocument: false,
		});
		ack("build-b");
		expect(ready()).toBe(true);
		contents.emit(failure, {}, -1, "", "", true);
		ack("build-b");
		expect(ready()).toBe(false);
	}
});

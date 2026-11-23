import { pathToFileURL } from "node:url";
import type { WebContents } from "electron";
import { SHORTKIT_RENDERER_READY } from "shared/shortkit-build";

const renderers = new WeakMap<WebContents, () => boolean>();

export function trackShortkitRenderer(
	contents: WebContents,
	commit: string,
	htmlFile: string,
) {
	const expected = pathToFileURL(htmlFile).href;
	let mounted = false;
	let failed = false;
	const fail = () => {
		mounted = false;
		failed = true;
	};
	contents.on("did-start-navigation", ({ isMainFrame, isSameDocument }) => {
		if (isMainFrame && !isSameDocument) {
			mounted = false;
			failed = false;
		}
	});
	contents.on("did-navigate", () => {
		mounted = false;
	});
	contents.on("did-fail-load", (_event, _code, _message, _url, mainFrame) => {
		if (mainFrame) fail();
	});
	contents.on("render-process-gone", fail);
	contents.on("preload-error", fail);
	contents.ipc.on(SHORTKIT_RENDERER_READY, (event, build, ready) => {
		if (
			!failed &&
			event.senderFrame &&
			event.senderFrame === contents.mainFrame &&
			event.senderFrame.url.split("#", 1)[0] === expected &&
			build === commit
		)
			mounted = ready === true;
	});
	const ready = () =>
		mounted &&
		!contents.isDestroyed() &&
		!contents.isCrashed() &&
		!contents.isLoadingMainFrame();
	renderers.set(contents, ready);
	return ready;
}

export function isShortkitRendererReady(contents?: WebContents): boolean {
	return contents ? renderers.get(contents)?.() === true : false;
}

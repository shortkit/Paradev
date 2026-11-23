import { join } from "node:path";
import { BrowserWindow } from "electron";
import { registerRoute } from "lib/window-loader";
import { trackShortkitRenderer } from "main/lib/shortkit-renderer-health";
import { shortkitCommit } from "shared/shortkit-build";
import type { WindowProps } from "shared/types";

export function createWindow({ id, ...settings }: WindowProps) {
	const window = new BrowserWindow(settings);

	const htmlFile = join(__dirname, "../renderer/index.html");
	if (shortkitCommit && id === "main")
		trackShortkitRenderer(window.webContents, shortkitCommit, htmlFile);
	registerRoute({
		id,
		browserWindow: window,
		htmlFile,
	});

	window.on("closed", window.destroy);

	return window;
}

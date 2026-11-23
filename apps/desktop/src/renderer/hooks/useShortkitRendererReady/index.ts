import { useLayoutEffect } from "react";
import { SHORTKIT_RENDERER_READY, shortkitCommit } from "shared/shortkit-build";

export function useShortkitRendererReady(usable: boolean) {
	useLayoutEffect(() => {
		if (!shortkitCommit || !usable) return;
		window.ipcRenderer.send(SHORTKIT_RENDERER_READY, shortkitCommit, true);
		return () =>
			window.ipcRenderer.send(SHORTKIT_RENDERER_READY, shortkitCommit, false);
	}, [usable]);
}

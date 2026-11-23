import { chmodSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { join } from "node:path";
import { app } from "electron";
import { shortkitCommit } from "shared/shortkit-build";
import { SUPERSET_HOME_DIR } from "./app-environment";
import type { getHostServiceCoordinator } from "./host-service-coordinator";
import { ShortkitChatDrain } from "./shortkit-chat-drain";
import { isShortkitRendererReady } from "./shortkit-renderer-health";
import { getAllWindows } from "./window-registry/window-registry";

export function startShortkitUpdateControl(
	coordinator: ReturnType<typeof getHostServiceCoordinator>,
	loadAuth: () => Promise<{
		token: string | null;
		organizationIds: string[] | null;
	}>,
	quit: () => void,
): void {
	const socket = join(SUPERSET_HOME_DIR, "desktop-update.sock");
	const drain = new ShortkitChatDrain(SUPERSET_HOME_DIR);
	// Only the instance holding Electron's single-instance lock gets here.
	drain.cancel();
	rmSync(socket, { force: true });
	const server = createServer(async (request, response) => {
		const shouldQuit =
			request.method === "POST" &&
			request.url === `/quit/${process.pid}/${shortkitCommit}`;
		if (
			!shouldQuit &&
			!(request.method === "GET" && request.url === "/status")
		) {
			response.writeHead(404).end();
			return;
		}
		let token: string | null = null;
		const cancel = () => {
			if (token === null) return;
			drain.cancel();
			token = null;
		};
		try {
			if (shouldQuit) {
				token = drain.begin();
				response.once("close", () => {
					if (!response.writableFinished) cancel();
				});
			}
			const auth = await loadAuth();
			if (auth.token && !auth.organizationIds)
				throw new Error("Membership unavailable");
			const { instances, assertCurrent } = coordinator.captureUpdateHosts();
			if (auth.organizationIds?.some((id) => !instances.has(id)))
				throw new Error("Host unavailable");
			const hosts = await Promise.all(
				[...instances].map(async ([id, instance]) => {
					const result = await fetch(
						`http://127.0.0.1:${instance.port}/desktop/shortkit-update${token ? `/${token}` : ""}`,
						{
							method: shouldQuit ? "POST" : "GET",
							headers: { Authorization: `Bearer ${instance.secret}` },
							redirect: "error",
							signal: AbortSignal.timeout(2500),
						},
					);
					if (!result.ok) throw new Error("Cannot query host");
					const host = await result.json();
					if (
						host.organizationId !== id ||
						host.commit !== shortkitCommit ||
						typeof host.activeChats !== "boolean" ||
						host.pid !== instance.pid ||
						(shouldQuit && !host.activeChats && (!token || host.held !== token))
					)
						throw new Error("Unknown host state");
					return { activeChats: host.activeChats === true, pid: instance.pid };
				}),
			);
			assertCurrent();
			const activeChats = hosts.some((host) => host.activeChats);
			if (shouldQuit && activeChats) {
				cancel();
				response.writeHead(409).end("Active chat work");
				return;
			}
			if (response.destroyed) {
				cancel();
				return;
			}
			const status = {
				commit: shortkitCommit,
				pid: process.pid,
				executable: process.execPath,
				userData: app.getPath("userData"),
				home: SUPERSET_HOME_DIR,
				activeChats,
				ready: isShortkitRendererReady(getAllWindows()[0]?.webContents),
				organizations: [...instances.keys()].sort(),
				hostPids: hosts.map((host) => host.pid),
			};
			response
				.writeHead(200, { "content-type": "application/json" })
				.end(JSON.stringify(status), (error?: Error | null) => {
					if (!token) return;
					try {
						if (error) throw error;
						assertCurrent();
						quit();
					} catch {
						cancel();
					}
				});
		} catch {
			cancel();
			if (!response.destroyed)
				response.writeHead(503).end("Cannot establish fork host or chat state");
		}
	});
	server.on("error", (error) =>
		console.error("[shortkit-update] Control socket unavailable", error),
	);
	server.listen(socket, () => chmodSync(socket, 0o600));
}

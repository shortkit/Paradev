import { chmodSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { join } from "node:path";
import { app, BrowserWindow } from "electron";
import { shortkitCommit } from "shared/shortkit-build";
import { SUPERSET_HOME_DIR } from "./app-environment";
import type { getHostServiceCoordinator } from "./host-service-coordinator";

export function startShortkitUpdateControl(
	coordinator: ReturnType<typeof getHostServiceCoordinator>,
	loadAuth: () => Promise<{
		token: string | null;
		organizationIds: string[] | null;
	}>,
	quit: () => void,
): void {
	const socket = join(SUPERSET_HOME_DIR, "desktop-update.sock");
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
		try {
			const auth = await loadAuth();
			if (auth.token && !auth.organizationIds)
				throw new Error("Membership unavailable");
			const organizations = new Set([
				...coordinator.getActiveOrganizationIds(),
				...(auth.organizationIds ?? []),
			]);
			const hosts = await Promise.all(
				[...organizations].map(async (id) => {
					const connection = coordinator.getConnection(id);
					if (!connection) throw new Error("Host unavailable");
					const result = await fetch(
						`http://127.0.0.1:${connection.port}/desktop/shortkit-update`,
						{
							headers: { Authorization: `Bearer ${connection.secret}` },
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
						!Number.isSafeInteger(host.pid) ||
						host.pid < 2
					)
						throw new Error("Unknown host state");
					return {
						activeChats: host.activeChats === true,
						pid: Number(host.pid),
					};
				}),
			);
			const activeChats = hosts.some((host) => host.activeChats);
			if (shouldQuit && activeChats) {
				response.writeHead(409).end("Active chat work");
				return;
			}
			const ready = BrowserWindow.getAllWindows().some(
				(w) =>
					!w.webContents.isCrashed() && !w.webContents.isLoadingMainFrame(),
			);
			const status = {
				commit: shortkitCommit,
				pid: process.pid,
				executable: process.execPath,
				userData: app.getPath("userData"),
				home: SUPERSET_HOME_DIR,
				activeChats,
				ready,
				organizations: [...organizations].sort(),
				hostPids: hosts.map((host) => host.pid),
			};
			response
				.writeHead(200, { "content-type": "application/json" })
				.end(JSON.stringify(status), () => {
					if (shouldQuit) quit();
				});
		} catch {
			response.writeHead(503).end("Cannot establish fork host or chat state");
		}
	});
	server.on("error", (error) =>
		console.error("[shortkit-update] Control socket unavailable", error),
	);
	server.listen(socket, () => chmodSync(socket, 0o600));
}

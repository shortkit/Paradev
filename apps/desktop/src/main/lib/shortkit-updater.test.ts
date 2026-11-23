import { expect, test } from "bun:test";

test("the fork ignores an upstream staged event and disables native checks and installation", () => {
	const result = Bun.spawnSync(
		[
			process.execPath,
			"-e",
			`
		import {mock} from "bun:test";
		import {EventEmitter} from "node:events";
		const native = new EventEmitter();
		const updater = {autoDownload:true, autoInstallOnAppQuit:true};
		mock.module("electron", () => ({app:{getVersion:() => "2.0.11"}, dialog:{}, autoUpdater:native}));
		mock.module("electron-updater", () => ({autoUpdater:updater}));
		mock.module("main/index", () => ({setSkipQuitConfirmation(){throw Error("unexpected quit")}}));
		mock.module("main/lib/app-state", () => ({appState:{}}));
		mock.module("electron-log/main", () => ({default:{}}));
		mock.module("@sentry/electron/main", () => ({}));
		const api = await import("./src/main/lib/auto-updater.ts");
		api.setupAutoUpdater();
		native.emit("update-downloaded");
		api.checkForUpdates(); api.checkForUpdatesInteractive(); api.installUpdate();
		console.log(JSON.stringify([updater.autoDownload, updater.autoInstallOnAppQuit, api.isUpdateReadyToInstall()]));
	`,
		],
		{
			cwd: `${import.meta.dir}/../../..`,
			env: {
				PATH: process.env.PATH,
				NODE_ENV: "production",
				SHORTKIT_BUILD_COMMIT: "b".repeat(40),
			},
		},
	);
	expect(result.stderr.toString()).toBe("");
	expect(result.exitCode).toBe(0);
	expect(JSON.parse(result.stdout.toString())).toEqual([false, false, false]);
});

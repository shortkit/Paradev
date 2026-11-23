import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { execFileSync } from "node:child_process";
import {
	chmodSync,
	existsSync,
	mkdirSync,
	mkdtempSync,
	readFileSync,
	rmSync,
	statSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import {
	BUNDLED_CLI_SHIM_MARKER,
	buildBundledCliShim,
	buildBundledPdevShim,
	getBundledCliBinaryName,
	getBundledCliShimName,
	installBundledCliShim,
	installBundledPdevShim,
	resolveBundledPdevPath,
} from "./bundled-cli";

describe("bundled CLI", () => {
	let tempDir: string;
	let binDir: string;
	let bundledCliPath: string;

	beforeEach(() => {
		tempDir = mkdtempSync(path.join(tmpdir(), "superset-bundled-cli-"));
		binDir = path.join(tempDir, "bin");
		bundledCliPath = path.join(tempDir, "resources", "bin", "superset");
		mkdirSync(path.dirname(bundledCliPath), { recursive: true });
		writeFileSync(bundledCliPath, "#!/bin/sh\n", { mode: 0o755 });
	});

	afterEach(() => {
		rmSync(tempDir, { recursive: true, force: true });
	});

	it("uses the platform-specific binary and shim names", () => {
		expect(getBundledCliBinaryName("darwin")).toBe("superset");
		expect(getBundledCliShimName("darwin")).toBe("superset");
		expect(getBundledCliBinaryName("win32")).toBe("superset.exe");
		expect(getBundledCliShimName("win32")).toBe("superset.cmd");
	});

	it("builds a POSIX shim that execs the bundled binary safely", () => {
		const cliPath =
			"/Applications/Superset Test.app/Contents/Resources/bin/super'set";
		const shim = buildBundledCliShim(cliPath, "darwin");

		expect(shim).toContain(BUNDLED_CLI_SHIM_MARKER);
		expect(shim).toContain(
			`exec '/Applications/Superset Test.app/Contents/Resources/bin/super'"'"'set' "$@"`,
		);
	});

	it("routes a fork shim from a stock shell and preserves the fork's own terminal context", () => {
		const forkHome = path.join(tempDir, ".paradev-shortkit");
		const shim = path.join(tempDir, "shim");
		writeFileSync(
			bundledCliPath,
			'#!/bin/sh\nprintf "%s|" "$SUPERSET_HOME_DIR" "$HOST_CLIENT_ID" "$SUPERSET_WORKSPACE_ID" "$SUPERSET_API_KEY" "$HOST_SERVICE_SECRET"',
		);
		writeFileSync(
			shim,
			buildBundledCliShim(bundledCliPath, "darwin", {
				home: forkHome,
				hostId: "fork-id",
			}),
			{ mode: 0o755 },
		);
		const call = (home: string) =>
			execFileSync(shim, {
				encoding: "utf8",
				env: {
					SUPERSET_HOME_DIR: home,
					HOST_CLIENT_ID: "inherited-id",
					SUPERSET_WORKSPACE_ID: "workspace",
					SUPERSET_API_KEY: "account",
					HOST_SERVICE_SECRET: "host",
				},
			});
		expect(call("/stock")).toBe(`${forkHome}|fork-id||||`);
		expect(call(forkHome)).toBe(`${forkHome}|fork-id|workspace|account|host|`);
	});

	it("resolves --local with the fork host id and manifest", () => {
		const home = path.join(tempDir, "fork");
		const orgDirectory = path.join(home, "host", "my-org");
		mkdirSync(orgDirectory, { recursive: true });
		writeFileSync(
			path.join(orgDirectory, "manifest.json"),
			JSON.stringify({
				pid: process.pid,
				endpoint: "http://127.0.0.1:54321",
				authToken: "fixture",
				organizationId: "my-org",
				startedAt: Date.now(),
			}),
		);
		const shim = path.join(tempDir, "superset");
		writeFileSync(
			shim,
			buildBundledCliShim(process.execPath, "darwin", {
				home,
				hostId: "fork-id",
			}),
			{ mode: 0o755 },
		);
		const result = execFileSync(
			shim,
			[
				"-e",
				`
			import { requireHostTarget } from "./src/lib/host-target/resolveHostFlags.ts";
			import { resolveHostTarget } from "./src/lib/host-target/resolveHostTarget.ts";
			const local = requireHostTarget({local:true});
			const {kind, hostId, ws} = await resolveHostTarget({requestedHostId:local, organizationId:"my-org", userJwt:""});
			console.log(JSON.stringify({kind, hostId, ws}));
		`,
			],
			{
				cwd: path.resolve(import.meta.dir, "../../../../../packages/cli"),
				encoding: "utf8",
				env: {
					PATH: process.env.PATH,
					SUPERSET_HOME_DIR: path.join(tempDir, "stock"),
					HOST_CLIENT_ID: "stock-id",
				},
			},
		);
		expect(JSON.parse(result)).toEqual({
			kind: "local",
			hostId: "fork-id",
			ws: { baseWsUrl: "ws://127.0.0.1:54321", token: "fixture" },
		});
	});

	it("installs an executable managed shim into the terminal bin directory", () => {
		const status = installBundledCliShim({
			binDir,
			bundledCliPath,
			platform: "darwin",
		});
		const shimPath = path.join(binDir, "superset");

		expect(status).toBe("installed");
		expect(existsSync(shimPath)).toBe(true);
		expect(readFileSync(shimPath, "utf-8")).toContain(BUNDLED_CLI_SHIM_MARKER);
		expect(statSync(shimPath).mode & 0o111).not.toBe(0);
		expect(readFileSync(path.join(binDir, "paradev"), "utf-8")).toBe(
			readFileSync(shimPath, "utf-8"),
		);
	});

	it("updates an existing managed shim", () => {
		const shimPath = path.join(binDir, "superset");
		mkdirSync(binDir, { recursive: true });
		writeFileSync(shimPath, `${BUNDLED_CLI_SHIM_MARKER}\nold\n`, {
			mode: 0o755,
		});

		const status = installBundledCliShim({
			binDir,
			bundledCliPath,
			platform: "darwin",
		});

		expect(status).toBe("installed");
		expect(readFileSync(shimPath, "utf-8")).toContain(bundledCliPath);
	});

	it("does not overwrite an unmanaged superset executable", () => {
		const shimPath = path.join(binDir, "superset");
		mkdirSync(binDir, { recursive: true });
		writeFileSync(shimPath, "#!/bin/sh\necho custom\n", { mode: 0o755 });
		chmodSync(shimPath, 0o755);

		const status = installBundledCliShim({
			binDir,
			bundledCliPath,
			platform: "darwin",
		});

		expect(status).toBe("skipped");
		expect(readFileSync(shimPath, "utf-8")).toBe("#!/bin/sh\necho custom\n");
	});

	it("returns missing when the bundled binary is unavailable", () => {
		const status = installBundledCliShim({
			binDir,
			bundledCliPath: path.join(tempDir, "missing", "superset"),
			platform: "darwin",
		});

		expect(status).toBe("missing");
	});
});

describe("bundled pdev", () => {
	let tempDir: string;
	let binDir: string;
	let bundledPdevPath: string;

	beforeEach(() => {
		tempDir = mkdtempSync(path.join(tmpdir(), "superset-bundled-pdev-"));
		binDir = path.join(tempDir, "bin");
		bundledPdevPath = path.join(tempDir, "resources", "bin", "pdev", "pdev");
		mkdirSync(path.dirname(bundledPdevPath), { recursive: true });
		writeFileSync(bundledPdevPath, "#!/bin/sh\n", { mode: 0o755 });
	});

	afterEach(() => {
		rmSync(tempDir, { recursive: true, force: true });
	});

	it("execs the bundled pdev without setting its environment", () => {
		const shim = buildBundledPdevShim(bundledPdevPath, "darwin");

		expect(shim).toContain(BUNDLED_CLI_SHIM_MARKER);
		expect(shim).toContain(`exec '${bundledPdevPath}' "$@"`);
		expect(shim).not.toContain("export ");
	});

	it("installs a managed pdev shim next to the superset shim", () => {
		const status = installBundledPdevShim({
			binDir,
			bundledPdevPath,
			platform: "darwin",
		});
		const shimPath = path.join(binDir, "pdev");

		expect(status).toBe("installed");
		expect(readFileSync(shimPath, "utf-8")).toContain(
			`exec '${bundledPdevPath}' "$@"`,
		);
		expect(statSync(shimPath).mode & 0o111).not.toBe(0);
	});

	it("does not overwrite a pdev the user installed", () => {
		const shimPath = path.join(binDir, "pdev");
		mkdirSync(binDir, { recursive: true });
		writeFileSync(shimPath, "#!/bin/sh\necho mine\n", { mode: 0o755 });

		const status = installBundledPdevShim({
			binDir,
			bundledPdevPath,
			platform: "darwin",
		});

		expect(status).toBe("skipped");
		expect(readFileSync(shimPath, "utf-8")).toBe("#!/bin/sh\necho mine\n");
	});

	it("installs nothing when pdev is not bundled", () => {
		const status = installBundledPdevShim({
			binDir,
			bundledPdevPath: path.join(tempDir, "missing", "pdev"),
			platform: "darwin",
		});

		expect(status).toBe("missing");
		expect(existsSync(path.join(binDir, "pdev"))).toBe(false);
	});

	it("removes its own shim once pdev is no longer bundled", () => {
		const shimPath = path.join(binDir, "pdev");
		installBundledPdevShim({ binDir, bundledPdevPath, platform: "darwin" });

		const status = installBundledPdevShim({
			binDir,
			bundledPdevPath: path.join(tempDir, "missing", "pdev"),
			platform: "darwin",
		});

		expect(status).toBe("missing");
		expect(existsSync(shimPath)).toBe(false);
	});

	it("keeps a user's pdev when pdev is not bundled", () => {
		const shimPath = path.join(binDir, "pdev");
		mkdirSync(binDir, { recursive: true });
		writeFileSync(shimPath, "#!/bin/sh\necho mine\n", { mode: 0o755 });

		installBundledPdevShim({
			binDir,
			bundledPdevPath: path.join(tempDir, "missing", "pdev"),
			platform: "darwin",
		});

		expect(readFileSync(shimPath, "utf-8")).toBe("#!/bin/sh\necho mine\n");
	});

	it("is never bundled on Windows", () => {
		expect(resolveBundledPdevPath("win32")).toBeNull();
	});
});

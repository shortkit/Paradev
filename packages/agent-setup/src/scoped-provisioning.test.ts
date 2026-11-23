import { expect, test } from "bun:test";
import {
	mkdirSync,
	mkdtempSync,
	readdirSync,
	readFileSync,
	rmSync,
	writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

test("an empty fork ledger provisions local hooks and leaves agent configurations alone", () => {
	const home = mkdtempSync(path.join(tmpdir(), "shortkit-provision-"));
	const appHome = path.join(home, ".paradev-shortkit");
	const codex = path.join(home, ".codex");
	const managed =
		"# >>> superset managed mcp servers >>>\n[mcp_servers.keep]\nurl = 'https://example.com'\n# <<< superset managed mcp servers <<<\n";
	try {
		const skillRoots = [".claude", ".agents"].map((name) =>
			path.join(home, name, "skills", "keep"),
		);
		for (const dir of skillRoots) {
			mkdirSync(dir, { recursive: true });
			writeFileSync(path.join(dir, ".superset-managed"), "");
			writeFileSync(path.join(dir, "SKILL.md"), "installed by stock");
		}
		mkdirSync(codex);
		writeFileSync(path.join(codex, "config.toml"), managed);
		const result = Bun.spawnSync(
			[
				process.execPath,
				"-e",
				`
			import {setupAgentIntegrations,setupSingleAgent,teardownSingleAgent,syncPlugins,reconcileMcpServers} from "./src/index.ts";
			setupAgentIntegrations(); setupSingleAgent("codex"); teardownSingleAgent("claude");
			await syncPlugins(); reconcileMcpServers({});
		`,
			],
			{
				cwd: path.resolve(import.meta.dir, ".."),
				env: { HOME: home, SUPERSET_HOME_DIR: appHome, PATH: process.env.PATH },
			},
		);
		expect(result.exitCode).toBe(0);
		expect(readFileSync(path.join(codex, "config.toml"), "utf8")).toBe(managed);
		expect(readdirSync(codex)).toEqual(["config.toml"]);
		for (const dir of skillRoots) {
			expect(readFileSync(path.join(dir, "SKILL.md"), "utf8")).toBe(
				"installed by stock",
			);
			expect(readdirSync(path.dirname(dir))).toEqual(["keep"]);
		}
		expect(
			readFileSync(path.join(appHome, "hooks/notify.sh"), "utf8"),
		).toContain("SUPERSET_HOME_DIR");
		expect(readdirSync(path.join(appHome, "bin"))).toContain("codex");
		expect(readdirSync(path.join(appHome, "zsh"))).toContain(".zshrc");
	} finally {
		rmSync(home, { recursive: true, force: true });
	}
});

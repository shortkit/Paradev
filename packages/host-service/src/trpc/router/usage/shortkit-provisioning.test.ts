import { afterEach, beforeEach, expect, spyOn, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { resolveUserHomeDir } from "@superset/agent-setup/paths";
import type { HostDb } from "../../../db";
import { seedAgentFolderTrust } from "../workspace-creation/shared/seed-agent-trust";
import {
	provisionClaudeAccount,
	provisionCodexAccount,
} from "./account-provisioning";
import * as accounts from "./default-account";
import * as sharing from "./session-share";

let previous: string | undefined;
beforeEach(() => {
	previous = process.env.SUPERSET_HOME_DIR;
	process.env.SUPERSET_HOME_DIR = join(
		resolveUserHomeDir(),
		".paradev-shortkit",
	);
});
afterEach(() => {
	if (previous === undefined) delete process.env.SUPERSET_HOME_DIR;
	else process.env.SUPERSET_HOME_DIR = previous;
});

test("switching fork accounts skips Claude and Codex session merges", async () => {
	const claude = spyOn(sharing, "shareClaudeSessionState").mockImplementation(
		() => {},
	);
	const codex = spyOn(sharing, "shareCodexSessionState").mockImplementation(
		() => {},
	);
	try {
		await provisionClaudeAccount("/unused-profile");
		await provisionCodexAccount("/unused-profile");
		expect(claude).not.toHaveBeenCalled();
		expect(codex).not.toHaveBeenCalled();
	} finally {
		claude.mockRestore();
		codex.mockRestore();
	}
});

test("a projectless fork launch leaves the selected Codex account's trust file intact", async () => {
	const profile = mkdtempSync(join(tmpdir(), "fork-trust-"));
	const config = join(profile, "config.toml");
	const original = 'model = "fixture"\n';
	const selection = spyOn(accounts, "resolveDefaultAccountEnv").mockReturnValue(
		{ CODEX_HOME: profile },
	);
	try {
		writeFileSync(config, original);
		await seedAgentFolderTrust({} as HostDb, "/fixture-session", {
			presetId: "codex",
			command: "codex",
			env: {},
		});
		expect(readFileSync(config, "utf8")).toBe(original);
	} finally {
		selection.mockRestore();
		rmSync(profile, { recursive: true, force: true });
	}
});

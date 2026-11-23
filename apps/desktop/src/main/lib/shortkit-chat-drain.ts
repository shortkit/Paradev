import { randomUUID } from "node:crypto";
import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// A cancelled or timed-out HTTP request can still reach a host. Both processes
// consult the same token so even a late acknowledgement releases on cancellation.
export class ShortkitChatDrain {
	private readonly file: string;
	private held: string | null = null;

	constructor(home: string) {
		this.file = join(home, "desktop-chat-drain");
	}

	begin(): string {
		const token = randomUUID();
		writeFileSync(this.file, token, { flag: "wx", mode: 0o600 });
		return token;
	}

	cancel(): void {
		rmSync(this.file, { force: true });
	}

	private current(): string | null {
		try {
			return readFileSync(this.file, "utf8");
		} catch (error) {
			if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
			throw error;
		}
	}

	holdIfIdle(token: string, activeChats: boolean): string | null {
		if (this.current() !== token) throw new Error("Update request expired");
		if (activeChats) return null;
		this.held = token;
		return token;
	}

	assertAdmission(): void {
		if (this.held === null) return;
		if (this.held === this.current())
			throw new Error("Desktop update in progress");
		this.held = null;
	}
}

import { join } from "node:path";
import {
	resolveUserHomeDir,
	SHORTKIT_HOME_NAME,
} from "@superset/agent-setup/paths";
import { getMachineId, hostIdFromMachineId } from "@superset/shared/host-info";
import { shortkitCommit } from "shared/shortkit-build";

export const shortkitEnvironment = shortkitCommit
	? {
			home: join(resolveUserHomeDir(), SHORTKIT_HOME_NAME),
			hostId: hostIdFromMachineId(`dev.shortkit.paradev/${getMachineId()}`),
		}
	: undefined;

if (shortkitEnvironment) {
	process.env.SUPERSET_HOME_DIR = shortkitEnvironment.home;
	process.env.HOST_CLIENT_ID = shortkitEnvironment.hostId;
}

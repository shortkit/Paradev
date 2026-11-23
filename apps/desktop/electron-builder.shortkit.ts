import type { Configuration } from "electron-builder";
import { CURRENT_PROTOCOL_VERSION } from "../../packages/pty-daemon/src/protocol/version";
import base from "./electron-builder";
import { localizeMacDisplayName } from "./scripts/localize-display-name";
import { PROTOCOL_VERSION } from "./src/main/lib/terminal-host/types";

const commit = process.env.SHORTKIT_BUILD_COMMIT;
if (!commit || !/^[0-9a-f]{40}$/.test(commit)) {
	throw new Error("Package the committed fork using shortkit-update prepare");
}
const productName = "Paradev ShortKit";
const config: Configuration = {
	...base,
	appId: "dev.shortkit.paradev",
	productName,
	extraMetadata: { name: "paradev-shortkit", productName },
	protocols: { name: productName, schemes: ["superset-shortkit"] },
	publish: null,
	npmRebuild: false,
	generateUpdatesFilesForAllChannels: false,
	afterPack: localizeMacDisplayName(productName),
	mac: {
		...base.mac,
		target: "dir",
		identity: "-",
		notarize: false,
		extendInfo: {
			...base.mac?.extendInfo,
			CFBundleName: productName,
			CFBundleDisplayName: productName,
			ShortKitCommit: commit,
			ShortKitDaemonProtocols: `${PROTOCOL_VERSION}/${CURRENT_PROTOCOL_VERSION}`,
		},
	},
};
export default config;

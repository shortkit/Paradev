import { I18nProvider } from "@lingui/react";
import { i18n, initI18n } from "@superset/i18n";
import type { ReactNode } from "react";
import { shortkitCommit, shortkitLabel } from "shared/shortkit-build";
import { RendererErrorBoundary } from "../RendererErrorBoundary";

if (!i18n.locale) initI18n();

export function RendererLayout({ children }: { children: ReactNode }) {
	return (
		<RendererErrorBoundary>
			<I18nProvider i18n={i18n}>
				{children}
				{shortkitCommit && (
					<span className="pointer-events-none fixed bottom-1 right-2 z-50 select-none rounded bg-background/90 px-1.5 font-mono text-[10px] text-muted-foreground">
						{shortkitLabel}
					</span>
				)}
			</I18nProvider>
		</RendererErrorBoundary>
	);
}

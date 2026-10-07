// The screen covers Hermes' dashboard, but its navigation stays in the DOM: without this, Tab and screen
// readers walk through links nobody can see. While the screen is mounted, everything else is `inert`.

/** What isolate uses from an Element; enough to test it without a DOM. */
export interface DomNode {
	tagName: string;
	parentElement: DomNode | null;
	children: ArrayLike<DomNode>;
	/** Optional: children are Element, and in the DOM only HTMLElement declares inert. */
	inert?: boolean;
}

/** Make every sibling along the node -> body chain inert; return the function that restores them.
 * ponytail: marks the nodes that exist at mount; if Hermes adds or replaces siblings while the screen is
 * open, those stay active. If that happens, watch body with a MutationObserver. */
export function isolate(node: DomNode): () => void {
	const touched: DomNode[] = [];
	for (
		let n = node;
		n.parentElement && n.parentElement.tagName !== "HTML";
		n = n.parentElement
	) {
		for (const sibling of Array.from(n.parentElement.children)) {
			if (sibling === n || sibling.inert) continue;
			sibling.inert = true;
			touched.push(sibling);
		}
	}
	return () => {
		for (const n of touched) n.inert = false;
	};
}

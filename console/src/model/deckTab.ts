// Opening a deck that needs its scripts in the deck viewer (`deck.html`, its own document and
// policy). The tab is opened with `noopener`, so it has no handle on this one; the inlined deck
// goes to it over a BroadcastChannel named by a random id in its URL fragment, answered once
// and closed. Never postMessage (the tab has no opener) and never storage.

export const DECK_CHANNEL = 'dsl-deck-';
/** How long the console waits for the viewer to ask for its deck. */
export const DECK_WAIT_MS = 30000;

export interface DeckDeps {
  open: (url: string, target: string, features: string) => unknown;
  channel: (name: string) => Pick<BroadcastChannel, 'postMessage' | 'close'> & { onmessage: ((e: MessageEvent) => void) | null };
  random: () => string;
  base: string;
}

function randomId(): string {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return [...b].map((x) => x.toString(16).padStart(2, '0')).join('');
}

const browser = (): DeckDeps => ({
  open: (u, t, f) => window.open(u, t, f),
  channel: (n) => new BroadcastChannel(n),
  random: randomId,
  base: import.meta.env?.BASE_URL ?? '/',
});

/** Open `html` (one self-contained deck) in a new viewer tab. Call from a click: it opens a window. */
export function openDeck(html: string, title: string, deps: DeckDeps = browser()): string {
  const id = deps.random();
  const ch = deps.channel(`${DECK_CHANNEL}${id}`);
  let done = false;
  const stop = setTimeout(() => ch.close(), DECK_WAIT_MS);
  ch.onmessage = (e) => {
    if (done || !(e.data && e.data.ready === true)) return;
    done = true;
    clearTimeout(stop);
    ch.postMessage({ html, title });
    ch.close();
  };
  deps.open(`${deps.base}deck.html#${id}`, '_blank', 'noopener');
  return id;
}

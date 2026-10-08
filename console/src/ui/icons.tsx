// The mockup's icon set, as components, and the Hertie mark the top bar carries (decision 0035
// rule 2).

export const Check = () => (
  <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.2 6.4l2.4 2.4 5.2-5.6" /></svg>
);
export const Skip = () => (
  <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M3 6h6" /></svg>
);
export const Fail = () => (
  <svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M3.5 3.5l5 5M8.5 3.5l-5 5" /></svg>
);
export const Alert = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="6.5" /><path d="M8 4.6v4.2M8 11.2v.2" /></svg>
);
export const Eye = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z" /><circle cx="8" cy="8" r="2" /></svg>
);
export const Ext = () => (
  <svg viewBox="0 0 12 12" width="11" height="11" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M5 2H2.5v7.5H10V7M7 2h3v3M10 2L5.5 6.5" /></svg>
);
export const Lock = () => (
  <svg viewBox="0 0 12 12" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="2.5" y="5.5" width="7" height="5" rx="1" /><path d="M4 5.5V4a2 2 0 014 0v1.5" /></svg>
);
export const Gh = () => (
  <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38v-1.33c-2.23.48-2.7-1.07-2.7-1.07-.36-.92-.89-1.17-.89-1.17-.73-.5.06-.49.06-.49.8.06 1.23.83 1.23.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 014 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.2c0 .21.15.46.55.38A8 8 0 0016 8c0-4.42-3.58-8-8-8z" /></svg>
);
export const Bldg = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M2.5 14.5V3.5l5-2 5 2v11M1 14.5h14M5 6h1M5 9h1M10 6h1M10 9h1M7 14.5v-3h2v3" /></svg>
);
export const Pin = () => (
  <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><path d="M8 15s5-4.6 5-8.5A5 5 0 003 6.5C3 10.4 8 15 8 15z" /><circle cx="8" cy="6.5" r="1.8" /></svg>
);
/** A folder in the file tree, drawn open while it is expanded. */
export const Folder = ({ open = false }: { open?: boolean }) => (
  <svg class="ft-icon" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" aria-hidden="true">
    {open ? <path d="M1.5 12.5v-9h4l1.5 1.5h6v2M1.5 12.5l2-5.5h11l-2 5.5z" /> : <path d="M1.5 3.5h4l1.5 1.5h7.5v7.5h-13z" />}
  </svg>
);
/** A file in the file tree. */
export const File = () => (
  <svg class="ft-icon" viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 1.5h6l3 3v10h-9zM9.5 1.5v3h3" /></svg>
);

/**
 * The Hertie School mark (the site theme's `assets/images/logo.png`) as inline SVG, crisp and
 * transparent in both themes (decision 0035 rule 2): six pillars under a lintel, a crown over
 * the middle two; the middle pillars and the crown red, the rest grey. Drawn in the
 * original's 900-unit coordinates, cropped to the mark.
 */
export const HertieMark = () => (
  <svg class="hertie-mark" viewBox="140 230 632 403" height="26" aria-hidden="true">
    <g fill="#8f8578">
      {[140, 250, 618, 728].map((x) => <rect x={x} y="345" width="44" height="288" />)}
      <rect x="140" y="288" width="630" height="27" />
    </g>
    <g fill="#b2001e">
      {[360, 508].map((x) => <rect x={x} y="345" width="44" height="288" />)}
      <rect x="360" y="230" width="192" height="28" />
    </g>
  </svg>
);

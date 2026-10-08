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

/**
 * The site's tab glyphs (decision 0035 rule 3): Font Awesome Free 5.15.4 solid icons, drawn
 * inline (the console's CSP loads no font CDN). Font Awesome Free icons are CC BY 4.0
 * (https://fontawesome.com/license/free). Each is its viewBox width (the height is 512) and path.
 */
const FA: Record<string, [number, string]> = {
  'home': [576, 'M280.37 148.26L96 300.11V464a16 16 0 0 0 16 16l112.06-.29a16 16 0 0 0 15.92-16V368a16 16 0 0 1 16-16h64a16 16 0 0 1 16 16v95.64a16 16 0 0 0 16 16.05L464 480a16 16 0 0 0 16-16V300L295.67 148.26a12.19 12.19 0 0 0-15.3 0zM571.6 251.47L488 182.56V44.05a12 12 0 0 0-12-12h-56a12 12 0 0 0-12 12v72.61L318.47 43a48 48 0 0 0-61 0L4.34 251.47a12 12 0 0 0-1.6 16.9l25.5 31A12 12 0 0 0 45.15 301l235.22-193.74a12.19 12.19 0 0 1 15.3 0L530.9 301a12 12 0 0 0 16.9-1.6l25.5-31a12 12 0 0 0-1.7-16.93z'],
  'calendar-week': [448, 'M0 464c0 26.5 21.5 48 48 48h352c26.5 0 48-21.5 48-48V192H0v272zm64-192c0-8.8 7.2-16 16-16h288c8.8 0 16 7.2 16 16v64c0 8.8-7.2 16-16 16H80c-8.8 0-16-7.2-16-16v-64zM400 64h-48V16c0-8.8-7.2-16-16-16h-32c-8.8 0-16 7.2-16 16v48H160V16c0-8.8-7.2-16-16-16h-32c-8.8 0-16 7.2-16 16v48H48C21.5 64 0 85.5 0 112v48h448v-48c0-26.5-21.5-48-48-48z'],
  'calendar-alt': [448, 'M0 464c0 26.5 21.5 48 48 48h352c26.5 0 48-21.5 48-48V192H0v272zm320-196c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v40c0 6.6-5.4 12-12 12h-40c-6.6 0-12-5.4-12-12v-40zm0 128c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v40c0 6.6-5.4 12-12 12h-40c-6.6 0-12-5.4-12-12v-40zM192 268c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v40c0 6.6-5.4 12-12 12h-40c-6.6 0-12-5.4-12-12v-40zm0 128c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v40c0 6.6-5.4 12-12 12h-40c-6.6 0-12-5.4-12-12v-40zM64 268c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v40c0 6.6-5.4 12-12 12H76c-6.6 0-12-5.4-12-12v-40zm0 128c0-6.6 5.4-12 12-12h40c6.6 0 12 5.4 12 12v40c0 6.6-5.4 12-12 12H76c-6.6 0-12-5.4-12-12v-40zM400 64h-48V16c0-8.8-7.2-16-16-16h-32c-8.8 0-16 7.2-16 16v48H160V16c0-8.8-7.2-16-16-16h-32c-8.8 0-16 7.2-16 16v48H48C21.5 64 0 85.5 0 112v48h448v-48c0-26.5-21.5-48-48-48z'],
  'book-reader': [512, 'M352 96c0-53.02-42.98-96-96-96s-96 42.98-96 96 42.98 96 96 96 96-42.98 96-96zM233.59 241.1c-59.33-36.32-155.43-46.3-203.79-49.05C13.55 191.13 0 203.51 0 219.14v222.8c0 14.33 11.59 26.28 26.49 27.05 43.66 2.29 131.99 10.68 193.04 41.43 9.37 4.72 20.48-1.71 20.48-11.87V252.56c-.01-4.67-2.32-8.95-6.42-11.46zm248.61-49.05c-48.35 2.74-144.46 12.73-203.78 49.05-4.1 2.51-6.41 6.96-6.41 11.63v245.79c0 10.19 11.14 16.63 20.54 11.9 61.04-30.72 149.32-39.11 192.97-41.4 14.9-.78 26.49-12.73 26.49-27.06V219.14c-.01-15.63-13.56-28.01-29.81-27.09z'],
  'flask': [448, 'M437.2 403.5L320 215V64h8c13.3 0 24-10.7 24-24V24c0-13.3-10.7-24-24-24H120c-13.3 0-24 10.7-24 24v16c0 13.3 10.7 24 24 24h8v151L10.8 403.5C-18.5 450.6 15.3 512 70.9 512h306.2c55.7 0 89.4-61.5 60.1-108.5zM137.9 320l48.2-77.6c3.7-5.2 5.8-11.6 5.8-18.4V64h64v160c0 6.9 2.2 13.2 5.8 18.4l48.2 77.6h-172z'],
  'book': [448, 'M448 360V24c0-13.3-10.7-24-24-24H96C43 0 0 43 0 96v320c0 53 43 96 96 96h328c13.3 0 24-10.7 24-24v-16c0-7.5-3.5-14.3-8.9-18.7-4.2-15.4-4.2-59.3 0-74.7 5.4-4.3 8.9-11.1 8.9-18.6zM128 134c0-3.3 2.7-6 6-6h212c3.3 0 6 2.7 6 6v20c0 3.3-2.7 6-6 6H134c-3.3 0-6-2.7-6-6v-20zm0 64c0-3.3 2.7-6 6-6h212c3.3 0 6 2.7 6 6v20c0 3.3-2.7 6-6 6H134c-3.3 0-6-2.7-6-6v-20zm253.4 250H96c-17.7 0-32-14.3-32-32 0-17.6 14.4-32 32-32h285.4c-1.9 17.1-1.9 46.9 0 64z'],
  'user-graduate': [448, 'M319.4 320.6L224 416l-95.4-95.4C57.1 323.7 0 382.2 0 454.4v9.6c0 26.5 21.5 48 48 48h352c26.5 0 48-21.5 48-48v-9.6c0-72.2-57.1-130.7-128.6-133.8zM13.6 79.8l6.4 1.5v58.4c-7 4.2-12 11.5-12 20.3 0 8.4 4.6 15.4 11.1 19.7L3.5 242c-1.7 6.9 2.1 14 7.6 14h41.8c5.5 0 9.3-7.1 7.6-14l-15.6-62.3C51.4 175.4 56 168.4 56 160c0-8.8-5-16.1-12-20.3V87.1l66 15.9c-8.6 17.2-14 36.4-14 57 0 70.7 57.3 128 128 128s128-57.3 128-128c0-20.6-5.3-39.8-14-57l96.3-23.2c18.2-4.4 18.2-27.1 0-31.5l-190.4-46c-13-3.1-26.7-3.1-39.7 0L13.6 48.2c-18.1 4.4-18.1 27.2 0 31.6z'],
  'folder-open': [576, 'M572.694 292.093L500.27 416.248A63.997 63.997 0 0 1 444.989 448H45.025c-18.523 0-30.064-20.093-20.731-36.093l72.424-124.155A64 64 0 0 1 152 256h399.964c18.523 0 30.064 20.093 20.73 36.093zM152 224h328v-48c0-26.51-21.49-48-48-48H272l-64-64H48C21.49 64 0 85.49 0 112v278.046l69.077-118.418C86.214 242.25 117.989 224 152 224z'],
  'chalkboard-teacher': [640, 'M208 352c-2.39 0-4.78.35-7.06 1.09C187.98 357.3 174.35 360 160 360c-14.35 0-27.98-2.7-40.95-6.91-2.28-.74-4.66-1.09-7.05-1.09C49.94 352-.33 402.48 0 464.62.14 490.88 21.73 512 48 512h224c26.27 0 47.86-21.12 48-47.38.33-62.14-49.94-112.62-112-112.62zm-48-32c53.02 0 96-42.98 96-96s-42.98-96-96-96-96 42.98-96 96 42.98 96 96 96zM592 0H208c-26.47 0-48 22.25-48 49.59V96c23.42 0 45.1 6.78 64 17.8V64h352v288h-64v-64H384v64h-76.24c19.1 16.69 33.12 38.73 39.69 64H592c26.47 0 48-22.25 48-49.59V49.59C640 22.25 618.47 0 592 0z'],
  'folder': [512, 'M464 128H272l-64-64H48C21.49 64 0 85.49 0 112v288c0 26.51 21.49 48 48 48h416c26.51 0 48-21.49 48-48V176c0-26.51-21.49-48-48-48z'],
};

/** One Font Awesome glyph at the text's size, in the text's colour. */
export const FaIcon = ({ name }: { name: string }) => {
  const [w, d] = FA[name] ?? FA.folder;
  return <svg class="fa-icon" viewBox={`0 0 ${w} 512`} fill="currentColor" aria-hidden="true"><path d={d} /></svg>;
};

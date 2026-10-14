// A person's picture, round: an instructor card's, and the Instructors tab's Photo column.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { IMG_HOSTS, pictureOf } from '../model/student';
import { PHOTO_DIR } from '../tiers/people';
import { studentData } from '../screens/Student';
import { useLoad } from './load';

/** Up to two capitals for a picture's stand-in: "Dr Ana Lopez" -> "AL". */
export const initials = (name: string) => {
  const n = name.replace(/^(Prof\.|Dr\.)\s+/g, '').split(/[\s,]+/).filter((w) => /^[A-Z]/.test(w));
  return (n.length > 1 ? n[0][0] + n[n.length - 1][0] : (n[0] ?? name).slice(0, 2)).toUpperCase();
};

/**
 * `picture`: a URL the image policy loads directly (a GitHub avatar) as is; a picture on the
 * semester's site (its URL, a site path `/_images/pp/x.jpg`, or a bare name in the photo folder)
 * read through the API as the site would show it; the initials, when there is none or it does not
 * load. `small` is the table cell's thumbnail, labelled with the name; the card's larger one sits
 * beside the name, so it is decoration.
 */
export function Avatar({ picture, name, org, small = false }: { picture: string; name: string; org: string; small?: boolean }) {
  const env = useEnv();
  const [failed, setFailed] = useState(false);
  const raw = picture.trim();
  const direct = IMG_HOSTS.test(raw);
  const url = !raw || direct ? '' : /^https?:\/\//.test(raw) ? raw : pictureOf(raw.startsWith('/') ? raw : `/${PHOTO_DIR}/${raw}`, org);
  const load = useLoad(env && url ? () => studentData(env.client).picture(org, url) : null, [url, org]);
  const src = direct ? raw : load.kind === 'ready' ? load.value : '';
  const cls = `p-avatar${small ? ' small' : ''}`;
  if (src && !failed) return <img class={cls} src={src} alt={small ? name : ''} aria-hidden={small ? undefined : 'true'} onError={() => setFailed(true)} />;
  return small
    ? <span class={`${cls}${raw ? '' : ' none'}`} role="img" aria-label={raw ? name : `${name}: no photo`}>{initials(name)}</span>
    : <span class={cls} aria-hidden="true">{initials(name)}</span>;
}

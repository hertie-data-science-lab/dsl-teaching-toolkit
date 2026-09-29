// The Open split button (decision 0017), modelled on GitHub's Code button: the main part
// does the last choice made (remembered per login in this browser), the arrow opens every
// choice: on GitHub, on github.dev, in VS Code, in GitHub Desktop, in the editor Your setup
// names, or the clone command to copy. A menu button in the WAI-ARIA sense: the arrow opens
// it from the keyboard (Enter, Space, Down, Up), arrows move through it, Escape closes it
// and gives focus back.

import { signal } from '@preact/signals';
import { useEffect, useId, useRef, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { SETUP_HREF, defaultItem, isWeb, openItems, type OpenItem, type RepoRef, type Setup } from '../model/open';
import { rememberOpen, yourSetup } from '../model/prefs';
import { Ext } from './icons';

/** Bumped when a choice is remembered, so every Open button on the page takes the new default. */
const changed = signal(0);
function useSetup(login: string): [Setup | null, (item: OpenItem) => void] {
  void changed.value;
  const setup = yourSetup(login);
  return [setup, (item) => {
    rememberOpen(login, item.choice);
    changed.value++;
  }];
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function OpenButton({ small, quiet, ...ref }: RepoRef & { small?: boolean; quiet?: boolean }) {
  const env = useEnv();
  const [setup, remember] = useSetup(env?.user.login ?? '');
  const items = openItems(ref, setup);
  const main = defaultItem(items, setup);
  const [open, setOpen] = useState(false);
  const [focusAt, setFocusAt] = useState<'first' | 'last' | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const id = useId();
  const wrap = useRef<HTMLDivElement>(null);
  const caret = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [open]);
  useEffect(() => {
    if (!open || !focusAt) return;
    const all = menuItems();
    (focusAt === 'first' ? all[0] : all[all.length - 1])?.focus();
    setFocusAt(null);
  }, [open, focusAt]);
  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(null), 2000);
    return () => clearTimeout(t);
  }, [note]);

  const menuItems = () => [...(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
  const show = (at: 'first' | 'last') => {
    setOpen(true);
    setFocusAt(at);
  };
  const shut = () => {
    setOpen(false);
    caret.current?.focus();
  };
  const choose = (item: OpenItem) => {
    remember(item);
    // From the menu, focus goes back to the arrow rather than to the page.
    if (open) shut();
    if (item.copy) {
      void copyText(item.copy).then((ok) => {
        setNote(ok ? 'Copied' : 'Could not copy: select it below');
        if (!ok) setOpen(true);
      });
    }
  };
  const onCaretKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      show('first');
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      show('last');
    }
  };
  // Firefox toggles a button on Space's keyup as well: the keydown already opened it.
  const onCaretKeyUp = (e: KeyboardEvent) => {
    if (e.key === ' ') e.preventDefault();
  };
  const onMenuKey = (e: KeyboardEvent) => {
    const all = menuItems();
    const at = all.indexOf(document.activeElement as HTMLElement);
    const go = (i: number) => {
      e.preventDefault();
      all[(i + all.length) % all.length]?.focus();
    };
    if (e.key === 'ArrowDown') go(at + 1);
    else if (e.key === 'ArrowUp') go(at - 1);
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(all.length - 1);
    else if (e.key === 'Escape') {
      e.preventDefault();
      shut();
    } else if (e.key === 'Tab') setOpen(false);
  };

  const cls = `btn${quiet ? ' quiet' : ''}${small ? ' small' : ''}`;
  const link = (item: OpenItem, extra: Record<string, unknown>, children: preact.ComponentChildren) =>
    item.href ? (
      <a href={item.href} {...(isWeb(item.href) ? { target: '_blank', rel: 'noopener' } : {})} onClick={() => choose(item)} {...extra}>{children}</a>
    ) : (
      <button type="button" onClick={() => choose(item)} {...extra}>{children}</button>
    );
  const entry = (item: OpenItem) =>
    link(item, { role: 'menuitem', tabIndex: -1, class: item.copy ? 'pm-copy' : undefined }, (
      <>
        <span>{item.label}</span>
        {item.copy ? <code class="pm-cmd">{item.copy}</code> : item.href && isWeb(item.href) ? <Ext /> : null}
      </>
    ));
  const online = items.filter((i) => i.group === 'online');
  const local = items.filter((i) => i.group === 'local');
  const folder = !!setup?.folder.trim();
  return (
    <div class={`split${small ? ' small' : ''}`} ref={wrap}>
      {link(main, { class: `${cls} split-main`, ...(small ? { title: main.label, 'aria-label': main.label } : {}) }, <>{note && main.copy ? note : small ? 'Open' : main.label}{main.href && isWeb(main.href) ? <Ext /> : null}</>)}
      <button ref={caret} type="button" class={`${cls} split-caret`} aria-haspopup="menu" aria-expanded={open} aria-controls={id} aria-label={`More ways to open ${ref.repo}`}
        onClick={() => (open ? setOpen(false) : show('first'))} onKeyDown={onCaretKey} onKeyUp={onCaretKeyUp}>
        <span class="caret" aria-hidden="true" />
      </button>
      <div class="popmenu open-menu" id={id} role="menu" aria-label={`Open ${ref.repo}`} hidden={!open} onKeyDown={onMenuKey}>
        <div class="pm-h" role="presentation">In the browser</div>
        {online.map(entry)}
        <hr />
        <div class="pm-h" role="presentation">On your computer</div>
        {local.map(entry)}
        <hr />
        <a href={SETUP_HREF} role="menuitem" tabIndex={-1} onClick={() => setOpen(false)}>
          <span>{folder ? 'Change your setup' : 'Set up a local folder'}</span>
        </a>
      </div>
      <span class="sr" role="status">{note ?? ''}</span>
    </div>
  );
}

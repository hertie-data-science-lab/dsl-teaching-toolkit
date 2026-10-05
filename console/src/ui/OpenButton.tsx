// The Open split button (decision 0017), modelled on GitHub's Code button, on the instructor
// screens and the student Set up page alike (decision 0027): the main part does the last
// choice made (remembered per login in this browser), the arrow opens every choice: on
// GitHub, on github.dev, in VS Code, in GitHub Desktop, or in the editor Profile names. Where the folder check can tell (decision 0023), it offers Open or
// Clone, whichever applies, and the main part reads "Clone" or "Open"; it renders with both
// and "Open or clone" and narrows once it knows, and the arrow's click asks for read
// permission after a reload. Clone and Open in VS Code
// carry a `?` (decision 0024 rule 7), the clone's holding the command. A menu button in the
// WAI-ARIA sense: the arrow opens it from the keyboard (Enter, Space, Down, Up), arrows move
// through its items, Escape closes it and gives focus back. A `?` is not an item: off the
// arrow keys and the tab order, it opens on hover or a tap.

import { signal } from '@preact/signals';
import { useEffect, useId, useRef, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { courseFolder, defaultItem, isWeb, mainLabel, openItems, profileHref, repoCloneCommand, type OpenItem, type RepoRef, type Setup } from '../model/open';
import { askFolderOnce, folderChanged, isCloned } from '../model/localFolder';
import { rememberOpen, yourSetup } from '../model/prefs';
import { Hint } from './Hint';
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

/**
 * Whether the folder check finds `repo` in `home`'s course folder; undefined until it first
 * answers, or when it cannot tell. A recheck of the same repo keeps the last answer until the
 * new one comes.
 */
function useCloned(login: string, home: string, repo: string, setup: Setup | null): boolean | undefined {
  const key = `${login}/${home}/${repo}`;
  const [answer, setAnswer] = useState<{ key: string; cloned?: boolean }>({ key });
  const version = folderChanged.value;
  const folder = courseFolder(setup, home);
  useEffect(() => {
    if (!login || !folder) return setAnswer({ key });
    let live = true;
    void isCloned(login, home, repo, setup).then((cloned) => live && setAnswer({ key, cloned }));
    return () => {
      live = false;
    };
  }, [key, folder, setup?.folder, version]);
  // An answer about another repo (the props changed) is no answer.
  return answer.key === key ? answer.cloned : undefined;
}

export function OpenButton({ small, quiet, ...ref }: RepoRef & { small?: boolean; quiet?: boolean }) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  const [setup, remember] = useSetup(login);
  const home = ref.home ?? ref.org;
  const cloned = useCloned(login, home, ref.repo, setup);
  const items = openItems(ref, setup, cloned);
  const main = defaultItem(items, setup, cloned);
  const label = mainLabel(main, cloned);
  const [open, setOpen] = useState(false);
  const [focusAt, setFocusAt] = useState<'first' | 'last' | null>(null);
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
  // A `?` in the menu is not one of its items (decision 0024): out of the tab order, its text
  // the description of the item beside it.
  useEffect(() => {
    wrap.current?.querySelectorAll<HTMLElement>('.open-menu .pm-row').forEach((row) => {
      const btn = row.querySelector<HTMLElement>('.hint-btn');
      const pop = row.querySelector<HTMLElement>('.hint-pop');
      if (btn) btn.tabIndex = -1;
      if (pop) row.querySelector('[role="menuitem"]')?.setAttribute('aria-describedby', pop.id);
    });
  });
  useEffect(() => {
    if (!open || !focusAt) return;
    const all = menuItems();
    (focusAt === 'first' ? all[0] : all[all.length - 1])?.focus();
    setFocusAt(null);
  }, [open, focusAt]);

  const menuItems = () => [...(wrap.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
  const show = (at: 'first' | 'last') => {
    if (login) void askFolderOnce(login);
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
  const link = (item: OpenItem, extra: Record<string, unknown>, children: preact.ComponentChildren) => (
    <a href={item.href} {...(isWeb(item.href) ? { target: '_blank', rel: 'noopener' } : {})} onClick={() => choose(item)} {...extra}>{children}</a>
  );
  const folder = !!courseFolder(setup, home);
  const hints = {
    open: <Hint label="About opening in VS Code">Opens the repo’s folder on your computer. Clone it first if it is not there yet.</Hint>,
    clone: <Hint label="About cloning in VS Code">VS Code asks where to put it{folder ? '; choose your course folder' : ''}. Or run: <code class="pm-cmd">{repoCloneCommand(ref, setup)}</code></Hint>,
  };
  const entry = (item: OpenItem) => {
    const row = link(item, { role: 'menuitem', tabIndex: -1 }, (
      <>
        <span>{item.label}</span>
        {isWeb(item.href) ? <Ext /> : null}
      </>
    ));
    return item.hint ? <div class="pm-row" role="none">{row}{hints[item.hint]}</div> : row;
  };
  const online = items.filter((i) => i.group === 'online');
  const local = items.filter((i) => i.group === 'local');
  return (
    <div class={`split${small ? ' small' : ''}`} ref={wrap}>
      {/* The small button keeps one short word; its title and name carry the whole label. */}
      {link(main, { class: `${cls} split-main`, ...(small ? { title: label, 'aria-label': label } : {}) }, <>{small ? (cloned === false ? 'Clone' : 'Open') : label}{isWeb(main.href) ? <Ext /> : null}</>)}
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
        <a href={profileHref(home, !!ref.home)} role="menuitem" tabIndex={-1} onClick={() => setOpen(false)}>
          <span>{folder ? 'Change your profile' : 'Set up a local folder'}</span>
        </a>
      </div>
    </div>
  );
}

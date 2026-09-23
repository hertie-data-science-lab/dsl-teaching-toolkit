import { render } from 'preact';
import { App, createDeps, createState } from './app';
import './styles/tokens.css';
import './styles/console.css';

try {
  const saved = localStorage.getItem('console-theme');
  if (saved === 'dark' || saved === 'light') document.documentElement.setAttribute('data-theme', saved);
} catch {
  /* storage unavailable */
}

render(<App state={createState(createDeps())} />, document.getElementById('app')!);

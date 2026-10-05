import { render } from 'preact';
import { App, createDeps, createState } from './app';
import { savedTheme } from './ui/shell';
import './styles/tokens.css';
import './styles/console.css';
import './styles/open.css';

const theme = savedTheme();
if (theme) document.documentElement.setAttribute('data-theme', theme);

render(<App state={createState(createDeps())} />, document.getElementById('app')!);

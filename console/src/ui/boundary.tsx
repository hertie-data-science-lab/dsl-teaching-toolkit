// Catches a screen that throws while rendering, so the shell and every other screen keep working.

import { Component, type ComponentChildren } from 'preact';

export class ScreenBoundary extends Component<{ children: ComponentChildren }, { error: Error | null }> {
  state = { error: null as Error | null };

  componentDidCatch(error: unknown) {
    this.setState({ error: error instanceof Error ? error : new Error(String(error)) });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <section class="panel section stub" role="alert">
        <h2>This screen hit an error</h2>
        <p class="footnote"><code>{error.message}</code></p>
        <p><a class="textlink" href="#cohort">Back to This week</a></p>
      </section>
    );
  }
}

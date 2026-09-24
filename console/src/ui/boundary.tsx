// Catches a screen that throws while rendering, so the shell and every other screen keep working.

import { Component, type ComponentChildren } from 'preact';

const onWeek = () => typeof location !== 'undefined' && location.hash === '#semester';

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
        {/* The route key resets the boundary, so the link must change the hash: This week itself goes Home. */}
        <p>{onWeek() ? <a class="textlink" href="#">Back to Home</a> : <a class="textlink" href="#semester">Back to This week</a>}</p>
      </section>
    );
  }
}

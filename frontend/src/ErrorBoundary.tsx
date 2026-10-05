import { Component, type PropsWithChildren } from 'react'

export default class ErrorBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return <main className="app-shell">
      <section className="panel loading-state" role="alert">
        <h1>Lazy Clipper</h1>
        <h2>The workspace could not be displayed.</h2>
        <p>Reload the workspace to try again. Processing will not be resubmitted automatically.</p>
        <button type="button" className="clip-button" onClick={() => window.location.reload()}>Reload workspace</button>
      </section>
    </main>
  }
}

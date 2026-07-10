import { Component } from 'react'

// A render-time exception anywhere below this boundary would otherwise unmount
// the whole SPA to a blank screen. Catch it and show a recoverable message while
// the surrounding chrome (sidebar/topbar) stays mounted. Reset via the `resetKey`
// prop (route path) so navigating to another page clears a caught error.
export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidUpdate(prevProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center h-full gap-4 px-6 text-center">
          <div className="text-4xl">⚠️</div>
          <h2 className="text-lg font-semibold text-[#e2e8f0]">Something broke on this page</h2>
          <p className="text-sm text-[#94a3b8] max-w-md">
            {this.state.error.message || 'An unexpected error occurred while rendering.'}
          </p>
          <button
            onClick={() => window.location.reload()}
            className="mt-1 text-sm text-[#94a3b8] hover:text-[#e2e8f0] border border-[#2d3148] px-4 py-2 rounded-lg transition-colors"
          >
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

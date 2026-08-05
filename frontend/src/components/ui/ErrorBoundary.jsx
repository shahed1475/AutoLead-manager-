import { Component } from 'react'
import { AlertOctagon, RefreshCw, Home } from 'lucide-react'

// Wraps the routed page outlet. Without this, any render-time throw in a
// page component white-screens the entire app (confirmed absent app-wide).
// Class component is required — React has no hook-based error boundary API.
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('AutoLead: render error caught by ErrorBoundary', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="h-full flex items-center justify-center p-6">
          <div className="card max-w-md w-full p-6 text-center space-y-4">
            <div className="w-12 h-12 mx-auto rounded-xl bg-red-500/10 border border-red-500/30 flex items-center justify-center">
              <AlertOctagon size={20} className="text-red-400" />
            </div>
            <div>
              <p className="text-sm font-semibold text-slate-200">This page hit an unexpected error</p>
              <p className="text-xs text-slate-500 mt-1">
                The rest of the app is still fine — try reloading this page.
              </p>
            </div>
            <div className="flex items-center justify-center gap-2">
              <button
                onClick={() => this.setState({ error: null })}
                className="btn-secondary text-xs"
              >
                <RefreshCw size={12} /> Try again
              </button>
              <button
                onClick={() => { this.setState({ error: null }); window.location.assign('/dashboard') }}
                className="btn-secondary text-xs"
              >
                <Home size={12} /> Go to Dashboard
              </button>
            </div>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

import { Component, type ErrorInfo, type ReactNode } from "react";

import { STORAGE_KEY } from "../hooks/useChat";

interface State {
  hasError: boolean;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI crashed", error, info.componentStack);
  }

  private recover = () => {
    localStorage.removeItem(STORAGE_KEY);
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="fatal">
        <h1>Something went wrong</h1>
        <p>The HR Assistant hit an unexpected error.</p>
        <button type="button" className="ghost-button" onClick={this.recover}>
          Start over
        </button>
      </div>
    );
  }
}

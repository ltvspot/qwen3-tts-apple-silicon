import React from "react";

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      attempt: 0,
    };
  }

  static getDerivedStateFromError(error) {
    return {
      hasError: true,
      error,
    };
  }

  componentDidCatch(error, errorInfo) {
    // eslint-disable-next-line no-console
    console.error("ErrorBoundary caught a render error", error, errorInfo);
  }

  handleRetry = () => {
    this.setState((currentState) => ({
      attempt: currentState.attempt + 1,
      hasError: false,
      error: null,
    }));
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="mx-auto max-w-3xl rounded-[2rem] border border-rose-200 bg-rose-50 p-8 text-rose-900 shadow-sm">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-rose-500">
            Application Error
          </p>
          <h1 className="mt-3 text-2xl font-semibold text-rose-950">
            Something went wrong
          </h1>
          <p className="mt-3 text-sm leading-7 text-rose-700">
            {this.state.error?.message || "A rendering error interrupted the interface."}
          </p>
          <button
            className="mt-6 rounded-full bg-rose-600 px-5 py-2.5 text-sm font-semibold text-white transition hover:bg-rose-500"
            onClick={this.handleRetry}
            type="button"
          >
            Try Again
          </button>
        </div>
      );
    }

    return <React.Fragment key={this.state.attempt}>{this.props.children}</React.Fragment>;
  }
}

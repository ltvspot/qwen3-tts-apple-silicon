import React, { act } from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

jest.mock("./components/ErrorBoundary", () => ({
  __esModule: true,
  default: ({ children }) => children,
}));

jest.mock("./pages/BookDetail", () => ({
  __esModule: true,
  default: () => <div>Book Detail</div>,
}));

jest.mock("./pages/Library", () => ({
  __esModule: true,
  default: () => <div>Library</div>,
}));

jest.mock("./pages/QA", () => ({
  __esModule: true,
  default: () => <div>QA</div>,
}));

jest.mock("./pages/Queue", () => ({
  __esModule: true,
  default: () => <div>Queue</div>,
}));

jest.mock("./pages/Settings", () => ({
  __esModule: true,
  default: () => <div>Settings</div>,
}));

jest.mock("./pages/VoiceLab", () => ({
  __esModule: true,
  default: () => <div>Voice Lab</div>,
}));

async function waitFor(assertion, timeout = 2000) {
  const startTime = Date.now();

  while (true) {
    try {
      assertion();
      return;
    } catch (error) {
      if (Date.now() - startTime > timeout) {
        throw error;
      }

      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 20);
        });
      });
    }
  }
}

describe("App routes", () => {
  let container;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    window.history.pushState({}, "", "/");
    container.remove();
  });

  async function renderAppAt(pathname) {
    window.history.pushState({}, "", pathname);

    await act(async () => {
      root.render(<App />);
    });
  }

  test("test_404_route", async () => {
    await renderAppAt("/nonexistent");

    await waitFor(() => {
      expect(container.textContent).toContain("Page not found");
    });
  });

  test("test_404_link_to_home", async () => {
    await renderAppAt("/missing");

    await waitFor(() => {
      const homeLinks = Array.from(container.querySelectorAll('a[href="/"]'));
      const backLink = homeLinks.find((link) => link.textContent.includes("Back to Library"));
      expect(backLink).toBeDefined();
    });
  });
});

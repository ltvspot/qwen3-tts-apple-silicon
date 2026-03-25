import React, { act } from "react";
import ReactDOM from "react-dom/client";
import ErrorBoundary from "./ErrorBoundary";

describe("ErrorBoundary", () => {
  let container;
  let root;
  let consoleErrorSpy;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
    consoleErrorSpy = jest.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(async () => {
    consoleErrorSpy.mockRestore();

    await act(async () => {
      root.unmount();
    });

    container.remove();
  });

  test("catches render errors and retries the subtree", async () => {
    let shouldThrow = true;

    function Bomb() {
      if (shouldThrow) {
        throw new Error("Kaboom");
      }

      return <div>Recovered content</div>;
    }

    await act(async () => {
      root.render(
        <ErrorBoundary>
          <Bomb />
        </ErrorBoundary>,
      );
    });

    expect(container.textContent).toContain("Something went wrong");
    expect(container.textContent).toContain("Kaboom");

    shouldThrow = false;

    await act(async () => {
      container.querySelector("button").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    expect(container.textContent).toContain("Recovered content");
  });
});

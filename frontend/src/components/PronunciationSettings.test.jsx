import React, { act } from "react";
import ReactDOM from "react-dom/client";
import PronunciationSettings from "./PronunciationSettings";

function createJsonResponse(payload, options = {}) {
  return {
    json: async () => payload,
    ok: options.ok ?? true,
    status: options.status ?? 200,
  };
}

function setFormValue(element, value) {
  const prototype = Object.getPrototypeOf(element);
  const valueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  valueSetter.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

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

describe("Pronunciation settings", () => {
  let container;
  let fetchMock;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
    fetchMock = jest.fn((url, options) => {
      if (url === "/api/pronunciation") {
        return Promise.resolve(createJsonResponse({
          global: { Thoreau: "thuh-ROH" },
          per_book: {},
        }));
      }
      if (url === "/api/pronunciation/suggestions") {
        return Promise.resolve(createJsonResponse([
          {
            book_id: 3,
            chapter_n: 1,
            book_title: "Walden",
            word: "Concord",
            reason: "Detected in a deep-QA transcription mismatch and looks like a proper noun.",
          },
        ]));
      }
      if (url === "/api/pronunciation/global/Walden" && options?.method === "PUT") {
        return Promise.resolve(createJsonResponse({
          global: { Thoreau: "thuh-ROH", Walden: "WAWL-den" },
          per_book: {},
        }));
      }
      if (url === "/api/pronunciation/global/Thoreau" && options?.method === "DELETE") {
        return Promise.resolve(createJsonResponse({
          global: {},
          per_book: {},
        }));
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });
    global.fetch = fetchMock;
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    container.remove();
    delete global.fetch;
  });

  async function renderComponent() {
    await act(async () => {
      root.render(<PronunciationSettings />);
    });
  }

  test("loads dictionary entries and suggestions", async () => {
    await renderComponent();

    await waitFor(() => {
      expect(container.textContent).toContain("Shared pronunciations");
      expect(container.textContent).toContain("Thoreau");
      expect(container.textContent).toContain("Concord");
    });
  });

  test("saves and deletes pronunciation entries", async () => {
    await renderComponent();

    await waitFor(() => {
      expect(container.textContent).toContain("Thoreau");
    });

    await act(async () => {
      setFormValue(container.querySelector('input[aria-label="Global pronunciation word"]'), "Walden");
      setFormValue(container.querySelector('input[aria-label="Global pronunciation value"]'), "WAWL-den");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Save Global Entry"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => url === "/api/pronunciation/global/Walden")).toBe(true);
      expect(container.textContent).toContain("Saved global pronunciation for Walden.");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "Delete")
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url, options]) => url === "/api/pronunciation/global/Thoreau" && options?.method === "DELETE")).toBe(true);
      expect(container.textContent).toContain("Deleted pronunciation for Thoreau.");
    });
  });
});

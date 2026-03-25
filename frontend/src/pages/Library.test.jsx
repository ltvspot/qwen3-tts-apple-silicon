import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import Library from "./Library";

const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => {
  const actual = jest.requireActual("react-router-dom");

  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function createBook(overrides) {
  return {
    author: "Unknown Author",
    chapter_count: 0,
    created_at: "2026-03-24T00:00:00+00:00",
    folder_path: "placeholder-folder",
    id: 1,
    narrator: "Kent Zimering",
    page_count: null,
    status: "not_started",
    subtitle: null,
    title: "Placeholder Title",
    trim_size: null,
    updated_at: "2026-03-24T00:00:00+00:00",
    ...overrides,
  };
}

function createJsonResponse(payload) {
  return {
    json: async () => payload,
    ok: true,
  };
}

function createDeferredResponse() {
  let resolve;

  return {
    promise: new Promise((resolver) => {
      resolve = resolver;
    }),
    resolve,
  };
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

function setFormValue(element, value, eventName) {
  const prototype = Object.getPrototypeOf(element);
  const valueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;

  valueSetter.call(element, value);
  element.dispatchEvent(new Event(eventName, { bubbles: true }));
}

describe("Library page", () => {
  let container;
  let root;
  let fetchMock;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);

    fetchMock = jest.fn();
    global.fetch = fetchMock;
    mockNavigate.mockReset();
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    container.remove();
    delete global.fetch;
  });

  function getBookIds() {
    return Array.from(container.querySelectorAll("[data-book-id]")).map((node) =>
      Number(node.getAttribute("data-book-id")),
    );
  }

  function getButtonByText(label) {
    return Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent.trim() === label,
    );
  }

  async function renderLibrary() {
    await act(async () => {
      root.render(
        <MemoryRouter
          future={{
            v7_relativeSplatPath: true,
            v7_startTransition: true,
          }}
        >
          <Library />
        </MemoryRouter>,
      );
    });
  }

  test("fetches paginated books, supports live search, sorts client-side, and navigates on card click", async () => {
    const stats = {
      not_started: 1,
      parsed: 1,
      generating: 0,
      generated: 1,
      qa: 0,
      qa_approved: 0,
      exported: 0,
    };

    const wizard = createBook({
      author: "Ursula K. Le Guin",
      chapter_count: 12,
      folder_path: "0001-a-wizard-of-earthsea",
      id: 1,
      page_count: 205,
      title: "A Wizard of Earthsea",
      trim_size: "6x9",
    });
    const piranesi = createBook({
      author: "Susanna Clarke",
      chapter_count: 18,
      folder_path: "0002-piranesi",
      id: 2,
      page_count: 272,
      status: "generated",
      subtitle: "A Novel",
      title: "Piranesi",
      trim_size: "5x8",
    });
    const atuan = createBook({
      author: "Ursula K. Le Guin",
      chapter_count: 10,
      folder_path: "0003-the-tombs-of-atuan",
      id: 3,
      page_count: 180,
      status: "parsed",
      title: "The Tombs of Atuan",
      trim_size: "6x9",
    });

    fetchMock
      .mockResolvedValueOnce(
        createJsonResponse({
          books: [piranesi, wizard],
          stats,
          total: 3,
        }),
      )
      .mockResolvedValueOnce(
        createJsonResponse({
          books: [atuan],
          stats,
          total: 3,
        }),
      );

    await renderLibrary();

    await waitFor(() => {
      expect(getBookIds()).toEqual([1, 2, 3]);
    });

    expect(fetchMock.mock.calls).toHaveLength(2);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/library?limit=500&offset=0");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/library?limit=500&offset=2");
    expect(container.textContent).toContain("Showing 3 of 3 books");
    expect(container.textContent).toContain("QA Approved");
    expect(container.textContent).toContain("No subtitle indexed yet.");

    const searchInput = container.querySelector('input[type="text"]');
    await act(async () => {
      setFormValue(searchInput, "LE GUIN", "input");
    });

    await waitFor(() => {
      expect(getBookIds()).toEqual([1, 3]);
    });
    expect(container.textContent).toContain("Showing 2 of 3 books");

    await act(async () => {
      setFormValue(searchInput, "", "input");
    });

    const pageCountButton = getButtonByText("Page Count");
    await act(async () => {
      pageCountButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(getBookIds()).toEqual([2, 1, 3]);

    const piranesiCard = container.querySelector('[data-book-id="2"]');
    await act(async () => {
      piranesiCard.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(mockNavigate).toHaveBeenCalledWith("/book/2");
  });

  test("shows determinate progress while loading paginated library batches", async () => {
    const deferredSecondPage = createDeferredResponse();
    const stats = {
      not_started: 3,
      parsed: 0,
      generating: 0,
      generated: 0,
      qa: 0,
      qa_approved: 0,
      exported: 0,
    };

    fetchMock
      .mockResolvedValueOnce(
        createJsonResponse({
          books: [
            createBook({ id: 1, title: "One" }),
            createBook({ id: 2, title: "Two" }),
          ],
          stats,
          total: 3,
        }),
      )
      .mockImplementationOnce(() => deferredSecondPage.promise);

    await renderLibrary();

    await waitFor(() => {
      expect(container.textContent).toContain("Loading library... 2 / 3 books");
    });

    deferredSecondPage.resolve(
      createJsonResponse({
        books: [createBook({ id: 3, title: "Three" })],
        stats,
        total: 3,
      }),
    );

    await waitFor(() => {
      expect(getBookIds()).toEqual([1, 2, 3]);
    });
  });

  test("refetches on status filter changes and refreshes the filtered list after scanning", async () => {
    const initialStats = {
      not_started: 1,
      parsed: 1,
      generating: 0,
      generated: 0,
      qa: 0,
      qa_approved: 0,
      exported: 0,
    };
    const updatedStats = {
      not_started: 1,
      parsed: 2,
      generating: 0,
      generated: 0,
      qa: 0,
      qa_approved: 0,
      exported: 0,
    };

    const draftBook = createBook({
      author: "Robin McKinley",
      chapter_count: 14,
      folder_path: "0101-the-blue-sword",
      id: 101,
      page_count: 240,
      title: "The Blue Sword",
      trim_size: "6x9",
    });
    const parsedBook = createBook({
      author: "Robin McKinley",
      chapter_count: 19,
      folder_path: "0102-the-hero-and-the-crown",
      id: 102,
      page_count: 310,
      status: "parsed",
      title: "The Hero and the Crown",
      trim_size: "6x9",
    });
    const scannedBook = createBook({
      author: "Patricia A. McKillip",
      chapter_count: 17,
      folder_path: "0103-ombria-in-shadow",
      id: 103,
      page_count: 298,
      status: "parsed",
      title: "Ombria in Shadow",
      trim_size: "5x8",
    });

    const deferredScan = createDeferredResponse();
    let latestLibraryPayload = {
      books: [draftBook, parsedBook],
      stats: initialStats,
      total: 2,
    };
    let scanProgressPayload = {
      elapsed_seconds: 12,
      files_found: 3,
      files_processed: 2,
      new_books: 1,
      scanning: true,
    };

    fetchMock.mockImplementation((url) => {
      if (url === "/api/library?limit=500&offset=0") {
        return Promise.resolve(createJsonResponse(latestLibraryPayload));
      }

      if (url === "/api/library?limit=500&offset=0&status_filter=parsed") {
        return Promise.resolve(
          createJsonResponse({
            books: latestLibraryPayload.books.filter((book) => book.status === "parsed"),
            stats: latestLibraryPayload.stats,
            total: latestLibraryPayload.books.filter((book) => book.status === "parsed").length,
          }),
        );
      }

      if (url === "/api/library/scan") {
        return deferredScan.promise;
      }

      if (url === "/api/library/scan/progress") {
        return Promise.resolve(createJsonResponse(scanProgressPayload));
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderLibrary();

    await waitFor(() => {
      expect(getBookIds()).toEqual([101, 102]);
    });

    const statusSelect = container.querySelector("select");
    await act(async () => {
      setFormValue(statusSelect, "parsed", "change");
    });

    await waitFor(() => {
      expect(getBookIds()).toEqual([102]);
    });
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/library?limit=500&offset=0&status_filter=parsed",
    );

    const scanButton = getButtonByText("Scan Library");
    await act(async () => {
      scanButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(scanButton.disabled).toBe(true);
    expect(scanButton.textContent).toBe("Scanning...");
    expect(fetchMock.mock.calls[2]).toEqual([
      "/api/library/scan",
      { method: "POST" },
    ]);
    expect(fetchMock.mock.calls[3][0]).toBe("/api/library/scan/progress");
    expect(container.textContent).toContain("Scanning library... 2 / 3 files");

    latestLibraryPayload = {
      books: [parsedBook, scannedBook],
      stats: updatedStats,
      total: 2,
    };
    scanProgressPayload = {
      elapsed_seconds: 14,
      files_found: 3,
      files_processed: 3,
      new_books: 1,
      scanning: false,
    };
    await act(async () => {
      deferredScan.resolve(createJsonResponse({ errors: [], new_books: 1, total_found: 3, total_indexed: 3 }));
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(getBookIds()).toEqual([102, 103]);
    });
    expect(fetchMock.mock.calls[4][0]).toBe(
      "/api/library?limit=500&offset=0&status_filter=parsed",
    );
    expect(scanButton.disabled).toBe(false);
    expect(scanButton.textContent).toBe("Scan Library");
    expect(container.textContent).toContain("Scan complete! Found 1 new books.");
  });

  test("shows a retry action when the library request fails and recovers on retry", async () => {
    const retryStats = {
      not_started: 1,
      parsed: 0,
      generating: 0,
      generated: 0,
      qa: 0,
      qa_approved: 0,
      exported: 0,
    };

    fetchMock
      .mockResolvedValueOnce({
        json: async () => ({}),
        ok: false,
      })
      .mockResolvedValueOnce(
        createJsonResponse({
          books: [
            createBook({
              author: "Retry Author",
              folder_path: "0201-retried-book",
              id: 201,
              title: "Retried Book",
            }),
          ],
          stats: retryStats,
          total: 1,
        }),
      );

    await renderLibrary();

    await waitFor(() => {
      expect(container.textContent).toContain("Failed to fetch library");
      expect(getButtonByText("Retry Load")).toBeTruthy();
    });

    await act(async () => {
      getButtonByText("Retry Load").dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });

    await waitFor(() => {
      expect(getBookIds()).toEqual([201]);
    });
  });
});

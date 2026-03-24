import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import BookDetail from "./BookDetail";

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
    author: "Jane Doe",
    chapter_count: 3,
    created_at: "2026-03-24T00:00:00+00:00",
    folder_path: "1007-test-chronicle",
    id: 7,
    narrator: "Kent Zimering",
    page_count: 145,
    status: "parsed",
    subtitle: "A Test Story",
    title: "The Test Chronicle",
    trim_size: "6x9",
    updated_at: "2026-03-24T00:00:00+00:00",
    ...overrides,
  };
}

function createChapter(overrides) {
  return {
    audio_path: null,
    book_id: 7,
    created_at: "2026-03-24T00:00:00+00:00",
    duration_seconds: null,
    id: 701,
    number: 0,
    qa_notes: null,
    qa_status: "not_reviewed",
    status: "pending",
    text_content: "Opening credits text.",
    title: "Opening Credits",
    type: "opening_credits",
    updated_at: "2026-03-24T00:00:00+00:00",
    word_count: 3,
    ...overrides,
  };
}

function createJsonResponse(payload, options = {}) {
  return {
    json: async () => payload,
    ok: options.ok ?? true,
    status: options.status ?? 200,
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

describe("BookDetail page", () => {
  let alertMock;
  let confirmMock;
  let container;
  let fetchMock;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);

    fetchMock = jest.fn();
    global.fetch = fetchMock;
    mockNavigate.mockReset();

    alertMock = jest.spyOn(window, "alert").mockImplementation(() => {});
    confirmMock = jest.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(async () => {
    alertMock.mockRestore();
    confirmMock.mockRestore();

    await act(async () => {
      root.unmount();
    });

    container.remove();
    delete global.fetch;
  });

  async function renderBookDetail() {
    await act(async () => {
      root.render(
        <MemoryRouter
          initialEntries={["/book/7"]}
          future={{
            v7_relativeSplatPath: true,
            v7_startTransition: true,
          }}
        >
          <Routes>
            <Route element={<BookDetail />} path="/book/:id" />
          </Routes>
        </MemoryRouter>,
      );
    });
  }

  function getButtonByText(label) {
    return Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent.includes(label),
    );
  }

  test("loads book data, selects the first chapter, and navigates back to the library", async () => {
    const book = createBook({});
    const chapters = [
      createChapter({}),
      createChapter({
        id: 702,
        number: 2,
        text_content: "Chapter two body text.",
        title: "The Beginning",
        type: "chapter",
        word_count: 4,
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("The Test Chronicle");
      expect(container.textContent).toContain("Opening credits text.");
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/book/7");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/book/7/chapters");
    expect(container.textContent).toContain("A Test Story");
    expect(container.textContent).toContain("Jane Doe");
    expect(container.textContent).toContain("Narrated by Kent Zimering");

    await act(async () => {
      getButtonByText("Back to Library").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(mockNavigate).toHaveBeenCalledWith("/");
  });

  test("edits and saves chapter text through the chapter update API", async () => {
    const book = createBook({});
    const chapters = [
      createChapter({}),
      createChapter({
        id: 702,
        number: 2,
        text_content: "Original chapter body.",
        title: "The Beginning",
        type: "chapter",
        word_count: 3,
      }),
    ];
    const updatedChapter = createChapter({
      id: 702,
      number: 2,
      text_content: "Revised chapter text for testing now.",
      title: "The Beginning",
      type: "chapter",
      word_count: 6,
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(updatedChapter));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Opening credits text.");
    });

    await act(async () => {
      container
        .querySelector('[data-chapter-id="702"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Original chapter body.");
    });

    await act(async () => {
      getButtonByText("Edit Chapter").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const textarea = container.querySelector('textarea[aria-label="Chapter text editor"]');
    await act(async () => {
      setFormValue(textarea, updatedChapter.text_content, "input");
    });

    await act(async () => {
      getButtonByText("Save Changes").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
      expect(container.textContent).toContain(updatedChapter.text_content);
      expect(container.textContent).toContain("6 words");
    });

    expect(fetchMock.mock.calls[2][0]).toBe("/api/book/7/chapter/2/text");
    expect(fetchMock.mock.calls[2][1]).toMatchObject({
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
    });
    expect(fetchMock.mock.calls[2][1].body).toBe(
      JSON.stringify({ text_content: updatedChapter.text_content }),
    );
  });

  test("persists narration settings and confirms before discarding unsaved edits", async () => {
    const book = createBook({});
    const chapters = [
      createChapter({}),
      createChapter({
        id: 702,
        number: 2,
        text_content: "Chapter two body text.",
        title: "The Beginning",
        type: "chapter",
        word_count: 4,
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Opening credits text.");
    });

    await act(async () => {
      getButtonByText("Dramatic Reading").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const emotionInput = container.querySelector("#emotion-input");
    expect(emotionInput.value).toBe("dramatic");
    expect(container.textContent).toContain("0.95x playback");

    await act(async () => {
      getButtonByText("Edit Chapter").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const textarea = container.querySelector('textarea[aria-label="Chapter text editor"]');
    await act(async () => {
      setFormValue(textarea, "Unsaved rewrite for opening credits.", "input");
    });

    confirmMock.mockReturnValueOnce(false);
    await act(async () => {
      container
        .querySelector('[data-chapter-id="702"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(window.confirm).toHaveBeenCalledWith("Discard unsaved chapter edits?");
    expect(container.textContent).toContain("Unsaved changes");
    expect(container.textContent).toContain("Opening Credits");

    confirmMock.mockReturnValueOnce(true);
    await act(async () => {
      container
        .querySelector('[data-chapter-id="702"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Chapter two body text.");
    });

    expect(emotionInput.value).toBe("dramatic");
  });

  test("renders a not-found state when the book record is missing", async () => {
    fetchMock.mockResolvedValueOnce(
      createJsonResponse({ detail: "Book 7 not found" }, { ok: false, status: 404 }),
    );

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Book not found");
    });

    await act(async () => {
      getButtonByText("Back to Library").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(mockNavigate).toHaveBeenCalledWith("/");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

import React, { act } from "react";
import ReactDOM from "react-dom/client";
import TextPreview from "./TextPreview";

describe("TextPreview", () => {
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

    container.remove();
  });

  async function renderPreview(chapter) {
    await act(async () => {
      root.render(
        <TextPreview
          chapter={chapter}
          draftText=""
          editMode={false}
          hasUnsavedChanges={false}
          onBeginEdit={() => {}}
          onCancelEdit={() => {}}
          onSave={() => {}}
          onTextChange={() => {}}
          saveErrorMessage=""
          saving={false}
        />,
      );
    });
  }

  test("shows introduction titles directly in the header", async () => {
    await renderPreview({
      id: 1,
      number: 1,
      text_content: "Intro text.",
      title: "Introduction",
      type: "introduction",
    });

    expect(container.textContent).toContain("Introduction");
    expect(container.textContent).not.toContain("Introduction: Introduction");
  });

  test("shows chapter titles directly in the header", async () => {
    await renderPreview({
      id: 2,
      number: 2,
      text_content: "Book One text.",
      title: "Book One",
      type: "chapter",
    });

    expect(container.textContent).toContain("Book One");
    expect(container.textContent).not.toContain("Chapter 2: Book One");
  });
});

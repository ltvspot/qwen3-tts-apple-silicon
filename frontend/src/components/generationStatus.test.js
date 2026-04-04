import { getChapterLabel } from "./generationStatus";

describe("getChapterLabel", () => {
  test("returns introduction titles without an extra type prefix", () => {
    expect(
      getChapterLabel({
        number: 1,
        title: "Introduction",
        type: "introduction",
      }),
    ).toBe("Introduction");

    expect(
      getChapterLabel({
        number: 1,
        title: "Preface",
        type: "introduction",
      }),
    ).toBe("Preface");
  });

  test("returns chapter titles directly without a synthetic chapter number prefix", () => {
    expect(
      getChapterLabel({
        number: 2,
        title: "Book One",
        type: "chapter",
      }),
    ).toBe("Book One");

    expect(
      getChapterLabel({
        number: 2,
        title: "Chapter 1",
        type: "chapter",
      }),
    ).toBe("Chapter 1");
  });

  test("falls back to the numeric chapter label when no title exists", () => {
    expect(
      getChapterLabel({
        number: 5,
        title: "",
        type: "chapter",
      }),
    ).toBe("Chapter 5");
  });
});

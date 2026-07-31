import "@testing-library/jest-dom/vitest";

Object.defineProperty(URL, "createObjectURL", {
  configurable: true,
  value: () => "blob:test-image",
});

Object.defineProperty(URL, "revokeObjectURL", {
  configurable: true,
  value: () => undefined,
});

import assert from "node:assert/strict";
import test from "node:test";
import { orderChatProducts } from "../src/lib/chat-products.ts";

test("uses the order of the cards persisted by the server", () => {
  const first = { key: "a", note: "recommendation" };
  const second = { key: "b" };
  assert.deepEqual(orderChatProducts([first, second, { key: "a" }], ["b", "a"]), [second, first]);
});

test("the legacy order is stable and duplicate cards are omitted", () => {
  const first = { key: "a", note: "recommendation" };
  const second = { key: "b" };
  assert.deepEqual(orderChatProducts([first, { key: "a" }, second]), [first, second]);
  assert.deepEqual(orderChatProducts([first, second], []), []);
});

import { describe, expect, it } from "vitest";
import { isTempId, removeRow, settleInto, tempId, trackCreate, upsertById } from "./optimistic";

/** A create we control the timing of, the way the network controls it. */
function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e?: unknown) => void } {
  let resolve!: (v: T) => void;
  let reject!: (e?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

describe("tempId", () => {
  it("marks itself as temporary", () => {
    expect(isTempId(tempId())).toBe(true);
    expect(isTempId("9f3c-real")).toBe(false);
  });

  it("does not collide inside one millisecond", () => {
    // Two strokes can finish in the same tick — a lasso drag commits a whole
    // selection in one pass — and colliding ids would have them settle onto
    // each other.
    const ids = new Set(Array.from({ length: 500 }, () => tempId()));
    expect(ids.size).toBe(500);
  });
});

describe("upsertById", () => {
  it("replaces the temp in place, keeping order", () => {
    const rows = [{ id: "a" }, { id: "temp-1" }, { id: "c" }];
    expect(upsertById(rows, "temp-1", { id: "real" })).toEqual([
      { id: "a" },
      { id: "real" },
      { id: "c" },
    ]);
  });

  it("appends when the temp has already left the list", () => {
    // The bug this exists for: `map` alone silently drops the settled row, so
    // a stroke that IS on the server stops being on the page.
    const rows = [{ id: "a" }];
    expect(upsertById(rows, "temp-1", { id: "real" })).toEqual([{ id: "a" }, { id: "real" }]);
  });
});

describe("removeRow", () => {
  it("waits for an in-flight create and deletes the server's id", async () => {
    const create = deferred<{ id: string }>();
    const temp = tempId();
    void trackCreate(temp, create.promise);

    const deleted: string[] = [];
    const done = removeRow(temp, async (id) => {
      deleted.push(id);
    });

    // Nothing yet: the POST has not come back, so there is no id to delete.
    await flush();
    expect(deleted).toEqual([]);

    create.resolve({ id: "server-77" });
    await done;
    expect(deleted).toEqual(["server-77"]);
  });

  it("does nothing for a temp the server never accepted", async () => {
    const create = deferred<{ id: string }>();
    const temp = tempId();
    void trackCreate(temp, create.promise).catch(() => undefined);

    const deleted: string[] = [];
    const done = removeRow(temp, async (id) => {
      deleted.push(id);
    });
    create.reject(new Error("refused"));
    await done;
    expect(deleted).toEqual([]);
  });

  it("does nothing for a temp that was never registered", async () => {
    // A 局部-eraser fragment exists only in the cache until its gesture
    // commits; asking to delete it must not invent a server call.
    const deleted: string[] = [];
    await removeRow(tempId(), async (id) => {
      deleted.push(id);
    });
    expect(deleted).toEqual([]);
  });

  it("passes a real id straight through", async () => {
    const deleted: string[] = [];
    await removeRow("server-3", async (id) => {
      deleted.push(id);
    });
    expect(deleted).toEqual(["server-3"]);
  });
});

describe("settleInto", () => {
  it("upserts a settled create", () => {
    const temp = tempId();
    expect(settleInto([{ id: temp }], temp, { id: "real" })).toEqual([{ id: "real" }]);
  });

  it("does NOT resurrect a row deleted while its create was in flight", async () => {
    // Delete-during-create: both handlers are waiting on the same promise, and
    // the create's was registered first, so it runs first. Without the
    // discard mark it would put the row back and the reader would watch
    // something they just deleted reappear — and stay, while the server
    // drops it.
    const create = deferred<{ id: string }>();
    const temp = tempId();
    const tracked = trackCreate(temp, create.promise);

    const deleted: string[] = [];
    const removal = removeRow(temp, async (id) => {
      deleted.push(id);
    });

    let cache: { id: string }[] = [{ id: temp }];
    void tracked.then((row) => {
      cache = settleInto(cache, temp, row);
    });
    cache = cache.filter((r) => r.id !== temp); // the optimistic removal

    create.resolve({ id: "server-9" });
    await removal;
    await flush();

    expect(cache).toEqual([]);
    expect(deleted).toEqual(["server-9"]);
  });

  it("stops discarding once the create has settled, so ids can be reused", async () => {
    const create = deferred<{ id: string }>();
    const temp = tempId();
    void trackCreate(temp, create.promise);
    const removal = removeRow(temp, async () => undefined);
    create.resolve({ id: "server-1" });
    await removal;
    await flush();
    // The registration is gone, so a later row under the same key is not
    // silently swallowed by a stale discard mark.
    expect(settleInto([], temp, { id: "later" })).toEqual([{ id: "later" }]);
  });
});

/**
 * The temp-id protocol — one place, because getting it wrong loses marks.
 *
 * Every annotation is written optimistically: the client mints a `temp-…` id,
 * puts the row straight into the query cache so the mark appears under the
 * pen, and POSTs. When the server's row arrives it replaces the temp.
 *
 * Two assumptions were made about that id independently at six call sites, and
 * both are false for exactly the window the id exists in:
 *
 * 1. **"A temp id means nothing is on the server."** It means nothing is on
 *    the server YET. Between pen-up and the POST resolving — ten sequential
 *    round trips for a five-stroke lasso drag, comfortably a second or two on
 *    a tablet's wifi — a delete that skips the row because its id looks
 *    temporary leaves the row on the server forever. The reader sees one copy,
 *    reopens the paper, and finds two.
 * 2. **"The row I am replacing is still in the cache."** A reconciler written
 *    as `prev.map(r => r.id === tempId ? row : r)` silently does nothing if a
 *    later gesture has already removed the temp — so the settled row never
 *    enters the cache and becomes invisible while remaining on the server.
 *
 * So: an in-flight create is REGISTERED here, and anything that wants to
 * delete or patch a row waits for its real id first. Shared by ink, tape and
 * notes because all three make the same promise to the reader.
 */

/** Rows whose create is still in flight, keyed by the temp id they were shown
 *  under. Resolves to the server's id, or null if the create was refused. */
const inFlight = new Map<string, Promise<string | null>>();

/** Temps deleted before their create came back. Their server rows are being
 *  removed, so the settled row must NOT be reconciled into any cache. */
const discarded = new Set<string>();

/** Is this a client-minted id that the server has never seen under that name? */
export const isTempId = (id: string): boolean => id.startsWith("temp-");

/** Mint one. Time plus randomness: two strokes finished in the same
 *  millisecond must not collide, and they can be. */
export const tempId = (): string =>
  `temp-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;

/**
 * Register a create that is on its way, and hand back the same promise.
 *
 * The registration is removed when it settles, so the map holds only what is
 * genuinely outstanding — normally nothing, briefly a handful.
 */
export function trackCreate<T extends { id: string }>(temp: string, create: Promise<T>): Promise<T> {
  inFlight.set(
    temp,
    create.then(
      (row) => row.id,
      () => null,
    ),
  );
  void inFlight.get(temp)?.finally(() => {
    inFlight.delete(temp);
    discarded.delete(temp);
  });
  return create;
}

/**
 * Remove a row, waiting for its create if one is still in the air.
 *
 * Marks the temp as discarded first, so that when the create does come back
 * its reconciler leaves the cache alone. Without that mark the two settle in
 * the wrong order — the create's handler was registered first, so it runs
 * first and puts the row back — and the reader watches something they just
 * deleted reappear, then stay on screen while the server drops it.
 */
export async function removeRow(
  id: string,
  remove: (realId: string) => Promise<unknown>,
): Promise<void> {
  discarded.add(id);
  const real = await serverId(id);
  if (real === null) return; // never persisted; the optimistic removal is all of it
  await remove(real);
}

/**
 * Reconcile a settled create into a cached list: upsert, unless the row was
 * deleted while its create was in flight.
 */
export function settleInto<T extends { id: string }>(rows: T[], temp: string, row: T): T[] {
  if (discarded.has(temp)) return rows;
  return upsertById(rows, temp, row);
}

/**
 * The id the server actually knows this row by, waiting for the create if it
 * has not landed yet.
 *
 * `null` means there is nothing on the server to act on: either the create was
 * refused, or this temp id was never registered (a row that only ever existed
 * locally, like a 局部-eraser fragment before its gesture commits).
 */
export async function serverId(id: string): Promise<string | null> {
  if (!isTempId(id)) return id;
  const pending = inFlight.get(id);
  return pending ? await pending : null;
}

/**
 * Put a settled row into a list, replacing its temp if present and appending
 * it if not.
 *
 * The append is the point. `map` alone is a silent no-op when the temp has
 * already been removed by a later gesture, which loses a row that exists on
 * the server — invisible until the next full reload, and by then duplicated.
 */
export function upsertById<T extends { id: string }>(rows: T[], tempIdent: string, row: T): T[] {
  let found = false;
  const next = rows.map((r) => {
    if (r.id !== tempIdent) return r;
    found = true;
    return row;
  });
  return found ? next : [...next, row];
}

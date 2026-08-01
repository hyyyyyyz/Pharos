export function getDelta(deltaMap: string | undefined, nfcPos: number): number;
export function nfcToOriginal(deltaMap: string | undefined, nfcPos: number): number;
export function nfcToOriginalLocal(deltaMap: string | undefined, entryStartNFC: number, localNFCPos: number): number;
export function mergeDeltaMaps(mapA: string | undefined, mapB: string | undefined, nfcLenA: number): string;
export function composeDeltaMaps(outer: string | undefined, inner: string | undefined, finalLength: number): string;

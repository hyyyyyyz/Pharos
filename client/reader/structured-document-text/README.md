# Structured Document Text

Structured Document Text (SDT) is Zotero's structured text format for documents.

SDT stores a normalized document tree together with document metadata, outline, page mappings, source anchors, and text ranges needed for reading, annotation, search, and retrieval workflows. It is currently produced for PDFs, EPUBs, and web snapshots.

The logical SDT model is:

```js
{
  schemaVersion,
  metadata,
  catalog,
  content
}
```

`metadata` describes the processor and source document. `catalog` contains document-level structures that point into content, including pages and outline. `content` contains the top-level structured text blocks.

The default persisted form is a binary `.sdt` pack. JSON is used internally and for debugging and streaming workflows.

## Use Cases

- Reading mode (Desktop/iOS/Android/Web) with text annotations
- Text layer for the Zotero Reader PDF viewer
- Outline preview outside the Reader (e.g., in the item pane)
- Structured context for agents
- Section-level chunking for embeddings
- Disk-backed or remote random access (e.g., S3) to large documents

## SDT Pack

An SDT pack stores the logical SDT model in a compact binary container optimized for random access.

The file is organized roughly as:

```text
header
index
metadata
catalog
content chunks
```

The header identifies the file as SDT and stores the pack/schema version. The index stores the metadata and catalog sizes plus the content chunk offsets and block boundaries.

`metadata` and `catalog` are stored as separate compressed JSON sections. `content` is split into compressed chunks of top-level blocks. Each content chunk includes a small block offset table, so a reader can extract individual blocks after reading only the relevant chunk.

This layout lets consumers read metadata, pages, outline, selected blocks, or the full document depending on what they need, without always loading and parsing the whole structure. Memory use can stay bounded by the sections or chunks being accessed, and small reads remain fast even across many SDT files.

SDT packs are produced by `document-worker`.

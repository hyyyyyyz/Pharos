//! Read projections over the versioned Zotero mirror.

use std::path::PathBuf;

use rusqlite::{params, params_from_iter, types::Value as SqlValue, Connection, OptionalExtension};
use serde_json::Value;

use super::{
    mirror::ZoteroMirror,
    model::{
        LibraryKind, ZoteroAttachment, ZoteroCollection, ZoteroFulltext, ZoteroItem,
        ZoteroItemDetail, ZoteroItemQuery, ZoteroItemRef, ZoteroItemSummary, ZoteroLibrary,
        ZoteroLibraryRef, ZoteroPage, ZoteroSavedSearch, ZoteroTag, ZoteroTagRef,
    },
};

#[derive(Debug, Clone, Copy, Default)]
pub struct MirrorMetrics {
    pub libraries: u64,
    pub items: u64,
    pub attachments: u64,
    pub collections: u64,
    pub notes: u64,
    pub annotations: u64,
    pub available_attachments: u64,
}

impl ZoteroMirror {
    pub fn list_libraries(&self) -> Result<Vec<ZoteroLibrary>, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT source_id, library_id, library_type, name, library_version,\n\
                            editable, files_editable, raw_json\n\
                     FROM libraries WHERE deleted = 0\n\
                     ORDER BY CASE library_type WHEN 'user' THEN 0 ELSE 1 END, lower(name)",
                )
                .map_err(db_error)?;
            let rows = statement
                .query_map([], library_from_row)
                .map_err(db_error)?;
            rows.collect::<Result<Vec<_>, _>>().map_err(db_error)
        })
    }

    pub fn library(&self, reference: &ZoteroLibraryRef) -> Result<ZoteroLibrary, String> {
        self.with_connection(|connection| {
            connection
                .query_row(
                    "SELECT source_id, library_id, library_type, name, library_version,\n\
                            editable, files_editable, raw_json\n\
                     FROM libraries WHERE source_id = ?1 AND library_id = ?2 AND deleted = 0",
                    params![reference.source_id, reference.library_id],
                    library_from_row,
                )
                .optional()
                .map_err(db_error)?
                .ok_or_else(|| "Zotero 文库不在本地镜像中，请重新连接。".to_string())
        })
    }

    pub fn list_collections(
        &self,
        reference: &ZoteroLibraryRef,
    ) -> Result<Vec<ZoteroCollection>, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT c.source_id, c.library_id, c.collection_key, c.version, c.name,\n\
                            c.parent_key, c.raw_json, c.deleted,\n\
                            (SELECT COUNT(DISTINCT ic.item_key) FROM item_collections ic\n\
                             JOIN items i ON i.source_id = ic.source_id\n\
                                AND i.library_id = ic.library_id AND i.item_key = ic.item_key\n\
                             WHERE ic.source_id = c.source_id AND ic.library_id = c.library_id\n\
                                AND ic.collection_key = c.collection_key AND i.deleted = 0)\n\
                     FROM collections c\n\
                     WHERE c.source_id = ?1 AND c.library_id = ?2 AND c.deleted = 0\n\
                     ORDER BY lower(c.name)",
                )
                .map_err(db_error)?;
            let rows = statement
                .query_map(params![reference.source_id, reference.library_id], |row| {
                    Ok(ZoteroCollection {
                        source_id: row.get(0)?,
                        library_id: row.get(1)?,
                        key: row.get(2)?,
                        version: row.get(3)?,
                        name: row.get(4)?,
                        parent_key: row.get(5)?,
                        raw: parse_json(row.get::<_, String>(6)?),
                        deleted: row.get(7)?,
                        item_count: row.get(8)?,
                    })
                })
                .map_err(db_error)?;
            rows.collect::<Result<Vec<_>, _>>().map_err(db_error)
        })
    }

    pub fn list_saved_searches(
        &self,
        reference: &ZoteroLibraryRef,
    ) -> Result<Vec<ZoteroSavedSearch>, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT source_id, library_id, search_key, version, name, conditions_json,\n\
                            raw_json, deleted\n\
                     FROM saved_searches\n\
                     WHERE source_id = ?1 AND library_id = ?2 AND deleted = 0\n\
                     ORDER BY lower(name)",
                )
                .map_err(db_error)?;
            let rows = statement
                .query_map(params![reference.source_id, reference.library_id], |row| {
                    Ok(ZoteroSavedSearch {
                        source_id: row.get(0)?,
                        library_id: row.get(1)?,
                        key: row.get(2)?,
                        version: row.get(3)?,
                        name: row.get(4)?,
                        conditions: parse_json(row.get::<_, String>(5)?),
                        raw: parse_json(row.get::<_, String>(6)?),
                        deleted: row.get(7)?,
                    })
                })
                .map_err(db_error)?;
            rows.collect::<Result<Vec<_>, _>>().map_err(db_error)
        })
    }

    pub fn list_tags(&self, reference: &ZoteroLibraryRef) -> Result<Vec<ZoteroTag>, String> {
        self.with_connection(|connection| {
            let mut statement = connection
                .prepare(
                    "SELECT source_id, library_id, tag, tag_type, item_count\n\
                     FROM library_tags WHERE source_id = ?1 AND library_id = ?2\n\
                     ORDER BY lower(tag)",
                )
                .map_err(db_error)?;
            let rows = statement
                .query_map(params![reference.source_id, reference.library_id], |row| {
                    Ok(ZoteroTag {
                        source_id: row.get(0)?,
                        library_id: row.get(1)?,
                        tag: row.get(2)?,
                        kind: row.get(3)?,
                        item_count: row.get(4)?,
                    })
                })
                .map_err(db_error)?;
            rows.collect::<Result<Vec<_>, _>>().map_err(db_error)
        })
    }

    pub fn query_items(
        &self,
        query: &ZoteroItemQuery,
    ) -> Result<ZoteroPage<ZoteroItemSummary>, String> {
        let limit = query.limit.clamp(1, 500);
        self.with_connection(|connection| {
            let (where_sql, parameters) = item_filters(query);
            let total: u64 = connection
                .query_row(
                    &format!("SELECT COUNT(*) FROM items i WHERE {where_sql}"),
                    params_from_iter(parameters.iter()),
                    |row| row.get(0),
                )
                .map_err(db_error)?;
            let mut page_parameters = parameters;
            page_parameters.push(SqlValue::Integer(i64::from(limit)));
            page_parameters.push(SqlValue::Integer(i64::from(query.offset)));
            let sql = format!(
                "SELECT i.source_id, i.library_id, i.item_key, i.version, i.item_type,\n\
                        i.parent_key, i.display_title, i.abstract_note, i.date_added,\n\
                        i.date_modified, i.creators_json, i.relations_json, i.raw_json,\n\
                        i.deleted,\n\
                        (SELECT COUNT(*) FROM items child WHERE child.source_id = i.source_id\n\
                            AND child.library_id = i.library_id AND child.parent_key = i.item_key\n\
                            AND child.deleted = 0),\n\
                        (SELECT COUNT(*) FROM attachments a WHERE a.source_id = i.source_id\n\
                            AND a.library_id = i.library_id\n\
                            AND (a.parent_key = i.item_key OR a.item_key = i.item_key)),\n\
                        (SELECT COUNT(*) FROM attachments a WHERE a.source_id = i.source_id\n\
                            AND a.library_id = i.library_id AND a.path_status = 'available'\n\
                            AND (a.parent_key = i.item_key OR a.item_key = i.item_key))\n\
                 FROM items i WHERE {where_sql}\n\
                 ORDER BY i.deleted, COALESCE(i.date_modified, i.date_added, '') DESC,\n\
                          lower(COALESCE(i.display_title, i.item_key))\n\
                 LIMIT ? OFFSET ?"
            );
            let mut statement = connection.prepare(&sql).map_err(db_error)?;
            let rows = statement
                .query_map(params_from_iter(page_parameters.iter()), summary_from_row)
                .map_err(db_error)?;
            let items = rows.collect::<Result<Vec<_>, _>>().map_err(db_error)?;
            Ok(ZoteroPage {
                items,
                total,
                limit,
                offset: query.offset,
            })
        })
    }

    pub fn item_detail(&self, reference: &ZoteroItemRef) -> Result<ZoteroItemDetail, String> {
        self.with_connection(|connection| {
            let item = read_item(connection, reference)?
                .ok_or_else(|| "这个 Zotero 条目已不在本地镜像中，请重新同步。".to_string())?;
            let attachments = read_attachments(connection, reference)?;
            let children = read_children(connection, reference)?;
            let annotations = read_annotations(connection, reference)?;
            Ok(ZoteroItemDetail {
                item,
                attachments,
                children,
                annotations,
            })
        })
    }

    pub fn item_children(
        &self,
        reference: &ZoteroItemRef,
    ) -> Result<Vec<ZoteroItemSummary>, String> {
        self.with_connection(|connection| read_children(connection, reference))
    }

    pub fn attachment_path(&self, public_id: &str) -> Result<Option<PathBuf>, String> {
        self.with_connection(|connection| {
            connection
                .query_row(
                    "SELECT local_path FROM attachments\n\
                     WHERE public_id = ?1 AND path_status = 'available'",
                    [public_id],
                    |row| row.get::<_, Option<String>>(0),
                )
                .optional()
                .map(|value| value.flatten().map(PathBuf::from))
                .map_err(db_error)
        })
    }

    pub fn fulltext(&self, reference: &ZoteroItemRef) -> Result<Option<ZoteroFulltext>, String> {
        self.with_connection(|connection| {
            connection
                .query_row(
                    "SELECT version, content, indexed_pages, total_pages FROM fulltext\n\
                     WHERE source_id = ?1 AND library_id = ?2 AND item_key = ?3",
                    params![
                        reference.source_id,
                        reference.library_id,
                        reference.item_key
                    ],
                    |row| {
                        let content: Option<String> = row.get(1)?;
                        Ok(content.map(|content| ZoteroFulltext {
                            source_id: reference.source_id.clone(),
                            library_id: reference.library_id.clone(),
                            item_key: reference.item_key.clone(),
                            version: row.get(0).unwrap_or_default(),
                            content,
                            indexed_pages: row.get(2).ok().flatten(),
                            total_pages: row.get(3).ok().flatten(),
                        }))
                    },
                )
                .optional()
                .map(|value| value.flatten())
                .map_err(db_error)
        })
    }

    pub fn store_fulltext(&self, fulltext: &ZoteroFulltext) -> Result<(), String> {
        self.with_connection(|connection| {
            connection
                .execute(
                    "INSERT INTO fulltext(source_id, library_id, item_key, version, content,\n\
                        indexed_pages, total_pages, synced_at)\n\
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, strftime('%s','now'))\n\
                     ON CONFLICT(source_id, library_id, item_key) DO UPDATE SET\n\
                        version = excluded.version, content = excluded.content,\n\
                        indexed_pages = excluded.indexed_pages, total_pages = excluded.total_pages,\n\
                        synced_at = excluded.synced_at",
                    params![
                        fulltext.source_id,
                        fulltext.library_id,
                        fulltext.item_key,
                        fulltext.version,
                        fulltext.content,
                        fulltext.indexed_pages,
                        fulltext.total_pages,
                    ],
                )
                .map(|_| ())
                .map_err(db_error)
        })
    }

    pub fn metrics(&self) -> Result<MirrorMetrics, String> {
        self.with_connection(|connection| {
            Ok(MirrorMetrics {
                libraries: scalar(
                    connection,
                    "SELECT COUNT(*) FROM libraries WHERE deleted = 0",
                )?,
                items: scalar(connection, "SELECT COUNT(*) FROM items WHERE deleted = 0")?,
                attachments: scalar(connection, "SELECT COUNT(*) FROM attachments")?,
                collections: scalar(
                    connection,
                    "SELECT COUNT(*) FROM collections WHERE deleted = 0",
                )?,
                notes: scalar(
                    connection,
                    "SELECT COUNT(*) FROM items WHERE item_type = 'note' AND deleted = 0",
                )?,
                annotations: scalar(
                    connection,
                    "SELECT COUNT(*) FROM items WHERE item_type = 'annotation' AND deleted = 0",
                )?,
                available_attachments: scalar(
                    connection,
                    "SELECT COUNT(*) FROM attachments WHERE path_status = 'available'",
                )?,
            })
        })
    }
}

fn library_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ZoteroLibrary> {
    let kind: String = row.get(2)?;
    Ok(ZoteroLibrary {
        source_id: row.get(0)?,
        library_id: row.get(1)?,
        kind: if kind == "group" {
            LibraryKind::Group
        } else {
            LibraryKind::User
        },
        name: row.get(3)?,
        version: row.get(4)?,
        editable: row.get(5)?,
        files_editable: row.get(6)?,
        raw: parse_json(row.get::<_, String>(7)?),
    })
}

fn item_filters(query: &ZoteroItemQuery) -> (String, Vec<SqlValue>) {
    let mut clauses = Vec::new();
    let mut values = Vec::new();
    if !query.include_deleted {
        clauses.push("i.deleted = 0".to_string());
    }
    if let Some(library) = &query.library {
        clauses.push("i.source_id = ? AND i.library_id = ?".to_string());
        values.push(SqlValue::Text(library.source_id.clone()));
        values.push(SqlValue::Text(library.library_id.clone()));
    }
    if let Some(collection_key) = query.collection_key.as_deref() {
        clauses.push(
            "EXISTS (SELECT 1 FROM item_collections ic WHERE ic.source_id = i.source_id\n\
                AND ic.library_id = i.library_id AND ic.item_key = i.item_key\n\
                AND ic.collection_key = ?)"
                .to_string(),
        );
        values.push(SqlValue::Text(collection_key.to_string()));
    }
    if let Some(search_key) = query.saved_search_key.as_deref() {
        clauses.push(
            "EXISTS (SELECT 1 FROM saved_search_items ssi WHERE ssi.source_id = i.source_id
                AND ssi.library_id = i.library_id AND ssi.item_key = i.item_key
                AND ssi.search_key = ?)"
                .to_string(),
        );
        values.push(SqlValue::Text(search_key.to_string()));
    }
    if let Some(parent_key) = query.parent_key.as_deref() {
        clauses.push("i.parent_key = ?".to_string());
        values.push(SqlValue::Text(parent_key.to_string()));
    } else {
        clauses.push("i.parent_key IS NULL".to_string());
        if query.item_types.is_empty() {
            clauses.push("i.item_type NOT IN ('note', 'annotation')".to_string());
        }
    }
    if !query.item_types.is_empty() {
        clauses.push(format!(
            "i.item_type IN ({})",
            std::iter::repeat("?")
                .take(query.item_types.len())
                .collect::<Vec<_>>()
                .join(",")
        ));
        values.extend(query.item_types.iter().cloned().map(SqlValue::Text));
    }
    if let Some(tag) = query.tag.as_deref() {
        clauses.push(
            "EXISTS (SELECT 1 FROM item_tags it WHERE it.source_id = i.source_id\n\
                AND it.library_id = i.library_id AND it.item_key = i.item_key AND it.tag = ?)"
                .to_string(),
        );
        values.push(SqlValue::Text(tag.to_string()));
    }
    if let Some(search) = query
        .search
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        clauses.push(
            "(lower(COALESCE(i.display_title, '')) LIKE ? OR\n\
              lower(COALESCE(i.abstract_note, '')) LIKE ? OR\n\
              lower(i.creators_json) LIKE ?)"
                .to_string(),
        );
        let pattern = format!("%{}%", search.to_lowercase());
        values.extend([
            SqlValue::Text(pattern.clone()),
            SqlValue::Text(pattern.clone()),
            SqlValue::Text(pattern),
        ]);
    }
    if clauses.is_empty() {
        clauses.push("1 = 1".to_string());
    }
    (clauses.join(" AND "), values)
}

fn summary_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ZoteroItemSummary> {
    let raw = parse_json(row.get::<_, String>(12)?);
    let data = raw_data(&raw);
    Ok(ZoteroItemSummary {
        source_id: row.get(0)?,
        library_id: row.get(1)?,
        key: row.get(2)?,
        version: row.get(3)?,
        item_type: row.get(4)?,
        parent_key: row.get(5)?,
        title: row.get(6)?,
        abstract_note: row.get(7)?,
        year: raw_string(data, "date").as_deref().and_then(parse_year),
        venue: [
            "publicationTitle",
            "proceedingsTitle",
            "conferenceName",
            "repository",
            "university",
        ]
        .into_iter()
        .find_map(|key| raw_string(data, key).filter(|value| !value.trim().is_empty())),
        doi: raw_string(data, "DOI")
            .map(|value| normalize_doi(&value))
            .filter(|value| !value.is_empty()),
        url: raw_string(data, "url").filter(|value| !value.trim().is_empty()),
        date_added: row.get(8)?,
        date_modified: row.get(9)?,
        creators: parse_json_vec(row.get::<_, String>(10)?),
        tags: raw_tags(&raw),
        collection_keys: raw_collection_keys(&raw),
        deleted: row.get(13)?,
        child_count: row.get(14)?,
        attachment_count: row.get(15)?,
        available_attachment_count: row.get(16)?,
    })
}

fn read_item(
    connection: &Connection,
    reference: &ZoteroItemRef,
) -> Result<Option<ZoteroItem>, String> {
    connection
        .query_row(
            "SELECT source_id, library_id, item_key, version, item_type, parent_key,\n\
                    display_title, abstract_note, date_added, date_modified, creators_json,\n\
                    relations_json, raw_json, deleted\n\
             FROM items WHERE source_id = ?1 AND library_id = ?2 AND item_key = ?3",
            params![
                reference.source_id,
                reference.library_id,
                reference.item_key
            ],
            |row| {
                let raw = parse_json(row.get::<_, String>(12)?);
                Ok(ZoteroItem {
                    source_id: row.get(0)?,
                    library_id: row.get(1)?,
                    key: row.get(2)?,
                    version: row.get(3)?,
                    item_type: row.get(4)?,
                    parent_key: row.get(5)?,
                    title: row.get(6)?,
                    abstract_note: row.get(7)?,
                    date_added: row.get(8)?,
                    date_modified: row.get(9)?,
                    creators: parse_json_vec(row.get::<_, String>(10)?),
                    tags: raw_tags(&raw),
                    collection_keys: raw_collection_keys(&raw),
                    relations: parse_json(row.get::<_, String>(11)?),
                    raw,
                    deleted: row.get(13)?,
                })
            },
        )
        .optional()
        .map_err(db_error)
}

fn read_children(
    connection: &Connection,
    reference: &ZoteroItemRef,
) -> Result<Vec<ZoteroItemSummary>, String> {
    let mut statement = connection
        .prepare(
            "SELECT i.source_id, i.library_id, i.item_key, i.version, i.item_type,\n\
                    i.parent_key, i.display_title, i.abstract_note, i.date_added,\n\
                    i.date_modified, i.creators_json, i.relations_json, i.raw_json, i.deleted,\n\
                    (SELECT COUNT(*) FROM items c WHERE c.source_id = i.source_id\n\
                        AND c.library_id = i.library_id AND c.parent_key = i.item_key AND c.deleted = 0),\n\
                    (SELECT COUNT(*) FROM attachments a WHERE a.source_id = i.source_id\n\
                        AND a.library_id = i.library_id AND (a.parent_key = i.item_key OR a.item_key = i.item_key)),\n\
                    (SELECT COUNT(*) FROM attachments a WHERE a.source_id = i.source_id\n\
                        AND a.library_id = i.library_id AND a.path_status = 'available'\n\
                        AND (a.parent_key = i.item_key OR a.item_key = i.item_key))\n\
             FROM items i WHERE i.source_id = ?1 AND i.library_id = ?2\n\
                AND i.parent_key = ?3 AND i.deleted = 0\n\
             ORDER BY CASE i.item_type WHEN 'attachment' THEN 0 WHEN 'note' THEN 1\n\
                        WHEN 'annotation' THEN 2 ELSE 3 END, lower(COALESCE(i.display_title, ''))",
        )
        .map_err(db_error)?;
    let rows = statement
        .query_map(
            params![
                reference.source_id,
                reference.library_id,
                reference.item_key
            ],
            summary_from_row,
        )
        .map_err(db_error)?;
    rows.collect::<Result<Vec<_>, _>>().map_err(db_error)
}

fn read_attachments(
    connection: &Connection,
    reference: &ZoteroItemRef,
) -> Result<Vec<ZoteroAttachment>, String> {
    let mut statement = connection
        .prepare(
            "SELECT source_id, library_id, item_key, version, parent_key, public_id, link_mode,\n\
                    content_type, filename, path_status, file_size, local_path, raw_json\n\
             FROM attachments WHERE source_id = ?1 AND library_id = ?2\n\
                AND (parent_key = ?3 OR item_key = ?3)\n\
             ORDER BY CASE path_status WHEN 'available' THEN 0 ELSE 1 END, lower(COALESCE(filename, ''))",
        )
        .map_err(db_error)?;
    let rows = statement
        .query_map(
            params![
                reference.source_id,
                reference.library_id,
                reference.item_key
            ],
            |row| {
                let path_status: String = row.get(9)?;
                Ok(ZoteroAttachment {
                    source_id: row.get(0)?,
                    library_id: row.get(1)?,
                    key: row.get(2)?,
                    version: row.get(3)?,
                    parent_key: row.get(4)?,
                    public_id: row.get(5)?,
                    link_mode: row.get(6)?,
                    content_type: row.get(7)?,
                    filename: row.get(8)?,
                    available: path_status == "available",
                    size_bytes: row.get(10)?,
                    local_path: row.get::<_, Option<String>>(11)?.map(PathBuf::from),
                    raw: parse_json(row.get::<_, String>(12)?),
                })
            },
        )
        .map_err(db_error)?;
    rows.collect::<Result<Vec<_>, _>>().map_err(db_error)
}

fn read_annotations(
    connection: &Connection,
    reference: &ZoteroItemRef,
) -> Result<Vec<ZoteroItemSummary>, String> {
    let mut statement = connection
        .prepare(
            "SELECT i.source_id, i.library_id, i.item_key, i.version, i.item_type,\n\
                    i.parent_key, i.display_title, i.abstract_note, i.date_added,\n\
                    i.date_modified, i.creators_json, i.relations_json, i.raw_json, i.deleted,\n\
                    0, 0, 0\n\
             FROM items i WHERE i.source_id = ?1 AND i.library_id = ?2\n\
                AND i.item_type = 'annotation' AND i.deleted = 0\n\
                AND i.parent_key IN (\n\
                    SELECT a.item_key FROM attachments a WHERE a.source_id = ?1\n\
                        AND a.library_id = ?2 AND (a.parent_key = ?3 OR a.item_key = ?3)\n\
                )\n\
             ORDER BY i.date_added, i.item_key",
        )
        .map_err(db_error)?;
    let rows = statement
        .query_map(
            params![
                reference.source_id,
                reference.library_id,
                reference.item_key
            ],
            summary_from_row,
        )
        .map_err(db_error)?;
    rows.collect::<Result<Vec<_>, _>>().map_err(db_error)
}

fn raw_data(raw: &Value) -> &Value {
    raw.get("data").unwrap_or(raw)
}

fn raw_tags(raw: &Value) -> Vec<ZoteroTagRef> {
    raw_data(raw)
        .get("tags")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|tag| {
            Some(ZoteroTagRef {
                tag: tag.get("tag")?.as_str()?.to_string(),
                kind: tag.get("type").and_then(Value::as_i64),
            })
        })
        .collect()
}

fn raw_collection_keys(raw: &Value) -> Vec<String> {
    raw_data(raw)
        .get("collections")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(ToString::to_string)
        .collect()
}

fn raw_string(value: &Value, key: &str) -> Option<String> {
    value.get(key)?.as_str().map(ToString::to_string)
}

fn normalize_doi(raw: &str) -> String {
    let value = raw.trim();
    for prefix in ["https://doi.org/", "http://doi.org/", "doi:"] {
        if value.to_ascii_lowercase().starts_with(prefix) {
            return value[prefix.len()..].trim().to_string();
        }
    }
    value.to_string()
}

fn parse_year(raw: &str) -> Option<i32> {
    raw.as_bytes()
        .windows(4)
        .filter_map(|window| std::str::from_utf8(window).ok())
        .filter_map(|window| window.parse::<i32>().ok())
        .find(|year| (1000..=2999).contains(year))
}

fn parse_json(raw: String) -> Value {
    serde_json::from_str(&raw).unwrap_or(Value::Null)
}

fn parse_json_vec<T: serde::de::DeserializeOwned>(raw: String) -> Vec<T> {
    serde_json::from_str(&raw).unwrap_or_default()
}

fn scalar(connection: &Connection, sql: &str) -> Result<u64, String> {
    connection
        .query_row(sql, [], |row| row.get(0))
        .map_err(db_error)
}

fn db_error(error: rusqlite::Error) -> String {
    format!("无法读取 Zotero 本地镜像：{error}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn item_filters_are_parameterised() {
        let query = ZoteroItemQuery {
            library: Some(ZoteroLibraryRef {
                source_id: "zotero-local".into(),
                library_id: "1".into(),
            }),
            search: Some("%' OR 1=1 --".into()),
            tag: Some("AI".into()),
            ..ZoteroItemQuery::default()
        };
        let (sql, values) = item_filters(&query);
        assert!(!sql.contains("1=1 --"));
        assert!(values
            .iter()
            .any(|value| matches!(value, SqlValue::Text(text) if text.contains("1=1"))));
    }

    #[test]
    fn metadata_projection_normalises_year_and_doi() {
        assert_eq!(parse_year("2024-11-03"), Some(2024));
        assert_eq!(parse_year("forthcoming"), None);
        assert_eq!(
            normalize_doi("https://doi.org/10.1000/example"),
            "10.1000/example"
        );
        assert_eq!(normalize_doi("DOI:10.1000/example"), "10.1000/example");
    }
}

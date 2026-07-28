//! Versioned SQLite mirror for Zotero metadata and relations.

use std::path::PathBuf;

use rusqlite::{params, Connection, OptionalExtension, Transaction};

use super::local_api::LibraryDelta;
use super::model::{LibrarySnapshot, ProviderCapabilities, ProviderKind, ZoteroLibrary};

const SCHEMA_VERSION: i64 = 3;

#[derive(Debug, Clone)]
pub struct ZoteroMirror {
    path: PathBuf,
}

impl ZoteroMirror {
    pub fn open(path: impl Into<PathBuf>) -> Result<Self, String> {
        let path = path.into();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|error| format!("无法创建 Zotero 镜像目录：{error}"))?;
        }
        let mirror = Self { path };
        mirror.with_connection(|connection| migrate(connection))?;
        Ok(mirror)
    }

    pub fn replace_library(&self, snapshot: &LibrarySnapshot) -> Result<(), String> {
        let library = snapshot
            .library
            .as_ref()
            .ok_or_else(|| "Zotero 快照缺少文库信息。".to_string())?;
        self.with_connection(|connection| {
            let transaction = connection
                .transaction()
                .map_err(|error| format!("无法开始 Zotero 镜像事务：{error}"))?;
            replace_library_in_transaction(&transaction, snapshot, library)?;
            transaction
                .commit()
                .map_err(|error| format!("无法提交 Zotero 镜像事务：{error}"))
        })
    }

    pub fn library_version(
        &self,
        source_id: &str,
        library_id: &str,
    ) -> Result<Option<u64>, String> {
        self.with_connection(|connection| {
            connection
                .query_row(
                    "SELECT library_version FROM libraries\n\
                     WHERE source_id = ?1 AND library_id = ?2 AND deleted = 0",
                    params![source_id, library_id],
                    |row| row.get(0),
                )
                .optional()
                .map_err(|error| format!("无法读取 Zotero 同步版本：{error}"))
        })
    }

    pub fn apply_delta(&self, delta: &LibraryDelta) -> Result<(), String> {
        self.with_connection(|connection| {
            let transaction = connection
                .transaction()
                .map_err(|error| format!("无法开始 Zotero 增量同步事务：{error}"))?;
            apply_delta_in_transaction(&transaction, delta)?;
            transaction
                .commit()
                .map_err(|error| format!("无法提交 Zotero 增量同步：{error}"))
        })
    }

    pub fn get_meta(&self, key: &str) -> Result<Option<String>, String> {
        self.with_connection(|connection| {
            connection
                .query_row(
                    "SELECT value FROM mirror_meta WHERE key = ?1",
                    [key],
                    |row| row.get(0),
                )
                .optional()
                .map_err(|error| format!("无法读取 Zotero 镜像元数据：{error}"))
        })
    }

    pub fn set_meta(&self, key: &str, value: &str) -> Result<(), String> {
        self.with_connection(|connection| {
            connection
                .execute(
                    "INSERT INTO mirror_meta(key, value) VALUES (?1, ?2)\n\
                     ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    params![key, value],
                )
                .map(|_| ())
                .map_err(|error| format!("无法写入 Zotero 镜像元数据：{error}"))
        })
    }

    pub fn reconcile_libraries(
        &self,
        source_id: &str,
        active_ids: &[String],
    ) -> Result<(), String> {
        self.with_connection(|connection| {
            let transaction = connection
                .transaction()
                .map_err(|error| format!("无法开始 Zotero 文库核对事务：{error}"))?;
            let mut statement = transaction
                .prepare("SELECT library_id FROM libraries WHERE source_id = ?1 AND deleted = 0")
                .map_err(|error| format!("无法读取 Zotero 文库：{error}"))?;
            let existing = statement
                .query_map([source_id], |row| row.get::<_, String>(0))
                .map_err(|error| format!("无法读取 Zotero 文库：{error}"))?
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| format!("无法读取 Zotero 文库：{error}"))?;
            drop(statement);
            for library_id in existing {
                if !active_ids.contains(&library_id) {
                    transaction
                        .execute(
                            "UPDATE libraries SET deleted = 1 WHERE source_id = ?1 AND library_id = ?2",
                            params![source_id, library_id],
                        )
                        .map_err(|error| format!("无法核对 Zotero 文库：{error}"))?;
                }
            }
            transaction
                .commit()
                .map_err(|error| format!("无法提交 Zotero 文库核对：{error}"))
        })
    }

    pub(crate) fn with_connection<T>(
        &self,
        operation: impl FnOnce(&mut Connection) -> Result<T, String>,
    ) -> Result<T, String> {
        let mut connection = Connection::open(&self.path)
            .map_err(|error| format!("无法打开 Zotero 镜像：{error}"))?;
        configure(&connection)?;
        operation(&mut connection)
    }
}

fn configure(connection: &Connection) -> Result<(), String> {
    connection
        .execute_batch(
            "PRAGMA foreign_keys = ON;\n\
             PRAGMA journal_mode = WAL;\n\
             PRAGMA synchronous = NORMAL;\n\
             PRAGMA busy_timeout = 5000;",
        )
        .map_err(|error| format!("无法配置 Zotero 镜像：{error}"))
}

fn migrate(connection: &mut Connection) -> Result<(), String> {
    configure(connection)?;
    let original: i64 = connection
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .map_err(|error| format!("无法读取 Zotero 镜像版本：{error}"))?;
    if original > SCHEMA_VERSION {
        return Err(format!(
            "Zotero 镜像版本 {original} 高于当前客户端支持的 {SCHEMA_VERSION}。"
        ));
    }
    if original == SCHEMA_VERSION {
        return Ok(());
    }
    if original < 1 {
        let transaction = connection
            .transaction()
            .map_err(|error| format!("无法开始 Zotero 镜像迁移：{error}"))?;
        transaction
            .execute_batch(
                r#"
            CREATE TABLE mirror_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE sources (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                display_name TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                last_seen_at INTEGER
            );
            CREATE TABLE libraries (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                library_type TEXT NOT NULL,
                name TEXT NOT NULL,
                editable INTEGER NOT NULL DEFAULT 0,
                files_editable INTEGER NOT NULL DEFAULT 0,
                library_version INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source_id, library_id)
            );
            CREATE TABLE collections (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                collection_key TEXT NOT NULL,
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                parent_key TEXT,
                raw_json TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source_id, library_id, collection_key)
            );
            CREATE INDEX collections_parent_idx
                ON collections(source_id, library_id, parent_key);
            CREATE TABLE items (
                id INTEGER PRIMARY KEY,
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                version INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                parent_key TEXT,
                display_title TEXT,
                abstract_note TEXT,
                date_added TEXT,
                date_modified TEXT,
                creators_json TEXT NOT NULL,
                relations_json TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                UNIQUE (source_id, library_id, item_key)
            );
            CREATE INDEX items_parent_idx ON items(source_id, library_id, parent_key);
            CREATE INDEX items_type_idx ON items(source_id, library_id, item_type);
            CREATE TABLE item_collections (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                collection_key TEXT NOT NULL,
                PRIMARY KEY (source_id, library_id, item_key, collection_key)
            );
            CREATE TABLE item_tags (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                tag TEXT NOT NULL,
                tag_type INTEGER,
                PRIMARY KEY (source_id, library_id, item_key, tag)
            );
            CREATE INDEX item_tags_tag_idx ON item_tags(source_id, library_id, tag);
            CREATE TABLE attachments (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                parent_key TEXT,
                public_id TEXT NOT NULL UNIQUE,
                link_mode TEXT,
                content_type TEXT,
                filename TEXT,
                local_path TEXT,
                path_status TEXT NOT NULL DEFAULT 'unknown',
                file_size INTEGER,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (source_id, library_id, item_key)
            );
            CREATE TABLE saved_searches (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                search_key TEXT NOT NULL,
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                conditions_json TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source_id, library_id, search_key)
            );
            CREATE TABLE library_tags (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                tag_type INTEGER,
                item_count INTEGER,
                PRIMARY KEY (source_id, library_id, tag)
            );
            CREATE TABLE fulltext (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                content TEXT,
                indexed_pages INTEGER,
                total_pages INTEGER,
                synced_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (source_id, library_id, item_key)
            );
            CREATE TABLE sync_state (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0,
                last_success_at INTEGER,
                last_error TEXT,
                PRIMARY KEY (source_id, library_id, entity_type)
            );
            CREATE TABLE tombstones (
                source_id TEXT NOT NULL,
                library_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                version INTEGER NOT NULL,
                deleted_at INTEGER NOT NULL,
                PRIMARY KEY (source_id, library_id, entity_type, entity_key)
            );
            "#,
            )
            .map_err(|error| format!("无法迁移 Zotero 镜像：{error}"))?;
        transaction
            .pragma_update(None, "user_version", 1)
            .map_err(|error| format!("无法更新 Zotero 镜像版本：{error}"))?;
        transaction
            .commit()
            .map_err(|error| format!("无法提交 Zotero 镜像迁移：{error}"))?;
    }

    let current: i64 = connection
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .map_err(|error| format!("无法读取 Zotero 镜像版本：{error}"))?;
    if current < 2 {
        let transaction = connection
            .transaction()
            .map_err(|error| format!("无法开始 Zotero 镜像迁移：{error}"))?;
        transaction
            .execute_batch(
                "ALTER TABLE attachments ADD COLUMN version INTEGER NOT NULL DEFAULT 0;\n\
                 CREATE INDEX attachments_parent_idx\n\
                    ON attachments(source_id, library_id, parent_key);\n\
                 CREATE INDEX items_modified_idx\n\
                    ON items(source_id, library_id, date_modified DESC);",
            )
            .map_err(|error| format!("无法迁移 Zotero 镜像：{error}"))?;
        transaction
            .pragma_update(None, "user_version", 2)
            .map_err(|error| format!("无法更新 Zotero 镜像版本：{error}"))?;
        transaction
            .commit()
            .map_err(|error| format!("无法提交 Zotero 镜像迁移：{error}"))?;
    }
    let current: i64 = connection
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .map_err(|error| format!("无法读取 Zotero 镜像版本：{error}"))?;
    if current < 3 {
        let transaction = connection
            .transaction()
            .map_err(|error| format!("无法开始 Zotero 镜像迁移：{error}"))?;
        transaction
            .execute_batch(
                "CREATE TABLE saved_search_items (
                    source_id TEXT NOT NULL,
                    library_id TEXT NOT NULL,
                    search_key TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    PRIMARY KEY (source_id, library_id, search_key, item_key)
                 );
                 CREATE INDEX saved_search_items_item_idx
                    ON saved_search_items(source_id, library_id, item_key);",
            )
            .map_err(|error| format!("无法迁移 Zotero 保存搜索结果：{error}"))?;
        transaction
            .pragma_update(None, "user_version", 3)
            .map_err(|error| format!("无法更新 Zotero 镜像版本：{error}"))?;
        transaction
            .commit()
            .map_err(|error| format!("无法提交 Zotero 镜像迁移：{error}"))?;
    }
    Ok(())
}

fn replace_library_in_transaction(
    transaction: &Transaction<'_>,
    snapshot: &LibrarySnapshot,
    library: &ZoteroLibrary,
) -> Result<(), String> {
    let source_id = &library.source_id;
    let library_id = &library.library_id;
    for table in [
        "item_collections",
        "item_tags",
        "saved_search_items",
        "attachments",
        "items",
        "collections",
        "saved_searches",
        "library_tags",
    ] {
        transaction
            .execute(
                &format!("DELETE FROM {table} WHERE source_id = ?1 AND library_id = ?2"),
                params![source_id, library_id],
            )
            .map_err(|error| format!("无法更新 Zotero 镜像：{error}"))?;
    }

    transaction
        .execute(
            "INSERT INTO sources(id, kind, display_name, capabilities_json, last_seen_at)\n\
             VALUES (?1, ?2, ?3, ?4, strftime('%s','now'))\n\
             ON CONFLICT(id) DO UPDATE SET\n\
                kind = excluded.kind, display_name = excluded.display_name,\n\
                capabilities_json = excluded.capabilities_json, last_seen_at = excluded.last_seen_at",
            params![
                source_id,
                provider_kind_label(ProviderKind::LocalApi),
                "本机 Zotero",
                json(&ProviderCapabilities::local_api())?,
            ],
        )
        .map_err(|error| format!("无法更新 Zotero 数据源：{error}"))?;
    transaction
        .execute(
            "INSERT INTO libraries(source_id, library_id, library_type, name, editable,\n\
                files_editable, library_version, raw_json, deleted)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 0)\n\
             ON CONFLICT(source_id, library_id) DO UPDATE SET\n\
                library_type = excluded.library_type, name = excluded.name,\n\
                editable = excluded.editable, files_editable = excluded.files_editable,\n\
                library_version = excluded.library_version, raw_json = excluded.raw_json, deleted = 0",
            params![
                source_id,
                library_id,
                library.kind.as_str(),
                library.name,
                library.editable,
                library.files_editable,
                library.version,
                json(&library.raw)?,
            ],
        )
        .map_err(|error| format!("无法更新 Zotero 文库：{error}"))?;

    for collection in &snapshot.collections {
        transaction.execute(
            "INSERT INTO collections(source_id, library_id, collection_key, version, name, parent_key, raw_json, deleted)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![collection.source_id, collection.library_id, collection.key, collection.version,
                collection.name, collection.parent_key, json(&collection.raw)?, collection.deleted],
        ).map_err(|error| format!("无法写入 Zotero 分类：{error}"))?;
    }
    for item in &snapshot.items {
        transaction.execute(
            "INSERT INTO items(source_id, library_id, item_key, version, item_type, parent_key,\n\
                display_title, abstract_note, date_added, date_modified, creators_json, relations_json, raw_json, deleted)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)",
            params![item.source_id, item.library_id, item.key, item.version, item.item_type,
                item.parent_key, item.title, item.abstract_note, item.date_added, item.date_modified,
                json(&item.creators)?, json(&item.relations)?, json(&item.raw)?, item.deleted],
        ).map_err(|error| format!("无法写入 Zotero 条目：{error}"))?;
        for collection_key in &item.collection_keys {
            transaction.execute(
                "INSERT OR IGNORE INTO item_collections(source_id, library_id, item_key, collection_key) VALUES (?1, ?2, ?3, ?4)",
                params![item.source_id, item.library_id, item.key, collection_key],
            ).map_err(|error| format!("无法写入 Zotero 分类关系：{error}"))?;
        }
        for tag in &item.tags {
            transaction.execute(
                "INSERT OR REPLACE INTO item_tags(source_id, library_id, item_key, tag, tag_type) VALUES (?1, ?2, ?3, ?4, ?5)",
                params![item.source_id, item.library_id, item.key, tag.tag, tag.kind],
            ).map_err(|error| format!("无法写入 Zotero 标签关系：{error}"))?;
        }
    }
    for attachment in &snapshot.attachments {
        transaction.execute(
            "INSERT INTO attachments(source_id, library_id, item_key, parent_key, public_id, link_mode,\n\
                content_type, filename, local_path, path_status, file_size, raw_json, version)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![attachment.source_id, attachment.library_id, attachment.key, attachment.parent_key,
                attachment.public_id, attachment.link_mode, attachment.content_type, attachment.filename,
                attachment.local_path.as_ref().map(|path| path.to_string_lossy().into_owned()),
                if attachment.available { "available" } else { "missing" }, attachment.size_bytes,
                json(&attachment.raw)?, attachment.version],
        ).map_err(|error| format!("无法写入 Zotero 附件：{error}"))?;
    }
    for search in &snapshot.searches {
        transaction.execute(
            "INSERT INTO saved_searches(source_id, library_id, search_key, version, name, conditions_json, raw_json, deleted)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![search.source_id, search.library_id, search.key, search.version, search.name,
                json(&search.conditions)?, json(&search.raw)?, search.deleted],
        ).map_err(|error| format!("无法写入 Zotero 保存的搜索：{error}"))?;
    }
    for membership in &snapshot.search_memberships {
        for item_key in &membership.item_keys {
            transaction
                .execute(
                    "INSERT OR IGNORE INTO saved_search_items(source_id, library_id, search_key, item_key)
                     VALUES (?1, ?2, ?3, ?4)",
                    params![
                        membership.source_id,
                        membership.library_id,
                        membership.search_key,
                        item_key
                    ],
                )
                .map_err(|error| format!("无法写入 Zotero 保存搜索结果：{error}"))?;
        }
    }
    for tag in &snapshot.tags {
        transaction.execute(
            "INSERT INTO library_tags(source_id, library_id, tag, tag_type, item_count) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![tag.source_id, tag.library_id, tag.tag, tag.kind, tag.item_count],
        ).map_err(|error| format!("无法写入 Zotero 标签：{error}"))?;
    }
    for fulltext in &snapshot.fulltext {
        transaction
            .execute(
                "INSERT INTO fulltext(source_id, library_id, item_key, version, synced_at)\n\
                 VALUES (?1, ?2, ?3, ?4, strftime('%s','now'))\n\
                 ON CONFLICT(source_id, library_id, item_key) DO UPDATE SET\n\
                    version = excluded.version, synced_at = excluded.synced_at",
                params![
                    fulltext.source_id,
                    fulltext.library_id,
                    fulltext.item_key,
                    fulltext.version
                ],
            )
            .map_err(|error| format!("无法写入 Zotero 全文索引：{error}"))?;
    }
    transaction
        .execute(
            "INSERT INTO sync_state(source_id, library_id, entity_type, version, last_success_at, last_error)\n\
             VALUES (?1, ?2, 'library', ?3, strftime('%s','now'), NULL)\n\
             ON CONFLICT(source_id, library_id, entity_type) DO UPDATE SET\n\
                version = excluded.version, last_success_at = excluded.last_success_at, last_error = NULL",
            params![source_id, library_id, library.version],
        )
        .map_err(|error| format!("无法更新 Zotero 同步状态：{error}"))?;
    Ok(())
}

fn apply_delta_in_transaction(
    transaction: &Transaction<'_>,
    delta: &LibraryDelta,
) -> Result<(), String> {
    let library = &delta.library;
    let source_id = &library.source_id;
    let library_id = &library.library_id;
    transaction
        .execute(
            "INSERT INTO sources(id, kind, display_name, capabilities_json, last_seen_at)\n\
             VALUES (?1, ?2, ?3, ?4, strftime('%s','now'))\n\
             ON CONFLICT(id) DO UPDATE SET kind = excluded.kind,\n\
                display_name = excluded.display_name, capabilities_json = excluded.capabilities_json,\n\
                last_seen_at = excluded.last_seen_at",
            params![
                source_id,
                provider_kind_label(ProviderKind::LocalApi),
                "本机 Zotero",
                json(&ProviderCapabilities::local_api())?,
            ],
        )
        .map_err(|error| format!("无法更新 Zotero 数据源：{error}"))?;
    transaction
        .execute(
            "INSERT INTO libraries(source_id, library_id, library_type, name, editable,\n\
                files_editable, library_version, raw_json, deleted)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, 0)\n\
             ON CONFLICT(source_id, library_id) DO UPDATE SET\n\
                library_type = excluded.library_type, name = excluded.name,\n\
                editable = excluded.editable, files_editable = excluded.files_editable,\n\
                library_version = excluded.library_version, raw_json = excluded.raw_json, deleted = 0",
            params![
                source_id,
                library_id,
                library.kind.as_str(),
                library.name,
                library.editable,
                library.files_editable,
                library.version,
                json(&library.raw)?,
            ],
        )
        .map_err(|error| format!("无法更新 Zotero 文库：{error}"))?;

    for collection in &delta.collections {
        transaction
            .execute(
                "INSERT INTO collections(source_id, library_id, collection_key, version, name,\n\
                    parent_key, raw_json, deleted) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)\n\
                 ON CONFLICT(source_id, library_id, collection_key) DO UPDATE SET\n\
                    version = excluded.version, name = excluded.name, parent_key = excluded.parent_key,\n\
                    raw_json = excluded.raw_json, deleted = excluded.deleted",
                params![
                    collection.source_id,
                    collection.library_id,
                    collection.key,
                    collection.version,
                    collection.name,
                    collection.parent_key,
                    json(&collection.raw)?,
                    collection.deleted,
                ],
            )
            .map_err(|error| format!("无法增量写入 Zotero 分类：{error}"))?;
    }

    for item in &delta.items {
        transaction
            .execute(
                "DELETE FROM item_collections WHERE source_id = ?1 AND library_id = ?2 AND item_key = ?3",
                params![item.source_id, item.library_id, item.key],
            )
            .map_err(|error| format!("无法更新 Zotero 分类关系：{error}"))?;
        transaction
            .execute(
                "DELETE FROM item_tags WHERE source_id = ?1 AND library_id = ?2 AND item_key = ?3",
                params![item.source_id, item.library_id, item.key],
            )
            .map_err(|error| format!("无法更新 Zotero 标签关系：{error}"))?;
        transaction
            .execute(
                "DELETE FROM attachments WHERE source_id = ?1 AND library_id = ?2 AND item_key = ?3",
                params![item.source_id, item.library_id, item.key],
            )
            .map_err(|error| format!("无法更新 Zotero 附件：{error}"))?;
        transaction
            .execute(
                "INSERT INTO items(source_id, library_id, item_key, version, item_type, parent_key,\n\
                    display_title, abstract_note, date_added, date_modified, creators_json,\n\
                    relations_json, raw_json, deleted)\n\
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14)\n\
                 ON CONFLICT(source_id, library_id, item_key) DO UPDATE SET\n\
                    version = excluded.version, item_type = excluded.item_type,\n\
                    parent_key = excluded.parent_key, display_title = excluded.display_title,\n\
                    abstract_note = excluded.abstract_note, date_added = excluded.date_added,\n\
                    date_modified = excluded.date_modified, creators_json = excluded.creators_json,\n\
                    relations_json = excluded.relations_json, raw_json = excluded.raw_json,\n\
                    deleted = excluded.deleted",
                params![
                    item.source_id,
                    item.library_id,
                    item.key,
                    item.version,
                    item.item_type,
                    item.parent_key,
                    item.title,
                    item.abstract_note,
                    item.date_added,
                    item.date_modified,
                    json(&item.creators)?,
                    json(&item.relations)?,
                    json(&item.raw)?,
                    item.deleted,
                ],
            )
            .map_err(|error| format!("无法增量写入 Zotero 条目：{error}"))?;
        for collection_key in &item.collection_keys {
            transaction
                .execute(
                    "INSERT OR IGNORE INTO item_collections(source_id, library_id, item_key, collection_key)\n\
                     VALUES (?1, ?2, ?3, ?4)",
                    params![item.source_id, item.library_id, item.key, collection_key],
                )
                .map_err(|error| format!("无法增量写入 Zotero 分类关系：{error}"))?;
        }
        for tag in &item.tags {
            transaction
                .execute(
                    "INSERT OR REPLACE INTO item_tags(source_id, library_id, item_key, tag, tag_type)\n\
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                    params![item.source_id, item.library_id, item.key, tag.tag, tag.kind],
                )
                .map_err(|error| format!("无法增量写入 Zotero 标签关系：{error}"))?;
        }
    }

    for attachment in &delta.attachments {
        transaction
            .execute(
                "INSERT INTO attachments(source_id, library_id, item_key, parent_key, public_id,\n\
                    link_mode, content_type, filename, local_path, path_status, file_size, raw_json, version)\n\
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)\n\
                 ON CONFLICT(source_id, library_id, item_key) DO UPDATE SET\n\
                    parent_key = excluded.parent_key, public_id = excluded.public_id,\n\
                    link_mode = excluded.link_mode, content_type = excluded.content_type,\n\
                    filename = excluded.filename, local_path = excluded.local_path,\n\
                    path_status = excluded.path_status, file_size = excluded.file_size,\n\
                    raw_json = excluded.raw_json, version = excluded.version",
                params![
                    attachment.source_id,
                    attachment.library_id,
                    attachment.key,
                    attachment.parent_key,
                    attachment.public_id,
                    attachment.link_mode,
                    attachment.content_type,
                    attachment.filename,
                    attachment
                        .local_path
                        .as_ref()
                        .map(|path| path.to_string_lossy().into_owned()),
                    if attachment.available { "available" } else { "missing" },
                    attachment.size_bytes,
                    json(&attachment.raw)?,
                    attachment.version,
                ],
            )
            .map_err(|error| format!("无法增量写入 Zotero 附件：{error}"))?;
    }

    for search in &delta.searches {
        transaction
            .execute(
                "INSERT INTO saved_searches(source_id, library_id, search_key, version, name,\n\
                    conditions_json, raw_json, deleted) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)\n\
                 ON CONFLICT(source_id, library_id, search_key) DO UPDATE SET\n\
                    version = excluded.version, name = excluded.name,\n\
                    conditions_json = excluded.conditions_json, raw_json = excluded.raw_json,\n\
                    deleted = excluded.deleted",
                params![
                    search.source_id,
                    search.library_id,
                    search.key,
                    search.version,
                    search.name,
                    json(&search.conditions)?,
                    json(&search.raw)?,
                    search.deleted,
                ],
            )
            .map_err(|error| format!("无法增量写入 Zotero 搜索：{error}"))?;
    }
    transaction
        .execute(
            "DELETE FROM saved_search_items WHERE source_id = ?1 AND library_id = ?2",
            params![source_id, library_id],
        )
        .map_err(|error| format!("无法更新 Zotero 保存搜索结果：{error}"))?;
    for membership in &delta.search_memberships {
        for item_key in &membership.item_keys {
            transaction
                .execute(
                    "INSERT OR IGNORE INTO saved_search_items(source_id, library_id, search_key, item_key)
                     VALUES (?1, ?2, ?3, ?4)",
                    params![
                        membership.source_id,
                        membership.library_id,
                        membership.search_key,
                        item_key
                    ],
                )
                .map_err(|error| format!("无法增量写入 Zotero 保存搜索结果：{error}"))?;
        }
    }

    transaction
        .execute(
            "DELETE FROM library_tags WHERE source_id = ?1 AND library_id = ?2",
            params![source_id, library_id],
        )
        .map_err(|error| format!("无法更新 Zotero 标签：{error}"))?;
    for tag in &delta.tags {
        transaction
            .execute(
                "INSERT INTO library_tags(source_id, library_id, tag, tag_type, item_count)\n\
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    tag.source_id,
                    tag.library_id,
                    tag.tag,
                    tag.kind,
                    tag.item_count
                ],
            )
            .map_err(|error| format!("无法增量写入 Zotero 标签：{error}"))?;
    }
    for fulltext in &delta.fulltext {
        transaction
            .execute(
                "INSERT INTO fulltext(source_id, library_id, item_key, version, synced_at)\n\
                 VALUES (?1, ?2, ?3, ?4, strftime('%s','now'))\n\
                 ON CONFLICT(source_id, library_id, item_key) DO UPDATE SET\n\
                    version = excluded.version, content = NULL, indexed_pages = NULL,\n\
                    total_pages = NULL, synced_at = excluded.synced_at",
                params![
                    fulltext.source_id,
                    fulltext.library_id,
                    fulltext.item_key,
                    fulltext.version,
                ],
            )
            .map_err(|error| format!("无法增量写入 Zotero 全文索引：{error}"))?;
    }

    mark_missing(
        transaction,
        "collections",
        "collection_key",
        "collection",
        source_id,
        library_id,
        &delta.current_collection_keys,
        library.version,
    )?;
    mark_missing(
        transaction,
        "saved_searches",
        "search_key",
        "search",
        source_id,
        library_id,
        &delta.current_search_keys,
        library.version,
    )?;
    let missing_items = missing_keys(
        transaction,
        "items",
        "item_key",
        source_id,
        library_id,
        &delta.current_item_keys,
    )?;
    for item_key in missing_items {
        transaction
            .execute(
                "UPDATE items SET deleted = 1 WHERE source_id = ?1 AND library_id = ?2 AND item_key = ?3",
                params![source_id, library_id, item_key],
            )
            .map_err(|error| format!("无法标记 Zotero 删除条目：{error}"))?;
        transaction
            .execute(
                "DELETE FROM attachments WHERE source_id = ?1 AND library_id = ?2 AND item_key = ?3",
                params![source_id, library_id, item_key],
            )
            .map_err(|error| format!("无法清理 Zotero 删除附件：{error}"))?;
        write_tombstone(
            transaction,
            source_id,
            library_id,
            "item",
            &item_key,
            library.version,
        )?;
    }

    transaction
        .execute(
            "INSERT INTO sync_state(source_id, library_id, entity_type, version, last_success_at, last_error)\n\
             VALUES (?1, ?2, 'library', ?3, strftime('%s','now'), NULL)\n\
             ON CONFLICT(source_id, library_id, entity_type) DO UPDATE SET\n\
                version = excluded.version, last_success_at = excluded.last_success_at, last_error = NULL",
            params![source_id, library_id, library.version],
        )
        .map_err(|error| format!("无法更新 Zotero 增量同步状态：{error}"))?;
    Ok(())
}

fn mark_missing(
    transaction: &Transaction<'_>,
    table: &str,
    key_column: &str,
    entity_type: &str,
    source_id: &str,
    library_id: &str,
    current_keys: &std::collections::HashSet<String>,
    version: u64,
) -> Result<(), String> {
    for key in missing_keys(
        transaction,
        table,
        key_column,
        source_id,
        library_id,
        current_keys,
    )? {
        transaction
            .execute(
                &format!(
                    "UPDATE {table} SET deleted = 1 WHERE source_id = ?1 AND library_id = ?2 AND {key_column} = ?3"
                ),
                params![source_id, library_id, key],
            )
            .map_err(|error| format!("无法标记 Zotero 删除对象：{error}"))?;
        write_tombstone(
            transaction,
            source_id,
            library_id,
            entity_type,
            &key,
            version,
        )?;
    }
    Ok(())
}

fn missing_keys(
    transaction: &Transaction<'_>,
    table: &str,
    key_column: &str,
    source_id: &str,
    library_id: &str,
    current_keys: &std::collections::HashSet<String>,
) -> Result<Vec<String>, String> {
    let mut statement = transaction
        .prepare(&format!(
            "SELECT {key_column} FROM {table} WHERE source_id = ?1 AND library_id = ?2 AND deleted = 0"
        ))
        .map_err(|error| format!("无法核对 Zotero 对象：{error}"))?;
    let existing = statement
        .query_map(params![source_id, library_id], |row| {
            row.get::<_, String>(0)
        })
        .map_err(|error| format!("无法核对 Zotero 对象：{error}"))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| format!("无法核对 Zotero 对象：{error}"))?;
    Ok(existing
        .into_iter()
        .filter(|key| !current_keys.contains(key))
        .collect())
}

fn write_tombstone(
    transaction: &Transaction<'_>,
    source_id: &str,
    library_id: &str,
    entity_type: &str,
    entity_key: &str,
    version: u64,
) -> Result<(), String> {
    transaction
        .execute(
            "INSERT INTO tombstones(source_id, library_id, entity_type, entity_key, version, deleted_at)\n\
             VALUES (?1, ?2, ?3, ?4, ?5, strftime('%s','now'))\n\
             ON CONFLICT(source_id, library_id, entity_type, entity_key) DO UPDATE SET\n\
                version = excluded.version, deleted_at = excluded.deleted_at",
            params![source_id, library_id, entity_type, entity_key, version],
        )
        .map(|_| ())
        .map_err(|error| format!("无法记录 Zotero 删除对象：{error}"))
}

fn json(value: &impl serde::Serialize) -> Result<String, String> {
    serde_json::to_string(value).map_err(|error| format!("无法编码 Zotero 数据：{error}"))
}

fn provider_kind_label(kind: ProviderKind) -> &'static str {
    match kind {
        ProviderKind::Connector => "connector",
        ProviderKind::LocalApi => "local-api",
        ProviderKind::Cloud => "cloud",
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashSet;

    use rusqlite::Connection;
    use serde_json::json;

    use super::*;
    use crate::zotero::model::{
        LibraryKind, ZoteroCollection, ZoteroCreator, ZoteroFulltextIndex, ZoteroItem,
        ZoteroSavedSearch, ZoteroSavedSearchMembership, ZoteroTagRef,
    };

    #[test]
    fn migration_is_idempotent_and_versioned() {
        let mut connection = Connection::open_in_memory().unwrap();
        migrate(&mut connection).unwrap();
        migrate(&mut connection).unwrap();
        let version: i64 = connection
            .pragma_query_value(None, "user_version", |row| row.get(0))
            .unwrap();
        assert_eq!(version, SCHEMA_VERSION);
    }

    #[test]
    fn snapshot_preserves_nested_collections_and_multi_membership() {
        let mut connection = Connection::open_in_memory().unwrap();
        migrate(&mut connection).unwrap();
        let library = ZoteroLibrary {
            source_id: "zotero-local".into(),
            library_id: "0".into(),
            kind: LibraryKind::User,
            name: "My Library".into(),
            version: 42,
            editable: false,
            files_editable: false,
            raw: json!({}),
        };
        let snapshot = LibrarySnapshot {
            library: Some(library.clone()),
            collections: vec![
                ZoteroCollection {
                    source_id: "zotero-local".into(),
                    library_id: "0".into(),
                    key: "ROOT".into(),
                    version: 1,
                    name: "Root".into(),
                    parent_key: None,
                    item_count: 1,
                    deleted: false,
                    raw: json!({}),
                },
                ZoteroCollection {
                    source_id: "zotero-local".into(),
                    library_id: "0".into(),
                    key: "CHILD".into(),
                    version: 1,
                    name: "Child".into(),
                    parent_key: Some("ROOT".into()),
                    item_count: 1,
                    deleted: false,
                    raw: json!({}),
                },
            ],
            items: vec![ZoteroItem {
                source_id: "zotero-local".into(),
                library_id: "0".into(),
                key: "ITEM".into(),
                version: 2,
                item_type: "journalArticle".into(),
                parent_key: None,
                title: Some("Paper".into()),
                abstract_note: None,
                date_added: None,
                date_modified: None,
                creators: vec![ZoteroCreator {
                    creator_type: Some("author".into()),
                    first_name: Some("Ada".into()),
                    last_name: Some("Lovelace".into()),
                    name: None,
                }],
                tags: vec![ZoteroTagRef {
                    tag: "AI".into(),
                    kind: None,
                }],
                collection_keys: vec!["ROOT".into(), "CHILD".into()],
                relations: json!({}),
                raw: json!({}),
                deleted: false,
            }],
            fulltext: vec![ZoteroFulltextIndex {
                source_id: "zotero-local".into(),
                library_id: "0".into(),
                item_key: "ATTACH".into(),
                version: 3,
            }],
            searches: vec![ZoteroSavedSearch {
                source_id: "zotero-local".into(),
                library_id: "0".into(),
                key: "SEARCH".into(),
                version: 4,
                name: "Reading queue".into(),
                deleted: false,
                conditions: json!([]),
                raw: json!({}),
            }],
            search_memberships: vec![ZoteroSavedSearchMembership {
                source_id: "zotero-local".into(),
                library_id: "0".into(),
                search_key: "SEARCH".into(),
                item_keys: vec!["ITEM".into()],
            }],
            ..LibrarySnapshot::default()
        };
        let transaction = connection.transaction().unwrap();
        replace_library_in_transaction(&transaction, &snapshot, &library).unwrap();
        transaction.commit().unwrap();

        let membership: i64 = connection
            .query_row("SELECT COUNT(*) FROM item_collections", [], |row| {
                row.get(0)
            })
            .unwrap();
        let nested: String = connection
            .query_row(
                "SELECT parent_key FROM collections WHERE collection_key = 'CHILD'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let saved_search_membership: i64 = connection
            .query_row("SELECT COUNT(*) FROM saved_search_items", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(membership, 2);
        assert_eq!(nested, "ROOT");
        assert_eq!(saved_search_membership, 1);
    }

    #[test]
    fn delta_reconciliation_records_permanent_deletions() {
        let mut connection = Connection::open_in_memory().unwrap();
        migrate(&mut connection).unwrap();
        let mut library = ZoteroLibrary {
            source_id: "zotero-local".into(),
            library_id: "0".into(),
            kind: LibraryKind::User,
            name: "My Library".into(),
            version: 1,
            editable: false,
            files_editable: false,
            raw: json!({}),
        };
        let snapshot = LibrarySnapshot {
            library: Some(library.clone()),
            items: vec![ZoteroItem {
                source_id: "zotero-local".into(),
                library_id: "0".into(),
                key: "ITEM".into(),
                version: 1,
                item_type: "journalArticle".into(),
                parent_key: None,
                title: Some("Paper".into()),
                abstract_note: None,
                date_added: None,
                date_modified: None,
                creators: vec![],
                tags: vec![],
                collection_keys: vec![],
                relations: json!({}),
                raw: json!({}),
                deleted: false,
            }],
            ..LibrarySnapshot::default()
        };
        let transaction = connection.transaction().unwrap();
        replace_library_in_transaction(&transaction, &snapshot, &library).unwrap();
        transaction.commit().unwrap();

        library.version = 2;
        let delta = LibraryDelta {
            library,
            collections: vec![],
            items: vec![],
            attachments: vec![],
            searches: vec![],
            search_memberships: vec![],
            tags: vec![],
            fulltext: vec![],
            current_collection_keys: HashSet::new(),
            current_item_keys: HashSet::new(),
            current_search_keys: HashSet::new(),
        };
        let transaction = connection.transaction().unwrap();
        apply_delta_in_transaction(&transaction, &delta).unwrap();
        transaction.commit().unwrap();

        let deleted: bool = connection
            .query_row(
                "SELECT deleted FROM items WHERE item_key = 'ITEM'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        let tombstones: u64 = connection
            .query_row("SELECT COUNT(*) FROM tombstones", [], |row| row.get(0))
            .unwrap();
        assert!(deleted);
        assert_eq!(tombstones, 1);
    }
}

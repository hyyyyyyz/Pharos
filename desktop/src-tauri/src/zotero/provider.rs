//! Contracts implemented by every Zotero data source.

use std::{future::Future, pin::Pin};

use super::model::{LibrarySnapshot, ProviderCapabilities, ZoteroLibrary};

pub type ProviderFuture<'a, T> = Pin<Box<dyn Future<Output = Result<T, String>> + Send + 'a>>;

/// A provider returns Zotero-native entities. UI projections and persistence
/// are deliberately outside the provider so Local API, Connector, and Cloud
/// cannot silently diverge into three incompatible models.
pub trait ZoteroProvider: Send + Sync {
    fn id(&self) -> &'static str;
    fn capabilities(&self) -> ProviderCapabilities;
    fn probe(&self) -> ProviderFuture<'_, bool>;
    fn libraries(&self) -> ProviderFuture<'_, Vec<ZoteroLibrary>>;
    fn snapshot<'a>(&'a self, library: &'a ZoteroLibrary) -> ProviderFuture<'a, LibrarySnapshot>;
}

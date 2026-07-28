//! Provider-neutral Zotero integration.
//!
//! The existing `zotero_local` module remains the compatibility facade while
//! this module becomes the durable data layer shared by the Local API,
//! Pharos Connector, and (later) Zotero Cloud providers.

pub mod commands;
pub mod local_api;
pub mod mirror;
pub mod model;
pub mod provider;
pub mod repository;

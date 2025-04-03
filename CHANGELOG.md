# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.8] - 2025-04-03

### Added

- 🔒 **SSL Support via '--ssl-certfile' and '--ssl-keyfile'**: Easily enable HTTPS for your mcpo servers by passing certificate and key files—ideal for securing deployments in production, enabling encrypted communication between clients (e.g. browsers, AI agents) and your MCP tools without external proxies.

## [0.0.7] - 2025-04-03

### Added

- 🖼️ **Image Content Output Support**: mcpo now gracefully handles image outputs from MCP tools—returning them directly as binary image content so users can render or download visuals instantly, unlocking powerful new use cases like dynamic charts, AI art, and diagnostics through any standard HTTP client or browser.

## [0.0.6] - 2025-04-02

### Added

- 🔐 **CLI Auth with --api-key**: Secure your endpoints effortlessly with the new --api-key option, enabling basic API key authentication for instant protection without custom middleware or external auth systems—ideal for public or multi-agent deployments.
- 🌐 **Flexible CORS Access via --cors-allow-origins**: Unlock controlled cross-origin access with the new --cors-allow-origins CLI flag—perfect for integrating mcpo with frontend apps, remote UIs, or cloud dashboards while maintaining CORS security.

### Fixed

- 🧹 **Cleaner Proxy Output**: Dropped None arguments from proxy requests, resulting in reduced clutter and improved interoperability with servers expecting clean inputs—ensuring more reliable downstream performance with MCP tools.
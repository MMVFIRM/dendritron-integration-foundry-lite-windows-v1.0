# Autonomous Discovery

## Goal

A normal Foundry Lite user should never upload API documentation or manually define a schema.

## Discovery cascade

### 1. Formal REST specifications

Foundry checks standard and linked paths for OpenAPI or Swagger documents. These receive the highest structural confidence because they describe request and response contracts directly.

### 2. GraphQL introspection

Foundry probes conventional GraphQL endpoints with the standard introspection query. This is read-only at the GraphQL semantic level, although it uses HTTP POST.

### 3. OData metadata

Foundry parses EDMX metadata and derives entities, fields, key properties, and operations.

### 4. Capability indexes

Foundry looks for authenticated resource or object indexes, retrieves representative read-only responses, and checks allowed methods with `OPTIONS`.

### 5. Behavioral read-only inference

If no formal metadata exists, Foundry inspects safe JSON responses from common read surfaces and infers fields and basic operations. The System Profile records that its source is behavioral and therefore less authoritative.

## Evidence

Every probe records:

- Method
- URL
- Status code
- Accepted, rejected, or errored outcome
- Detail when available

The UI summarizes success. The full evidence remains in the System Profile and discovery report.

## Safety rules

The default cascade performs:

- GET requests
- OPTIONS requests
- GraphQL schema introspection

It does not create, update, or delete external records during discovery.

A writable operation is inferred only from formal specifications, GraphQL mutation definitions, OData metadata, or explicit allowed-method evidence. Safe preview uses simulation and does not execute the external write.

## Authentication

Supported v0.1 authorization inputs:

- None
- API key
- Bearer token
- Basic credentials for discovery
- Local public-client OAuth for Google Sheets, Microsoft 365, and Salesforce

The Generic HTTP execution adapter resolves OAuth access tokens from the encrypted local vault. Authorization, token exchange, refresh, and supported revocation calls go directly from the desktop to the provider. MMV operates no OAuth broker. Provider capability profiles define the safe objects and operations available after sign-in. Advanced API-key, bearer-token, and basic inputs remain available for custom systems.

## Opaque systems

When no safe capability surface can be discovered, Foundry reports that it cannot safely learn the system. It does not invent operations.

Future discovery providers can add:

- Additional local OAuth provider and capability profiles
- Local discovery agents
- Database information-schema inspection
- Message broker schemas
- Browser network observation
- Desktop application RPC inspection
- User-demonstrated behavior capture

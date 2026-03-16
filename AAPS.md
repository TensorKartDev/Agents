# The AGX Agent Packaging Standard (AAPS)

Version: `0.1-draft`

## Purpose

The AGX Agent Packaging Standard (AAPS) defines the contract for AGX-compatible agent packages. It specifies:

- how agents are packaged
- how agents are installed
- how agents are discovered
- how agent ownership is tracked
- how agent packages are secured

AAPS is intended to become the stable foundation for the AGX ecosystem so agent developers, runtime operators, marketplace tooling, and enterprise deployment teams all build against the same packaging model.

## Scope

AAPS covers the package boundary between:

- the AGX runtime/framework
- separately maintained agent packages
- the admin and marketplace surfaces that install and govern them

AAPS does not define AGX planning semantics in full, nor does it define a container runtime format or a remote marketplace protocol yet.

## Design principles

- Portable: a package should move between local, shared, and enterprise environments without source edits.
- Declarative: package metadata and workflow behavior should be described by manifests and config, not hidden in runtime patches.
- Separated: agent packages, their workflows, and their tests remain distinct from the AGX runtime codebase.
- Auditable: installs, upgrades, owners, and executions should be attributable.
- Secure by default: identity, tenancy, upload validation, and runtime permissions must be explicit.
- Extensible: later versions can add signing, provenance, and richer permission models without breaking the basic package layout.

## Standard terms

### AGX runtime

The AGX framework code, including the CLI, web server, orchestrators, persistence layer, auth layer, and built-in tool registry.

### Agent package

A separately packaged unit that contains one AGX agent definition and its supporting assets.

### Package root

The top-level directory inside an agent package containing the required manifest and workflow config.

### Package slug

The stable identifier used for discovery and registry membership. In the current AGX implementation the slug is the directory name under the configured agent-pack root.

### Registry

A YAML file listing which package slugs are discoverable by the runtime.

### Tenant

An organizational boundary under which users, packages, and runs may be owned and scoped.

## Package model

An AAPS package contains exactly one package root.

Minimum required structure:

```text
<agent-package>/
├── agent.yaml
└── config.yaml
```

Recommended structure:

```text
<agent-package>/
├── agent.yaml
├── config.yaml
├── README.md
├── assets/
├── docs/
├── tests/
└── code/
```

Rules:

- `agent.yaml` is required.
- `config.yaml` is required unless `agent.yaml` points at a different valid AGX config path.
- The package must contain only one package root when distributed as an archive.
- Tests must remain package-local and must not be merged into the AGX runtime test suite during installation.
- Optional package code must stay package-local and be referenced through declared config/import paths rather than copied into `src/agx`.

## Required manifest: `agent.yaml`

`agent.yaml` is the AAPS manifest. It defines discovery metadata and points the runtime to the AGX workflow config.

Minimum required fields:

```yaml
name: "Disk Triage Agent"
description: "Analyzes disk usage and proposes cleanup actions."
config_path: "config.yaml"
version: "1.0.0"
```

Supported AGX metadata today includes:

- `name`
- `description`
- `icon`
- `config_path`
- `inputs`
- `outputs`
- `capabilities`
- `version`
- `compatibility`
- `pricing`

Recommended AAPS metadata for standardization:

```yaml
publisher:
  name: "Example Team"
license: "Proprietary"
support:
  email: "agents@example.com"
security:
  network_access: false
  secrets_required: []
permissions:
  tools:
    - "disk_usage"
    - "file_inventory"
```

Runtime expectations:

- the manifest must be syntactically valid YAML
- required fields must be present
- `config_path` must resolve inside the package root
- metadata must not be accepted if it escapes the package root or attempts to overwrite runtime paths

## Workflow config contract: `config.yaml`

The workflow config is an AGX runtime contract, not an arbitrary package file. It must resolve as a valid AGX project configuration.

It may define:

- agents
- tasks
- tools
- approvals
- bindings
- middleware and observability defaults

AAPS requires:

- the config must validate under the AGX config schema
- task and tool references must be resolvable by the runtime
- package configs must not assume they are installed inside the AGX framework source tree

## Discovery and registry rules

An AAPS-compliant runtime discovers packages from configurable workspace paths rather than hard-coded repo locations.

The current AGX runtime uses:

- `AGX_AGENTS_DIR`
- `AGX_AGENT_REGISTRY`

Discovery rules:

- the runtime scans the configured agent-pack root for package directories
- the registry lists allowed package slugs
- only valid registered packages are surfaced in UI and admin flows
- invalid manifests must be skipped without breaking discovery of other packages
- the runtime must not require packages to live under `src/agx`

## Installation standard

AAPS defines two installation modes.

### 1. Filesystem install

An operator places a package directory in the configured agent-pack root and registers its slug.

Required runtime behavior:

1. validate `agent.yaml`
2. validate the referenced workflow config
3. confirm registry membership
4. expose the package through discovery

### 2. Marketplace upload install

An authenticated user uploads a packaged archive through the AGX admin surface.

Required runtime behavior:

1. authenticate the uploader
2. validate archive type
3. reject unsafe extraction paths
4. locate exactly one package root
5. validate `agent.yaml`
6. validate the workflow config
7. install into the configured agent-pack root
8. update the registry
9. persist package ownership and audit metadata
10. expose the package through discovery

Current AGX behavior aligns with this model by accepting `.zip` packages through the admin surface and installing them into the configured agent directory.

## Upgrade and replacement rules

AAPS packages should be versioned with semantic versioning.

Recommended rules:

- a package slug remains stable across compatible updates
- a new upload of the same slug replaces the installed package contents for that slug
- the runtime records the new version and update timestamp
- the runtime preserves install ownership and audit history
- active runs must not be corrupted by package replacement

If a deployment requires stronger guarantees, package replacement should be implemented as staged version installs with explicit activation.

## Security standard

Security is part of the packaging contract, not an afterthought.

### Upload and extraction

An AAPS runtime must:

- accept only supported archive formats
- reject path traversal and absolute-path extraction
- reject archives without a valid package root
- reject packages whose manifest or config escapes the package root

### Identity and authorization

An AAPS runtime must:

- authenticate AGX site and admin access through the same FastAPI security boundary
- associate installs, runs, and deletes with authenticated users
- authorize package modification through runtime roles, not package metadata

### Tenancy

An AAPS runtime should:

- associate packages with an owning user and tenant where multi-tenant operation exists
- scope package visibility, run visibility, and admin actions to the authenticated tenant unless an admin overrides scope

### Runtime separation

An AAPS install must not:

- patch AGX runtime source files
- merge package tests into runtime tests
- depend on hard-coded package paths inside the runtime source tree

An AAPS install may:

- place package files in the configured package root
- update the configured registry
- persist ownership and package metadata in the runtime database

### Secrets and credentials

AAPS packages must not embed live secrets in:

- `agent.yaml`
- workflow config
- package assets

Secrets should be injected by deployment configuration, runtime secret stores, or external integrations.

### Future hardening

Future AAPS revisions should define:

- package signatures
- checksum verification
- provenance metadata
- permission declarations
- package trust policy
- dependency and CVE scanning hooks
- sandbox requirements for agent-local code

## Ownership, tenancy, and users

AAPS assumes enterprise operation where packages and runs are attributable.

Recommended model:

- one tenant represents an organization boundary
- each user has a unique email address
- each user belongs to a tenant
- each package install has an owner user
- each run has an owner user

Example:

- tenant: `Emerson`
- users:
  - `ashish.madkaikar@emerson.com`
  - `nitin.k@emerson.com`

External identity providers such as Google and GitHub may be used for sign-in, but AAPS does not require any specific provider. The runtime remains responsible for mapping authenticated users to AGX tenant and role policy.

## Operational metadata

An AAPS runtime should track, at minimum:

- package slug
- package version
- package owner
- package tenant, where applicable
- upload timestamp
- last update timestamp
- restart count, if the runtime tracks replacement events
- traffic count or run count
- last run timestamp
- run ownership
- run audit events

## Compatibility contract

Packages should declare compatibility with:

- AGX runtime version
- supported orchestration engines
- optional external dependencies
- Python version, where package-local code exists
- OS or tool prerequisites, where relevant

Example:

```yaml
compatibility:
  agx: ">=0.2.0"
  engine:
    - "autogen"
    - "legacy"
  python: ">=3.10"
  system_dependencies:
    - "binwalk"
    - "ripgrep"
```

If a runtime cannot satisfy declared compatibility, it should reject install or mark the package unavailable rather than failing at run time with opaque errors.

## Compliance levels

To keep adoption practical, AAPS can be interpreted in levels.

### Level 1: Basic package compliance

- valid `agent.yaml`
- valid AGX workflow config
- package discovered via configured registry

### Level 2: Managed install compliance

- authenticated upload/install
- ownership persisted
- audit metadata persisted
- tenant/user scoping enforced where applicable

### Level 3: Hardened enterprise compliance

- package verification and provenance
- explicit permission model
- compatibility policy enforcement
- security scanning or approval workflow

## Non-goals in this draft

This version of AAPS does not yet define:

- a binary signing format
- a container image format for agents
- a remote package repository protocol
- a standard lockfile for agent-local Python code
- a universal execution sandbox model

## Current AGX interpretation

The current AGX implementation already aligns with AAPS in several ways:

- manifest-driven discovery
- registry-based package visibility
- configurable workspace paths
- `.zip` upload installation through the admin surface
- authenticated package ownership
- tenant-aware runtime users
- run and package metadata persistence

What remains is mostly standardization and hardening, not invention of a new packaging model.

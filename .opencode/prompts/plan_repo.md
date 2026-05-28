Plan this repository for agentic execution.

YOUR ONLY JOB:

Create a minimal execution system for stateless implementation and review.

This is NOT traditional software planning.

Do NOT create:
- architecture.md
- implementation_plan.md
- weekly plans
- PM artifacts
- effort estimates
- risk tables
- onboarding docs
- technical essays
- long explanations

Only create and maintain:

docs/
├── project_brief.md
├── current_state.md
├── devlog.md
└── build_plan/
    ├── index.md
    ├── BU001.md
    ├── BU002.md
    ├── BU003.md
    └── ...

==================================================
PRIMARY OBJECTIVE
==================================================

The repository must support:

- stateless Builder sessions
- isolated execution
- minimal context usage
- incremental development
- low architectural drift
- production-oriented delivery

Repository documentation must be sufficient
for work continuation without prior chat history.

==================================================
PROJECT GOAL
==================================================

[PROJECT DESCRIPTION]

==================================================
FROZEN DECISIONS
==================================================

[NON-NEGOTIABLE TECH DECISIONS]

==================================================
REPOSITORY STRUCTURE
==================================================

Plan the minimal repository structure required
for near-term execution.

Prefer:
- low coupling
- modular organization
- feature isolation
- executable increments
- dependency-first delivery

Avoid:
- enterprise abstractions
- premature scalability
- unnecessary folders
- unused infrastructure
- speculative systems

Only plan folders required by near-term BUs.

==================================================
PROJECT BRIEF
==================================================

Create:

docs/project_brief.md

Purpose:

Provide stable repository context.

This document must remain:
- concise
- stable
- architecture-focused
- implementation-agnostic

Include ONLY:
- project purpose
- core capabilities
- architecture principles
- tech stack
- critical constraints

Do NOT include:
- roadmap
- implementation details
- BU descriptions
- future planning

==================================================
CURRENT STATE
==================================================

Create:

docs/current_state.md

Purpose:

Provide persistent execution memory.

This document must allow a stateless Builder
session to recover repository state and continue work.

Keep concise but explicit.

Include:

1. Active Execution
- current BU
- status
- next BU
- last completed BU
- blocked BU

2. Repository State
- implemented systems
- important modules
- relevant paths
- active architecture assumptions

3. Working Memory
- active decisions
- critical constraints
- known issues
- recent relevant changes

Do NOT include:
- roadmap explanations
- long status reports
- speculative planning
- excessive implementation history

==================================================
DEVLOG
==================================================

Create:

docs/devlog.md

Purpose:

Append-only recovery memory.

Store:
- completed BU
- files changed
- important technical decisions
- short recovery notes

Keep entries concise.

==================================================
BUILD PLAN STRUCTURE
==================================================

Create:

docs/build_plan/index.md
docs/build_plan/BUXXX.md

Each BU must live in its own file.

Builder Units must be:

- self-contained
- independently executable
- isolated from future work
- minimal in scope
- reviewable
- production-oriented

A BU must not require reading
other BU files.

==================================================
PLANNING RULES
==================================================

Break implementation into Builder Units (BU).

Each BU must:

- implement ONE responsibility
- touch maximum 3 files
- fit one Builder iteration
- minimize reasoning complexity
- support isolated validation
- remain boringly small
- produce executable value

Target:

~50–150 LOC per BU

If uncertain:
split into smaller BUs.

==================================================
GRANULARITY RULES
==================================================

Bad:

BU001 Project Foundation
- config
- logging
- bootstrap

Good:

BU001 App Bootstrap
BU002 Config Loader
BU003 Logging Setup

Avoid:
- UI + backend together
- storage + processing together
- infrastructure + feature together
- giant milestones
- multi-system work

==================================================
BUILD STRATEGY
==================================================

Optimize for:

- MVP-first delivery
- dependency-first execution
- vertical slices
- minimal setup
- executable increments

Build only enough infrastructure
to unlock the next BU.

Every BU should represent
a production-style delivery increment.

==================================================
MANDATORY BU EXECUTION SEQUENCE
==================================================

Every BU must be executable
through the following sequence:

1. Understand
- recover repository context
- understand dependencies
- identify existing implementation constraints

2. Implement
- perform minimal scoped implementation
- follow Allowed Files strictly
- avoid unrelated changes

3. Validate
- confirm implementation works
- verify no syntax/runtime failures
- verify Definition of Done

4. Review Readiness
- confirm scope boundaries respected
- remove obvious defects
- ensure implementation consistency

5. Document
- update current_state.md
- append devlog.md

BU planning should naturally support
this execution lifecycle.

==================================================
VALIDATION STRATEGY
==================================================

Every BU must include lightweight validation.

Default validation:

Required:
- feature executes successfully
- no syntax/runtime errors
- repository still builds
- Definition of Done verified

Testing should be proportional
to implementation complexity.

MANDATORY TESTS FOR:
- business logic
- algorithms
- financial calculations
- parsers
- stateful systems
- queueing logic
- transformations
- critical workflows

OPTIONAL TESTS FOR:
- simple wiring
- scaffolding
- configuration
- trivial UI
- boilerplate

Avoid excessive testing overhead
during early MVP delivery.

Favor lightweight validation first,
production hardening later.

==================================================
STRICT BU FORMAT
==================================================

Use EXACTLY this structure.

# BU001 — Name

Status:
Pending

Goal:
Short implementation goal.

Context:
Why this BU exists.

Allowed Files:
- explicit file list only

Dependencies:
Required:
- BUXXX

Blocks:
- BUXXX

Tasks:
- concrete implementation tasks

Execution Sequence:
1. Understand
2. Implement
3. Validate
4. Review Readiness
5. Document

Validation Requirements:

Required:
- executable behavior verified
- no syntax/runtime errors
- Definition of Done satisfied

Tests Required:
- yes/no

If yes:
- explicitly define what must be tested

Out of Scope:
- forbidden work

Definition of Done:
[ ] checklist

Reviewer Criteria:

Scope Validation:
[ ] only requested scope implemented
[ ] no future BU leakage
[ ] Allowed Files respected

Functional Validation:
[ ] feature works as expected
[ ] validation requirements satisfied
[ ] failure modes acceptable

Architecture Validation:
[ ] repository structure preserved
[ ] no unnecessary abstractions
[ ] implementation consistent with project_brief

Code Quality:
[ ] readable
[ ] minimal
[ ] no obvious dead code

Documentation Validation:
[ ] current_state updated
[ ] devlog appended

Next:
BUXXX

==================================================
INDEX FORMAT
==================================================

Use EXACTLY this format.

| BU | Name | Status | Depends On | Next |
|---|---|---|---|---|
| BU001 | App Bootstrap | Pending | none | BU002 |

==================================================
IMPORTANT
==================================================

Optimize ONLY for execution.

The generated repository documentation
must be sufficient for stateless execution.

Favor:
- deterministic execution
- isolated work
- small iterations
- low ambiguity
- production-oriented delivery

Avoid:
- overengineering
- speculative architecture
- unnecessary abstractions
- oversized BUs
- excessive process overhead
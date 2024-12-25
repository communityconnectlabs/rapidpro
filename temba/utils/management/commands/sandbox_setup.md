# Sandbox Configuration Tool for RapidPro

## Description

The `sandbox_setup` command is a dev/sandbox fixture management tool designed to extend RapidPro’s capabilities. By combining the functionality of test databases with enhanced configurability and version control, the tool will:

1. Enable resetting organizations, users, and flows to initial predefined states.
2. Standardize UUIDs across environments to ensure consistency.
3. Allow data migration for sandbox flows, organizations, and configurations, keeping them current through version tracking and a lightweight Erlang-style `code_up` methodology for upgrades.
4. Offer configurable and optional components, allowing granular control over which organizations, flows, and related entities are included in the sandbox environment.
5. Apply database changes without requiring a full flush, enabling faster iteration cycles.

By addressing these key points, the tool provides a consistent and efficient environment for running end-to-end tests, integrating components like Courier and Mailroom, and collaborating on configurations.

* * *

## Personas

### DevOps Engineer - Jamie

```yaml
- name: Jamie
  profile: Jamie is a mid-career DevOps engineer with experience managing CI/CD pipelines.
  dob: 1987-08-22
  income: $95,000
  location: Austin, TX
  bio: Jamie seeks tools to streamline sandbox configuration and eliminate repetitive tasks.
  impact: Jamie values automation and consistency in deploying environments for testing.
```

### Developer - Alex

```yaml
- name: Alex
  profile: A backend developer working on RapidPro extensions.
  dob: 1991-04-15
  income: $80,000
  location: Berlin, Germany
  bio: Alex often struggles with managing multiple environment setups for feature testing.
  impact: Alex needs tools to quickly reset and test sandbox environments without conflicts.
```

### QA Analyst - Priya

```yaml
- name: Priya
  profile: A QA analyst focusing on flow testing and reliability in multi-org environments.
  dob: 1994-06-10
  income: $70,000
  location: Bangalore, India
  bio: Priya aims to test updates in a controlled and predictable sandbox setting.
  impact: Priya requires reliable tools for setting up test scenarios and tracking configurations.
```

* * *

## User Stories

### SND-001 - Restoring Sandbox to Initial State

```story
- ticket-number: SND-001
  title: Restore sandbox environment
  profiles: [Jamie, Alex, Priya]
  story: |
      As a user managing a sandbox environment,
      I would like to reset my organization and flows to initial values,
      so that I can ensure a clean slate for testing.
  acceptance-criteria:
      - name: Reset Org
        criteria:
            Given a sandboxed organization,
            When I run `sandbox_config restore`,
            Then the organization and flows are reset to their predefined state.
```

### SND-002 - Data Migration Support

```story
- ticket-number: SND-002
  title: Migrate sandbox configurations
  profiles: [Jamie, Alex]
  story: |
      As a developer,
      I would like to update sandbox data to the latest configurations,
      so that I can ensure compatibility with current implementations.
  acceptance-criteria:
      - name: Migrate Data
        criteria:
            Given outdated sandbox data,
            When I run `sandbox_config migrate`,
            Then the sandbox is updated to the latest flow and org definitions.
```

### SND-003 - Provide Feature-Specific Flows for Testing

```story
- ticket-number: SND-003
  title: Access predefined flows for specific features
  profiles: [Alex, Priya]
  story: |
      As a developer or QA analyst,
      I would like access to predefined flows covering specific features like opt-outs and attachments,
      so that I can quickly test and validate these functionalities.
  acceptance-criteria:
      - name: List Feature Flows
        criteria:
            Given a sandbox environment,
            When I run `sandbox_config list --flows`,
            Then I see a list of predefined flows and their respective feature coverage.
      - name: Import Feature Flow
        criteria:
            Given a sandbox environment,
            When I run `sandbox_config load --flow=opt-out`,
            Then the opt-out feature flow is imported into the sandbox for testing.
```

### SND-004 - Create Known Orgs and Flows for Integration Testing

```story
- ticket-number: SND-004
  title: Setup known orgs and flows
  profiles: [Jamie, Alex, Priya]
  story: |
      As a developer,
      I would like to set up predefined organizations and flows,
      so that I can ensure consistent integration tests across all components.
  acceptance-criteria:
      - name: Configure Known Org
        criteria:
            Given an integration test environment,
            When I run `sandbox_config setup --org=test-org`,
            Then the predefined organization and associated flows are created with fixed UUIDs.
```

### SND-005 - Facilitate Collaboration on Data Setup and Flow Structures

```story
- ticket-number: SND-005
  title: Share data setup and flow structures
  profiles: [Alex, Priya]
  story: |
      As a developer or implementor,
      I would like to share sandbox configurations and flow structures,
      so that others can review, modify, and test them collaboratively.
  acceptance-criteria:
      - name: Export Configuration
        criteria:
            Given a sandbox environment,
            When I run `sandbox_config export --org=test-org`,
            Then the organization's setup and flows are exported in a shareable format.
      - name: Import Shared Configuration
        criteria:
            Given a sandbox environment,
            When I run `sandbox_config import --file=shared_config.json`,
            Then the sandbox is updated with the shared configuration.
```

### SND-006 - Simplify Sandbox Setup for Developers

```story
- ticket-number: SND-006
  title: Straightforward sandbox setup
  profiles: [Jamie, Alex]
  story: |
      As a developer,
      I would like an easy-to-use setup command,
      so that I can quickly prepare a fully functional sandbox environment for testing.
  acceptance-criteria:
      - name: Quick Setup Command
        criteria:
            Given a clean environment,
            When I run `sandbox_config setup --all`,
            Then a complete sandbox is created, including organizations, flows, and other required components.
```

### SND-007 - Ensure Cross-Component Testing Compatibility

```story
- ticket-number: SND-007
  title: Cross-component E2E test compatibility
  profiles: [Jamie, Alex, Priya]
  story: |
      As a developer,
      I would like to ensure that sandbox configurations are compatible with other components like Courier and Mailroom,
      so that I can perform end-to-end tests across the entire system.
  acceptance-criteria:
      - name: Validate Component Compatibility
        criteria:
            Given a sandbox environment,
            When I run `sandbox_config validate --components=courier,mailroom`,
            Then the tool checks and reports any configuration issues related to these components.
```

### SND-008 - Version Control for Sandbox Data

```story
- ticket-number: SND-008
  title: Track and update sandbox data versions
  profiles: [Alex, Jamie]
  story: |
      As a developer,
      I would like to track versions of sandbox data and upgrade as needed,
      so that my environment remains up to date with the latest schema and flow definitions.
  acceptance-criteria:
      - name: List Sandbox Versions
        criteria:
            Given a sandbox environment,
            When I run `sandbox_config version --list`,
            Then I see a list of all current and available data versions.
      - name: Upgrade Sandbox Data
        criteria:
            Given a sandbox environment,
            When I run `sandbox_config upgrade`,
            Then all components are upgraded to their latest versions.
```

### SND-009 - Partial Sandbox Updates

```story
- ticket-number: SND-009
  title: Support partial updates to sandbox
  profiles: [Alex, Priya]
  story: |
      As a developer,
      I would like to selectively update parts of my sandbox,
      so that I can test new configurations without disrupting existing setups.
  acceptance-criteria:
      - name: Update Single Flow
        criteria:
            Given a sandbox environment,
            When I run `sandbox_config update --flow=opt-out`,
            Then only the opt-out flow is updated to its latest version.
      - name: Update Org Settings
        criteria:
            Given a sandbox environment,
            When I run `sandbox_config update --org=test-org`,
            Then only the specified organization's settings are updated.
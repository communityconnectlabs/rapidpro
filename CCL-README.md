CCL README
============

CCL extensions to RapidPro. 

Current merge-base tag v7.2.4


----------

Analysis of Upstream Changes(GenAI)


I'll analyze these changes and group them by type and significance to help with merge planning.

Major Architectural Changes:
1. Database & Models
- New models added: OrgMembership, Team, TicketDailyCount, Media, Topic
- Major changes to ContactGroup model with addition of is_system field and smart/system group concepts
- Added new constraints for data integrity (flow names, sessions, runs)
- Dropped legacy models: TopUp, Debit, ChannelConnection, OrgActivity
- Reworked TembaModel to be a base model class with UUID and name

2. Authentication & Permissions
- Moved from role-specific m2m tables to OrgMembership model
- Reworked permission checking to use cached role permissions
- Created User proxy model to properly handle monkey-patched methods
- Simplified API authentication and rate limiting

3. Workspace/Organization Changes
- Added workspace plan concept
- Introduced workspace limits and shared usage tracking
- Prevented grandchild org creation
- Added features field to control functionality access
- Removed multi-org and multi-user flags

Channel & Integration Updates:
1. New Channels
- Added WhatsApp Cloud (WAC) channel type
- Added JustCall channel type
- Made Twitter channels beta-only
- Added Instagram channel improvements

2. Channel Management
- Improved channel claiming processes
- Added phone number verification flows
- Enhanced channel log handling and storage

UI/UX Improvements:
1. New UI Features
- Added new content menu system
- Added ticket management interface
- Improved contact management screens
- Added workspace selection features
- New modals for common actions

2. Frontend Architecture
- Moved to yarn for package management
- Updated to newer versions of components
- Removed legacy jQuery dependencies
- Improved SPA (Single Page Application) handling

API & Integration Changes:
1. API Improvements
- Added new endpoints for tickets and media
- Enhanced flow results and export capabilities
- Added preview_start endpoint for flows
- Improved error handling and validation

2. Data Format Changes
- Updated message event payloads
- Enhanced contact reference serialization
- Added support for anon_display in contact data

Security & Performance:
1. Security
- Removed Sentry support
- Updated authentication mechanisms
- Improved token handling for social channels

2. Performance
- Added new database indexes
- Improved query performance
- Enhanced caching strategies

Cleanup & Technical Debt:
1. Code Cleanup
- Removed unused settings and configurations
- Dropped legacy UUID fields
- Removed Google Analytics
- Cleaned up unused utilities and helpers

2. Dependencies
- Updated to newer versions of Django
- Updated testing dependencies
- Removed outdated third-party integrations

For merge recommendations:
1. Prioritize structural changes (database models, permissions)
2. Handle channel updates separately as they're mostly isolated
3. UI changes can be merged incrementally
4. API changes should be carefully reviewed for backwards compatibility
5. Security updates should be prioritized
6. Performance improvements can be merged independently
7. Cleanup tasks can be done last

The most critical areas to focus on would be the database model changes (especially around OrgMembership and Contact Groups) and the permission system changes, as these are foundational changes that other features depend on.

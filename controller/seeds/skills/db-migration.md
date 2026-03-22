---
name: Database Migration
description: Use when creating, reviewing, or troubleshooting database migrations
---

# Database Migration Best Practices

## When to Use
- Writing a new schema migration (add table, column, index)
- Reviewing a migration PR for safety and reversibility
- Planning a data backfill or transformation
- Deploying migrations to production with zero downtime

## Instructions

1. **Reversibility**: Every migration must have a rollback path. For `CREATE TABLE`, the rollback is `DROP TABLE`. For `ADD COLUMN`, the rollback is `DROP COLUMN`. If a migration is not reversible (e.g., dropping data), document this explicitly and require manual approval.

2. **Zero-downtime strategy**:
   - Never rename a column directly. Instead: add new column, backfill, update app to use both, drop old column.
   - Never add a `NOT NULL` column without a default. The deploy sequence is: add column as nullable, backfill, then add the constraint.
   - Add indexes with `CREATE INDEX CONCURRENTLY` (Postgres) to avoid locking the table.
   - Split large migrations into multiple smaller ones.

3. **Data backfills**:
   - Run backfills in batches (1000-5000 rows per batch) with a short sleep between batches
   - Always backfill in a separate migration from the schema change
   - Log progress: "Backfilled 50000/200000 rows (25%)"
   - Make backfills idempotent with `WHERE new_column IS NULL`

4. **Testing**:
   - Test migration against a copy of production data (or realistic volume)
   - Verify rollback works cleanly
   - Check that the migration completes within acceptable time on production-size data
   - Validate application works with both old and new schema during deploy

5. **Naming convention**: Use timestamps for ordering: `20240315_001_add_users_email_index.sql`. Include a short description of what the migration does.

## Checklist
- [ ] Migration has a tested rollback/down script
- [ ] No column renames or NOT NULL additions without safe deploy sequence
- [ ] Indexes created concurrently (if Postgres)
- [ ] Data backfills run in batches and are idempotent
- [ ] Migration tested against production-scale data volume
- [ ] Application works with both old and new schema during rollout

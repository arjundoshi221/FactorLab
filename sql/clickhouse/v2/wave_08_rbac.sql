CREATE ROLE IF NOT EXISTS factorlab_research;
CREATE ROLE IF NOT EXISTS factorlab_admin;
CREATE ROLE IF NOT EXISTS factorlab_ingest;

GRANT SELECT ON research.* TO factorlab_research;
REVOKE ALL ON market.* FROM factorlab_research;
REVOKE ALL ON fundamentals.* FROM factorlab_research;
REVOKE ALL ON alt.* FROM factorlab_research;
REVOKE ALL ON derived.* FROM factorlab_research;

GRANT SELECT, INSERT ON market.* TO factorlab_ingest;
GRANT SELECT, INSERT ON fundamentals.* TO factorlab_ingest;
GRANT SELECT, INSERT ON alt.* TO factorlab_ingest;
GRANT SELECT ON ref.* TO factorlab_ingest;

-- Clone every grant the migration principal is itself allowed to delegate. This is
-- portable across self-hosted installations where ALL includes optional global
-- capabilities (for example NAMED COLLECTION ADMIN) that the DBA may not expose.
GRANT CURRENT GRANTS ON *.* TO factorlab_admin;

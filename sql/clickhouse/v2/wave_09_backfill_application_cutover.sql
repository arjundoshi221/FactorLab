-- Run only after pausing legacy writers and completing the final Wave 1-4 catch-up.
-- Reruns can append physical duplicates; FINAL must yield the same logical rows.
INSERT INTO meta.expected_series_canonical
SELECT * FROM meta.expected_series FINAL;

INSERT INTO meta.session_coverage_canonical
SELECT * FROM meta.session_coverage FINAL;

INSERT INTO meta.recovery_state_canonical
SELECT * FROM meta.recovery_state FINAL;

INSERT INTO meta.hub_schema_layouts
SELECT * FROM factorlab.hub_schema_layouts FINAL;

"""Source conformance for the ishares provider (factorlab.testkit.source_conformance).

These fail until real captures are recorded under tests/fixtures/ref.universe_membership/
(docs/architecture/07 §13.1): a provider does not ship without them.
"""

from factorlab.testkit.source_conformance import conformance_tests

globals().update(conformance_tests("ishares"))

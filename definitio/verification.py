"""Compatibility adapter. The only verifier implementation lives in definitio.verifier.

Readback-based postconditions: GEOS predicates share the compute kernel, so these
checks detect corrupt grouping, counts, schema and fake empty results. They do not
establish an independent geometry oracle or calibrated tolerance.
"""
from __future__ import annotations

from . import verifier


def verify_output(verb, parameters, inputs, output, proposal):
    ctx = verifier.context(stage="commit", verb=verb, vid="", parameters=parameters, inputs=inputs,
                           proposal=proposal, output=output, store=None, db=None, planned_inputs=[],
                           write_scope=None, actual_writes=[], authorization=None, workspace=None,
                           file_artifact=None)
    result = verifier.run(ctx, predicates=(verifier.operation_readback_invariants,))
    verifier.enforce(result)
    return verifier.legacy_checks(result)
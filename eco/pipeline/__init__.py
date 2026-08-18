"""Standalone helpers for interacting with the SwissFEL pipeline server.

This subpackage is deliberately kept *separate* from the main eco device tree
for now. Its focus is uploading user Python code to the pipeline server
(``cam_server``) so that per-shot calculations — e.g. on beam-synchronous (BS)
streams — can be *offloaded* onto the server, close to the data source.

See :mod:`eco.pipeline.pipeline_server`.
"""

from .pipeline_server import (
    PipelineServer,
    PROCESS_IMAGE_TEMPLATE,
    CUSTOM_PROCESS_TEMPLATE,
)

__all__ = [
    "PipelineServer",
    "PROCESS_IMAGE_TEMPLATE",
    "CUSTOM_PROCESS_TEMPLATE",
]

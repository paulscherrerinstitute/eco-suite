---
added: 2026-08-18
tags: [pipeline, data]
details: examples/pipeline_offload
---

# Run your own per-shot calculation on the pipeline server

Heavy per-shot BS-stream or camera calculations can run on cam_server, next
to the data, with the result coming back as a new stream:

```python
from eco.pipeline import PipelineServer
ps = PipelineServer()
ps.offload_function("my_custom_instance", process)   # `process` does its own imports
```

Experimental. More: {doc}`../examples/pipeline_offload`

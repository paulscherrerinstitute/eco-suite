"""Throwaway scope module for smoke-testing scripts/eco-box-server end to
end (real subprocess, real network, real box listener) - not part of the
package's public surface, deleted after the smoke test."""
from eco.elements.adjustable import DummyAdjustable
from eco.utilities.config import Namespace

namespace = Namespace(name="smoketest")
namespace.append_obj(DummyAdjustable, name="theta", module_name=None)

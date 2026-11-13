"""``POST /api/schedules/<id>/run_now`` answered 500 for every schedule.

The handler imported ``services.scheduling.scheduler.Scheduler``, which that
module does not define (it declares ``ScheduleRunner``; the APScheduler import
is not a declared dependency either), inside the route's ``except Exception``
net. A feature that is not wired in this tree must say so: 503 with
SCHEDULER_UNAVAILABLE, and nothing recorded as having fired.

Found by the parameterised-route probe (``tools/audit/probe_param_routes.py``).
"""


from __future__ import annotations

import pathlib
import sys
import uuid

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


def test_run_now_without_a_scheduler_is_503_not_500(app, admin_client):
    tag = uuid.uuid4().hex[:8]
    resp = admin_client.post("/api/schedules", json={
        "name": f"run_now_{tag}", "schedule_type": "retention", "interval_minutes": 60})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    schedule_id = resp.get_json()["schedule"]["id"]

    fired = admin_client.post(f"/api/schedules/{schedule_id}/run_now")
    # 503 when the scheduler is absent (this deployment), 200 when a runner
    # exists - never 500 either way.
    assert fired.status_code in (200, 503), (fired.status_code, fired.get_data(as_text=True)[:300])
    if fired.status_code == 503:
        assert fired.get_json()["error"]["code"] == "SCHEDULER_UNAVAILABLE"

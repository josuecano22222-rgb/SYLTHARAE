"""The lock has to yield a *working* OpenCV, and say so when it does not.

Round 22 spent a round and a half blaming the code for an environment that
looked right: `requirements.lock` listed both `opencv-python` and
`opencv-python-headless`, both unpack into the same `cv2/` directory, the GUI
wheel was written last, and `import cv2` raised `ImportError: libGL.so.1`. The
package set matched the lock byte for byte while 31 ids failed and 84 skipped.

So the environment tool is now asked two questions, not one:

* does the installed set hash to the lock? (it always did)
* does `import cv2` work? (it did not, and nothing noticed)

These tests pin the second question, including the part that matters most: a
lock whose OpenCV cannot load must make `--check` fail loudly rather than print a
report and exit 0.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.audit import env_lock  # noqa: E402

UNUSABLE = {"usable": False, "owners": ["opencv-python", "opencv-python-headless"],
            "error": "ImportError: libGL.so.1: cannot open shared object file"}
USABLE = {"usable": True, "owners": ["opencv-python-headless"], "version": "5.0.0"}


@pytest.fixture
def lock_with_opencv(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.lock"
    lock.write_text("flask==3.1.3\nopencv-python-headless==5.0.0.93\n", encoding="utf-8")
    monkeypatch.setattr(env_lock, "LOCK", lock)
    return lock


def _report(**overrides):
    base = {"python": "3.11.2", "executable": "/x/python", "packages": 2,
            "set_hash": "abc", "lock_hash": "abc", "lock_present": True,
            "match": True, "label": None, "engines": {},
            "missing_from_this_env": [], "not_in_the_lock": [],
            "opencv": dict(USABLE)}
    base.update(overrides)
    return base


def test_a_lock_whose_opencv_cannot_load_fails_the_check(lock_with_opencv, monkeypatch):
    """The regression that cost a round: match == True and cv2 broken."""
    monkeypatch.setattr(env_lock, "report", lambda label=None: _report(opencv=dict(UNUSABLE)))
    exit_code = env_lock.main(["--check"])
    assert exit_code == 3, ("a lock whose OpenCV cannot load must fail the check, "
                            f"not report success (exit was {exit_code})")


def test_a_lock_with_working_opencv_passes_the_check(lock_with_opencv, monkeypatch):
    monkeypatch.setattr(env_lock, "report", lambda label=None: _report())
    assert env_lock.main(["--check"]) == 0


def test_a_lock_without_opencv_is_not_asked_the_question(tmp_path, monkeypatch):
    lock = tmp_path / "requirements.lock"
    lock.write_text("flask==3.1.3\n", encoding="utf-8")
    monkeypatch.setattr(env_lock, "LOCK", lock)
    monkeypatch.setattr(env_lock, "report", lambda label=None: _report(opencv=dict(UNUSABLE)))
    assert env_lock.main(["--check"]) == 0, (
        "a lock that ships no OpenCV cannot be refused for having no OpenCV")


def test_the_report_says_which_distributions_own_cv2():
    """Two owners are the mechanism, so the report has to name them."""
    state = env_lock.opencv_state()
    assert set(state) >= {"usable", "owners"}
    assert isinstance(state["owners"], list)
    if not state["usable"]:
        assert state.get("error"), "an unusable cv2 has to say why"


def test_only_one_distribution_owns_cv2_in_this_environment():
    """Whatever this environment is, the fighting-over-cv2/ state is refused.

    In the locked environment this asserts the headless wheel is the sole owner.
    Elsewhere it is skipped rather than failed: the point of the test is the
    invariant, and the lock check above is what enforces it where it applies.
    """
    state = env_lock.opencv_state()
    if not state["usable"]:
        pytest.skip(f"cv2 is not usable in this interpreter: {state.get('error')}")
    assert state["owners"] == ["opencv-python-headless"], (
        "cv2/ is provided by " + (", ".join(state["owners"]) or "no distribution")
        + "; exactly one distribution may own it, and the GUI build needs libGL")


def test_the_rebuild_tool_installs_without_resolving_dependencies():
    """`--no-deps` is the fix, not a detail: resolution reintroduces the GUI build."""
    source = (pathlib.Path(__file__).resolve().parents[2]
              / "tools" / "audit" / "rebuild_lock_env.py").read_text(encoding="utf-8")
    assert '"--no-deps"' in source
    assert "opencv-python-headless" in source
    lock = (pathlib.Path(__file__).resolve().parents[2] / "requirements.lock").read_text(
        encoding="utf-8")
    live = [line for line in lock.splitlines()
            if line.strip() and not line.startswith("#")]
    assert "opencv-python==5.0.0.93" not in live, (
        "the GUI OpenCV build is back in the lock; it owns cv2/ and needs libGL.so.1")
    assert "opencv-python-headless==5.0.0.93" in live

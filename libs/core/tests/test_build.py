"""factorlab.core.build: what the image stamped into the process environment."""

from factorlab.core.build import BuildInfo, build_info


def test_build_info_reads_the_image_stamp(monkeypatch):
    monkeypatch.setenv("FACTORLAB_COMPONENT", "api")
    monkeypatch.setenv("FACTORLAB_VERSION", "1.2.0")
    monkeypatch.setenv("FACTORLAB_COMMIT", "c" * 40)
    monkeypatch.setenv("FACTORLAB_RELEASE_ID", "")
    assert build_info() == BuildInfo("api", "1.2.0", "c" * 40, None)

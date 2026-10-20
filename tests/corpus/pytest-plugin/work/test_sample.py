def test_greeting(greeting):
    assert greeting.lower() == "hello from the plugin"


def test_tmp(tmp_path):
    (tmp_path / "x").write_text("x")
    assert (tmp_path / "x").read_text() == "x"

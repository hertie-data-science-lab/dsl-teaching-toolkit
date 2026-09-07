"""What a bootstrap PUBLISHES onto a course org: the bot token, and where fault mail goes.

`DSL_MAINTAINER_EMAIL` is the toolkit maintainer's address. Centrally it is a repository
VARIABLE on this repo (an address is not a credential, and a masked secret cannot be read
back to check it); `.github/workflows/bootstrap-org.yml` passes it into the run's env, and
`--propagate-secret` copies it onto the org as an org SECRET - the one route that reaches a
Free-plan org's public `.github`, where the scheduler and the send workflows run, and
mirrors into its private infra repos.

Three properties, and all three were once assumed rather than tested: an org that gets the
address, an org that does not (which must still bootstrap green - `mailer.maintainer_address`
falls back to the send mailbox), and the log, which must never carry the address itself.
Every faculty workflow runs in a PUBLIC repo.
"""

from __future__ import annotations

import pytest

from dsl_course import bootstrap_course as bc
from dsl_course import mailer
from tests.conftest import stub_bootstrap

ADDRESS = "maintainer@example.org"


def _propagating_run(monkeypatch, published: list) -> None:
    """A course-org bootstrap with `--propagate-secret` and a usable bot token, with every
    org secret write recorded instead of made."""
    stub_bootstrap(monkeypatch)
    monkeypatch.setenv("DSL_BOT_TOKEN", "s3cret")
    monkeypatch.setattr(bc, "set_org_secret", lambda *a: published.append(a) or True)
    monkeypatch.setattr(
        "sys.argv", ["bootstrap_course", "--org", "Course-Org", "--propagate-secret"]
    )


def test_propagate_secret_publishes_the_maintainer_address_too(monkeypatch):
    # The whole point of the central variable: a new org can mail a fault home without
    # anyone remembering a manual step.
    published: list = []
    _propagating_run(monkeypatch, published)
    monkeypatch.setenv(mailer.MAINTAINER_ENV, ADDRESS)

    assert bc.main() == 0
    assert published == [
        ("Course-Org", "DSL_BOT_TOKEN", "s3cret"),
        ("Course-Org", mailer.MAINTAINER_ENV, ADDRESS),
    ]


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "blank", "spaces"])
def test_an_unset_maintainer_address_skips_and_stays_green(monkeypatch, capsys, value):
    # An org bootstrapped before the variable existed, or a maintainer running the CLI by
    # hand, has nothing to copy - and that is a normal state, not a half-finished org.
    # Blank counts as unset: an empty repository variable renders as an empty env value,
    # and publishing that would set a secret nobody can tell from a real address.
    published: list = []
    _propagating_run(monkeypatch, published)
    if value is None:
        monkeypatch.delenv(mailer.MAINTAINER_ENV, raising=False)
    else:
        monkeypatch.setenv(mailer.MAINTAINER_ENV, value)

    assert bc.main() == 0
    assert published == [("Course-Org", "DSL_BOT_TOKEN", "s3cret")]
    out = capsys.readouterr().out
    assert f"[skip] {mailer.MAINTAINER_ENV} not in this run's env" in out
    assert "falls back to GRAPH_SENDER" in out


def test_a_failed_maintainer_write_reds_the_bootstrap(monkeypatch, capsys):
    # The address was meant to be on this org and silently is not: every fault mail from
    # it goes to the shared send mailbox instead, weeks later, with a green run behind it.
    stub_bootstrap(monkeypatch)
    monkeypatch.setenv("DSL_BOT_TOKEN", "s3cret")
    monkeypatch.setenv(mailer.MAINTAINER_ENV, ADDRESS)
    monkeypatch.setattr(
        bc, "set_org_secret", lambda org, name, value: name != mailer.MAINTAINER_ENV
    )
    monkeypatch.setattr(
        "sys.argv", ["bootstrap_course", "--org", "Course-Org", "--propagate-secret"]
    )

    assert bc.main() == 1
    assert "bootstrap incomplete" in capsys.readouterr().err


def test_without_propagate_secret_the_address_is_not_published(monkeypatch):
    # A hand-run bootstrap that only VALIDATES the token has no central env to copy from -
    # whatever happens to be exported on the maintainer's laptop is not the estate's
    # setting, and writing it onto an org would be the same mistake ghcli.bot_token
    # refuses to make with a personal GH_TOKEN.
    stub_bootstrap(monkeypatch)
    monkeypatch.setenv(mailer.MAINTAINER_ENV, ADDRESS)
    published: list = []
    monkeypatch.setattr(bc, "set_org_secret", lambda *a: published.append(a) or True)
    monkeypatch.setattr("sys.argv", ["bootstrap_course", "--org", "Course-Org"])

    assert bc.main() == 0
    assert published == []


def test_the_maintainer_address_travels_by_the_org_secret_route(monkeypatch):
    # The same call the bot token makes, so the public `.github` receives it (a `gh` org
    # secret defaults to `private` visibility, which excludes public repos) and every
    # PRIVATE infra repo gets the mirror the Free-plan delivery gap needs.
    calls: list = []
    monkeypatch.setenv(mailer.MAINTAINER_ENV, ADDRESS)
    monkeypatch.setattr(bc, "repo_exists", lambda org, r: True)
    monkeypatch.setattr(bc, "repo_is_private", lambda org, r: r == "classroom-config")
    monkeypatch.setattr(bc, "gh", lambda *a, **k: calls.append((a, k)) or (0, ""))

    assert bc.propagate_maintainer_email("Course-Org") == 0
    org_call, mirror = calls
    assert org_call[0][:3] == ("secret", "set", mailer.MAINTAINER_ENV)
    assert org_call[0][org_call[0].index("--visibility") + 1] == "selected"
    assert ".github" in org_call[0][org_call[0].index("--repos") + 1]
    assert mirror[0][mirror[0].index("--repo") + 1] == "Course-Org/classroom-config"
    # stdin, never argv: `ps` on a shared runner reads the whole command line.
    for args, kwargs in calls:
        assert ADDRESS not in args
        assert kwargs["stdin"] == ADDRESS


def test_the_maintainer_address_never_reaches_the_log(monkeypatch, capsys):
    # The log of a course org's `.github` is world-readable, and this address is a real
    # person's inbox. The NAME is what a maintainer needs to see; the value never is.
    monkeypatch.setenv(mailer.MAINTAINER_ENV, ADDRESS)
    monkeypatch.setattr(bc, "repo_exists", lambda org, r: True)
    monkeypatch.setattr(bc, "repo_is_private", lambda org, r: False)
    monkeypatch.setattr(bc, "gh", lambda *a, **k: (0, ""))

    assert bc.propagate_maintainer_email("Course-Org") == 0
    captured = capsys.readouterr()
    assert ADDRESS not in captured.out + captured.err
    assert mailer.MAINTAINER_ENV in captured.out

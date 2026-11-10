"""The PO-token sidecar wiring: CLIPS_STUDIO_POT_PROVIDER_URL becomes a
base_url extractor argument on every yt-dlp request the engine makes, and is
absent — the options untouched — when no sidecar was pointed at it.

Proof-of-Origin tokens are how yt-dlp defeats YouTube's "Sign in to confirm
you're not a bot" wall on a hosted IP (the yt-dlp PO Token Guide's own
recommendation, via the bgutil provider). compose runs that provider as
pot-provider; the engine reaches it through this one environment variable
because inside a container, the plugin's 127.0.0.1 default is the engine
itself. Nothing here signs in: the token attests the client, an account
never enters the picture, and a desktop install without the variable keeps
the anonymous download it always had.
"""

from sources import ytdlp_common

ENV = ytdlp_common._PROVIDER_ENV


def test_without_a_sidecar_the_options_are_untouched(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    assert ytdlp_common._provider_opts({"quiet": True}) == {"quiet": True}


def test_the_sidecars_address_becomes_an_extractor_argument(monkeypatch):
    monkeypatch.setenv(ENV, "http://pot-provider:4416")
    out = ytdlp_common._provider_opts({"quiet": True})
    assert out["extractor_args"] == {
        "youtubepot-bgutilhttp": {"base_url": ["http://pot-provider:4416"]}
    }
    assert out["quiet"] is True


def test_a_callers_own_extractor_arguments_survive(monkeypatch):
    monkeypatch.setenv(ENV, "http://pot-provider:4416")
    caller = {"extractor_args": {"youtube": {"player_client": ["mweb"]}}}
    out = ytdlp_common._provider_opts(caller)
    assert out["extractor_args"]["youtube"] == {"player_client": ["mweb"]}
    assert out["extractor_args"]["youtubepot-bgutilhttp"] == {
        "base_url": ["http://pot-provider:4416"]
    }


def test_the_callers_dict_is_not_mutated(monkeypatch):
    monkeypatch.setenv(ENV, "http://pot-provider:4416")
    opts = {"quiet": True}
    ytdlp_common._provider_opts(opts)
    assert opts == {"quiet": True}

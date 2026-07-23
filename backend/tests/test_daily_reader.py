"""Tests for the daily reading layer (:mod:`pharos.daily.reader`).

**No test here touches the network.** An autouse fixture replaces
``urllib.request.urlopen`` with a bomb; cases that need a provider install a
fake OpenAI-compatible endpoint over it. This has to hold behind the GFW and in
CI, and it also means no test can ever spend the user's tokens.

The module's own stated invariant is *never fabricate*, and that is what most
of this file exercises. Two properties matter more than the rest:

**An unconfigured install must produce "待解读", never a summary.** That is the
first test below, and it is the single most important one in the suite. If it
ever goes green while ``read_paper`` returns something, the user's reading list
has been quietly poisoned with invented content — a failure they cannot detect
by looking, because a fabricated card is indistinguishable from a real one.

**A malformed answer must fail loudly rather than be patched up.** Every
validation case asserts a raise, not a repaired card. Filling in a missing
``results`` highlight locally would be the same defect wearing a hat.

The other running theme is key hygiene: the API key is asserted absent from
request bodies, exception messages, and returned values, because exception text
here is persisted verbatim into ``DailyPaper.read_error`` and rendered in the
UI. That is a one-way door.
"""

from __future__ import annotations

import dataclasses
import email.message
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest
from pharos.config import LLMProvider, Settings
from pharos.daily import reader
from pharos.daily.directions import DIRECTIONS
from pharos.daily.reader import (
    HIGHLIGHT_KEYS,
    SCORE_KEYS,
    InvalidReading,
    ProviderError,
    ReaderError,
    ReaderUnavailable,
    is_available,
    read_paper,
)

API_KEY = "sk-must-never-appear-anywhere-in-output"

# --------------------------------------------------------------------------- #
# isolation
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if a test reaches the real ``urlopen``."""

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a test attempted a live network call")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record retry backoff instead of serving it."""
    slept: list[float] = []
    monkeypatch.setattr(reader.time, "sleep", slept.append)
    return slept


class _FakeSettings:
    """Just enough of :class:`Settings` for the reader's one lookup."""

    def __init__(self, provider: LLMProvider | None) -> None:
        self._provider = provider

    def provider_for(self, task: str) -> LLMProvider | None:
        return self._provider if task == "chat" else None


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> LLMProvider:
    """A configured, entirely fictional OpenAI-compatible relay."""
    configured = LLMProvider(
        name="custom",
        api_key=API_KEY,
        base_url="https://relay.invalid/v1",
        model="test-model-1",
        timeout=5.0,
    )
    monkeypatch.setattr(reader, "get_settings", lambda: _FakeSettings(configured))
    return configured


# --------------------------------------------------------------------------- #
# a fake OpenAI-compatible endpoint
# --------------------------------------------------------------------------- #


class _Call:
    """One recorded request: what we sent, and to where."""

    def __init__(self, request: urllib.request.Request) -> None:
        self.url = request.full_url
        self.headers = dict(request.headers)
        raw = request.data if isinstance(request.data, (bytes, bytearray)) else b"{}"
        self.payload: dict[str, Any] = json.loads(raw)

    @property
    def messages(self) -> list[dict[str, str]]:
        return list(self.payload.get("messages", []))

    @property
    def system_prompt(self) -> str:
        return str(self.messages[0]["content"])

    @property
    def user_prompt(self) -> str:
        return str(self.messages[1]["content"])


class _FakeResponse:
    """Stand-in for the object ``urlopen`` yields, including ``read1``."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self.status = status
        self._buf = io.BytesIO(body)

    def read1(self, size: int = -1) -> bytes:
        return self._buf.read1(size)

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    """An HTTPError that still carries a readable vendor error body."""
    headers = email.message.Message()
    return urllib.error.HTTPError(
        "https://relay.invalid/v1/chat/completions", code, "err", headers, io.BytesIO(body)
    )


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch) -> Callable[..., list[_Call]]:
    """Queue responses for the fake endpoint; hand back the recorded calls.

    Each item is either a ``bytes`` body (served as 200), an exception to
    raise, or a ``_FakeResponse``. Running past the end is an error rather than
    a silent repeat, so a test cannot accidentally assert against a response it
    never queued.
    """

    def _install(*responses: Any) -> list[_Call]:
        calls: list[_Call] = []
        remaining = list(responses)

        def _fake_urlopen(request: urllib.request.Request, **kwargs: Any) -> _FakeResponse:
            calls.append(_Call(request))
            if not remaining:
                raise AssertionError(f"unexpected request #{len(calls)} to {request.full_url}")
            item = remaining.pop(0)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, _FakeResponse):
                return item
            return _FakeResponse(item)

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        return calls

    return _install


# --------------------------------------------------------------------------- #
# card fixtures
# --------------------------------------------------------------------------- #

_SUMMARY = (
    "这篇论文想解决 VLA 策略在长程操作上容易漂移的问题。作者把动作 token 和视觉 token "
    "一起送进一个统一的 transformer，用潜在动力学损失做辅助监督。在 LIBERO-Pro 上比最强"
    "基线高 38.6 个点，长程任务的提升尤其明显。"
)

_HIGHLIGHTS = {
    "contribution": "提出一个统一的动作-视觉 token 架构，缓解长程操作中的策略漂移。",
    "innovation": "相比 OpenVLA 只做行为克隆，这里额外引入了潜在动力学预测作为辅助任务。",
    "method": "输入是第三人称 RGB 加语言指令，主干是 7B VLM，损失为动作交叉熵加潜在预测 L2，"
    "在 970k 条真机轨迹上训练。",
    "results": "在 LIBERO-Pro 上成功率 84.2%，比最强基线高 38.6 个点，并给出了消融。",
}

#: No ``relevance``, deliberately. A reading is shared by every account on the
#: instance, so the model is no longer asked how relevant a paper is — that
#: question has no single answer here, and is settled per caller at query time
#: from their own directions. See ``test_rubric_asks_for_nothing_reader_specific``.
_SCORES = {
    "recency": 10,
    "popularity": 7.5,
    "quality": 8.0,
    "recommendation": 8.7,
}


def _card(**overrides: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "summary_zh": _SUMMARY,
        "highlights": dict(_HIGHLIGHTS),
        "scores": dict(_SCORES),
    }
    card.update(overrides)
    return card


def _completion(content: str, *, finish_reason: str = "stop") -> bytes:
    """A chat-completions envelope carrying ``content``."""
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": "test-model-1",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish_reason,
                }
            ],
        }
    ).encode()


def _envelope(**body: Any) -> bytes:
    return json.dumps(body).encode()


def _card_response(**overrides: Any) -> bytes:
    return _completion(json.dumps(_card(**overrides), ensure_ascii=False))


def _read() -> Any:
    """Read a fixed paper; every test uses the same input so cases differ only
    in what the provider answers."""
    return read_paper(
        "OpenVLA-2: A Vision-Language-Action Model",
        "We present a vision-language-action policy trained on 970k episodes.",
        domain="VLA",
        authors=["Ada Lovelace", "Grace Hopper"],
    )


# --------------------------------------------------------------------------- #
# THE critical property: no provider => no reading, and above all no invention
# --------------------------------------------------------------------------- #


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real :class:`Settings` with every credential removed from the environment.

    Uses the genuine Settings class rather than a stub so this exercises the
    actual "fresh clone, no .env" path a new user hits.
    """
    for name in [key for key in dict(__import__("os").environ) if key.startswith("PHAROS_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(reader, "get_settings", lambda: Settings(_env_file=None))


def test_is_available_is_false_without_a_provider(unconfigured: None) -> None:
    assert is_available() is False


def test_unconfigured_reader_raises_instead_of_inventing_a_card(unconfigured: None) -> None:
    """The most important test in this file.

    With no provider, ``read_paper`` must refuse. The caller leaves the paper
    ``pending`` and the UI says "待解读". Anything else here — a placeholder
    summary, a default score, an empty-but-successful Reading — is a card the
    user will read as if a model wrote it.
    """
    with pytest.raises(ReaderUnavailable) as excinfo:
        _read()

    # The message must tell the user how to fix it, not just that it broke.
    assert "PHAROS_CHAT_PROVIDER" in str(excinfo.value)


def test_reader_unavailable_is_not_an_error_state(unconfigured: None) -> None:
    """``pending`` and ``error`` are different outcomes, so they are different types.

    A caller writing ``except ProviderError`` must not accidentally mark an
    unconfigured install's papers as failed readings.
    """
    with pytest.raises(ReaderUnavailable):
        _read()
    assert issubclass(ReaderUnavailable, ReaderError)
    assert not issubclass(ReaderUnavailable, ProviderError)


def test_unconfigured_reader_never_reaches_the_network(unconfigured: None) -> None:
    """It fails before any request, so a batch of 60 papers costs nothing."""
    # _forbid_network is still armed; a request would raise AssertionError.
    with pytest.raises(ReaderUnavailable):
        _read()


def test_a_provider_missing_its_key_counts_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Half-configured is unconfigured — better than a confusing vendor 401."""
    half = LLMProvider(name="custom", api_key=None, base_url="https://relay.invalid", model="m")
    monkeypatch.setattr(
        reader, "get_settings", lambda: _FakeSettings(half if half.configured else None)
    )
    assert is_available() is False
    with pytest.raises(ReaderUnavailable):
        _read()


def test_is_available_is_true_once_configured(provider: LLMProvider) -> None:
    assert is_available() is True


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


def test_reads_a_clean_json_card(provider: LLMProvider, serve: Callable[..., list[_Call]]) -> None:
    calls = serve(_card_response())
    reading = _read()

    assert reading.summary_zh == _SUMMARY
    assert reading.highlights == _HIGHLIGHTS
    assert set(reading.scores) == set(SCORE_KEYS)
    assert reading.scores["recommendation"] == 8.7
    # An int in the JSON becomes a float, so the column type is stable.
    assert reading.scores["recency"] == 10.0
    # The model that actually produced the card, for later re-reads.
    assert reading.model == "test-model-1"
    assert len(calls) == 1


def test_posts_to_the_openai_compatible_endpoint(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    calls = serve(_card_response())
    _read()
    assert calls[0].url == "https://relay.invalid/v1/chat/completions"
    assert calls[0].payload["model"] == "test-model-1"


def test_reading_is_immutable(provider: LLMProvider, serve: Callable[..., list[_Call]]) -> None:
    """A Reading records what a named model said; editing it makes that a lie."""
    serve(_card_response())
    reading = _read()
    with pytest.raises(dataclasses.FrozenInstanceError):
        reading.summary_zh = "tampered"


def test_prompt_carries_the_paper_and_the_matched_direction(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    calls = serve(_card_response())
    _read()
    user = calls[0].user_prompt
    assert "OpenVLA-2" in user
    assert "970k episodes" in user
    assert "VLA" in user  # anchors `relevance`
    assert "Ada Lovelace" in user  # anchors `popularity`


def test_rubric_asks_for_nothing_reader_specific(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """The rubric must not ask a shared reading to judge a personal question.

    Replaces ``test_prompt_describes_every_configured_direction``, which asserted
    the opposite: that every entry of the global ``DIRECTIONS`` table appeared in
    the system prompt, so the model could score ``relevance`` against it. That
    made sense while the operator's interests *were* everyone's. With directions
    configured per user it is the bug — one paper is read once and the card is
    shown to every account, so a relevance scored here is scored against somebody
    else's reading list while looking, on the card, entirely personal.

    So the assertion is inverted: the prompt must not request a relevance score,
    must not present an interest list to score against, and must say plainly that
    the card is shared. Relevance now comes from
    :func:`pharos.daily.user_directions.relevance_for` at query time.
    """
    calls = serve(_card_response())
    _read()
    system = calls[0].system_prompt

    # Checked against the *requested JSON schema*, where every key the model is
    # asked for appears quoted. A bare "relevance" is expected elsewhere in the
    # prompt — it is named explicitly in order to be forbidden, and telling the
    # model "do not emit relevance" is far more reliable than hoping it does not
    # think of it.
    assert '"relevance"' not in system
    assert "不要输出 `relevance`" in system
    assert "共享" in system  # the prompt states the card is shared

    # The old rubric listed the global directions verbatim as interest areas.
    # None of them may appear as a thing the model is told the reader cares about.
    for direction in DIRECTIONS:
        assert direction not in system

    # What it does still ask for, all properties of the paper itself.
    for key in ("recency", "popularity", "quality", "recommendation"):
        assert f'"{key}"' in system


def test_relevance_from_the_model_is_ignored_rather_than_rejected(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """A model that emits ``relevance`` anyway must not fail the card.

    Prompts are instructions, not schemas, and an older or chattier model may
    volunteer the key. Dropping it silently is right — the card is otherwise
    perfectly good, and the number would be overridden downstream regardless —
    but it must not survive into ``Reading.scores``, where a caller could mistake
    it for something this reader asked about.
    """
    serve(_card_response(scores={**_SCORES, "relevance": 9.2}))
    reading = _read()
    assert "relevance" not in reading.scores
    assert set(reading.scores) == set(SCORE_KEYS)


def test_prompt_forbids_inventing_numbers(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """The never-fabricate rule is instructed as well as validated."""
    calls = serve(_card_response())
    _read()
    assert "不要编造" in calls[0].system_prompt


def test_scores_are_rounded_to_one_decimal(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    serve(_card_response(scores={**_SCORES, "quality": 7.4499}))
    assert _read().scores["quality"] == 7.4


def test_numeric_strings_are_accepted_as_scores(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """Models routinely quote numbers; that is a formatting slip, not a lie."""
    serve(_card_response(scores={**_SCORES, "popularity": "8.5"}))
    assert _read().scores["popularity"] == 8.5


def test_long_abstract_is_truncated_before_sending(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """A 100k-character "abstract" is a broken record, not extra signal."""
    calls = serve(_card_response())
    read_paper("A Title", "world model " * 20_000)
    assert len(calls[0].user_prompt) < 10_000
    assert "…" in calls[0].user_prompt


def test_author_list_is_capped(provider: LLMProvider, serve: Callable[..., list[_Call]]) -> None:
    """Large collaborations run to 300 names; the lab signal is in the first few."""
    calls = serve(_card_response())
    read_paper("A Title", "An abstract about a world model.", authors=[f"A{i}" for i in range(50)])
    assert "A11" in calls[0].user_prompt
    assert "A49" not in calls[0].user_prompt


# --------------------------------------------------------------------------- #
# response parsing — JSON mode is best-effort, so the content varies
# --------------------------------------------------------------------------- #


def test_parses_json_inside_a_markdown_fence(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    blob = json.dumps(_card(), ensure_ascii=False)
    serve(_completion(f"```json\n{blob}\n```"))
    assert _read().summary_zh == _SUMMARY


def test_parses_json_surrounded_by_prose(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    blob = json.dumps(_card(), ensure_ascii=False)
    serve(_completion(f"好的，这是这篇论文的卡片：\n\n{blob}\n\n希望有帮助！"))
    assert _read().summary_zh == _SUMMARY


def test_braces_inside_string_values_do_not_break_extraction(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """A greedy or lazy regex fails here; balanced scanning must track strings.

    The summary carries an unbalanced ``}`` and the method a nested ``{...}``,
    both inside JSON strings. A lazy ``\\{.*?\\}`` stops at the first of them
    and a greedy one swallows the trailing prose.
    """
    summary = (
        '这篇论文提出用 {state, action} 二元组表示轨迹，输出格式形如 {"a": 1} 这样的结构，'
        "并且在解析时以 } 作为终止符。方法简单但在 LIBERO 上很有效，提升了 12 个点。"
    )
    highlights = {
        **_HIGHLIGHTS,
        "method": "用 {obs, act} 配对输入，损失写作 L = ||a - â||^2 的形式。",
    }
    blob = json.dumps(_card(summary_zh=summary, highlights=highlights), ensure_ascii=False)
    serve(_completion(f"卡片如下：\n{blob}\n以上。"))

    reading = _read()
    assert reading.summary_zh == summary
    assert reading.highlights["method"] == highlights["method"]


def test_escaped_quotes_inside_strings_survive(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    summary = (
        '作者把这种做法称为 "latent rollout"，并在三个 benchmark 上验证了它的有效性，'
        "平均提升 5 个点。"
    )
    serve(_completion(json.dumps(_card(summary_zh=summary), ensure_ascii=False)))
    assert _read().summary_zh == summary


@pytest.mark.parametrize(
    "content",
    [
        "抱歉，我无法完成这个请求。",  # prose only, no object at all
        "",  # handled upstream as empty content
        "{ 这不是 JSON",  # unterminated
        '{"summary_zh": }',  # syntactically broken
        "[1, 2, 3]",  # valid JSON, wrong shape
    ],
)
def test_unusable_content_raises_rather_than_returning_a_blank_card(
    provider: LLMProvider, serve: Callable[..., list[_Call]], content: str
) -> None:
    serve(_completion(content))
    with pytest.raises(ReaderError):
        _read()


# --------------------------------------------------------------------------- #
# validation — strict, and deliberately non-repairing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("missing", HIGHLIGHT_KEYS)
def test_a_missing_highlight_fails_the_card(
    provider: LLMProvider, serve: Callable[..., list[_Call]], missing: str
) -> None:
    """Three-quarters of a card is not a card.

    Filling the gap locally would produce something the user reads as the
    model's judgement, which is the exact failure this module exists to avoid.
    """
    highlights = {k: v for k, v in _HIGHLIGHTS.items() if k != missing}
    serve(_card_response(highlights=highlights))
    with pytest.raises(InvalidReading, match=missing):
        _read()


def test_an_empty_highlight_fails_the_card(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    serve(_card_response(highlights={**_HIGHLIGHTS, "results": "   "}))
    with pytest.raises(InvalidReading, match="results"):
        _read()


@pytest.mark.parametrize("summary", ["", "   ", None, 42, ["a", "b"]])
def test_an_empty_or_non_string_summary_fails_the_card(
    provider: LLMProvider, serve: Callable[..., list[_Call]], summary: Any
) -> None:
    serve(_card_response(summary_zh=summary))
    with pytest.raises(InvalidReading, match="summary_zh"):
        _read()


def test_a_one_liner_summary_fails_the_card(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """The card is a 2-4 sentence read, not a headline."""
    serve(_card_response(summary_zh="一个新的 VLA 模型。"))
    with pytest.raises(InvalidReading, match="too short"):
        _read()


def test_an_echoed_abstract_fails_the_card(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """A wall of text means the model translated instead of summarising."""
    serve(_card_response(summary_zh="这篇论文提出了一个方法。" * 200))
    with pytest.raises(InvalidReading, match="too long"):
        _read()


@pytest.mark.parametrize("missing", SCORE_KEYS)
def test_a_missing_score_fails_the_card(
    provider: LLMProvider, serve: Callable[..., list[_Call]], missing: str
) -> None:
    """A missing score cannot be defaulted: 7.0 is itself a claim."""
    serve(_card_response(scores={k: v for k, v in _SCORES.items() if k != missing}))
    with pytest.raises(InvalidReading, match=missing):
        _read()


@pytest.mark.parametrize("bad", ["high", "很高", None, "", [8], {"value": 8}, True, False])
def test_a_non_numeric_score_fails_the_card(
    provider: LLMProvider, serve: Callable[..., list[_Call]], bad: Any
) -> None:
    """Including booleans, which are ints in Python and would silently score 1.0."""
    serve(_card_response(scores={**_SCORES, "popularity": bad}))
    with pytest.raises(InvalidReading, match="popularity"):
        _read()


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_score_fails_the_card(
    provider: LLMProvider, serve: Callable[..., list[_Call]], bad: float
) -> None:
    # json.dumps emits bare NaN/Infinity, which the module's own json.loads accepts.
    serve(_completion(json.dumps(_card(scores={**_SCORES, "quality": bad}), ensure_ascii=False)))
    with pytest.raises(InvalidReading, match="quality"):
        _read()


@pytest.mark.parametrize(("given", "expected"), [(15, 10.0), (-3, 0.0), (11.5, 10.0)])
def test_an_out_of_range_score_is_clamped_not_rejected(
    provider: LLMProvider, serve: Callable[..., list[_Call]], given: float, expected: float
) -> None:
    """Documents a deliberate asymmetry in the module, worth knowing about.

    A *missing* or *non-numeric* score raises, because there is nothing to
    represent. An out-of-range one is clamped instead: the number is real, the
    model's relative ordering survives, and the score column stays inside the
    range the UI renders. Change this only on purpose — a clamp does discard
    ordering *among* out-of-range values, so if the model starts emitting 12s
    and 15s regularly it is the prompt that needs fixing, not the clamp.
    """
    serve(_card_response(scores={**_SCORES, "popularity": given}))
    assert _read().scores["popularity"] == expected


@pytest.mark.parametrize("shape", [None, "not an object", ["a"], 7])
def test_malformed_highlights_and_scores_containers_fail(
    provider: LLMProvider, serve: Callable[..., list[_Call]], shape: Any
) -> None:
    serve(_card_response(highlights=shape))
    with pytest.raises(InvalidReading, match="highlights"):
        _read()


@pytest.mark.parametrize("shape", [None, "not an object", ["a"], 7])
def test_malformed_scores_container_fails(
    provider: LLMProvider, serve: Callable[..., list[_Call]], shape: Any
) -> None:
    serve(_card_response(scores=shape))
    with pytest.raises(InvalidReading, match="scores"):
        _read()


# --------------------------------------------------------------------------- #
# the language gate
# --------------------------------------------------------------------------- #


def test_an_english_summary_is_rejected(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """English output means the model ignored the instruction — a failure.

    The user reads these cards in Chinese; an English one is not a card that
    merely needs formatting, it is evidence the model did not follow the brief.
    """
    english = (
        "This paper proposes a unified action-vision token architecture that "
        "mitigates policy drift in long-horizon manipulation, reaching 84.2% "
        "success on LIBERO-Pro, a gain of 38.6 points over the strongest baseline."
    )
    serve(_card_response(summary_zh=english))
    with pytest.raises(InvalidReading, match="not Chinese"):
        _read()


def test_all_english_highlights_are_rejected(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    english = {
        "contribution": "A unified action-vision token architecture.",
        "innovation": "Adds latent dynamics prediction as an auxiliary task.",
        "method": "A 7B VLM backbone trained on 970k real robot trajectories.",
        "results": "84.2% success on LIBERO-Pro, 38.6 points above the baseline.",
    }
    serve(_card_response(highlights=english))
    with pytest.raises(InvalidReading, match="highlights"):
        _read()


def test_chinese_heavy_in_technical_english_is_accepted(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """The gate is a floor, not a purity test — real cards look like this.

    The prompt explicitly tells the model to keep model, benchmark and metric
    names in English, so a naive "must be mostly Chinese characters" check
    would reject the module's own intended output.
    """
    dense = (
        "作者用 OpenVLA-7B 做 backbone，在 LIBERO-Pro、CALVIN ABC-D 和 RLBench 上评估，"
        "success rate 分别是 84.2%、71.0% 和 66.3%，比 Diffusion Policy 与 RT-2-X 都高。"
    )
    serve(_card_response(summary_zh=dense))
    assert _read().summary_zh == dense


def test_a_token_chinese_phrase_does_not_pass_the_gate(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """A ratio alone would let "好。" score 1.0, so there is a floor on count too."""
    serve(
        _card_response(
            highlights={
                "contribution": "好。",
                "innovation": "New.",
                "method": "A 7B VLM backbone trained on 970k trajectories with an L2 loss.",
                "results": "84.2% success on LIBERO-Pro, well above every prior baseline.",
            }
        )
    )
    with pytest.raises(InvalidReading, match="highlights"):
        _read()


# --------------------------------------------------------------------------- #
# provider-side failures
# --------------------------------------------------------------------------- #


def test_empty_choices_is_a_provider_error(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    serve(_envelope(choices=[]))
    with pytest.raises(ProviderError, match="no choices"):
        _read()


def test_a_non_json_body_is_a_provider_error(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """A captive portal or misrouted proxy answering HTML with a 200."""
    serve(b"<html><body>Gateway</body></html>")
    with pytest.raises(ProviderError, match="non-JSON"):
        _read()


def test_an_error_envelope_returned_with_200_is_surfaced(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """Some relays report failures in the body while still answering 200."""
    serve(_envelope(error={"message": "model overloaded", "type": "server_error"}))
    with pytest.raises(ProviderError, match="model overloaded"):
        _read()


def test_a_refusal_is_reported_as_itself(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """The user should see *why* the paper went unread, not a parse error."""
    body = json.dumps(
        {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": None, "refusal": "I can't help."},
                    "finish_reason": "stop",
                }
            ]
        }
    ).encode()
    serve(body)
    with pytest.raises(ProviderError, match="refused"):
        _read()


def test_a_truncated_card_is_refused_not_salvaged(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """``finish_reason="length"`` means the object never finished.

    A partial card is exactly the kind of half-truth that must not be stored:
    the fields that made it through look completely normal.
    """
    partial = json.dumps(_card(), ensure_ascii=False)[:200]
    serve(_completion(partial, finish_reason="length"))
    with pytest.raises(ProviderError, match="truncat|token limit"):
        _read()


def test_empty_content_reports_the_finish_reason(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    serve(_completion("", finish_reason="content_filter"))
    with pytest.raises(ProviderError, match="content_filter"):
        _read()


def test_an_unreachable_provider_is_a_provider_error(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    serve(urllib.error.URLError("name resolution failed"))
    with pytest.raises(ProviderError, match="unreachable"):
        _read()


def test_a_socket_timeout_is_a_provider_error(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    serve(TimeoutError("timed out"))
    with pytest.raises(ProviderError):
        _read()


def test_a_4xx_is_not_retried(provider: LLMProvider, serve: Callable[..., list[_Call]]) -> None:
    """401 means the key is wrong; repeating it just burns the rate limit."""
    calls = serve(_http_error(401, b'{"error":{"message":"invalid api key"}}'))
    with pytest.raises(ProviderError, match="401"):
        _read()
    assert len(calls) == 1


def test_a_rate_limit_is_retried_then_succeeds(
    provider: LLMProvider, serve: Callable[..., list[_Call]], _no_sleeping: list[float]
) -> None:
    """This is a 60-paper unattended batch; one 429 must not fail the day."""
    calls = serve(_http_error(429, b"slow down"), _card_response())
    assert _read().summary_zh == _SUMMARY
    assert len(calls) == 2
    assert _no_sleeping  # it actually backed off


def test_retries_are_bounded(
    provider: LLMProvider, serve: Callable[..., list[_Call]], _no_sleeping: list[float]
) -> None:
    calls = serve(*[_http_error(429, b"slow down")] * 3)
    with pytest.raises(ProviderError, match="429"):
        _read()
    assert len(calls) == 3


def test_a_relay_rejecting_json_mode_is_retried_without_it(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """JSON mode is best-effort; the prompt already asks for a bare object.

    Failing a paper over an optional parameter a self-hosted relay does not
    implement would be a pointless loss.
    """
    calls = serve(
        _http_error(400, b'{"error":{"message":"response_format is not supported"}}'),
        _card_response(),
    )
    assert _read().summary_zh == _SUMMARY
    assert "response_format" in calls[0].payload
    assert "response_format" not in calls[1].payload


def test_an_unrelated_400_is_not_retried(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    calls = serve(_http_error(400, b'{"error":{"message":"context length exceeded"}}'))
    with pytest.raises(ProviderError, match="400"):
        _read()
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# refusing to read nothing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("title", "abstract"),
    [("", "An abstract."), ("A Title", ""), ("  ", "  "), ("A Title", "   ")],
)
def test_an_empty_paper_is_refused_without_calling_the_model(
    provider: LLMProvider, serve: Callable[..., list[_Call]], title: str, abstract: str
) -> None:
    """Asking for a card about an empty abstract is asking to have one invented."""
    calls = serve()  # any request at all fails the test
    with pytest.raises(InvalidReading):
        read_paper(title, abstract)
    assert calls == []


# --------------------------------------------------------------------------- #
# key hygiene — exception text lands in the DB and then on screen
# --------------------------------------------------------------------------- #


def test_the_key_travels_only_in_the_authorization_header(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    calls = serve(_card_response())
    _read()
    call = calls[0]
    assert call.headers["Authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in json.dumps(call.payload)
    assert API_KEY not in call.url


def test_a_key_echoed_back_by_the_provider_is_scrubbed(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    """Adversarial: some gateways quote the offending credential in their error.

    That message is persisted verbatim into ``DailyPaper.read_error`` and
    rendered in the UI, so scrubbing has to happen before it is raised — this
    is a one-way door.
    """
    body = json.dumps({"error": {"message": f"key {API_KEY} is revoked"}}).encode()
    serve(_http_error(403, body))
    with pytest.raises(ProviderError) as excinfo:
        _read()

    assert API_KEY not in str(excinfo.value)
    assert "***" in str(excinfo.value)


def test_a_key_echoed_in_a_200_error_envelope_is_scrubbed(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    serve(_envelope(error={"message": f"bad credential {API_KEY}"}))
    with pytest.raises(ProviderError) as excinfo:
        _read()
    assert API_KEY not in str(excinfo.value)


@pytest.mark.parametrize(
    "response",
    [
        b"<html>gateway</html>",
        json.dumps({"choices": []}).encode(),
        json.dumps({"error": {"message": "boom"}}).encode(),
    ],
)
def test_no_failure_path_leaks_the_key(
    provider: LLMProvider, serve: Callable[..., list[_Call]], response: bytes
) -> None:
    """Swept across failure shapes, since each builds its message differently."""
    serve(response)
    with pytest.raises(ReaderError) as excinfo:
        _read()
    assert API_KEY not in repr(excinfo.value)
    assert API_KEY not in str(excinfo.value.args)


def test_a_successful_reading_carries_no_credential(
    provider: LLMProvider, serve: Callable[..., list[_Call]]
) -> None:
    serve(_card_response())
    reading = _read()
    assert API_KEY not in repr(reading)

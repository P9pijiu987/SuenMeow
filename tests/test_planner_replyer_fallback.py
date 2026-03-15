import pytest

from bot.planner import Planner, PlannerInput
from bot.replyer import Replyer


class FakeLlmClient:
    class _Resp:
        def __init__(self, content: str) -> None:
            self.content = content

    async def chat(self, route_name: str, system_prompt: str, user_prompt: str, temperature: float = 0.7):
        return self._Resp(
            '{"should_reply": false, "priority": "skip", "target_username": null, '
            '"target_post_number": null, "reason": "content is not worth a reply", '
            '"style_notes": "none", "memory_action": "none"}'
        )

    @staticmethod
    def parse_json_object(content: str):
        import json

        return json.loads(content)


@pytest.mark.anyio
async def test_planner_falls_back_without_llm() -> None:
    planner = Planner()
    decision = await planner.decide(
        PlannerInput(
            trigger_reason="notification",
            topic_id=1,
            posts=[{"username": "alice", "post_number": 3, "raw_text": "hello world"}],
            user_memories={"alice": "likes concise replies"},
            self_memory="stay concise",
        ),
        "hello world context",
        llm_client=None,
    )
    assert decision.should_reply is True
    assert decision.target_username == "alice"
    assert "memory-aware" in decision.style_notes


@pytest.mark.anyio
async def test_planner_uses_model_reason_and_should_reply_flag() -> None:
    planner = Planner()
    decision = await planner.decide(
        PlannerInput(
            trigger_reason="notification",
            topic_id=1,
            posts=[{"username": "alice", "post_number": 3, "raw_text": "hello world"}],
            user_memories={},
            self_memory="",
        ),
        "hello world context",
        llm_client=FakeLlmClient(),
        system_prompt="planner",
        user_prompt="ctx",
    )
    assert decision.should_reply is False
    assert decision.reason == "content is not worth a reply"


@pytest.mark.anyio
async def test_replyer_falls_back_without_llm() -> None:
    replyer = Replyer()
    draft = await replyer.generate(
        decision=(await Planner().decide(
            PlannerInput(
                trigger_reason="notification",
                topic_id=1,
                posts=[{"username": "alice", "post_number": 3, "raw_text": "hello world"}],
                user_memories={},
                self_memory="",
            ),
            "hello world context",
            llm_client=None,
        )),
        context="ctx",
        persona_text="# Core Persona\n\nObservant",
        llm_client=None,
    )
    assert draft.skipped is False
    assert "alice" in draft.content
